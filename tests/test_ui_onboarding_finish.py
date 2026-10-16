"""src/ui/onboarding_pages/finish.py testleri (PLAN.md Adım 10.2m; qtbot).

Sihirbazın içinde, yalnızca bitiş sayfası kayıtlıyken (tek sayfa = son sayfa, "Bitir" = "Otopilot'a git").
"""
import pytest
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QWizard

from src.core import secrets
from src.core.legal import record_consent
from src.core.models import Setting
from src.core.settings import (
    KEY_ONBOARDING_COMPLETED,
    KEY_SENDER_EMAIL,
    KEY_TEST_MODE,
    get_setting,
    set_setting,
)
from src.cv_builder.profile_editor import write_profile
from src.ui import onboarding_data as od
from src.ui import onboarding_summary
from src.ui.onboarding_pages import PAGE_FACTORIES
from src.ui.onboarding_pages.finish import FINISH_BUTTON_TEXT, FinishPage
from src.ui.onboarding_wizard import OnboardingWizard
from src.ui.test_mode_data import BACK_TO_TEST_BUTTON_TEXT, GO_LIVE_BUTTON_TEXT, NO_CONSENT_TITLE
from src.ui.test_mode_dialog import GoLiveDialog
from tests.conftest import build_sample_profile


class _FakeKeyring:
    def __init__(self) -> None:
        self.store: dict = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        del self.store[(service, username)]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(secrets, "keyring", _FakeKeyring())
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "home"))


@pytest.fixture
def profile_path(tmp_path):
    path = tmp_path / "master_profile.json"
    write_profile(path, build_sample_profile())
    return path


def _make_wizard(qtbot, engine, profile_path, *, reopen=False) -> OnboardingWizard:
    wizard = OnboardingWizard(
        engine, profile_path, reopen=reopen, page_factories={od.PAGE_FINISH: FinishPage}
    )
    qtbot.addWidget(wizard)
    wizard.show()
    return wizard


def _page(wizard) -> FinishPage:
    return wizard.page(od.PAGE_ORDER.index(od.PAGE_FINISH))


def _summary_texts(page: FinishPage) -> list[str]:
    return [label.text() for label in page.findChildren(QLabel)]


def test_finish_page_is_registered_and_last():
    assert PAGE_FACTORIES[od.PAGE_FINISH] is FinishPage
    assert od.PAGE_ORDER[-1] == od.PAGE_FINISH


def test_finish_button_says_go_to_autopilot(qtbot, engine, profile_path):
    wizard = _make_wizard(qtbot, engine, profile_path)

    assert wizard.button(QWizard.FinishButton).text() == FINISH_BUTTON_TEXT == "Otopilot'a git"


def test_summary_shows_the_saved_state(qtbot, engine, session, profile_path):
    set_setting(session, KEY_SENDER_EMAIL, "ben@gmail.com")
    session.commit()

    wizard = _make_wizard(qtbot, engine, profile_path)
    texts = _summary_texts(_page(wizard))

    assert "Test Kişi" in texts
    assert any("ben@gmail.com" in text for text in texts)


def test_test_mode_is_on_and_shown_green_on_a_fresh_install(qtbot, engine, profile_path):
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)

    assert page.test_mode_label.text() == onboarding_summary.TEST_MODE_ON_TEXT
    assert "#15803d" in page.test_mode_label.styleSheet()


def test_finishing_keeps_test_mode_on_and_completes_onboarding(qtbot, engine, session, profile_path):
    wizard = _make_wizard(qtbot, engine, profile_path)

    wizard.button(QWizard.FinishButton).click()

    assert wizard.result() == QDialog.Accepted
    session.expire_all()
    assert get_setting(session, KEY_ONBOARDING_COMPLETED) is True
    assert get_setting(session, KEY_TEST_MODE) is True
    assert session.get(Setting, KEY_TEST_MODE) is None  # sihirbaz test modu ayarına HİÇ dokunmadı


