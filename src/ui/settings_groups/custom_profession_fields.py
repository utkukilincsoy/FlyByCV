"""\"Diğer\" meslek alanları: meslek adı + virgülle ayrılmış arama kelimeleri (PLAN.md 10.2g/10.5c).

Hem kurulum sihirbazının hedef sayfası hem Ayarlar'ın "Mail şablonu" grubu aynı parçayı kullanır; yalnızca
meslek listesinde "Diğer" seçiliyken görünür (`set_active`). Değerler `custom_profession_name` /
`custom_profession_keywords` ayarlarına gider (bkz. `src/core/custom_profession.py`).
"""
from typing import Any, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFormLayout, QLineEdit, QVBoxLayout, QWidget

from src.core.custom_profession import (
    MAX_KEYWORDS,
    build_custom_preset,
    parse_keywords,
    validate_custom_profession,
)
from src.core.professions import ProfessionPreset
from src.ui.settings_data import SettingsForm
from src.ui.settings_groups.common import hint_label

HINT_TEXT = (
    f"Arama kelimelerini virgülle ayırın (en çok {MAX_KEYWORDS}), ör. diş kliniği, ortodonti. "
    "Kelimeler harita/Google Places aramalarında kullanılır. Bu meslek için OpenStreetMap etiketi "
    "olmadığından OpenStreetMap atlanır ve teknokent kullanılmaz; mail şablonu genel şablondan gelir, "
    "{meslek} yer tutucusu yazdığınız ad olur."
)


class CustomProfessionFields(QWidget):
    changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        form = QFormLayout()
        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText("ör. Diş hekimliği")
        self.keywords_edit = QLineEdit(self)
        self.keywords_edit.setPlaceholderText("ör. diş kliniği, ortodonti, ağız ve diş sağlığı")
        form.addRow("Meslek adı*", self.name_edit)
        form.addRow("Arama kelimeleri*", self.keywords_edit)
        self.name_edit.textChanged.connect(lambda *_: self.changed.emit())
        self.keywords_edit.textChanged.connect(lambda *_: self.changed.emit())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        layout.addWidget(hint_label(HINT_TEXT, self))
        self.setVisible(False)

    def set_active(self, active: bool) -> None:
        self.setVisible(active)

    def keywords(self) -> list[str]:
        return parse_keywords(self.keywords_edit.text())

    def values(self) -> dict[str, Any]:
        return {
            "custom_profession_name": self.name_edit.text().strip() or None,
            "custom_profession_keywords": self.keywords(),
        }

    def set_values(self, form: SettingsForm) -> None:
        self.name_edit.setText(form.custom_profession_name or "")
        self.keywords_edit.setText(", ".join(form.custom_profession_keywords))

    def errors(self) -> list[str]:
        values = self.values()
        return validate_custom_profession(
            values["custom_profession_name"], values["custom_profession_keywords"]
        )

    def preset(self) -> Optional[ProfessionPreset]:
        """Ekrandaki ad/kelimelerden bellekte paket; geçersizse `None`."""
        if self.errors():
            return None
        values = self.values()
        return build_custom_preset(values["custom_profession_name"], values["custom_profession_keywords"])
