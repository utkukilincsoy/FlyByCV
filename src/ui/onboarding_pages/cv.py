"""Sihirbazın dördüncü sayfası: CV modu (PLAN.md Adım 10.2h).

Ayarlar'ın "CV" grubu (`settings_groups.cv_group.CvGroup`) AYNEN kullanılır: mod, kendi PDF'ini seçme,
özelleştirme, en çok proje. Fark yalnızca iki şey: (1) "Kendi PDF'imi ekle" ÖNERİLEN olarak işaretlenir
ve taze kurulumda başta o seçili gelir (`onboarding_data.initial_cv_mode`; var olan bir kullanıcının
kayıtlı modu "İleri"de sessizce değişmez); (2) "ATS CV oluştur" seçilince Profil sekmesinin düzenleyicileri
(`profile_parts.ProfileSectionsPanel`: Deneyim/Eğitim/Projeler/Yetenekler) aynı sayfada görünür.

PDF seçimi CvGroup'ta olduğu gibi anında `paths.own_cv_path()`e kopyalanır (Ayarlar'la aynı davranış).
Profil değişiklikleri "İleri"de `master_profile.json`a yazılır; kullanıcı onları kendisi ekleyip düzenler,
hiçbir metin uydurulmaz (kural 1/9) ve liste aynıysa dosyaya dokunulmaz.
"""
import logging
from dataclasses import replace
from pathlib import Path
from typing import Optional

from pydantic import ValidationError
from PySide6.QtWidgets import QGroupBox, QLabel, QScrollArea, QVBoxLayout, QWidget
from sqlalchemy.engine import Engine
from sqlmodel import Session

from src.core.db import get_session
from src.core.settings import CV_MODE_OWN_PDF
from src.cv_builder.own_cv import has_own_cv
from src.cv_builder.profile_loader import MasterProfile
from src.ui.onboarding_data import (
    PAGE_CV,
    initial_cv_mode,
    load_existing_profile,
    merge_profile_sections,
    save_profile_sections,
    save_settings_fields,
    validate_cv,
)
from src.ui.onboarding_pages.base import PROFILE_ERROR_TEXT, OnboardingPage
from src.ui.profile_data import format_validation_error
from src.ui.profile_parts import ProfileSectionsPanel
from src.ui.settings_data import CV_MODE_LABELS_TR, read_form
from src.ui.settings_groups.common import hint_label
from src.ui.settings_groups.cv_group import CvGroup
from src.ui.wheel_guard import install_wheel_guard

logger = logging.getLogger(__name__)

TITLE = "CV'niz"
INTRO_TEXT = "Başvuru e-postalarına hangi CV'nin ekleneceğini seçin."
RECOMMENDED_SUFFIX = " (önerilen)"
MODE_HINT = (
    "Kendi PDF'iniz hazırsa en kolayı budur: CV'niz olduğu gibi her başvuruya eklenir, profil "
    "doldurmanız gerekmez. ATS seçeneği ise aşağıdaki bilgilerden her şirket için ATS uyumlu "
    "bir PDF üretir."
)
EDITORS_TITLE = "CV'nizde görünecek bilgiler"
EDITORS_HINT = (
    "ATS CV'si bu bilgilerden üretilir. Burada ekleyip düzenleyebilir, sonradan Profil "
    "sekmesinden de değiştirebilirsiniz."
)
NO_PROFILE_TEXT = "Profil dosyası henüz yok. Önce \"Kişisel bilgiler\" sayfasını doldurun."


class CvPage(OnboardingPage):
    page_id = PAGE_CV

    def __init__(
        self, engine: Engine, profile_path: Path, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(engine, profile_path, parent)
        self.setTitle(TITLE)
        self._loaded_profile: Optional[MasterProfile] = None
        self._profile_error: Optional[str] = None

        intro = QLabel(INTRO_TEXT, self)
        intro.setWordWrap(True)

        self.cv_group = CvGroup(self)
        own_pdf_index = self.cv_group.cv_mode_combo.findData(CV_MODE_OWN_PDF)
        self.cv_group.cv_mode_combo.setItemText(
            own_pdf_index, CV_MODE_LABELS_TR[CV_MODE_OWN_PDF] + RECOMMENDED_SUFFIX
        )
        self.cv_group.cv_mode_combo.currentIndexChanged.connect(self._sync_editors)

        self.sections_panel = ProfileSectionsPanel(parent=self)
        self.profile_status_label = QLabel(self)
        self.profile_status_label.setWordWrap(True)
        self.profile_status_label.setStyleSheet("color: #b91c1c;")
        editors_layout = QVBoxLayout()
        editors_layout.addWidget(hint_label(EDITORS_HINT, self))
        editors_layout.addWidget(self.profile_status_label)
        editors_layout.addWidget(self.sections_panel)
        self.editors_box = QGroupBox(EDITORS_TITLE, self)
        self.editors_box.setLayout(editors_layout)

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.addWidget(intro)
        content_layout.addWidget(hint_label(MODE_HINT, self))
        content_layout.addWidget(self.cv_group)
        content_layout.addWidget(self.editors_box)
        content_layout.addStretch(1)
        install_wheel_guard(content)  # kaydırırken mod/proje sayısı değişmesin

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

    def _is_ats_mode(self) -> bool:
        return self.cv_group.cv_mode_combo.currentData() != CV_MODE_OWN_PDF

    def _sync_editors(self, *_args) -> None:
        self.editors_box.setVisible(self._is_ats_mode())

    def _section_items(self) -> dict[str, list]:
        panel = self.sections_panel
        return {
            "experience": list(panel.experience_section.items),
            "education": list(panel.education_section.items),
            "projects": list(panel.projects_section.items),
            "skills": list(panel.skills_section.items),
        }

    def _load_profile(self) -> None:
        self._loaded_profile = None
        self._profile_error = None
        try:
            profile = load_existing_profile(self._profile_path)
        except ValueError as error:
            logger.exception("Sihirbaz profil dosyasını okuyamadı")
            self._profile_error = PROFILE_ERROR_TEXT.format(error=error)
        else:
            if profile is not None:
                self._loaded_profile = profile
                self.sections_panel.set_profile(profile)
        status = self._profile_error or (NO_PROFILE_TEXT if self._loaded_profile is None else "")
        self.profile_status_label.setText(status)
        self.profile_status_label.setVisible(bool(status))
        self.sections_panel.setEnabled(self._loaded_profile is not None)

    # -- OnboardingPage -------------------------------------------------------------------

    def load(self) -> None:
        with get_session(self._engine) as session:
            form = read_form(session)
            cv_mode = initial_cv_mode(session)
        self.cv_group.set_values(replace(form, cv_mode=cv_mode))
        self._load_profile()
        self._sync_editors()

    def validation_errors(self) -> list[str]:
        cv_mode = self.cv_group.cv_mode_combo.currentData()
        errors = validate_cv(
            cv_mode, has_own_cv(), profile_available=self._loaded_profile is not None
        )
        if errors or self._loaded_profile is None:
            return errors
        try:
            merge_profile_sections(self._loaded_profile, **self._section_items())
        except ValidationError as error:
            return [format_validation_error(error)]
        return []

    def save(self, session: Session) -> None:
        save_settings_fields(session, self.cv_group.values())
        if self._loaded_profile is not None:
            save_profile_sections(self._profile_path, **self._section_items())
