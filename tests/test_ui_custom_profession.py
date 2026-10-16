"""\"Diğer\" mesleğin arayüzdeki yolları: Ayarlar sekmesi, kaynaklar ve şablon sayfaları (PLAN.md 10.2g/10.5c).

Hedef sayfası testleri `tests/test_ui_onboarding_target.py`'de; çekirdek davranış `tests/test_custom_profession.py`'de.
"""
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox, QWizard

from src.core import secrets
from src.core.custom_profession import CUSTOM_PROFESSION_ID
from src.core.mail_template_presets import load_generic_mail_templates
from src.core.settings import (
    KEY_APPLICATION_TARGET,
    KEY_AUTOPILOT_CITY,
    KEY_CUSTOM_PROFESSION_KEYWORDS,
    KEY_CUSTOM_PROFESSION_NAME,
    KEY_MAIL_TEMPLATE_BODY,
    KEY_MAIL_TEMPLATE_SUBJECT,
    KEY_PROFESSION,
    KEY_USE_GOOGLE_PLACES,
    get_setting,
    set_setting,
)
from src.cv_builder.profile_editor import write_profile
from src.ui import onboarding_data as od
from src.ui.onboarding_pages.sources import NO_OSM_TAGS_TEXT, SourcesPage
from src.ui.onboarding_pages.template import TemplatePage
from src.ui.onboarding_wizard import OnboardingWizard
from src.ui.settings_tab import SettingsTab
from tests.conftest import build_sample_profile

NAME = "Diş hekimliği"


class _FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        del self.store[(service, username)]


@pytest.fixture(autouse=True)
def keyring_store(monkeypatch):
    fake = _FakeKeyring()
    monkeypatch.setattr(secrets, "keyring", fake)
    return fake


@pytest.fixture
def asked(monkeypatch):
    class Asked(list):
        answer = QMessageBox.No

    shown = Asked()

    def _question(parent, title, text, *args, **kwargs):
        shown.append(text)
        return shown.answer

    monkeypatch.setattr(QMessageBox, "question", _question)
    return shown


@pytest.fixture
def criticals(monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "critical", lambda parent, title, text, *a, **k: shown.append(text) or 0
    )
    return shown


@pytest.fixture
def warnings(monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(text) or 0
    )
    return shown


def _make_tab(qtbot, engine, tmp_path) -> SettingsTab:
    tab = SettingsTab(engine, tmp_path / "master_profile.json")
    qtbot.addWidget(tab)
    return tab


def _select_custom(tab: SettingsTab) -> None:
    tab._profession_combo.setCurrentIndex(tab._profession_combo.findData(CUSTOM_PROFESSION_ID))


# --- Ayarlar sekmesi -----------------------------------------------------------------------------


def test_settings_profession_list_ends_with_other_and_fields_follow_it(qtbot, engine, tmp_path, asked):
    tab = _make_tab(qtbot, engine, tmp_path)
    combo = tab._profession_combo
    fields = tab._template_group.custom_fields

    assert combo.itemData(combo.count() - 1) == CUSTOM_PROFESSION_ID
    assert fields.isHidden() is True

    _select_custom(tab)
    assert fields.isHidden() is False

    combo.setCurrentIndex(combo.findData("hukuk"))
    assert fields.isHidden() is True


def test_settings_save_and_reload_round_trip(qtbot, engine, tmp_path, session, asked):
    tab = _make_tab(qtbot, engine, tmp_path)
    _select_custom(tab)
    tab._custom_profession_name_edit.setText(NAME)
    tab._custom_profession_keywords_edit.setText("diş kliniği, ortodonti")
    assert tab.is_dirty() is True

    tab._on_save()

    session.expire_all()
    assert get_setting(session, KEY_PROFESSION) == CUSTOM_PROFESSION_ID
    assert get_setting(session, KEY_CUSTOM_PROFESSION_NAME) == NAME
    assert get_setting(session, KEY_CUSTOM_PROFESSION_KEYWORDS) == ["diş kliniği", "ortodonti"]
    assert tab.is_dirty() is False

    tab.reload()
    assert tab._profession_combo.currentData() == CUSTOM_PROFESSION_ID
    assert tab._custom_profession_name_edit.text() == NAME
    assert tab._custom_profession_keywords_edit.text() == "diş kliniği, ortodonti"
    assert tab._template_group.custom_fields.isHidden() is False
    assert tab.is_dirty() is False


def test_settings_save_is_refused_when_other_has_no_name_or_keywords(
    qtbot, engine, tmp_path, session, asked, criticals
):
    tab = _make_tab(qtbot, engine, tmp_path)
    _select_custom(tab)

    tab._on_save()

    assert any("Meslek adını yazın" in text and "arama kelimesi" in text for text in criticals)
    session.expire_all()
    assert get_setting(session, KEY_PROFESSION) is None


def test_settings_editing_custom_fields_marks_the_form_dirty(qtbot, engine, tmp_path, session, asked):
    set_setting(session, KEY_PROFESSION, CUSTOM_PROFESSION_ID)
    set_setting(session, KEY_CUSTOM_PROFESSION_NAME, NAME)
    set_setting(session, KEY_CUSTOM_PROFESSION_KEYWORDS, ["diş kliniği"])
    session.commit()
    tab = _make_tab(qtbot, engine, tmp_path)
    assert tab.is_dirty() is False

    tab._custom_profession_keywords_edit.setText("diş kliniği, implant")

    assert tab.is_dirty() is True


