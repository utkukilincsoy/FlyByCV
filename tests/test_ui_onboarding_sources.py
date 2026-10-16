"""src/ui/onboarding_pages/sources.py testleri (PLAN.md Adım 10.2j; qtbot).

Sihirbazın içinde, yalnızca kaynaklar sayfası kayıtlıyken (tek sayfa = son sayfa, "Bitir"). Şifre kasası
sahte, Places anahtar testi sahte: gerçek keyring/ağa dokunulmaz.
"""
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox, QWizard

from src.company_finder.google_places import PLACES_HARD_MAX_MONTHLY
from src.core import secrets
from src.core.settings import (
    KEY_AUTOPILOT_CITY,
    KEY_PLACES_MONTHLY_LIMIT,
    KEY_PROFESSION,
    KEY_USE_GOOGLE_PLACES,
    KEY_USE_OSM,
    get_setting,
    set_setting,
)
from src.ui import onboarding_data as od
from src.ui.jobs import PlacesKeyTestJobResult
from src.ui.onboarding_pages import PAGE_FACTORIES
from src.ui.onboarding_pages.sources import NO_OSM_TAGS_TEXT, SourcesPage
from src.ui.onboarding_wizard import OnboardingWizard
from src.ui.settings_data import PLACES_ON_CONFIRM_TITLE

CITIES = ["06", "34"]


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
def warnings(monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(text) or 0
    )
    return shown


@pytest.fixture
def asked(monkeypatch):
    """`QMessageBox.question` çağrılarını kaydeder; `asked.answer` ile cevap verilir."""

    class Asked(list):
        answer = QMessageBox.Yes

    shown = Asked()

    def _question(parent, title, text, *args, **kwargs):
        shown.append(title)
        return shown.answer

    monkeypatch.setattr(QMessageBox, "question", _question)
    return shown


def _make_wizard(qtbot, engine, tmp_path) -> OnboardingWizard:
    wizard = OnboardingWizard(
        engine, tmp_path / "master_profile.json", page_factories={od.PAGE_SOURCES: SourcesPage}
    )
    qtbot.addWidget(wizard)
    wizard.show()
    return wizard


def _page(wizard) -> SourcesPage:
    return wizard.page(od.PAGE_ORDER.index(od.PAGE_SOURCES))


def _finish(wizard) -> None:
    wizard.button(QWizard.FinishButton).click()


def _with_city(session, city="06", profession="hukuk"):
    set_setting(session, KEY_AUTOPILOT_CITY, city)
    set_setting(session, KEY_PROFESSION, profession)
    session.commit()


def test_sources_page_is_registered():
    assert PAGE_FACTORIES[od.PAGE_SOURCES] is SourcesPage


def test_fresh_install_defaults_osm_on_places_off(qtbot, engine, tmp_path):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)

    assert page.sources_group.use_osm_check.isChecked() is True
    assert page.places_group.use_places_check.isChecked() is False


def test_no_city_is_blocked_and_nothing_is_written(qtbot, engine, tmp_path, session, warnings):
    wizard = _make_wizard(qtbot, engine, tmp_path)

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert warnings == ["Bir şehir seçin."]
    session.expire_all()
    assert get_setting(session, KEY_AUTOPILOT_CITY) is None


def test_finish_saves_city_and_osm_choice(qtbot, engine, tmp_path, session):
    _with_city(session, "34")
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    page.sources_group.use_osm_check.setChecked(False)

    _finish(wizard)

    assert wizard.result() == QDialog.Accepted
    session.expire_all()
    assert get_setting(session, KEY_AUTOPILOT_CITY) == "34"
    assert get_setting(session, KEY_USE_OSM) is False
    assert get_setting(session, KEY_USE_GOOGLE_PLACES) is False


def test_summary_names_osm_for_a_profession_with_osm_tags(qtbot, engine, tmp_path, session):
    _with_city(session, "34", "hukuk")
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)

    assert "OpenStreetMap" in page.sources_group.sources_hint_label.text()
    assert page.no_osm_label.isHidden() is True


