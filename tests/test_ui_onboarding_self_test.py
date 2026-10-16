"""src/ui/onboarding_pages/self_test.py testleri (PLAN.md Adım 10.2l; qtbot).

Sihirbazın içinde, yalnızca bu sayfa kayıtlıyken (tek sayfa = son sayfa, "Bitir"). Gönderim işi sahte:
gerçek SMTP/Playwright/keyring'e dokunulmaz.
"""
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox, QWizard

from src.core import secrets
from src.core.settings import KEY_SENDER_EMAIL, set_setting
from src.cv_builder.profile_editor import write_profile
from src.sender.rate_limiter import HARD_MIN_DELAY_SECONDS
from src.ui import onboarding_data as od
from src.ui.jobs import SelfTestJobResult
from src.ui.onboarding_pages import PAGE_FACTORIES
from src.ui.onboarding_pages.self_test import (
    NO_RECIPIENT_TEXT,
    RESEND_TEXT,
    SEND_TEXT,
    SelfTestPage,
)
from src.ui.onboarding_wizard import OnboardingWizard
from tests.conftest import build_sample_profile


class _FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        del self.store[(service, username)]


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(secrets, "keyring", _FakeKeyring())
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "home"))
    secrets.set_mail_password("abcd efgh ijkl mnop")


@pytest.fixture
def warnings(monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(text) or 0
    )
    return shown


@pytest.fixture
def infos(monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda parent, title, text, *a, **k: shown.append(text) or 0
    )
    return shown


@pytest.fixture
def send_job(monkeypatch):
    """Sahte gönderim işi: `calls` kaç kez çağrıldığını, `result` dönecek sonucu tutar."""

    class Fake:
        calls = 0
        result = SelfTestJobResult(sent=True, recipient="ben@gmail.com", attachment_name="own_cv.pdf")

    Fake.calls = 0

    def _job(engine, profile_path, *, on_progress, token, **kwargs):
        Fake.calls += 1
        return Fake.result

    monkeypatch.setattr("src.ui.onboarding_pages.self_test.self_test_job", _job)
    return Fake


@pytest.fixture
def profile_path(tmp_path):
    path = tmp_path / "master_profile.json"
    write_profile(path, build_sample_profile())
    return path


@pytest.fixture
def sender(session):
    set_setting(session, KEY_SENDER_EMAIL, "ben@gmail.com")
    session.commit()


def _make_wizard(qtbot, engine, profile_path) -> OnboardingWizard:
    wizard = OnboardingWizard(engine, profile_path, page_factories={od.PAGE_SELF_TEST: SelfTestPage})
    qtbot.addWidget(wizard)
    wizard.show()
    return wizard


def _page(wizard) -> SelfTestPage:
    return wizard.page(od.PAGE_ORDER.index(od.PAGE_SELF_TEST))


def _finish(wizard) -> None:
    wizard.button(QWizard.FinishButton).click()


def _send(qtbot, page) -> None:
    page.send_button.click()
    qtbot.waitUntil(lambda: page._worker is None, timeout=2000)


def test_self_test_page_is_registered():
    assert PAGE_FACTORIES[od.PAGE_SELF_TEST] is SelfTestPage


def test_cannot_finish_before_sending_and_checkbox_is_locked(
    qtbot, engine, profile_path, sender, send_job, warnings
):
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)

    assert page.confirm_check.isEnabled() is False
    assert page.send_button.text() == SEND_TEXT
    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert "gönderin" in warnings[0]
    assert send_job.calls == 0


def test_checkbox_unlocks_after_a_successful_send_and_is_required(
    qtbot, engine, profile_path, sender, send_job, warnings
):
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)

    _send(qtbot, page)

    assert send_job.calls == 1
    assert page.confirm_check.isEnabled() is True
    assert page.confirm_check.isChecked() is False
    assert page.send_button.text() == RESEND_TEXT
    assert "ben@gmail.com" in page.status_label.text()
    assert "own_cv.pdf" in page.status_label.text()

    _finish(wizard)  # kutu işaretli değil
    assert wizard.result() != QDialog.Accepted
    assert "onay kutusunu" in warnings[-1]

    page.confirm_check.setChecked(True)
    _finish(wizard)
    assert wizard.result() == QDialog.Accepted


def test_failed_send_keeps_everything_locked_and_shows_the_reason(
    qtbot, engine, profile_path, sender, send_job, warnings
):
    send_job.result = SelfTestJobResult(error="mail gönderilemedi (SMTPAuthenticationError): 535")
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)

    _send(qtbot, page)

    assert page.confirm_check.isEnabled() is False
    assert "SMTPAuthenticationError" in page.status_label.text()
    assert "SMTPAuthenticationError" in warnings[0]
    warnings.clear()
    _finish(wizard)
    assert wizard.result() != QDialog.Accepted
    assert "gönderin" in warnings[0]


