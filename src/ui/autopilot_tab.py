"""Otopilot sekmesi: büyük Başlat/Durdur, canlı sayaçlar, o anki aşama, canlı log, durma sebebi.

`src/autopilot/runner.py::run_autopilot()`'u `src/ui/jobs.py::autopilot_run_job` ile
`src/ui/workers.py::QueueWorker` üzerinden arka planda çalıştırır -- yeni bir QThread kabuğu
YAZILMADI: `QueueWorker` zaten `job(on_event=, on_countdown=, token=)` imzasına uyan HERHANGİ
bir işi çalıştırabiliyor (Onay Kuyruğu sekmesinin "Gönderimi başlat"ıyla aynı kabuk).

Otopilot çalışırken Şirketler sekmesinin toplu işleri (içe aktar/tara/hazırla) ve Onay
Kuyruğu'nun gönder butonları (Tek gönder/Gönderimi başlat) kilitlenir (`set_autopilot_active`)
-- ikisi de Otopilot'un kullandığı aynı veritabanı satırlarını ve aynı Gmail hesabını
kullanıyor. Simetrik olarak Otopilot da o sekmelerden biri zaten bir iş çalıştırıyorsa BAŞLAMAZ.
"""
import logging
import time
from functools import partial
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from src.autopilot.runner import AutopilotEvent, build_source_preview, load_autopilot_stats
from src.core.db import get_session
from src.core.settings import KEY_SENDING_PAUSED, KEY_TEST_MODE, get_setting
from src.core.timeutil import now_istanbul
from src.cv_builder.profile_loader import MasterProfile, load_profile
from src.ui.autopilot_data import (
    DEFAULT_STOP_REASON_TEXT,
    PLACES_CONFIRM_TITLE,
    REAL_SEND_CONFIRM_TITLE,
    STAT_TITLES,
    TEST_MODE_BANNER_TEXT,
    format_log_line,
    format_real_send_confirm_text,
    format_source_preview,
    format_stage_text,
    format_stat_values,
    format_stop_reason_text,
)
from src.ui.companies_tab import CompaniesTab
from src.ui.icons import set_icon
from src.ui.jobs import AutopilotRunJobResult, autopilot_run_job
from src.ui.queue_data import format_countdown
from src.ui.queue_tab import QueueTab
from src.ui.workers import QueueWorker

logger = logging.getLogger(__name__)

#: Sayaçların (ve test modu şeridinin) kaç milisaniyede bir tazeleneceği -- `status_strip.py`
#: ile aynı fikir: başka bir sekmeden/pencereden yapılan bir değişiklik de birkaç saniye
#: içinde burada da görünsün.
REFRESH_INTERVAL_MS = 5000

#: Log panelinin en fazla tutacağı satır sayısı (`queue_tab.py` ile aynı bellek sınırı).
_LOG_MAX_BLOCK_COUNT = 2000

_BIG_BUTTON_STYLE = "font-size: 15px; font-weight: bold; padding: 10px 28px;"
_TEST_MODE_BANNER_STYLE = (
    "background-color: #F9C846; color: #000000; padding: 6px; font-weight: bold;"
)
_STAT_VALUE_STYLE = "font-size: 18px; font-weight: bold;"
_PREVIEW_STYLE = "color: gray;"
_PREVIEW_WARNING_STYLE = "color: #b91c1c; font-weight: bold;"


