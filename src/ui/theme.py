"""qt-material tema uygulayıcı: koyu (varsayılan) / açık. Ağa çıkmaz, yalnızca stil sayfası uygular.

Pencerelerdeki özel stiller (sarı uyarı satırı, kırmızı ACİL DURDUR, test modu şeridi, güncelleme
şeridi) kendi arka plan + yazı rengini birlikte verdiği için iki temada da okunur kalır.
"""
import logging
from typing import Optional

from PySide6.QtWidgets import QApplication

logger = logging.getLogger(__name__)

THEME_LIGHT = "light"
THEME_DARK = "dark"
THEMES = (THEME_LIGHT, THEME_DARK)
THEME_LABELS_TR = {THEME_LIGHT: "Açık", THEME_DARK: "Koyu"}

DEFAULT_THEME = THEME_DARK  # ayar hiç kaydedilmemişse (veya bozuksa) kullanılan tema

_QT_MATERIAL_THEME = {THEME_LIGHT: "light_blue.xml", THEME_DARK: "dark_blue.xml"}


def normalize_theme(name: object) -> str:
    return name if name in THEMES else DEFAULT_THEME


def apply_theme(app: Optional[QApplication], name: object) -> str:
    """Temayı uygular ve gerçekte uygulanan adı döndürür. qt-material yoksa/başarısızsa sessizce
    varsayılan Qt görünümünde kalır (uygulama açılmalı)."""
    theme = normalize_theme(name)
    if app is None:
        return theme
    try:
        from qt_material import apply_stylesheet

        apply_stylesheet(
            app,
            theme=_QT_MATERIAL_THEME[theme],
            invert_secondary=(theme == THEME_LIGHT),
            extra={"density_scale": "-1"},
        )
    except Exception:
        logger.warning("Tema uygulanamadı; varsayılan görünümle devam.", exc_info=True)
    return theme
