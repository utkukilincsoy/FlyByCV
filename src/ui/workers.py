"""Herhangi bir `src/ui/jobs.py` işini arka planda çalıştıran ince QThread kabuğu.

Kendisi veritabanına HİÇ dokunmaz: oturumu iş fonksiyonu kendi açar (bkz. `jobs.py`).
`on_progress`, işçi iş parçacığında çalışır ve sadece bir Signal yayar -- widget'a asla
doğrudan dokunmaz (Qt kuralı: widget'lar yalnızca GUI iş parçacığından güncellenir).
"""
import logging
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from src.ui.jobs import CancelToken, Progress

logger = logging.getLogger(__name__)


class JobWorker(QThread):
    """Herhangi bir işi (`src/ui/jobs.py`) arka planda çalıştırır.

    Sinyal adı bilinçli olarak `finished_ok`, `finished` DEĞİL -- `QThread`'in zaten
    kendi `finished` sinyali var (iş başarılı da başarısız da bitince ateşlenir).
    """

    progress = Signal(int, int, str)  # current, total, Türkçe mesaj
    finished_ok = Signal(object)  # işin döndürdüğü sonuç nesnesi
    failed = Signal(str)  # Türkçe hata mesajı

    def __init__(self, job: Callable[..., Any], parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._job = job
        self._token = CancelToken()

    @property
    def token(self) -> CancelToken:
        return self._token

    def cancel(self) -> None:
        self._token.cancel()

    def _emit_progress(self, progress: Progress) -> None:
        self.progress.emit(progress.current, progress.total, progress.message)

    def run(self) -> None:
        try:
            result = self._job(on_progress=self._emit_progress, token=self._token)
        except Exception as exc:
            logger.exception("Arka plan işi beklenmeyen şekilde başarısız oldu")
            self.failed.emit(f"Beklenmeyen hata: {exc}")
        else:
            self.finished_ok.emit(result)


class QueueWorker(QThread):
    """`src/ui/jobs.py::queue_run_job`'u arka planda çalıştırır (toplu gönderim turu).

    `JobWorker`'ın uzun-süren-tur karşılığı: modal bir `QProgressDialog` yerine sürekli akan
    iki sinyal var (`event`: her gönderim/atlama/hata/bekleme satırı; `countdown`: kalan
    saniye). `run_with_progress` burada KULLANILMAZ -- bir turun ne zaman biteceği belli
    değildir, modal bir "İptal" kutusu yanlış kabuk olurdu; `queue_tab.py` kendi log panelini
    ve "Durdur" butonunu yönetir.
    """

    event = Signal(object)  # src.sender.worker.QueueEvent
    countdown = Signal(float)  # kalan saniye (0.0 = geri sayım bitti/temizlendi)
    finished_ok = Signal(object)  # QueueRunJobResult
    failed = Signal(str)  # Türkçe hata mesajı

    def __init__(self, job: Callable[..., Any], parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._job = job
        self._token = CancelToken()

    @property
    def token(self) -> CancelToken:
        return self._token

    def cancel(self) -> None:
        self._token.cancel()

    def run(self) -> None:
        try:
            result = self._job(
                on_event=self.event.emit, on_countdown=self.countdown.emit, token=self._token
            )
        except Exception as exc:
            logger.exception("Gönderim turu beklenmeyen şekilde başarısız oldu")
            self.failed.emit(f"Beklenmeyen hata: {exc}")
        else:
            self.finished_ok.emit(result)


def run_with_progress(
    parent: QWidget,
    title: str,
    job: Callable[..., Any],
    on_done: Callable[[Any], None],
) -> JobWorker:
    """İşi bir `QProgressDialog` (İptal butonlu) ile arka planda çalıştırır.

    Dönen işçiye referans TUTULMALIDIR (çağıranın kendi bir alanında saklaması gerekir):
    yoksa Python çöp toplayıcısı çalışırken iş bitmeden nesne silinebilir.
    """
    dialog = QProgressDialog("Hazırlanıyor…", "İptal", 0, 1, parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)

    worker = JobWorker(job, parent)

    def _on_progress(current: int, total: int, message: str) -> None:
        dialog.setLabelText(message)
        dialog.setMaximum(max(total, 1))
        dialog.setValue(current)

    def _on_finished(result: Any) -> None:
        dialog.close()
        on_done(result)

    def _on_failed(message: str) -> None:
        dialog.close()
        QMessageBox.critical(parent, title, message)

    worker.progress.connect(_on_progress)
    worker.finished_ok.connect(_on_finished)
    worker.failed.connect(_on_failed)
    dialog.canceled.connect(worker.cancel)

    worker.start()
    dialog.show()

    return worker