def test_profession_without_osm_tags_shows_the_note(qtbot, engine, tmp_path, session):
    _with_city(session, "34", "ozel_meslek")
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    from src.core.professions import load_profession_presets

    page._professions["ozel_meslek"] = load_profession_presets()["hukuk"].model_copy(
        update={"id": "ozel_meslek", "osm_tags": None}
    )
    page.load()

    assert page.no_osm_label.isHidden() is False
    assert page.no_osm_label.text() == NO_OSM_TAGS_TEXT
    assert "OpenStreetMap" not in page.sources_group.sources_hint_label.text()


def test_enabling_places_asks_confirmation_and_yes_saves(
    qtbot, engine, tmp_path, session, asked
):
    _with_city(session)
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    page.places_group.use_places_check.setChecked(True)
    page.places_group.places_limit_spin.setValue(300)

    _finish(wizard)

    assert asked == [PLACES_ON_CONFIRM_TITLE]
    assert wizard.result() == QDialog.Accepted
    session.expire_all()
    assert get_setting(session, KEY_USE_GOOGLE_PLACES) is True
    assert get_setting(session, KEY_PLACES_MONTHLY_LIMIT) == 300


def test_enabling_places_and_answering_no_keeps_it_off_but_continues(
    qtbot, engine, tmp_path, session, asked
):
    _with_city(session)
    asked.answer = QMessageBox.No
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    page.places_group.use_places_check.setChecked(True)

    _finish(wizard)

    assert wizard.result() == QDialog.Accepted
    assert page.places_group.use_places_check.isChecked() is False
    session.expire_all()
    assert get_setting(session, KEY_USE_GOOGLE_PLACES) is False


def test_places_already_on_is_not_asked_again(qtbot, engine, tmp_path, session, asked):
    _with_city(session)
    set_setting(session, KEY_USE_GOOGLE_PLACES, True)
    session.commit()
    wizard = _make_wizard(qtbot, engine, tmp_path)

    _finish(wizard)

    assert asked == []
    assert wizard.result() == QDialog.Accepted


def test_places_off_is_never_asked(qtbot, engine, tmp_path, session, asked):
    _with_city(session)
    wizard = _make_wizard(qtbot, engine, tmp_path)

    _finish(wizard)

    assert asked == []


def test_no_confirmation_when_validation_fails(qtbot, engine, tmp_path, asked, warnings):
    wizard = _make_wizard(qtbot, engine, tmp_path)  # şehir yok
    _page(wizard).places_group.use_places_check.setChecked(True)

    _finish(wizard)

    assert asked == []
    assert warnings == ["Bir şehir seçin."]


def test_key_status_reaches_the_summary(qtbot, engine, tmp_path, session):
    _with_city(session, "34", "hukuk")
    set_setting(session, KEY_USE_GOOGLE_PLACES, True)
    session.commit()

    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    assert "Google Places" not in page.sources_group.sources_hint_label.text()  # anahtar yok

    secrets.set_api_key("google_places", "anahtar")
    page.places_group.refresh_key_status()

    assert page.places_group.key_status_label.text() == "kayıtlı ✓"
    assert "Google Places" in page.sources_group.sources_hint_label.text()


def test_places_key_test_runs_in_the_background(qtbot, engine, tmp_path, monkeypatch, warnings):
    calls = []

    def _job(*, on_progress, token):
        calls.append("ran")
        return PlacesKeyTestJobResult(no_key=True)

    monkeypatch.setattr("src.ui.onboarding_pages.sources.places_key_test_job", _job)
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)

    page.places_group.test_button.click()
    qtbot.waitUntil(lambda: page._worker is None, timeout=2000)

    assert calls == ["ran"]
    assert "API anahtarı yok" in warnings[0]


@pytest.mark.parametrize(
    "city,limit,fragment",
    [
        (None, 0, "Bir şehir seçin"),
        ("99", 0, "listede yok"),
        ("06", -1, "negatif"),
        ("06", PLACES_HARD_MAX_MONTHLY + 1, "en çok"),
    ],
)
def test_validate_sources_errors(city, limit, fragment):
    errors = od.validate_sources(city, CITIES, limit)
    assert len(errors) == 1 and fragment in errors[0]


def test_validate_sources_accepts_valid_choices():
    assert od.validate_sources("06", CITIES, 0) == []
    assert od.validate_sources("34", CITIES, PLACES_HARD_MAX_MONTHLY) == []
