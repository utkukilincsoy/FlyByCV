"""Onay Kuyruğu sekmesi: durum filtresi, kuyruk tablosu, sağda önizleme paneli, tekli işlemler,
toplu gönderim (başlat/durdur, canlı log, geri sayım).

Onayla/Reddet/kurtarma işlemleri kısa ömürlü bir oturumla GUI iş parçacığında yapılır (bunlar
anlık DB yazmaları). "Tek gönder" ve "Gönderimi başlat" ise SMTP'ye çıkabildiği için
`src/ui/jobs.py` + `src/ui/workers.py` üzerinden arka planda çalışır -- pencere donmaz
(CLAUDE.md kural 8): tekli gönderim `JobWorker` ile (5.1c), toplu tur `QueueWorker` ile (5.2b).
"""
import logging
import time
from functools import partial
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from src.core.db import get_session
from src.core.models import STATUS_SENDING, STATUS_SENT, Application
from src.core.settings import (
    KEY_SEND_MODE,
    KEY_SENDER_EMAIL,
    KEY_SENDING_PAUSED,
    KEY_TEST_MODE,
    SEND_MODE_LABELS_TR,
    get_setting,
)
from src.core.timeutil import now_istanbul
from src.cv_builder.profile_loader import load_profile
from src.sender.queue import approve_application, list_queue, mark_queued, mark_sent, reject_application
from src.sender.rate_limiter import can_send_now
from src.sender.smtp_sender import resolve_identity
from src.sender.worker import EVENT_FAILED, EVENT_SENT
from src.ui.icons import set_icon
from src.ui.jobs import QueueRunJobResult, SendJobResult, queue_run_job, send_one_job
from src.ui.queue_data import (
    STATUS_FILTER_CHOICES,
    ApplicationRow,
    format_countdown,
    format_log_line,
    format_run_summary,
    load_application_rows,
    plan_send,
)
from src.ui.queue_model import QueueStatusProxy, QueueTableModel
from src.ui.queue_preview import QueuePreviewPanel
from src.ui.workers import JobWorker, QueueWorker, run_with_progress

logger = logging.getLogger(__name__)

#: "Tek gönder" butonunun kapalı olduğu durumlar: zaten gönderilmiş ya da gönderimi sürüyor.
_SEND_BLOCKED_STATUSES = (STATUS_SENT, STATUS_SENDING)
#: Log panelinin en fazla tutacağı satır sayısı (bellek sınırı; uzun bir tur ekranı şişirmesin).
_LOG_MAX_BLOCK_COUNT = 2000


