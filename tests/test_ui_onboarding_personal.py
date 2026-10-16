"""src/ui/onboarding_pages/personal.py testleri (PLAN.md Adım 10.2f; qtbot, GERÇEK il listesiyle).

Sihirbazın içinde, yalnızca kişisel bilgi sayfası kayıtlıyken (tek sayfa = son sayfa, "Bitir").
"""
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox, QWizard

from src.core.settings import KEY_AUTOPILOT_CITY, get_setting, set_setting
from src.cv_builder.profile_editor import write_profile
from src.cv_builder.profile_loader import load_profile
from src.ui import onboarding_data as od
from src.ui.onboarding_pages import PAGE_FACTORIES
from src.ui.onboarding_pages.personal import PersonalPage
from src.ui.onboarding_wizard import OnboardingWizard
from tests.conftest import build_sample_profile


@pytest.fixture
def warnings(monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(text) or 0
    )
    return shown


@pytest.fixture
def profile_path(tmp_path):
    return tmp_path / "master_profile.json"


def _make_wizard(qtbot, engine, profile_path) -> OnboardingWizard:
    wizard = OnboardingWizard(
        engine, profile_path, page_factories={od.PAGE_PERSONAL: PersonalPage}
    )
    qtbot.addWidget(wizard)
    wizard.show()
    return wizard


def _page(wizard) -> PersonalPage:
    return wizard.page(od.PAGE_ORDER.index(od.PAGE_PERSONAL))


def _fill(page, *, name="Ayşe Kaya", email="ayse@example.com", phone="", linkedin="", city="06"):
    group = page.personal_group
    group.name_edit.setText(name)
    group.email_edit.setText(email)
    group.phone_edit.setText(phone)
    group.linkedin_edit.setText(linkedin)
    page.city_combo.setCurrentIndex(page.city_combo.findData(city))


def _finish(wizard) -> None:
    wizard.button(QWizard.FinishButton).click()


def test_personal_page_is_registered_between_welcome_and_the_rest():
    assert od.PAGE_PERSONAL in PAGE_FACTORIES
    assert PAGE_FACTORIES[od.PAGE_PERSONAL] is PersonalPage


def test_page_reuses_the_profile_group_with_location_and_intro_hidden(qtbot, engine, profile_path):
    wizard = _make_wizard(qtbot, engine, profile_path)  # sayfa, sihirbazla birlikte yaşar
    page = _page(wizard)

    assert page.personal_group.name_edit.isHidden() is False
    assert page.personal_group.phone_edit.isHidden() is False
    assert page.personal_group.linkedin_edit.isHidden() is False
    assert page.personal_group.location_edit.isHidden() is True
    assert page.personal_group.intro_edit.isHidden() is True


def test_city_list_has_all_provinces_and_is_searchable(qtbot, engine, profile_path):
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)

    assert page.city_combo.count() == 82  # "(seçilmedi)" + 81 il
    assert page.city_combo.isEditable() is True
    completer = page.city_combo.completer()
    completer.setCompletionPrefix("kara")  # "Ankara" ortasında geçer
    assert any("Ankara" in completer.completionModel().index(row, 0).data()
               for row in range(completer.completionCount()))


def test_empty_form_is_blocked_and_nothing_is_written(
    qtbot, engine, profile_path, session, warnings
):
    wizard = _make_wizard(qtbot, engine, profile_path)

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert len(warnings) == 1
    for fragment in ("Ad soyad", "E-posta", "şehir"):
        assert fragment in warnings[0]
    assert not profile_path.exists()
    session.expire_all()
    assert get_setting(session, KEY_AUTOPILOT_CITY) is None


def test_invalid_email_is_blocked(qtbot, engine, profile_path, warnings):
    wizard = _make_wizard(qtbot, engine, profile_path)
    _fill(_page(wizard), email="gecersiz")

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert "geçerli görünmüyor" in warnings[0]
    assert not profile_path.exists()


def test_finish_creates_a_minimal_profile_and_saves_the_city(
    qtbot, engine, profile_path, session
):
    wizard = _make_wizard(qtbot, engine, profile_path)
    _fill(
        _page(wizard),
        phone="0555 111 22 33",
        linkedin="https://linkedin.com/in/ayse",
        city="34",
    )

    _finish(wizard)

    assert wizard.result() == QDialog.Accepted
    info = load_profile(profile_path).personal_info
    assert (info.name, info.email) == ("Ayşe Kaya", "ayse@example.com")
    assert info.phone == "0555 111 22 33"
    assert info.linkedin == "https://linkedin.com/in/ayse"
    assert info.location is None and info.intro is None
    session.expire_all()
    assert get_setting(session, KEY_AUTOPILOT_CITY) == "34"


def test_existing_profile_and_city_are_prefilled(qtbot, engine, profile_path, session):
    write_profile(profile_path, build_sample_profile())
    set_setting(session, KEY_AUTOPILOT_CITY, "06")
    session.commit()

    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)

    assert page.personal_group.name_edit.text() == "Test Kişi"
    assert page.personal_group.email_edit.text() == "test@example.com"
    assert page.city_combo.currentData() == "06"


def test_saving_keeps_hidden_location_intro_and_the_rest_of_the_profile(
    qtbot, engine, profile_path
):
    original = build_sample_profile()
    info = original.personal_info.model_copy(update={"location": "İzmir", "intro": "Kısa tanıtım"})
    original = original.model_copy(update={"personal_info": info})
    write_profile(profile_path, original)
    wizard = _make_wizard(qtbot, engine, profile_path)
    _fill(_page(wizard), name="Yeni Ad", email="test@example.com")

    _finish(wizard)

    saved = load_profile(profile_path)
    assert saved.personal_info.name == "Yeni Ad"
    assert saved.personal_info.location == "İzmir"
    assert saved.personal_info.intro == "Kısa tanıtım"
    assert saved.experience == original.experience
    assert saved.summaries == original.summaries


def test_unchanged_existing_profile_is_not_rewritten(qtbot, engine, profile_path):
    write_profile(profile_path, build_sample_profile())
    wizard = _make_wizard(qtbot, engine, profile_path)
    _page(wizard).city_combo.setCurrentIndex(_page(wizard).city_combo.findData("06"))

    _finish(wizard)

    assert wizard.result() == QDialog.Accepted
    assert list(profile_path.parent.glob("master_profile.json.bak-*")) == []


def test_corrupt_profile_blocks_and_is_not_overwritten(qtbot, engine, profile_path, warnings):
    profile_path.write_text("{ bozuk", encoding="utf-8")
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)
    _fill(page)

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert page.error_label.isHidden() is False
    assert "okunamadı" in warnings[0]
    assert profile_path.read_text(encoding="utf-8") == "{ bozuk"


def test_unreadable_city_list_blocks_the_page(qtbot, engine, profile_path, monkeypatch, warnings):
    monkeypatch.setattr("src.ui.onboarding_pages.personal.load_cities", lambda: {})
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)
    _fill(page, city=None)

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert page.error_label.isHidden() is False
    assert "Şehir listesi okunamadı" in warnings[0]
    assert not profile_path.exists()
