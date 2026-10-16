"""Yakalanmamış hatalarda kullanıcıya Türkçe bir uyarı gösterir.

Tam traceback'i log dosyasına yazmak `src/core/logging_setup.py::install_exception_logging()`
işi; bu modül yalnızca `QApplication` VARSA ek olarak bir `QMessageBox` açar -- `src/core`
katmanı Qt'ye bağımlı olmasın diye (proje mimarisi) ikisi bilinçli olarak ayrı tutuldu.
"""
import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from src.core.logging_setup import install_exception_logging, log_path

logger = logging.getLogger(__name__)

CRASH_DIALOG_TITLE = "Beklenmeyen hata"
CRASH_DIALOG_TEXT = (
    "Beklenmeyen bir hata oluştu:\n\n{error}\n\n"
    "Ayrıntılar (tam hata kaydı) şu dosyaya yazıldı:\n{log_path}"
)


class CrashNotifier(QObject):
    """Ana iş parçacığında yaşar (bkz. `install_crash_handler`, `QApplication`'dan hemen
    sonra oluşturulur).

    `notify()` HANGİ iş parçacığından çağrılırsa çağrılsın (arka planda çalışan bir
    `QThread` işi içindeki yakalanmamış bir hata dahil), `crashed` sinyali Qt'nin otomatik
    bağlantı davranışıyla (`Qt.AutoConnection`: gönderen ve alıcı farklı iş parçacığındaysa
    kendiliğinden kuyruğa alınır) `_show`'u HER ZAMAN ana iş parçacığında çalıştırır --
    Qt widget'ları başka bir iş parçacığından doğrudan çağrılamaz.
    """

    crashed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.crashed.connect(self._show)

    def _show(self, message: str) -> None:
        QMessageBox.critical(None, CRASH_DIALOG_TITLE, message)

    def notify(self, error: BaseException) -> None:
        message = CRASH_DIALOG_TEXT.format(error=error, log_path=log_path())
        self.crashed.emit(message)


def install_crash_handler() -> None:
    """`configure_logging()`'den VE `QApplication` oluşturulduktan SONRA çağrılmalı.

    `QApplication.instance()` yoksa bildirim kurulmaz -- hata yine tam traceback'iyle log
    dosyasına yazılır (`install_exception_logging`), yalnızca gösterecek bir pencere olmaz.
    """
    app = QApplication.instance()
    notifier = CrashNotifier() if app is not None else None
    install_exception_logging(on_exception=notifier.notify if notifier is not None else None)