class QueueTab(QWidget):
    """Onay Kuyruğu sekmesi: üstte filtre, ortada tablo + önizleme, altta buton şeridi."""

    def __init__(
        self, engine: Engine, profile_path: Path, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._profile_path = profile_path
        self._worker: Optional[JobWorker] = None
        self._queue_worker: Optional[QueueWorker] = None
        self._autopilot_active = False

        self._filter_box = QComboBox(self)
        for label, value in STATUS_FILTER_CHOICES:
            self._filter_box.addItem(label, value)

        self._refresh_button = QPushButton("Yenile", self)
        set_icon(self._refresh_button, "refresh")
        self._refresh_button.clicked.connect(self.reload)

        top_row = QHBoxLayout()
        top_row.addWidget(self._filter_box)
        top_row.addWidget(self._refresh_button)
        top_row.addStretch(1)

        self._approve_button = QPushButton("Onayla", self)
        set_icon(self._approve_button, "approve")
        self._approve_button.clicked.connect(self._on_approve)

        self._reject_button = QPushButton("Reddet", self)
        set_icon(self._reject_button, "reject")
        self._reject_button.clicked.connect(self._on_reject)

        self._send_button = QPushButton("Tek gönder", self)
        set_icon(self._send_button, "send")
        self._send_button.clicked.connect(self._on_send_one)

        self._mark_sent_button = QPushButton("Gönderildi olarak işaretle", self)
        self._mark_sent_button.clicked.connect(self._on_mark_sent)

        self._mark_queued_button = QPushButton("Kuyruğa geri al", self)
        self._mark_queued_button.clicked.connect(self._on_mark_queued)

        button_row = QHBoxLayout()
        for button in (
            self._approve_button,
            self._reject_button,
            self._send_button,
            self._mark_sent_button,
            self._mark_queued_button,
        ):
            button_row.addWidget(button)
        button_row.addStretch(1)

        self._start_button = QPushButton("Gönderimi başlat", self)
        set_icon(self._start_button, "start")
        self._start_button.clicked.connect(self._on_start_queue_run)

        self._stop_button = QPushButton("Durdur", self)
        set_icon(self._stop_button, "stop")
        self._stop_button.clicked.connect(self._on_stop_queue_run)
        self._stop_button.setEnabled(False)

        self._countdown_label = QLabel(self)

        run_row = QHBoxLayout()
        run_row.addWidget(self._start_button)
        run_row.addWidget(self._stop_button)
        run_row.addWidget(self._countdown_label)
        run_row.addStretch(1)

        self._model = QueueTableModel(self)
        self._proxy = QueueStatusProxy(self)
        self._proxy.setSourceModel(self._model)

        self._table = QTableView(self)
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setSortingEnabled(True)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        self._preview = QueuePreviewPanel(self)

        table_splitter = QSplitter(Qt.Horizontal, self)
        table_splitter.addWidget(self._table)
        table_splitter.addWidget(self._preview)
        table_splitter.setStretchFactor(0, 2)
        table_splitter.setStretchFactor(1, 3)

        self._log_view = QPlainTextEdit(self)
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(_LOG_MAX_BLOCK_COUNT)

        outer_splitter = QSplitter(Qt.Vertical, self)
        outer_splitter.addWidget(table_splitter)
        outer_splitter.addWidget(self._log_view)
        outer_splitter.setStretchFactor(0, 3)
        outer_splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addLayout(button_row)
        layout.addLayout(run_row)
        layout.addWidget(outer_splitter)

        # Filtre değişikliği bağlantısı, tüm seçenekler eklendikten SONRA kurulur ki `addItem`
        # sırasında (indeks -1 -> 0) gereksiz bir tetiklenme olmasın.
        self._filter_box.currentIndexChanged.connect(self._on_filter_changed)

        self.reload()

    def reload(self) -> None:
        """Tabloyu veritabanından yeniden yükler."""
        with get_session(self._engine) as session:
            rows = load_application_rows(session)
        self._model.set_rows(rows)
        self._update_buttons(self._selected_row())
        self._refresh_preview(self._selected_row())

    def cancel_running_worker(self) -> None:
        """Çalışan bir arka plan işi varsa iptal eder ve kısa bir süre bekler.

        `MainWindow.closeEvent` tarafından çağrılır.
        """
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(5000)

    # -- seçim / satır yardımcıları ---------------------------------------------------

    def _selected_row(self) -> Optional[ApplicationRow]:
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            return None
        source_index = self._proxy.mapToSource(indexes[0])
        return self._model.row_at(source_index.row())

    def _update_buttons(self, row: Optional[ApplicationRow]) -> None:
        immutable = row is not None and row.status in (STATUS_SENT, STATUS_SENDING)
        self._approve_button.setEnabled(row is not None and not immutable)
        self._reject_button.setEnabled(row is not None and not immutable)
        self._send_button.setEnabled(
            row is not None
            and row.status not in _SEND_BLOCKED_STATUSES
            and not self._autopilot_active
        )

        recovering = row is not None and row.status == STATUS_SENDING
        self._mark_sent_button.setEnabled(recovering)
        self._mark_queued_button.setEnabled(recovering)

    def _refresh_preview(self, row: Optional[ApplicationRow]) -> None:
        if row is None:
            self._preview.clear()
            return
        with get_session(self._engine) as session:
            test_mode = bool(get_setting(session, KEY_TEST_MODE))
            identity_email = self._resolve_identity_email(session)
        self._preview.show_row(row, identity_email=identity_email, test_mode=test_mode)

    def _resolve_identity_email(self, session) -> str:
        """Önizlemedeki gönderen adresini bulur; profil bozuksa önizlemeyi ENGELLEMEZ.

        `sender_email` ayarı doluysa profile hiç bakılmaz (hızlı yol); boşsa profildeki
        kişisel bilgiye düşülür (`resolve_identity`), o da okunamazsa önizlemede bunu
        açıkça söyleyen bir metin gösterilir.
        """
        sender_email = get_setting(session, KEY_SENDER_EMAIL)
        if sender_email:
            return sender_email
        try:
            profile = load_profile(self._profile_path)
        except (FileNotFoundError, ValueError) as error:
            logger.warning(f"Profil okunamadı, önizlemede gönderen adresi gösterilemiyor: {error}")
            return "(profil okunamadı)"
        return resolve_identity(session, profile).email

    def _on_selection_changed(self, *_args: Any) -> None:
        row = self._selected_row()
        self._update_buttons(row)
        self._refresh_preview(row)

    def _on_filter_changed(self, index: int) -> None:
        self._proxy.set_status_filter(self._filter_box.itemData(index))

    # -- onayla / reddet / kurtarma (kısa ömürlü oturum, GUI iş parçacığı) --------------

    def _apply_queue_action(
        self, action: Callable[[Any, Application], Optional[str]], failure_title: str
    ) -> None:
        row = self._selected_row()
        if row is None:
            return

        with get_session(self._engine) as session:
            application = session.get(Application, row.id)
            if application is None:
                QMessageBox.warning(self, "Bulunamadı", "Bu başvuru artık mevcut değil.")
                self.reload()
                return

            reason = action(session, application)
            if reason is not None:
                QMessageBox.warning(self, failure_title, reason)
                return
            session.commit()

        self.reload()

    def _on_approve(self) -> None:
        self._apply_queue_action(approve_application, "Onaylanamadı")

    def _on_reject(self) -> None:
        self._apply_queue_action(reject_application, "Reddedilemedi")

    def _on_mark_sent(self) -> None:
        self._apply_queue_action(mark_sent, "İşaretlenemedi")

    def _on_mark_queued(self) -> None:
        self._apply_queue_action(mark_queued, "Kuyruğa alınamadı")

    # -- tek gönder (arka plan işçisi) -------------------------------------------------

    def _is_any_worker_running(self) -> bool:
        return (self._worker is not None and self._worker.isRunning()) or (
            self._queue_worker is not None and self._queue_worker.isRunning()
        )

    def is_job_running(self) -> bool:
        """Otopilot sekmesinin "Başlat"tan önce sorduğu soru: kendi bir işim çalışıyor mu?"""
        return self._is_any_worker_running()

    def set_autopilot_active(self, active: bool) -> None:
        """Otopilot çalışırken gönder butonlarını ("Tek gönder"/"Gönderimi başlat") kilitler.

        İkisi de aynı Gmail hesabını ve aynı `Application`/`Company` satırlarını Otopilot'la
        (`src/autopilot/runner.py`, MODE_AUTO altında `process_queue()`'yu KENDİSİ çağırıyor)
        paylaşıyor -- eşzamanlı çalışmaları aynı şirkete paralel iki gönderim denemesine yol
        açabilirdi. Onayla/Reddet gibi yalnızca DB yazan butonlar bundan ETKİLENMEZ.
        """
        self._autopilot_active = active
        self._update_buttons(self._selected_row())
        self._start_button.setEnabled(not active and not self._is_any_worker_running())

    def _guard_running(self) -> bool:
        if self._autopilot_active:
            QMessageBox.information(
                self, "Otopilot çalışıyor", "Otopilot çalışırken bu işlem kullanılamaz."
            )
            return True
        if self._is_any_worker_running():
            QMessageBox.information(
                self, "İş sürüyor", "Başka bir işlem bitmeden yeni bir işlem başlatılamaz."
            )
            return True
        return False

    def _on_send_one(self) -> None:
        if self._guard_running():
            return

        row = self._selected_row()
        if row is None:
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

        with get_session(self._engine) as session:
            sending_paused = bool(get_setting(session, KEY_SENDING_PAUSED))
            test_mode = bool(get_setting(session, KEY_TEST_MODE))
            window = can_send_now(session, test_mode=test_mode)

        plan = plan_send(row, window=window, sending_paused=sending_paused, test_mode=test_mode)

        if plan.blocked_reason:
            QMessageBox.critical(self, "Gönderim engellendi", plan.blocked_reason)
            return

        for prompt in plan.prompts:
            answer = QMessageBox.question(
                self,
                "Onay gerekli",
                prompt,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        self._start_job(
            "Mail gönderiliyor",
            partial(send_one_job, self._engine, row.id, profile, auto_mode=plan.auto_mode),
            self._on_send_done,
        )

    def _on_send_done(self, result: SendJobResult) -> None:
        self.reload()

        if result.no_account:
            QMessageBox.critical(
                self,
                "Şifre kayıtlı değil",
                "Mail uygulama şifresi kayıtlı değil. Kaydetmek için: "
                "python scripts/set_mail_password.py",
            )
            return
        if result.blocked_reason:
            QMessageBox.critical(self, "Gönderim engellendi", result.blocked_reason)
            return
        if result.refused_reason:
            QMessageBox.warning(self, "Gönderilmedi", result.refused_reason)
            return
        if result.error:
            QMessageBox.warning(self, "Gönderilmedi", result.error)
            return

        if result.test_mode:
            QMessageBox.information(
                self,
                "Test modu",
                f"Mail {result.envelope_to} adresine gönderildi (gerçek hedef: "
                f"{result.intended_to}). Kayıt \"kuyrukta\" bırakıldı.",
            )
        else:
            QMessageBox.information(
                self, "Gönderildi", f"Mail {result.envelope_to} adresine gönderildi."
            )

    # -- iş başlatma / bitirme ortak yardımcıları (bkz. companies_tab.py) --------------

    def _start_job(
        self, title: str, job: Callable[..., Any], on_done: Callable[[Any], None]
    ) -> None:
        self._set_actions_enabled(False)

        def _finish(result: Any) -> None:
            self._worker_finished()
            on_done(result)

        self._worker = run_with_progress(self, title, job, _finish)
        self._worker.failed.connect(lambda _message: self._worker_finished())

    def _worker_finished(self) -> None:
        self._set_actions_enabled(True)
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _set_actions_enabled(self, enabled: bool) -> None:
        if enabled:
            self._update_buttons(self._selected_row())
            self._start_button.setEnabled(not self._autopilot_active)
            return
        for button in (
            self._approve_button,
            self._reject_button,
            self._send_button,
            self._mark_sent_button,
            self._mark_queued_button,
            self._start_button,
        ):
            button.setEnabled(False)

    # -- toplu gönderim: başlat / durdur / canlı log / geri sayım (arka plan işçisi) ----

    def _append_log(self, symbol: str, message: str) -> None:
        timestamp = now_istanbul().strftime("%H:%M:%S")
        self._log_view.appendPlainText(f"[{timestamp}] {symbol} {message}")

    def _on_start_queue_run(self) -> None:
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

        with get_session(self._engine) as session:
            if bool(get_setting(session, KEY_SENDING_PAUSED)):
                QMessageBox.critical(
                    self,
                    "Gönderim engellendi",
                    "Gönderim ACİL DURDUR ile duraklatılmış. Önce acil durdur'u kaldırın.",
                )
                return

            mode = get_setting(session, KEY_SEND_MODE)
            test_mode = bool(get_setting(session, KEY_TEST_MODE))
            queue_size = len(list_queue(session))
            window = can_send_now(session, test_mode=test_mode)

        mode_label = SEND_MODE_LABELS_TR.get(mode, mode)
        window_text = (
            "Şu anda gönderim yapılabilir."
            if window.allowed
            else f"Şu anda gönderim yapılamaz: {window.reason}"
        )
        summary = (
            f"Mod: {mode_label}\n"
            f"Test modu: {'AÇIK (mailler size gider)' if test_mode else 'KAPALI'}\n"
            f"Kuyrukta bekleyen: {queue_size}\n"
            f"{window_text}\n\n"
            "Gönderimi başlatmak istiyor musunuz?"
        )

        if test_mode:
            answer = QMessageBox.question(
                self,
                "Gönderimi başlat",
                summary,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
        else:
            # Gerçek gönderim: kritik üslup + "GERÇEK GÖNDERİM" başlığı, varsayılan Vazgeç.
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle("GERÇEK GÖNDERİM")
            box.setText(summary)
            yes_button = box.addButton("Evet, başlat", QMessageBox.YesRole)
            box.addButton("Vazgeç", QMessageBox.NoRole)
            box.exec()
            answer = QMessageBox.Yes if box.clickedButton() is yes_button else QMessageBox.No

        if answer != QMessageBox.Yes:
            return

        self._log_view.clear()
        self._start_queue_worker(partial(queue_run_job, self._engine, profile, mode=None))

    def _start_queue_worker(self, job: Callable[..., Any]) -> None:
        self._set_actions_enabled(False)
        self._stop_button.setEnabled(True)

        self._queue_worker = QueueWorker(job, self)
        self._queue_worker.event.connect(self._on_queue_event)
        self._queue_worker.countdown.connect(self._on_queue_countdown)
        self._queue_worker.finished_ok.connect(self._on_queue_run_done)
        self._queue_worker.failed.connect(self._on_queue_run_failed)
        self._queue_worker.start()

    def _on_queue_event(self, event: Any) -> None:
        self._log_view.appendPlainText(format_log_line(event, at=now_istanbul()))
        if event.kind in (EVENT_SENT, EVENT_FAILED):
            self.reload()

    def _on_queue_countdown(self, remaining: float) -> None:
        self._countdown_label.setText(format_countdown(remaining))

    def _queue_worker_finished(self) -> None:
        self._set_actions_enabled(True)
        self._stop_button.setEnabled(False)
        self._countdown_label.setText("")
        if self._queue_worker is not None:
            self._queue_worker.deleteLater()
            self._queue_worker = None

    def _on_queue_run_done(self, result: QueueRunJobResult) -> None:
        self._queue_worker_finished()
        self.reload()

        if result.no_account:
            QMessageBox.critical(
                self,
                "Şifre kayıtlı değil",
                "Mail uygulama şifresi kayıtlı değil. Kaydetmek için: "
                "python scripts/set_mail_password.py",
            )
            return
        if result.blocked_reason:
            self._append_log("⛔", result.blocked_reason)
            QMessageBox.critical(self, "Gönderim engellendi", result.blocked_reason)
            return
        if result.run is not None:
            self._log_view.appendPlainText(format_run_summary(result.run))

    def _on_queue_run_failed(self, message: str) -> None:
        self._queue_worker_finished()
        self._append_log("✗", message)
        QMessageBox.critical(self, "Gönderim turu başarısız", message)

    def stop_queue_run(self) -> None:
        """Sürmekte olan gönderim turunu durdurur; tur yoksa hiçbir şey yapmaz.

        Sürmekte olan mail bitene kadar (`queue_run_job` -> `countdown_sleeper`'ın kontrol
        ettiği bekleme HARİÇ) devam eder -- gönderim atomik, yarıda kesilmez.
        """
        if self._queue_worker is None:
            return
        self._queue_worker.cancel()
        self._stop_button.setEnabled(False)
        self._append_log("■", "Durdurma istendi; sürmekte olan mail bitince duracak.")

    def _on_stop_queue_run(self) -> None:
        self.stop_queue_run()

    def on_emergency_stop(self) -> None:
        """Durum şeridindeki ACİL DURDUR butonuna basılınca `MainWindow` üzerinden çağrılır."""
        self._append_log("⛔", "ACİL DURDUR açıldı.")
        self.stop_queue_run()

    def is_queue_running(self) -> bool:
        """`MainWindow.closeEvent`'in kapanmadan önce sorduğu soru: bir tur sürüyor mu?"""
        return self._queue_worker is not None and self._queue_worker.isRunning()

    def wait_for_queue_run(self, timeout_ms: int) -> bool:
        """Sürmekte olan turun bitmesini bekler; arayüz donmasın diye `processEvents()` çağırır.

        Süre dolarsa `False` döner ve o kayıt `sending` durumunda kalabilir -- bütünlük
        `STATUS_SENDING` write-ahead işareti ve 5.2a'nın kurtarma butonlarıyla (`Gönderildi
        olarak işaretle` / `Kuyruğa geri al`) korunur.
        """
        if self._queue_worker is None:
            return True
        deadline = time.monotonic() + timeout_ms / 1000
        while self._queue_worker is not None and self._queue_worker.isRunning():
            if time.monotonic() >= deadline:
                return False
            QApplication.processEvents()
            self._queue_worker.wait(100)
        return True
