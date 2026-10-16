"""src/ui/onboarding_pages/welcome.py testleri (PLAN.md Adım 10.2e; qtbot, GERÇEK KVKK metniyle).

Sihirbazın içinde, gerçek `WelcomePage` ile: onay kutusu, kayıt, kaldığı yerden devam.
"""
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox, QWizard

from src.core.legal import LEGAL_TEXT_VERSION, has_consent, record_consent
from src.core.models import utcnow
from src.core.settings import (
    KEY_LEGAL_CONSENT_AT,
    KEY_LEGAL_CONSENT_VERSION,
    KEY_ONBOARDING_COMPLETED,
    get_setting,
    set_setting,
)
from src.ui import onboarding_data as od
from src.ui.onboarding_pages.welcome import WelcomePage
from src.ui.onboarding_wizard import OnboardingWizard


@pytest.fixture
def warnings(monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(text) or 0
    )
    return shown


def _make_wizard(qtbot, engine, tmp_path, *, reopen=False) -> OnboardingWizard:
    # Yalnızca hoş geldin sayfası: sonraki sayfalar kayıt defterine eklendikçe bu dosyanın
    # testleri ("tek kayıtlı sayfa = son sayfa") değişmesin.
    wizard = OnboardingWizard(
        engine,
        tmp_path / "master_profile.json",
        reopen=reopen,
        page_factories={od.PAGE_WELCOME: WelcomePage},
    )
    qtbot.addWidget(wizard)
    wizard.show()
    return wizard


def _welcome(wizard):
    return wizard.page(od.PAGE_ORDER.index(od.PAGE_WELCOME))


def test_welcome_is_the_first_registered_page_and_shows_the_real_legal_text(qtbot, engine, tmp_path):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _welcome(wizard)

    assert wizard.current_page_id() == od.PAGE_WELCOME
    assert "hukuki tavsiye" in page._panel.browser.toPlainText().lower()
    assert page._panel.checkbox.isChecked() is False


def test_next_without_consent_is_blocked_and_nothing_is_recorded(
    qtbot, engine, tmp_path, session, warnings
):
    wizard = _make_wizard(qtbot, engine, tmp_path)

    wizard.button(QWizard.FinishButton).click()

    assert wizard.result() != QDialog.Accepted
    assert len(warnings) == 1 and "onay kutusunu" in warnings[0]
    session.expire_all()
    assert has_consent(session) is False
    assert od.is_onboarding_needed(session) is True


def test_consent_is_recorded_when_box_checked_and_continued(qtbot, engine, tmp_path, session):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    _welcome(wizard)._panel.checkbox.setChecked(True)

    wizard.button(QWizard.FinishButton).click()  # tek kayıtlı sayfa = son sayfa

    assert wizard.result() == QDialog.Accepted
    session.expire_all()
    assert has_consent(session) is True
    assert od.is_onboarding_needed(session) is False


def test_checkbox_comes_prechecked_when_current_version_already_consented(
    qtbot, engine, tmp_path, session
):
    set_setting(session, KEY_LEGAL_CONSENT_VERSION, LEGAL_TEXT_VERSION)
    session.commit()

    wizard = _make_wizard(qtbot, engine, tmp_path)

    assert _welcome(wizard)._panel.checkbox.isChecked() is True


def test_checkbox_is_not_prechecked_for_an_outdated_legal_version(qtbot, engine, tmp_path, session):
    set_setting(session, KEY_LEGAL_CONSENT_VERSION, "1999-01-01")
    session.commit()

    wizard = _make_wizard(qtbot, engine, tmp_path)

    assert _welcome(wizard)._panel.checkbox.isChecked() is False


def test_consent_timestamp_is_not_rewritten_when_already_consented(qtbot, engine, tmp_path, session):
    earlier = utcnow().replace(year=2020)
    record_consent(session, now=earlier)
    session.commit()
    wizard = _make_wizard(qtbot, engine, tmp_path)

    wizard.button(QWizard.FinishButton).click()

    session.expire_all()
    assert get_setting(session, KEY_LEGAL_CONSENT_AT) == earlier.isoformat()


def test_unreadable_legal_text_blocks_the_wizard_without_recording_consent(
    qtbot, engine, tmp_path, session, monkeypatch, warnings
):
    def _fail():
        raise OSError("paket bozuk")

    monkeypatch.setattr("src.ui.onboarding_pages.welcome.load_legal_text", _fail)
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _welcome(wizard)

    assert page._panel is None
    assert page.consent_given() is False

    wizard.button(QWizard.FinishButton).click()

    assert wizard.result() != QDialog.Accepted
    session.expire_all()
    assert has_consent(session) is False


def test_reopened_wizard_for_onboarded_user_shows_welcome_with_existing_consent(
    qtbot, engine, tmp_path, session
):
    set_setting(session, KEY_ONBOARDING_COMPLETED, True)
    set_setting(session, KEY_LEGAL_CONSENT_VERSION, LEGAL_TEXT_VERSION)
    session.commit()

    wizard = _make_wizard(qtbot, engine, tmp_path, reopen=True)

    assert wizard.current_page_id() == od.PAGE_WELCOME
    assert _welcome(wizard)._panel.checkbox.isChecked() is True
