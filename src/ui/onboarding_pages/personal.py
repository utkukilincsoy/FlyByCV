"""Sihirbazın ikinci sayfası: kişisel bilgiler + şehir (PLAN.md Adım 10.2f).

Alanlar Profil sekmesiyle AYNI parçadan gelir (`profile_parts.PersonalInfoGroup`, Şehir/konum ve
Kısa tanıtım satırları gizli); şehir listesi Ayarlar'ın "Firma kaynakları" grubuyla aynı yardımcıdan
(`settings_groups.common.populate_city_combo`) dolar, burada ayrıca aranabilir yapılır. "İleri"de
kişisel bilgi `master_profile.json`a (profil yoksa yalnızca bu bilgilerle oluşturulur), şehir
`autopilot_city` ayarına yazılır.
"""
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QComboBox, QGroupBox, QLabel, QVBoxLayout, QWidget
from sqlalchemy.engine import Engine
from sqlmodel import Session

from src.core.cities import CityPreset, load_cities
from src.core.db import get_session
from src.core.settings import KEY_AUTOPILOT_CITY, get_setting
from src.ui.onboarding_data import (
    PAGE_PERSONAL,
    load_existing_profile,
    save_personal_info,
    save_settings_fields,
    validate_personal,
)
from src.ui.onboarding_pages.base import PROFILE_ERROR_TEXT, OnboardingPage
from src.ui.profile_parts import PersonalInfoGroup
from src.ui.settings_groups.common import (
    hint_label,
    make_searchable,
    populate_city_combo,
    select_index_by_data,
)

logger = logging.getLogger(__name__)

TITLE = "Kişisel bilgiler"
INTRO_TEXT = (
    "Bu bilgiler profilinize yazılır ve başvuru e-postasında ve CV'nizde kullanılır. "
    "Ad soyad ve e-posta zorunludur; telefon ve LinkedIn isteğe bağlıdır."
)
CITY_HINT = "Firmalar bu şehirde aranır. Listede yazarak arayabilirsiniz."
CITIES_ERROR_TEXT = (
    "Şehir listesi okunamadı, bu yüzden şehir seçilemiyor ve kuruluma devam edilemiyor. "
    "Uygulama dosyaları eksik veya bozuk olabilir; uygulamayı yeniden kurmayı deneyin."
)


class PersonalPage(OnboardingPage):
    page_id = PAGE_PERSONAL

    def __init__(
        self, engine: Engine, profile_path: Path, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(engine, profile_path, parent)
        self.setTitle(TITLE)
        self._cities: dict[str, CityPreset] = load_cities()
        self._profile_error: Optional[str] = None

        intro = QLabel(INTRO_TEXT, self)
        intro.setWordWrap(True)

        self.personal_group = PersonalInfoGroup(show_extras=False, parent=self)

        self.city_combo = QComboBox(self)
        populate_city_combo(self.city_combo, self._cities)
        make_searchable(self.city_combo)
        city_layout = QVBoxLayout()
        city_layout.addWidget(self.city_combo)
        city_layout.addWidget(hint_label(CITY_HINT, self))
        city_group = QGroupBox("Şehir*", self)
        city_group.setLayout(city_layout)

        self.error_label = QLabel(self)
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b91c1c;")
        self.error_label.setVisible(False)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.error_label)
        layout.addWidget(self.personal_group)
        layout.addWidget(city_group)
        layout.addStretch(1)

    def _show_error(self, text: Optional[str]) -> None:
        self.error_label.setText(text or "")
        self.error_label.setVisible(bool(text))

    # -- OnboardingPage -------------------------------------------------------------------

    def load(self) -> None:
        """Var olan profil ve kayıtlı şehirle doldurur (geri dönen/yeniden açan kullanıcı için)."""
        self._profile_error = None
        try:
            profile = load_existing_profile(self._profile_path)
        except ValueError as error:
            logger.exception("Sihirbaz profil dosyasını okuyamadı")
            self._profile_error = PROFILE_ERROR_TEXT.format(error=error)
        else:
            if profile is not None:
                self.personal_group.set_info(profile.personal_info)

        with get_session(self._engine) as session:
            saved_city = get_setting(session, KEY_AUTOPILOT_CITY)
        select_index_by_data(self.city_combo, saved_city)

        self._show_error(self._profile_error or (None if self._cities else CITIES_ERROR_TEXT))

    def validation_errors(self) -> list[str]:
        if self._profile_error:
            return [self._profile_error]
        if not self._cities:
            return [CITIES_ERROR_TEXT]
        info = self.personal_group.to_personal_info()
        return validate_personal(
            info.name, info.email, self.city_combo.currentData(), self._cities.keys()
        )

    def save(self, session: Session) -> None:
        save_personal_info(self._profile_path, self.personal_group.to_personal_info())
        save_settings_fields(session, {"autopilot_city": self.city_combo.currentData()})
