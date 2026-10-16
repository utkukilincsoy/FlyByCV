""""Görünüm" grubu: açık/koyu tema. Seçim hemen uygulanır ve kaydedilir (Kaydet düğmesine bağlı değil)."""
import logging
from typing import Optional

from PySide6.QtWidgets import QApplication, QComboBox, QFormLayout, QGroupBox, QWidget
from sqlalchemy.engine import Engine

from src.core.db import get_session
from src.core.settings import KEY_UI_THEME, get_setting, set_setting
from src.ui.theme import THEME_LABELS_TR, THEMES, apply_theme, normalize_theme

logger = logging.getLogger(__name__)


class AppearanceGroup(QGroupBox):
    def __init__(self, engine: Engine, parent: Optional[QWidget] = None) -> None:
        super().__init__("Görünüm", parent)
        self._engine = engine
        self.theme_combo = QComboBox(self)
        for theme in THEMES:
            self.theme_combo.addItem(THEME_LABELS_TR[theme], theme)
        self.load()
        self.theme_combo.currentIndexChanged.connect(self._on_changed)
        form = QFormLayout()
        form.addRow("Tema", self.theme_combo)
        self.setLayout(form)

    def load(self) -> None:
        try:
            with get_session(self._engine) as session:
                theme = normalize_theme(get_setting(session, KEY_UI_THEME))
        except Exception:
            logger.warning("Tema ayarı okunamadı", exc_info=True)
            theme = normalize_theme(None)
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(theme))
        self.theme_combo.blockSignals(False)

    def _on_changed(self) -> None:
        theme = self.theme_combo.currentData()
        try:
            with get_session(self._engine) as session:
                set_setting(session, KEY_UI_THEME, theme)
                session.commit()
        except Exception:
            logger.warning("Tema ayarı kaydedilemedi", exc_info=True)
        apply_theme(QApplication.instance(), theme)
