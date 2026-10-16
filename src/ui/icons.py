"""QtAwesome ikonları: tek yerden, aşırıya kaçmadan (yalnızca önemli düğmeler ve sekmeler).

İkon yüklenemezse (paket/yazı tipi sorunu) boş ikon döner; düğme metniyle çalışmaya devam eder.
"""
import logging

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QAbstractButton

logger = logging.getLogger(__name__)

ICON_COLOR = "#2979ff"  # açık ve koyu temada okunur mavi
ICON_COLOR_DISABLED = "#9e9e9e"

#: Mantıksal ad -> QtAwesome (Font Awesome 5 solid) adı.
ICON_NAMES = {
    "start": "fa5s.play",
    "stop": "fa5s.stop",
    "save": "fa5s.save",
    "add": "fa5s.plus",
    "import": "fa5s.file-import",
    "search": "fa5s.search",
    "send": "fa5s.paper-plane",
    "approve": "fa5s.check",
    "reject": "fa5s.times",
    "refresh": "fa5s.sync-alt",
    "prepare": "fa5s.file-alt",
    "warning": "fa5s.exclamation-triangle",
    "tab_autopilot": "fa5s.rocket",
    "tab_companies": "fa5s.building",
    "tab_queue": "fa5s.paper-plane",
    "tab_replies": "fa5s.inbox",
    "tab_profile": "fa5s.user",
    "tab_settings": "fa5s.cog",
    "tab_dashboard": "fa5s.chart-bar",
}


def get_icon(key: str) -> QIcon:
    try:
        import qtawesome as qta

        return qta.icon(ICON_NAMES[key], color=ICON_COLOR, color_disabled=ICON_COLOR_DISABLED)
    except Exception:
        logger.warning("İkon yüklenemedi: %s", key, exc_info=True)
        return QIcon()


def set_icon(button: QAbstractButton, key: str) -> None:
    button.setIcon(get_icon(key))