def test_resend_unchecks_the_confirmation(qtbot, engine, profile_path, sender, send_job):
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)
    clock = page._clock = _Clock()
    _send(qtbot, page)
    page.confirm_check.setChecked(True)

    clock.now += HARD_MIN_DELAY_SECONDS + 1
    _send(qtbot, page)

    assert send_job.calls == 2
    assert page.confirm_check.isChecked() is False
    assert page.confirm_check.isEnabled() is True


def test_repeated_clicks_are_rate_limited_until_the_cooldown_passes(
    qtbot, engine, profile_path, sender, send_job, infos
):
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)
    clock = page._clock = _Clock()
    _send(qtbot, page)

    clock.now += 5
    page.send_button.click()
    assert send_job.calls == 1
    assert "saniye sonra" in infos[0]

    clock.now += HARD_MIN_DELAY_SECONDS
    _send(qtbot, page)
    assert send_job.calls == 2


def test_refused_attempt_does_not_start_the_cooldown(
    qtbot, engine, profile_path, sender, send_job, warnings
):
    send_job.result = SelfTestJobResult(refused_reason="Gönderim ACİL DURDUR ile duraklatılmış.")
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)
    page._clock = _Clock()

    _send(qtbot, page)
    send_job.result = SelfTestJobResult(sent=True, recipient="ben@gmail.com", attachment_name="a.pdf")
    _send(qtbot, page)  # saat ilerlemedi ama reddedilen deneme bekletmedi

    assert send_job.calls == 2
    assert page.confirm_check.isEnabled() is True


@pytest.mark.parametrize(
    "change",
    [
        lambda session: set_setting(session, KEY_SENDER_EMAIL, "baska@gmail.com"),
        lambda session: set_setting(session, "cv_mode", "own_pdf"),  # varsayılan "ats"
        lambda session: set_setting(session, "mail_template_body", "Yeni gövde {sirket_adi}"),
    ],
)
def test_changing_account_cv_or_template_invalidates_the_sent_test(
    qtbot, engine, session, profile_path, sender, send_job, warnings, change
):
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)
    _send(qtbot, page)
    page.confirm_check.setChecked(True)
    assert page.test_sent() is True

    change(session)
    session.commit()
    page.load()  # önceki sayfalara dönüp tekrar gelmek

    assert page.test_sent() is False
    assert page.confirm_check.isEnabled() is False
    assert page.confirm_check.isChecked() is False
    assert "tekrar gönderin" in page.status_label.text()
    _finish(wizard)
    assert wizard.result() != QDialog.Accepted


def test_unchanged_settings_keep_the_sent_test_when_the_page_is_revisited(
    qtbot, engine, profile_path, sender, send_job
):
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)
    _send(qtbot, page)
    page.confirm_check.setChecked(True)

    page.load()

    assert page.test_sent() is True
    assert page.confirm_check.isChecked() is True
    assert page.confirm_check.isEnabled() is True


def test_removing_the_mail_password_invalidates_the_sent_test(
    qtbot, engine, profile_path, sender, send_job
):
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)
    _send(qtbot, page)

    secrets.delete_mail_password()

    assert page.test_sent() is False


def test_editing_the_profile_invalidates_the_sent_test(qtbot, engine, profile_path, sender, send_job):
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)
    _send(qtbot, page)

    profile = build_sample_profile()
    changed = profile.model_copy(
        update={"personal_info": profile.personal_info.model_copy(update={"phone": "0555 000 00 00"})}
    )
    write_profile(profile_path, changed)

    assert page.test_sent() is False  # ATS CV'si profilden üretilir; dosya değişti


def test_recipient_is_the_saved_sender_then_the_profile_email(
    qtbot, engine, session, profile_path, send_job
):
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)
    assert "test@example.com" in page.recipient_label.text()  # gönderen kayıtlı değil: profil e-postası

    set_setting(session, KEY_SENDER_EMAIL, "kayitli@gmail.com")
    session.commit()
    page.load()
    assert "kayitli@gmail.com" in page.recipient_label.text()


def test_missing_profile_and_sender_shows_a_hint_instead_of_a_recipient(
    qtbot, engine, tmp_path, send_job
):
    wizard = _make_wizard(qtbot, engine, tmp_path / "yok.json")

    assert _page(wizard).recipient_label.text() == NO_RECIPIENT_TEXT