class AutopilotTab(QWidget):
    """Otopilot sekmesi: üstte test modu şeridi + Başlat/Durdur, ortada sayaçlar, altta log."""

    def __init__(
        self,
        engine: Engine,
        profile_path: Path,
        companies_tab: CompaniesTab,
        queue_tab: QueueTab,
        parent: Optional[QWidget] = None,
        *,
        interval_ms: int = REFRESH_INTERVAL_MS,
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._profile_path = profile_path
        self._companies_tab = companies_tab
        self._queue_tab = queue_tab
        self._worker: Optional[QueueWorker] = None

        self._test_mode_banner = QLabel(TEST_MODE_BANNER_TEXT, self)
        self._test_mode_banner.setStyleSheet(_TEST_MODE_BANNER_STYLE)
        self._test_mode_banner.setVisible(False)

        self._start_button = QPushButton("Başlat", self)
        set_icon(self._start_button, "start")
        self._start_button.setStyleSheet(_BIG_BUTTON_STYLE)
        self._start_button.clicked.connect(self._on_start)

        self._stop_button = QPushButton("Durdur", self)
        set_icon(self._stop_button, "stop")
        self._stop_button.setStyleSheet(_BIG_BUTTON_STYLE)
        self._stop_button.setEnabled(False)
        self._stop_button.clicked.connect(self._on_stop)

        self._stage_label = QLabel(format_stage_text(None), self)
        self._countdown_label = QLabel(self)

        button_row = QHBoxLayout()
        button_row.addWidget(self._start_button)
        button_row.addWidget(self._stop_button)
        button_row.addWidget(self._stage_label)
        button_row.addWidget(self._countdown_label)
        button_row.addStretch(1)

        stats_grid = QGridLayout()
        self._stat_value_labels: list[QLabel] = []
        for index, title in enumerate(STAT_TITLES):
            box = QGroupBox(title, self)
            value_label = QLabel("—", box)
            value_label.setAlignment(Qt.AlignCenter)
            value_label.setStyleSheet(_STAT_VALUE_STYLE)
            box_layout = QVBoxLayout(box)
            box_layout.addWidget(value_label)
            stats_grid.addWidget(box, index // 4, index % 4)
            self._stat_value_labels.append(value_label)

        self._source_preview_label = QLabel(self)
        self._source_preview_label.setWordWrap(True)
        self._source_preview_label.setStyleSheet(_PREVIEW_STYLE)

        self._stop_reason_label = QLabel(DEFAULT_STOP_REASON_TEXT, self)
        self._stop_reason_label.setWordWrap(True)

        self._log_view = QPlainTextEdit(self)
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(_LOG_MAX_BLOCK_COUNT)

        layout = QVBoxLayout(self)
        layout.addWidget(self._test_mode_banner)
        layout.addLayout(button_row)
        layout.addWidget(self._source_preview_label)
        layout.addLayout(stats_grid)
        layout.addWidget(self._stop_reason_label)
        layout.addWidget(self._log_view)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.reload)
        self._timer.start(interval_ms)

        self.reload()
        self.refresh_source_preview()

    # -- sayaçlar / test modu şeridi -----------------------------------------------------

    def reload(self) -> None:
        """Sayaçları ve test modu şeridini kısa ömürlü bir oturumla tazeler."""
        with get_session(self._engine) as session:
            test_mode = bool(get_setting(session, KEY_TEST_MODE))
            stats = load_autopilot_stats(session)

        self._test_mode_banner.setVisible(test_mode)
        for label, value in zip(self._stat_value_labels, format_stat_values(stats)):
            label.setText(value)

    def refresh_source_preview(self) -> None:
        """Kaynak özeti + (Places açıksa) maliyet önizlemesi. 5 sn'lik sayaç zamanlayıcısından
        AYRI: keyring/önayar dosyalarını okur, bu yüzden yalnızca açılışta, sekme görününce,
        Ayarlar kaydedilince ve çalışma bitince tazelenir."""
        with get_session(self._engine) as session:
            preview = build_source_preview(session)
        text, warning = format_source_preview(preview)
        self._source_preview_label.setText(text)
        self._source_preview_label.setStyleSheet(_PREVIEW_WARNING_STYLE if warning else _PREVIEW_STYLE)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt adı)
        super().showEvent(event)
        if self._worker is None:
            self.refresh_source_preview()

    # -- Başlat -----------------------------------------------------------------------

    def _on_start(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        if self._companies_tab.is_job_running() or self._queue_tab.is_job_running():
            QMessageBox.information(
                self,
                "İş sürüyor",
                "Otopilot başlamadan önce Şirketler ya da Onay Kuyruğu sekmesindeki işlemin "
                "bitmesi gerekiyor.",
            )
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
            stats = load_autopilot_stats(session)
            preview = build_source_preview(session)

        if sending_paused:
            QMessageBox.critical(
                self,
                "Gönderim engellendi",
                "Gönderim ACİL DURDUR ile duraklatılmış. Önce acil durdur'u kaldırın.",
            )
            return

        if preview.places is not None:  # yalnızca Places açıkken: ücretli olabilir, açık onay
            places_text, places_warning = format_source_preview(preview)
            places_box = QMessageBox(self)
            places_box.setIcon(QMessageBox.Critical if places_warning else QMessageBox.Warning)
            places_box.setWindowTitle(PLACES_CONFIRM_TITLE)
            places_box.setText(places_text + "\n\nDevam edilsin mi?")
            places_yes = places_box.addButton("Evet, başlat", QMessageBox.YesRole)
            places_box.addButton("Vazgeç", QMessageBox.NoRole)
            places_box.exec()
            if places_box.clickedButton() is not places_yes:
                return

        if not test_mode:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle(REAL_SEND_CONFIRM_TITLE)
            box.setText(format_real_send_confirm_text(stats))
            yes_button = box.addButton("Evet, başlat", QMessageBox.YesRole)
            box.addButton("Vazgeç", QMessageBox.NoRole)
            box.exec()
            if box.clickedButton() is not yes_button:
                return

        self._log_view.clear()
        self._stage_label.setText(format_stage_text(None))
        self._stop_reason_label.setText(DEFAULT_STOP_REASON_TEXT)
        self._start_worker(profile)

    def _start_worker(self, profile: MasterProfile) -> None:
        self._set_running_ui(True)

        job = partial(autopilot_run_job, self._engine, profile)
        self._worker = QueueWorker(job, self)
        self._worker.event.connect(self._on_event)
        self._worker.countdown.connect(self._on_countdown)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _set_running_ui(self, running: bool) -> None:
        self._start_button.setEnabled(not running)
        self._stop_button.setEnabled(running)
        self._companies_tab.set_autopilot_active(running)
        self._queue_tab.set_autopilot_active(running)

    # -- çalışırken gelen olaylar -------------------------------------------------------

    def _on_event(self, event: AutopilotEvent) -> None:
        self._log_view.appendPlainText(format_log_line(event, at=now_istanbul()))
        self._stage_label.setText(format_stage_text(event.stage))
        self.reload()

    def _on_countdown(self, remaining: float) -> None:
        self._countdown_label.setText(format_countdown(remaining))

    def _worker_finished(self) -> None:
        self._set_running_ui(False)
        self._countdown_label.setText("")
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _on_finished(self, result: AutopilotRunJobResult) -> None:
        self._worker_finished()
        self.reload()
        self.refresh_source_preview()
        run = result.run
        if run is not None:
            self._stop_reason_label.setText(format_stop_reason_text(run.stop_reason))

    def _on_failed(self, message: str) -> None:
        self._worker_finished()
        timestamp = now_istanbul().strftime("%H:%M:%S")
        self._log_view.appendPlainText(f"[{timestamp}] ✗ {message}")
        self._stop_reason_label.setText(format_stop_reason_text(message))
        QMessageBox.critical(self, "Otopilot beklenmeyen şekilde durdu", message)

    # -- Durdur / ACİL DURDUR -----------------------------------------------------------

    def _on_stop(self) -> None:
        self.stop_autopilot_run()

    def stop_autopilot_run(self) -> None:
        """Sürmekte olan Otopilot çalışmasını durdurur; çalışma yoksa hiçbir şey yapmaz.

        Sürmekte olan mail bitene kadar devam eder -- gönderim atomik, yarıda kesilmez
        (`queue_tab.py::stop_queue_run` ile aynı ilke).
        """
        if self._worker is None:
            return
        self._worker.cancel()
        self._stop_button.setEnabled(False)
        timestamp = now_istanbul().strftime("%H:%M:%S")
        self._log_view.appendPlainText(
            f"[{timestamp}] ■ Durdurma istendi; sürmekte olan mail bitince duracak."
        )

    def on_emergency_stop(self) -> None:
        """Durum şeridindeki ACİL DURDUR butonuna basılınca `MainWindow` üzerinden çağrılır."""
        timestamp = now_istanbul().strftime("%H:%M:%S")
        self._log_view.appendPlainText(f"[{timestamp}] ⛔ ACİL DURDUR açıldı.")
        self.stop_autopilot_run()

    # -- MainWindow.closeEvent kancaları -------------------------------------------------

    def is_autopilot_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def wait_for_autopilot_run(self, timeout_ms: int) -> bool:
        """Sürmekte olan çalışmanın bitmesini bekler; arayüz donmasın diye `processEvents()`
        çağırır. Süre dolarsa `False` döner (`queue_tab.py::wait_for_queue_run` ile aynı ilke)."""
        if self._worker is None:
            return True
        deadline = time.monotonic() + timeout_ms / 1000
        while self._worker is not None and self._worker.isRunning():
            if time.monotonic() >= deadline:
                return False
            QApplication.processEvents()
            self._worker.wait(100)
        return True
