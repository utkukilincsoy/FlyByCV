"""Sürüm numarası ve uygulama kimliğinin TEK kaynağı (Faz 10 Adım 10.9a).

Sürümü değiştirmek için yalnızca `__version__`'ı düzenle: pencere başlığı, `QApplication.
setApplicationVersion`, PyInstaller dosya sürüm bilgisi (`flybycv.spec`) ve derleme
betikleri (`scripts/build_exe.ps1`) buradan okur. Hiçbir derleme dosyasına sürüm yazılmaz
(bkz. `tests/test_version.py`).

Bu modül BİLEREK başka hiçbir `src` modülünü import etmez: `.spec` dosyası PyInstaller'ın
içinde, paket yolları kurulmadan önce bunu dosya yolundan yükler.
"""
import re
from typing import Optional

#: Anlamsal sürüm: `MAJOR.MINOR.PATCH` (ör. "1.0.0"). Önek (`v`) yok; Git etiketi `v` + bu değerdir.
__version__ = "1.0.0"

APP_NAME = "FlybyCV"

#: "kullanici/depo" biçiminde GitHub deposu (Adım 10.9k). `None` yapılırsa güncelleme kontrolü
#: ağa HİÇ çıkmaz (Adım 10.9c).
GITHUB_REPO: Optional[str] = "utkukilincsoy/flybycv"

_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def version_tuple(version: str = __version__) -> tuple[int, int, int, int]:
    """`"1.2.3"` -> `(1, 2, 3, 0)`: Windows dosya sürümü dört sayılı olduğu için sona 0 eklenir."""
    match = _VERSION_PATTERN.match(version)
    if match is None:
        raise ValueError(f"Sürüm 'MAJOR.MINOR.PATCH' biçiminde olmalı: {version!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch, 0
