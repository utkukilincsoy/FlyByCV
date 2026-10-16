"""Sihirbazın firma kaynakları sayfası (PLAN.md Adım 10.2j).

Ayarlar'ın iki grubu AYNEN kullanılır: "Firma kaynakları" (`SourcesGroup`: şehir + OpenStreetMap, taze
kurulumda varsayılan AÇIK, anahtarsız ve ücretsiz) ve "Google Places" (`PlacesGroup`: isteğe bağlı, ücretli
olabilir, varsayılan KAPALI; anahtar şifre kasasına, "Anahtarı test et" arka planda). Sayfanın eklediği:
- Seçilen meslekte OpenStreetMap etiketi yoksa bunu söyleyen satır (kaynak seçici o durumda OSM'i atlar).
- Google Places kapalıyken açılırsa Ayarlar'daki AYNI Evet/Hayır onayı ("İleri"de, bir kez); reddedilirse
  kutu kapalı kalır ve devam edilir.
Hangi kaynakların kullanılacağı `SourcesGroup`un kendi özet satırında görünür (`select_sources`).
"""
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QLabel, QMessageBox, QScrollArea, QVBoxLayout, QWidget
from sqlalchemy.engine import Engine
from sqlmodel import Session

from src.company_finder.google_places import requests_this_month
from src.core.cities import CityPreset, load_cities
from src.core.custom_profession import CUSTOM_PROFESSION_ID, resolve_profession
from src.core.db import get_session
from src.core.professions import ProfessionPreset, load_profession_presets
from src.core.settings import KEY_PROFESSION, KEY_USE_GOOGLE_PLACES, get_setting
from src.ui.jobs import places_key_test_job
from src.ui.onboarding_data import PAGE_SOURCES, save_settings_fields, validate_sources
from src.ui.onboarding_pages.base import OnboardingPage
from src.ui.settings_data import (
    PLACES_ON_CONFIRM_TEXT,
    PLACES_ON_CONFIRM_TITLE,
    read_form,
)
from src.ui.settings_groups.common import hint_label, make_searchable
from src.ui.settings_groups.places_group import PlacesGroup, show_places_test_result
from src.ui.settings_groups.sources_group import SourcesContext, SourcesGroup
from src.ui.wheel_guard import install_wheel_guard

logger = logging.getLogger(__name__)

TITLE = "Firmalar nereden bulunacak?"
INTRO_TEXT = (
    "Uygulama ilan sitelerini taramaz; seçtiğiniz şehirdeki firmaları harita verisinden (OpenStreetMap, "
    "ücretsiz, anahtar gerekmez) bulur, sitelerinden e-posta adresini çıkarır. Kendi listenizi "
    "Firmalar sekmesinden CSV ile her zaman ekleyebilirsiniz. Google Places isteğe bağlıdır ve ücretli "
    "olabilir; kapalı kalması önerilir. Hepsini sonradan Ayarlar'dan değiştirebilirsiniz."
)
NO_OSM_TAGS_TEXT = (
    "Seçtiğiniz meslek için OpenStreetMap etiketi tanımlı değil; bu meslekte OpenStreetMap atlanır. "
    "Firmaları CSV ile içe aktarabilir ya da Google Places'i kullanabilirsiniz."
)
PLACES_TEST_JOB_TITLE = "Google Places anahtarı test ediliyor"


class SourcesPage(OnboardingPage):
    page_id = PAGE_SOURCES

    def __init__(
        self, engine: Engine, profile_path: Path, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(engine, profile_path, parent)
        self.setTitle(TITLE)
        self._cities: dict[str, CityPreset] = load_cities()
        self._professions: dict[str, ProfessionPreset] = load_profession_presets()
        self._profession_id: Optional[str] = None
        #: Seçili meslek paketi ("Diğer" ise kullanıcının ad/kelimelerinden üretilmiş), yoksa `None`.
        self._profession: Optional[ProfessionPreset] = None
        self._has_places_key = False

        intro = QLabel(INTRO_TEXT, self)
        intro.setWordWrap(True)

        self.sources_group = SourcesGroup(self._cities, self._professions, self._sources_context, self)
        make_searchable(self.sources_group.city_combo)
        self.places_group = PlacesGroup(self._places_requests_used, self)
        self.places_group.use_places_check.stateChanged.connect(self.sources_group.update_hint)
        self.places_group.key_status_changed.connect(self._on_key_status_changed)
        self.places_group.test_requested.connect(self._on_test_places_key)

        self.no_osm_label = hint_label(NO_OSM_TAGS_TEXT, self)
        self.no_osm_label.setStyleSheet("color: #b45309;")
        self.no_osm_label.setVisible(False)

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.addWidget(intro)
        content_layout.addWidget(self.sources_group)
        content_layout.addWidget(self.no_osm_label)
        content_layout.addWidget(self.places_group)
        content_layout.addStretch(1)
        install_wheel_guard(content)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

    # -- grupların dışarıdan okuduğu değerler -----------------------------------------------

    def _sources_context(self) -> SourcesContext:
        return SourcesContext(
            profession_id=self._profession_id,
            use_google_places=self.places_group.use_places_check.isChecked(),
            has_places_key=self._has_places_key,
            custom_profession=self._profession if self._profession_id == CUSTOM_PROFESSION_ID else None,
        )

    def _places_requests_used(self) -> int:
        with get_session(self._engine) as session:
            return requests_this_month(session)

    def _on_key_status_changed(self, saved: bool) -> None:
        self._has_places_key = saved
        self.sources_group.update_hint()

    def _refresh_osm_note(self) -> None:
        preset = self._profession
        self.no_osm_label.setVisible(preset is not None and not preset.osm_tags)

    def _on_test_places_key(self) -> None:
        if self.guard_running():
            return
        self.start_job(
            PLACES_TEST_JOB_TITLE,
            places_key_test_job,
            lambda result: show_places_test_result(self, result),
        )

    # -- Google Places açılış onayı -----------------------------------------------------------

    def _places_newly_enabled(self) -> bool:
        with get_session(self._engine) as session:
            was_on = bool(get_setting(session, KEY_USE_GOOGLE_PLACES))
        return self.places_group.use_places_check.isChecked() and not was_on

    def validatePage(self) -> bool:
        # Önce doğrulama (hatalıysa onay sorulmaz, sonra tekrar sorulmasın); onay reddedilirse kutu kapanır.
        if not self.validation_errors() and self._places_newly_enabled():
            answer = QMessageBox.question(
                self,
                PLACES_ON_CONFIRM_TITLE,
                PLACES_ON_CONFIRM_TEXT,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self.places_group.use_places_check.setChecked(False)
        return super().validatePage()

    # -- OnboardingPage -------------------------------------------------------------------

    def load(self) -> None:
        with get_session(self._engine) as session:
            form = read_form(session)
            self._profession_id = get_setting(session, KEY_PROFESSION)
            self._profession = resolve_profession(session, self._professions)
        self.sources_group.set_values(form)
        self.places_group.set_values(form)
        self.places_group.refresh_usage()
        self.places_group.refresh_key_status()  # anahtar durumunu okur, özeti tazeler
        self.sources_group.update_hint()
        self._refresh_osm_note()

    def validation_errors(self) -> list[str]:
        return validate_sources(
            self.sources_group.city_combo.currentData(),
            self._cities.keys(),
            self.places_group.places_limit_spin.value(),
        )

    def save(self, session: Session) -> None:
        save_settings_fields(session, {**self.sources_group.values(), **self.places_group.values()})
