"""Sihirbazın mail şablonu sayfası (PLAN.md Adım 10.2k).

Ayarlar'ın "Mail şablonu" grubu (`settings_groups.mail_template_group.MailTemplateGroup`) AYNEN kullanılır:
hedef / meslek / mail dili, konu, gövde, yer tutucu listesi ve "Önizle" (profildeki gerçek bilgilerle,
örnek bir firma adıyla doldurup gösterir; mail GÖNDERMEZ, ağa çıkmaz).

Şablon önerisi (hedef/meslek/dile göre) sayfa ilk açıldığında YALNIZCA şablon hâlâ uygulamanın genel
varsayılanıysa, yani kullanıcı hiç düzenlememişse ve bir kez sorulur (grubun mevcut onay penceresiyle,
önerilen metin gösterilir). Kullanıcının kendi metni asla sorusuz ezilmez (kural 1/9); Hayır denirse
varsayılan metin olduğu gibi kalır.
"""
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget
from sqlalchemy.engine import Engine
from sqlmodel import Session

from src.core.db import get_session
from src.core.mail_template_presets import load_generic_mail_templates
from src.core.professions import ProfessionPreset, load_profession_presets
from src.ui.onboarding_data import (
    PAGE_TEMPLATE,
    is_untouched_default_template,
    save_settings_fields,
    validate_template,
)
from src.ui.onboarding_pages.base import OnboardingPage
from src.ui.settings_data import read_form
from src.ui.settings_groups.mail_template_group import MailTemplateGroup
from src.ui.wheel_guard import install_wheel_guard

logger = logging.getLogger(__name__)

TITLE = "Başvuru e-postası"
INTRO_TEXT = (
    "Her firmaya giden e-postanın metni. {sirket_adi}, {ad_soyad} gibi yer tutucular her başvuruda "
    "profilinizden otomatik doldurulur; metni istediğiniz gibi değiştirebilirsiniz. \"Önizle\" ile örnek "
    "bir firma için nasıl görüneceğine bakın. Ayarlar'dan sonradan da düzenlenebilir."
)


class TemplatePage(OnboardingPage):
    page_id = PAGE_TEMPLATE

    def __init__(
        self, engine: Engine, profile_path: Path, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(engine, profile_path, parent)
        self.setTitle(TITLE)
        self._professions: dict[str, ProfessionPreset] = load_profession_presets()
        #: Öneri bu sayfa nesnesinde yalnızca bir kez sunulur (İleri/Geri ile gidip gelince tekrar sorulmaz).
        self._suggestion_offered = False

        intro = QLabel(INTRO_TEXT, self)
        intro.setWordWrap(True)

        self.template_group = MailTemplateGroup(
            self._professions, load_generic_mail_templates(), profile_path, self
        )

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.addWidget(intro)
        content_layout.addWidget(self.template_group)
        content_layout.addStretch(1)
        install_wheel_guard(content)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

    # -- OnboardingPage -------------------------------------------------------------------

    def load(self) -> None:
        with get_session(self._engine) as session:
            form = read_form(session)
        self.template_group.set_values(form)
        if self._suggestion_offered:
            return
        self._suggestion_offered = True
        if is_untouched_default_template(form.mail_template_subject, form.mail_template_body):
            self.template_group.offer_suggestion()

    def validation_errors(self) -> list[str]:
        group = self.template_group
        return [
            *validate_template(group.mail_subject_edit.text(), group.mail_body_edit.toPlainText()),
            *group.custom_profession_errors(),  # meslek "Diğer" ise ad + kelimeler
        ]

    def save(self, session: Session) -> None:
        save_settings_fields(session, self.template_group.values())