def test_settings_source_summary_follows_the_custom_keywords(qtbot, engine, tmp_path, session, asked):
    set_setting(session, KEY_AUTOPILOT_CITY, "06")
    set_setting(session, KEY_USE_GOOGLE_PLACES, True)
    session.commit()
    tab = _make_tab(qtbot, engine, tmp_path)
    tab._has_places_key = True
    _select_custom(tab)
    # Ad/kelime henüz yok → geçersiz → meslek yok → kaynak yok.
    assert "otomatik kaynak yok" in tab._sources_hint_label.text()

    tab._custom_profession_name_edit.setText(NAME)
    tab._custom_profession_keywords_edit.setText("diş kliniği")

    assert "Google Places" in tab._sources_hint_label.text()
    assert "OpenStreetMap" not in tab._sources_hint_label.text()  # etiket yok → OSM atlanır


def test_settings_selecting_other_offers_the_generic_template(qtbot, engine, tmp_path, session, asked):
    set_setting(session, KEY_APPLICATION_TARGET, "internship")
    session.commit()
    tab = _make_tab(qtbot, engine, tmp_path)
    asked.clear()

    _select_custom(tab)

    generic = load_generic_mail_templates()[("internship", "tr")]
    assert len(asked) == 1
    assert generic.subject in asked[0] and generic.body in asked[0]


# --- Sihirbaz: kaynaklar sayfası -------------------------------------------------------------------


def _wizard(qtbot, engine, tmp_path, page_id, factory) -> OnboardingWizard:
    wizard = OnboardingWizard(engine, tmp_path / "master_profile.json", page_factories={page_id: factory})
    qtbot.addWidget(wizard)
    wizard.show()
    return wizard


def _use_other(session, **extra):
    set_setting(session, KEY_PROFESSION, CUSTOM_PROFESSION_ID)
    set_setting(session, KEY_CUSTOM_PROFESSION_NAME, NAME)
    set_setting(session, KEY_CUSTOM_PROFESSION_KEYWORDS, ["diş kliniği", "ortodonti"])
    for key, value in extra.items():
        set_setting(session, key, value)
    session.commit()


def test_sources_page_other_profession_shows_the_no_osm_note(qtbot, engine, tmp_path, session):
    _use_other(session, **{KEY_AUTOPILOT_CITY: "34"})

    wizard = _wizard(qtbot, engine, tmp_path, od.PAGE_SOURCES, SourcesPage)
    page = wizard.page(od.PAGE_ORDER.index(od.PAGE_SOURCES))

    assert page.no_osm_label.isHidden() is False
    assert page.no_osm_label.text() == NO_OSM_TAGS_TEXT
    assert "OpenStreetMap" not in page.sources_group.sources_hint_label.text()


def test_sources_page_other_with_places_and_key_names_google(qtbot, engine, tmp_path, session):
    _use_other(session, **{KEY_AUTOPILOT_CITY: "34", KEY_USE_GOOGLE_PLACES: True})
    secrets.set_api_key("google_places", "anahtar")

    wizard = _wizard(qtbot, engine, tmp_path, od.PAGE_SOURCES, SourcesPage)
    page = wizard.page(od.PAGE_ORDER.index(od.PAGE_SOURCES))

    assert "Google Places" in page.sources_group.sources_hint_label.text()


# --- Sihirbaz: şablon sayfası ------------------------------------------------------------------------


def test_template_page_other_profession_gets_the_generic_suggestion_and_meslek_is_the_name(
    qtbot, engine, tmp_path, session, asked, monkeypatch
):
    _use_other(session, **{KEY_APPLICATION_TARGET: "internship"})
    asked.answer = QMessageBox.Yes
    write_profile(tmp_path / "master_profile.json", build_sample_profile())
    infos = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda parent, title, text, *a, **k: infos.append(text) or 0
    )

    wizard = _wizard(qtbot, engine, tmp_path, od.PAGE_TEMPLATE, TemplatePage)
    group = wizard.page(od.PAGE_ORDER.index(od.PAGE_TEMPLATE)).template_group

    generic = load_generic_mail_templates()[("internship", "tr")]
    assert group.mail_subject_edit.text() == generic.subject  # mesleğe özel paket yok → genel şablon

    group.mail_subject_edit.setText("{meslek} – {sirket_adi}")
    group.preview_button.click()
    assert NAME in infos[0]


def test_template_page_other_without_fields_is_blocked(qtbot, engine, tmp_path, session, asked, warnings):
    set_setting(session, KEY_PROFESSION, CUSTOM_PROFESSION_ID)  # ad/kelime yok
    session.commit()

    wizard = _wizard(qtbot, engine, tmp_path, od.PAGE_TEMPLATE, TemplatePage)
    wizard.button(QWizard.FinishButton).click()

    assert wizard.result() != QDialog.Accepted
    assert any("Meslek adını yazın" in text for text in warnings)


def test_template_page_saves_custom_fields_unchanged(qtbot, engine, tmp_path, session, asked):
    _use_other(session)  # hedef yok → öneri yok
    set_setting(session, KEY_MAIL_TEMPLATE_SUBJECT, "Konu {sirket_adi}")
    set_setting(session, KEY_MAIL_TEMPLATE_BODY, "Gövde {ad_soyad}")
    session.commit()

    wizard = _wizard(qtbot, engine, tmp_path, od.PAGE_TEMPLATE, TemplatePage)
    wizard.button(QWizard.FinishButton).click()

    assert wizard.result() == QDialog.Accepted
    session.expire_all()
    assert get_setting(session, KEY_PROFESSION) == CUSTOM_PROFESSION_ID
    assert get_setting(session, KEY_CUSTOM_PROFESSION_NAME) == NAME
    assert get_setting(session, KEY_CUSTOM_PROFESSION_KEYWORDS) == ["diş kliniği", "ortodonti"]