def test_reopened_wizard_with_test_mode_off_warns_in_red_and_keeps_it_off(
    qtbot, engine, session, profile_path
):
    set_setting(session, KEY_TEST_MODE, False)
    set_setting(session, KEY_ONBOARDING_COMPLETED, True)
    session.commit()
    wizard = _make_wizard(qtbot, engine, profile_path, reopen=True)
    page = _page(wizard)

    assert page.test_mode_label.text() == onboarding_summary.TEST_MODE_OFF_TEXT
    assert "#b91c1c" in page.test_mode_label.styleSheet()
    wizard.button(QWizard.FinishButton).click()
    session.expire_all()
    assert get_setting(session, KEY_TEST_MODE) is False  # kullanıcının seçimi sessizce değişmedi


def test_go_live_button_without_consent_changes_nothing(qtbot, engine, session, profile_path, monkeypatch):
    monkeypatch.setattr("src.ui.test_mode_data.has_consent", lambda session: False)
    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda parent, title, text: shown.append(title))
    )
    opened: list[int] = []
    monkeypatch.setattr(GoLiveDialog, "exec", lambda self: opened.append(1) or QDialog.Accepted)
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)

    assert page.test_mode_button.text() == GO_LIVE_BUTTON_TEXT
    page.test_mode_button.click()

    assert shown == [NO_CONSENT_TITLE]
    assert opened == []
    session.expire_all()
    assert get_setting(session, KEY_TEST_MODE) is True
    assert page.test_mode_label.text() == onboarding_summary.TEST_MODE_ON_TEXT


def test_go_live_button_confirmed_turns_test_mode_off_and_back(
    qtbot, engine, session, profile_path, monkeypatch
):
    record_consent(session)
    session.commit()
    monkeypatch.setattr(GoLiveDialog, "exec", lambda self: QDialog.Accepted)
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)

    page.test_mode_button.click()

    session.expire_all()
    assert get_setting(session, KEY_TEST_MODE) is False
    assert page.test_mode_label.text() == onboarding_summary.TEST_MODE_OFF_TEXT
    assert "#b91c1c" in page.test_mode_label.styleSheet()
    assert page.test_mode_button.text() == BACK_TO_TEST_BUTTON_TEXT

    page.test_mode_button.click()  # "Test moduna dön": onaysız, pencere açılmaz

    session.expire_all()
    assert get_setting(session, KEY_TEST_MODE) is True
    assert page.test_mode_label.text() == onboarding_summary.TEST_MODE_ON_TEXT
    assert page.test_mode_button.text() == GO_LIVE_BUTTON_TEXT


def test_go_live_button_cancelled_keeps_test_mode(qtbot, engine, session, profile_path, monkeypatch):
    record_consent(session)
    session.commit()
    monkeypatch.setattr(GoLiveDialog, "exec", lambda self: QDialog.Rejected)
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)

    page.test_mode_button.click()

    session.expire_all()
    assert get_setting(session, KEY_TEST_MODE) is True


def test_go_live_button_is_disabled_when_the_summary_failed(qtbot, engine, profile_path, monkeypatch):
    def broken(session, profile_path):
        raise RuntimeError("özet patladı")

    monkeypatch.setattr("src.ui.onboarding_pages.finish.build_finish_summary", broken)
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)

    assert page.test_mode_button.isEnabled() is False


def test_summary_failure_does_not_block_finishing(qtbot, engine, profile_path, monkeypatch):
    def broken(session, profile_path):
        raise RuntimeError("özet patladı")

    monkeypatch.setattr("src.ui.onboarding_pages.finish.build_finish_summary", broken)
    wizard = _make_wizard(qtbot, engine, profile_path)

    assert any("Özet okunamadı" in text for text in _summary_texts(_page(wizard)))
    wizard.button(QWizard.FinishButton).click()
    assert wizard.result() == QDialog.Accepted


def test_summary_is_rebuilt_every_time_the_page_is_shown(qtbot, engine, session, profile_path):
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)
    rows_before = page._summary_form.rowCount()
    set_setting(session, KEY_SENDER_EMAIL, "yeni@gmail.com")
    session.commit()

    page.load()

    assert page._summary_form.rowCount() == rows_before  # satırlar birikmedi
    assert any("yeni@gmail.com" in text for text in _summary_texts(page))
