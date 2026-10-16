"""Uygulamayı kendini kapatıp yeniden açacak şekilde yeniden başlatır (yedekten geri yükleme sonrası).

İki çalışma biçimi desteklenir:
- Paketlenmiş .exe: `sys.executable` uygulamanın kendisidir, argümanlar olduğu gibi verilir.
- Geliştirme (`python main.py`): `sys.executable` Python yorumlayıcısıdır; betik yolu MUTLAK yapılıp
  ilk argüman olarak verilir (yeni süreç başka bir çalışma klasöründe açılsa da bulunsun).

Yeni süreç bağımsız (`startDetached`) başlatılır; çağıran ardından kendini kapatır. Uygulamada
tek-örnek kilidi olmadığı için yeni süreç, eskisi kapanırken açılabilir.
"""
import logging
import os
import sys
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QProcess

from src.core import paths

logger = logging.getLogger(__name__)

#: `QProcess.startDetached(program, arguments, workingDirectory)` imzası. PySide6 `(bool, pid)`
#: döner; testler `bool` döndüren sahte bir başlatıcı verebilir.
Starter = Callable[[str, list[str], str], object]


def relaunch_command() -> tuple[str, list[str]]:
    """Yeniden başlatmak için çalıştırılacak program ve argümanları."""
    if paths.is_frozen():
        return sys.executable, list(sys.argv[1:])
    script = str(Path(sys.argv[0]).resolve())
    return sys.executable, [script, *sys.argv[1:]]


def relaunch(starter: Optional[Starter] = None) -> bool:
    """Uygulamanın yeni bir örneğini başlatır; başarılıysa True. Hiçbir durumda hata fırlatmaz."""
    program, arguments = relaunch_command()
    start = starter or QProcess.startDetached
    try:
        result = start(program, arguments, os.getcwd())
    except Exception as error:
        logger.warning(f"Uygulama yeniden başlatılamadı ({type(error).__name__})")
        return False
    started = bool(result[0]) if isinstance(result, tuple) else bool(result)
    if not started:
        logger.warning("Uygulama yeniden başlatılamadı (yeni süreç açılmadı)")
    return started
