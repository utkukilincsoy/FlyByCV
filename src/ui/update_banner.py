"""Durum şeridinin altındaki modal OLMAYAN "Yeni sürüm var" şeridi (Faz 10 Adım 10.9d).

Açılışta `start_check()` arka planda (`jobs.update_check_job`, `JobWorker`) sessiz bir kontrol yapar;
yeni sürüm varsa şerit görünür, yoksa (ya da ağ/ayar/günlük sınır yüzünden bilinemiyorsa) gizli kalır
ve kullanıcıya hiçbir şey gösterilmez. Şerit hiçbir şeyi KENDİLİĞİNDEN indirmez/kurmaz: "İndirme
sayfasını aç" yalnızca `update_check.release_page_url()` ile kodda kurulmuş bağlantıyı tarayıcıda
açar; indirmeyi kullanıcı yapar. "Bu sürümü atla" ayara yazılır, "Gizle" yalnızca bu oturum için.
"""
import logging
from functools import partial
from typing import Optional

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget
from sqlalchemy.engine import Engine

from src.core.db import get_session
from src.core.update_check import UpdateInfo, skip_version
from src.ui.jobs import UpdateCheckJobResult, update_check_job
from src.ui.workers import JobWorker

logger = logging.getLogger(__name__)

BANNER_STYLE = "background-color: #DCEBFA; color: #0B3D91; padding: 4px;"
OPEN_BUTTON_TEXT = "İndirme sayfasını aç"
SKIP_BUTTON_TEXT = "Bu sürümü atla"
HIDE_BUTTON_TEXT = "Gizle"


def format_banner_text(info: UpdateInfo) -> str:
    return f"Yeni sürüm var: {info.version}. İndirmek için sürüm sayfasını açabilirsiniz."


class UpdateBanner(QWidget):
    def __init__(self, engine: Engine, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._info: Optional[UpdateInfo] = None
        self._worker: Optional[JobWorker] = None

        self._label = QLabel(self)
        self._open_button = QPushButton(OPEN_BUTTON_TEXT, self)
        self._skip_button = QPushButton(SKIP_BUTTON_TEXT, self)
        self._hide_button = QPushButton(HIDE_BUTTON_TEXT, self)
        self._open_button.clicked.connect(self._on_open)
        self._skip_button.clicked.connect(self._on_skip)
        self._hide_button.clicked.connect(self.hide)

        layout = QHBoxLayout(self)
        layout.addWidget(self._label, 1)
        layout.addWidget(self._open_button)
        layout.addWidget(self._skip_button)
        layout.addWidget(self._hide_button)
        self.setStyleSheet(BANNER_STYLE)
        self.hide()

    @property
    def info(self) -> Optional[UpdateInfo]:
        return self._info

    def show_update(self, info: UpdateInfo) -> None:
        self._info = info
        self._label.setText(format_banner_text(info))
        self.show()

    def start_check(self) -> None:
        """Açılıştaki sessiz kontrol (`force=False`). Zaten çalışan bir kontrol varsa bir şey yapmaz."""
        if self._worker is not None and self._worker.isRunning():
            return
        worker = JobWorker(partial(update_check_job, self._engine, force=False), self)
        worker.finished_ok.connect(self._on_checked)
        worker.failed.connect(self._on_failed)
        # İşçi YALNIZCA iş parçacığı gerçekten bittiğinde (QThread.finished) bırakılır: `finished_ok`
        # `run()` dönmeden önce yayılır, o anda `deleteLater()` çalışan bir QThread'i yok edebilirdi.
        worker.finished.connect(self._release_worker)
        self._worker = worker
        worker.start()

    def cancel_running_worker(self) -> None:
        """`MainWindow.closeEvent` çağırır: pencere kapanırken kontrol işçisi yarım kalmasın."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(5000)

    def _release_worker(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _on_checked(self, result: UpdateCheckJobResult) -> None:
        if result.info is not None:
            self.show_update(result.info)

    def _on_failed(self, message: str) -> None:
        # Sessiz kural (`update_check` modül dokümanı): kullanıcıya hata gösterilmez.
        logger.debug(f"Güncelleme kontrolü işi başarısız (sessiz): {message}")

    def _on_open(self) -> None:
        if self._info is not None:
            QDesktopServices.openUrl(QUrl(self._info.url))

    def _on_skip(self) -> None:
        if self._info is not None:
            with get_session(self._engine) as session:
                skip_version(session, self._info.version)
        self.hide()
