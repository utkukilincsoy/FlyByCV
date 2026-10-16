"""Uygulama loglamasını kurar ve gürültülü kütüphane loglarını kısar.

`httpx` her HTTP isteğini, `google_genai` de her çağrının ayrıntısını INFO seviyesinde
yazıyor. Bir LLM çağrısında ekran bu satırlarla dolduğu için asıl mesaj kayboluyor.

Seviye sıfırlanmaz, yalnızca yükseltilir: gerçek bir hata (WARNING ve üstü) yine görünür.
`logging.basicConfig` çağrısı burada YAPILMAZ — her script kendi biçimini seçiyor;
yalnızca `configure_logging()` (paketlenen GUI'nin girişi, `main.py`) kendi kurulumunu yapar.
"""
import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from src.core import paths

logger = logging.getLogger(__name__)

#: INFO seviyesinde gereksiz ayrıntı basan kütüphaneler.
NOISY_LOGGERS = ("httpx", "google_genai")

LOG_FILE_NAME = "flybycv.log"
#: Şirket sitesi taraması/gönderim gibi işler aylarca çalışabilir; boyuta göre döndürme
#: (tarihe göre değil) log dosyasının sınırsız büyümesini basit bir şekilde engelliyor.
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

#: `on_exception` geri çağırmasının aldığı tek argüman: yakalanmamış hatanın kendisi.
ExceptionCallback = Callable[[BaseException], None]


def log_path() -> Path:
    """Log dosyasının tam yolu -- kullanıcıya gösterilecek hata mesajlarında kullanılır."""
    return paths.logs_dir() / LOG_FILE_NAME


def _ensure_std_streams() -> None:
    """`sys.stdout`/`sys.stderr` `None` ise (paketlenmiş `--windowed` .exe, konsol yok)
    `os.devnull`'a bağlar.

    Bu ikisi `None` kalırsa `print()` çağıran ya da `.write()` bekleyen herhangi bir kod
    (bizim değil, üçüncü parti bir kütüphanenin bile olsa -- ör. bir uyarı yazdıran bir
    paket) `AttributeError` ile çöker. `os.devnull`'a bağlamak yazılan her şeyi sessizce
    yutar ama çökmeyi engeller.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def quiet_noisy_loggers(level: int = logging.WARNING) -> None:
    """`NOISY_LOGGERS` içindeki logger'ları verilen seviyeye çeker."""
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(level)


def configure_logging(level: int = logging.INFO) -> None:
    """Kök logger'ı kurar: HER ZAMAN dosyaya (`paths.logs_dir()`), konsol VARSA konsola da.

    Paketlenmiş (.exe, `--windowed`) sürümde konsol yok — `sys.stderr` `None` olabilir.
    `StreamHandler(None)` bu durumda satırları sessizce kaybeder (çökmez ama loglar
    kaybolur); bu yüzden konsol handler'ı yalnızca `sys.stderr` gerçekten VARSA eklenir,
    dosya handler'ı ise koşulsuz eklenir — tek güvenilir kanal odur.

    Log dosyasına şifre, API anahtarı veya mail gövdesi/konusu YAZILMAZ: bu fonksiyon
    yalnızca handler kurar, hangi mesajların loglanacağına projedeki `logger.info/warning`
    çağrıları karar verir ve hiçbiri (kural 6 gereği) sır içermez.

    Tekrar çağrılırsa (ör. testte) önceki handler'lar temizlenir, aksi halde her çağrıda
    bir tane daha eklenip aynı satır birden çok kez yazılırdı.
    """
    # Konsol handler'ı kurulacak mı kararı `_ensure_std_streams()`'TEN ÖNCEKİ hâle göre
    # verilir: o fonksiyon `None` olan akışları `os.devnull`'a bağlar, sonrasında
    # `sys.stderr is not None` her zaman doğru olurdu ve konsolsuz ortamda da boşuna bir
    # handler eklenirdi (zararsız ama gereksiz).
    had_console = sys.stderr is not None
    _ensure_std_streams()

    log_dir = paths.logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / LOG_FILE_NAME,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)

    if had_console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    quiet_noisy_loggers()


def install_exception_logging(on_exception: Optional[ExceptionCallback] = None) -> None:
    """`sys.excepthook` VE `threading.excepthook`'u kurar; ikisi de yakalanmamış hatayı tam
    traceback'iyle `logger.critical(..., exc_info=...)` üzerinden log dosyasına yazar
    (`configure_logging()` ÖNCE çağrılmış olmalı -- dosya handler'ı o kurar).

    `on_exception` verilirse loglamadan SONRA, hatanın kendisiyle çağrılır -- ör. kullanıcıya
    bir `QMessageBox` gösteren bir geri çağırma. Bu modül Qt'ye BAĞIMLI DEĞİL (bkz. proje
    mimarisi, `src/core` UI katmanına girmez); dialog gösterme kararı çağırana (`src/ui`) ait.

    `KeyboardInterrupt` (Ctrl+C) burada YAKALANMAZ, Python'un varsayılan davranışına
    (`sys.__excepthook__`) bırakılır -- bir kullanıcı kesintisini "çökme" saymak yanlış olur.
    """

    def handle_exception(exc_type, exc_value, exc_traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical("Yakalanmamış hata", exc_info=(exc_type, exc_value, exc_traceback))
        if on_exception is not None:
            on_exception(exc_value)

    def handle_thread_exception(args: threading.ExceptHookArgs) -> None:
        thread_name = args.thread.name if args.thread is not None else "bilinmiyor"
        logger.critical(
            f"Yakalanmamış hata (iş parçacığı: {thread_name})",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        if on_exception is not None:
            on_exception(args.exc_value)

    sys.excepthook = handle_exception
    threading.excepthook = handle_thread_exception
