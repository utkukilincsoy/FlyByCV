"""Cevaplar sekmesi: kategori filtresi, tablo, sağda önizleme + elle düzeltme, "Şimdi kontrol et".

"Şimdi kontrol et" IMAP'a çıktığı için (CLAUDE.md kural 8) `src/ui/jobs.py::check_inbox_job`'u
`src/ui/workers.py::run_with_progress` ile arka planda çalıştırır -- pencere donmaz. Elle
düzeltme kısa ömürlü bir oturumla GUI iş parçacığında yapılır (`queue_tab.py`'nin onayla/reddet
kalıbıyla aynı -- anlık bir DB yazması, arka plana almaya gerek yok).

**Periyodik otomatik kontrol (Adım 7.7):** bir `QTimer`, sabit kısa bir aralıkla (`AUTO_CHECK_
TICK_MS`) `src/ui/replies_data.py::should_auto_check`'e sorar -- asıl "ne sıklıkla" kararı
`Setting.inbox_check_minutes`den (Ayarlar sekmesi) okunur, `QTimer`'ın kendi aralığı DEĞİL.
Otomatik tur `run_with_progress`'in modal `QProgressDialog`'unu KULLANMAZ (kullanıcı hiç
tetiklemediği bir işlem onu bir diyalogla kesmemeli); sonucu sessizce tabloyu tazeler ve
`logging`e yazar. `_worker` alanı elle tetiklenen "Şimdi kontrol et" ile PAYLAŞILIR -- aynı anda
en fazla bir arka plan işi (`_guard_running` ile aynı kısıt).
"""
import logging
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from src.core.db import get_session
from src.core.models import EmailReply, utcnow
from src.core.settings import KEY_INBOX_CHECK_MINUTES, get_setting
from src.cv_builder.profile_loader import MasterProfile, load_profile
from src.ui.jobs import CheckInboxJobResult, check_inbox_job
from src.ui.icons import set_icon
from src.ui.replies_data import (
    CATEGORY_FILTER_CHOICES,
    ReplyRow,
    block_reply_company,
    format_check_summary,
    load_reply_rows,
    set_reply_category,
    should_auto_check,
    unblock_reply_company,
)
from src.ui.replies_model import ReplyCategoryProxy, ReplyTableModel
from src.ui.replies_preview import ReplyPreviewPanel
from src.ui.workers import JobWorker, run_with_progress

logger = logging.getLogger(__name__)

#: `QTimer`'ın kendi yoklama aralığı (1 dakika) -- gerçek zamanlama kararı `should_auto_check`'in
#: `Setting.inbox_check_minutes`den okuduğu aralıktır, bu yalnızca onu ne sıklıkla SORACAĞIMIZ.
AUTO_CHECK_TICK_MS = 60_000


