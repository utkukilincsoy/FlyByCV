"""Sihirbazın üçüncü sayfası: hedef (iş / staj / gönüllü staj / yarı zamanlı) + meslek (PLAN.md 10.2g).

İki liste Ayarlar'ın "Mail şablonu" grubuyla AYNI yardımcılardan dolar
(`settings_groups.common.populate_target_combo` / `populate_profession_combo`); "İleri"de
`application_target` ve `profession` ayarlarına yazılır. Mail şablonu burada DEĞİŞTİRİLMEZ: hedef/
meslek/dile göre şablon önerisi şablon sayfasının (10.2k) işi, kullanıcının metni onaysız ezilmez.

Meslek listesinin sonundaki "Diğer": seçilince meslek adı + virgülle arama kelimeleri istenir
(`settings_groups.custom_profession_fields.CustomProfessionFields`, Ayarlar'daki "Mail şablonu" grubuyla
aynı parça) ve `custom_profession_name` / `custom_profession_keywords` ayarlarına yazılır.
"""
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QComboBox, QFormLayout, QGroupBox, QLabel, QVBoxLayout, QWidget
from sqlalchemy.engine import Engine
from sqlmodel import Session

from src.core.custom_profession import CUSTOM_PROFESSION_ID
from src.core.db import get_session
from src.core.professions import ProfessionPreset, load_profession_presets
from src.ui.onboarding_data import PAGE_TARGET, save_settings_fields, validate_target
from src.ui.onboarding_pages.base import OnboardingPage
from src.ui.settings_data import read_form
from src.ui.settings_groups.common import (
    hint_label,
    populate_profession_combo,
    populate_target_combo,
    select_index_by_data,
)
from src.ui.settings_groups.custom_profession_fields import CustomProfessionFields

logger = logging.getLogger(__name__)

TITLE = "Ne arıyorsunuz?"
INTRO_TEXT = (
    "Hedefiniz başvuru e-postasındaki ifadeyi, mesleğiniz de hangi tür firmaların aranacağını "
    "belirler. Mesleğiniz listede yoksa \"Diğer\"i seçip adını ve arama kelimelerini kendiniz yazın. "
    "Hepsini sonradan Ayarlar'dan değiştirebilirsiniz."
)
PROFESSIONS_ERROR_TEXT = (
    "Meslek listesi okunamadı, bu yüzden meslek seçilemiyor ve kuruluma devam edilemiyor. "
    "Uygulama dosyaları eksik veya bozuk olabilir; uygulamayı yeniden kurmayı deneyin."
)
COMPANY_TYPES_HINT = "Aranacak firma türleri: {types}."


class TargetPage(OnboardingPage):
    page_id = PAGE_TARGET

    def __init__(
        self, engine: Engine, profile_path: Path, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(engine, profile_path, parent)
        self.setTitle(TITLE)
        self._professions: dict[str, ProfessionPreset] = load_profession_presets()

        intro = QLabel(INTRO_TEXT, self)
        intro.setWordWrap(True)

        self.target_combo = QComboBox(self)
        populate_target_combo(self.target_combo)
        self.profession_combo = QComboBox(self)
        populate_profession_combo(self.profession_combo, self._professions)
        self.profession_combo.currentIndexChanged.connect(self._refresh_company_types)

        form = QFormLayout()
        form.addRow("Hedef*", self.target_combo)
        form.addRow("Meslek*", self.profession_combo)
        self.custom_fields = CustomProfessionFields(self)
        group_layout = QVBoxLayout()
        group_layout.addLayout(form)
        group_layout.addWidget(self.custom_fields)
        group = QGroupBox(self)
        group.setLayout(group_layout)

        self.company_types_label = hint_label("", self)

        self.error_label = QLabel(PROFESSIONS_ERROR_TEXT, self)
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b91c1c;")
        self.error_label.setVisible(not self._professions)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.error_label)
        layout.addWidget(group)
        layout.addWidget(self.company_types_label)
        layout.addStretch(1)

    def _refresh_company_types(self, *_args) -> None:
        self.custom_fields.set_active(self.profession_combo.currentData() == CUSTOM_PROFESSION_ID)
        preset = self._professions.get(self.profession_combo.currentData())  # "Diğer" burada yok → ipucu boş
        self.company_types_label.setText(
            COMPANY_TYPES_HINT.format(types=", ".join(preset.company_types)) if preset else ""
        )

    # -- OnboardingPage -------------------------------------------------------------------

    def load(self) -> None:
        with get_session(self._engine) as session:
            form = read_form(session)
        select_index_by_data(self.target_combo, form.application_target)
        select_index_by_data(self.profession_combo, form.profession)
        self.custom_fields.set_values(form)
        self._refresh_company_types()

    def validation_errors(self) -> list[str]:
        if not self._professions:
            return [PROFESSIONS_ERROR_TEXT]
        custom = self.custom_fields.values()
        return validate_target(
            self.target_combo.currentData(),
            self.profession_combo.currentData(),
            self._professions.keys(),
            custom["custom_profession_name"],
            custom["custom_profession_keywords"],
        )

    def save(self, session: Session) -> None:
        save_settings_fields(
            session,
            {
                "application_target": self.target_combo.currentData(),
                "profession": self.profession_combo.currentData(),
                **self.custom_fields.values(),
            },
        )