class RepliesTab(QWidget):
    """Cevaplar sekmesi: üstte filtre + "Şimdi kontrol et", ortada tablo + önizleme paneli."""

    def __init__(
        self,
        engine: Engine,
        profile_path: Path,
        parent: Optional[QWidget] = None,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._profile_path = profile_path
        self._worker: Optional[JobWorker] = None
        #: `should_auto_check`'e enjekte edilen "şu an" -- testte gerçek saate bağlı olmasın diye
        #: (`src/sender/worker.py::process_queue`nun `clock` parametresiyle aynı kalıp).
        self._clock = clock
        #: Son otomatik DENEME zamanı (başarılı/başarısız fark etmez) -- `Setting.
        #: inbox_last_check_at` DEĞİL, bkz. `should_auto_check` docstring'i.
        self._last_auto_attempt_at: Optional[datetime] = None

        self._filter_box = QComboBox(self)
        for label, value in CATEGORY_FILTER_CHOICES:
            self._filter_box.addItem(label, value)

        self._refresh_button = QPushButton("Yenile", self)
        set_icon(self._refresh_button, "refresh")
        self._refresh_button.clicked.connect(self.reload)

        self._check_now_button = QPushButton("Şimdi kontrol et", self)
        self._check_now_button.clicked.connect(self._on_check_now)

        top_row = QHBoxLayout()
        top_row.addWidget(self._filter_box)
        top_row.addWidget(self._refresh_button)
        top_row.addWidget(self._check_now_button)
        top_row.addStretch(1)

        self._model = ReplyTableModel(self)
        self._proxy = ReplyCategoryProxy(self)
        self._proxy.setSourceModel(self._model)

        self._table = QTableView(self)
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setSortingEnabled(True)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        self._preview = ReplyPreviewPanel(self)
        self._preview.category_corrected.connect(self._on_category_corrected)
        self._preview.block_requested.connect(self._on_block_requested)
        self._preview.unblock_requested.connect(self._on_unblock_requested)

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.addWidget(self._table)
        splitter.addWidget(self._preview)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addWidget(splitter)

        # Filtre değişikliği bağlantısı, tüm seçenekler eklendikten SONRA kurulur ki `addItem`
        # sırasında (indeks -1 -> 0) gereksiz bir tetiklenme olmasın (`queue_tab.py` ile aynı).
        self._filter_box.currentIndexChanged.connect(self._on_filter_changed)

        self.reload()

        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(AUTO_CHECK_TICK_MS)
        self._auto_timer.timeout.connect(self._on_auto_tick)
        self._auto_timer.start()

    def reload(self) -> None:
        """Tabloyu veritabanından yeniden yükler."""
        with get_session(self._engine) as session:
            rows = load_reply_rows(session)
        self._model.set_rows(rows)
        self._refresh_preview(self._selected_row())

    def cancel_running_worker(self) -> None:
        """Çalışan bir arka plan işi varsa iptal eder ve kısa bir süre bekler; zamanlayıcıyı durdurur.

        `MainWindow.closeEvent` tarafından çağrılır.
        """
        self._auto_timer.stop()
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(5000)

    # -- seçim / satır yardımcıları ---------------------------------------------------

    def _selected_row(self) -> Optional[ReplyRow]:
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            return None
        source_index = self._proxy.mapToSource(indexes[0])
        return self._model.row_at(source_index.row())

    def _refresh_preview(self, row: Optional[ReplyRow]) -> None:
        if row is None:
            self._preview.clear()
            return
        self._preview.show_row(row)

    def _on_selection_changed(self, *_args: Any) -> None:
        self._refresh_preview(self._selected_row())

    def _on_filter_changed(self, index: int) -> None:
        self._proxy.set_category_filter(self._filter_box.itemData(index))

    # -- elle düzeltme (kısa ömürlü oturum, GUI iş parçacığı) --------------------------

    def _on_category_corrected(self, reply_id: int, category: str) -> None:
        with get_session(self._engine) as session:
            reply = session.get(EmailReply, reply_id)
            if reply is None:
                QMessageBox.warning(self, "Bulunamadı", "Bu cevap artık mevcut değil.")
                self.reload()
                return

            error = set_reply_category(session, reply, category)
            if error is not None:
                QMessageBox.warning(self, "Kaydedilemedi", error)
                return
            session.commit()

        self.reload()

    # -- elle engelle / engeli kaldır (Adım 10.8b, kısa ömürlü oturum, GUI iş parçacığı) -------

    def _on_block_requested(self, reply_id: int) -> None:
        with get_session(self._engine) as session:
            reply = session.get(EmailReply, reply_id)
            if reply is None:
                QMessageBox.warning(self, "Bulunamadı", "Bu cevap artık mevcut değil.")
                self.reload()
                return
            block_reply_company(session, reply)
            session.commit()
        self.reload()

    def _on_unblock_requested(self, reply_id: int) -> None:
        answer = QMessageBox.question(
            self,
            "Engeli kaldır",
            "Bu alan adının engeli kaldırılacak; şirkete tekrar e-posta gönderilebilir hale gelir "
            "(reddedilmiş başvuru kuyrukta yeniden onay bekler). "
            "Şirket bir daha yazılmamasını istemişse engeli kaldırmak sizin sorumluluğunuzdadır. "
            "Devam edilsin mi?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        with get_session(self._engine) as session:
            reply = session.get(EmailReply, reply_id)
            if reply is None:
                QMessageBox.warning(self, "Bulunamadı", "Bu cevap artık mevcut değil.")
                self.reload()
                return
            unblock_reply_company(session, reply)
            session.commit()
        self.reload()

    # -- "Şimdi kontrol et" (arka plan işçisi, IMAP'a çıkar) ----------------------------

    def is_job_running(self) -> bool:
        """Ana pencerenin yedekten geri yüklemeden önce sorduğu soru: IMAP kontrolü sürüyor mu?"""
        return self._worker is not None and self._worker.isRunning()

    def _guard_running(self) -> bool:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(
                self, "İş sürüyor", "Başka bir işlem bitmeden yeni bir işlem başlatılamaz."
            )
            return True
        return False

    def _on_check_now(self) -> None:
        if self._guard_running():
            return

        try:
            profile = load_profile(self._profile_path)
        except (FileNotFoundError, ValueError) as error:
            QMessageBox.critical(
                self,
                "Profil okunamadı",
                f"{self._profile_path} okunamadı veya bozuk:\n\n{error}",
            )
            return

        self._start_job(
            "Gelen kutusu kontrol ediliyor",
            partial(check_inbox_job, self._engine, profile),
            self._on_check_done,
        )

    def _start_job(
        self, title: str, job: Callable[..., Any], on_done: Callable[[Any], None]
    ) -> None:
        self._check_now_button.setEnabled(False)

        def _finish(result: Any) -> None:
            self._worker_finished()
            on_done(result)

        self._worker = run_with_progress(self, title, job, _finish)
        self._worker.failed.connect(lambda _message: self._worker_finished())

    def _worker_finished(self) -> None:
        self._check_now_button.setEnabled(True)
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _on_check_done(self, result: CheckInboxJobResult) -> None:
        if result.connection_error:
            QMessageBox.critical(self, "Bağlantı hatası", result.connection_error)
            return
        if result.no_applications_sent:
            QMessageBox.information(self, "Gelen kutusu", result.error)
            return
        if result.error:
            QMessageBox.warning(self, "Gelen kutusu kontrol edilemedi", result.error)
            return

        self.reload()
        QMessageBox.information(
            self, "Gelen kutusu kontrol edildi", format_check_summary(result.result)
        )

    # -- periyodik otomatik kontrol (Adım 7.7, QTimer -- sessiz, modal diyalog YOK) ----

    def _on_auto_tick(self) -> None:
        """`_auto_timer`'ın her turunda çağrılır; çoğu turda `should_auto_check` `False` döner."""
        if self._worker is not None and self._worker.isRunning():
            return  # elle tetiklenmiş "Şimdi kontrol et" (ya da önceki bir otomatik tur) sürüyor

        with get_session(self._engine) as session:
            interval_minutes = get_setting(session, KEY_INBOX_CHECK_MINUTES)

        now = self._clock()
        if not should_auto_check(now, self._last_auto_attempt_at, interval_minutes):
            return
        self._last_auto_attempt_at = now

        try:
            profile = load_profile(self._profile_path)
        except (FileNotFoundError, ValueError) as error:
            logger.warning(f"Otomatik gelen kutusu kontrolü atlandı: profil okunamadı ({error})")
            return

        self._start_auto_check_job(profile)

    def _start_auto_check_job(self, profile: MasterProfile) -> None:
        """`check_inbox_job`'u `run_with_progress`SİZ (modal ilerleme kutusu olmadan) çalıştırır.

        Kullanıcının hiç tetiklemediği bir tur onu bir diyalogla KESMEMELİ; sonuç sessizce
        tabloyu tazeler ve `logging`e yazar (bkz. `_on_auto_check_done`). `_worker` alanı elle
        tetiklenen "Şimdi kontrol et" ile PAYLAŞILIR, aynı anda en fazla bir arka plan işi çalışır.
        """
        self._check_now_button.setEnabled(False)
        worker = JobWorker(partial(check_inbox_job, self._engine, profile))
        self._worker = worker
        worker.finished_ok.connect(self._on_auto_check_done)
        worker.failed.connect(self._on_auto_check_failed)
        worker.start()

    def _on_auto_check_done(self, result: CheckInboxJobResult) -> None:
        self._worker_finished()
        if result.connection_error:
            logger.warning(f"Otomatik gelen kutusu kontrolü başarısız: {result.connection_error}")
            return
        if result.no_applications_sent:
            return
        if result.error:
            logger.warning(f"Otomatik gelen kutusu kontrolü tamamlanamadı: {result.error}")
            return

        self.reload()
        logger.info(f"Otomatik gelen kutusu kontrolü tamamlandı: {format_check_summary(result.result)}")

    def _on_auto_check_failed(self, message: str) -> None:
        self._worker_finished()
        logger.warning(f"Otomatik gelen kutusu kontrolü beklenmeyen şekilde başarısız oldu: {message}")
