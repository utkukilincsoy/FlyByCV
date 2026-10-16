"""src/ui/onboarding_pages/mail.py testleri (PLAN.md Adım 10.2i; qtbot).

Sihirbazın içinde, yalnızca mail sayfası kayıtlıyken (tek sayfa = son sayfa, "Bitir"). Şifre kasası ve
bağlantı testi işi sahte: gerçek keyring/SMTP/IMAP'e dokunulmaz.
"""
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox, QWizard

from src.core import secrets
from src.core.mail_providers import load_mail_provider_presets
from src.core.settings import (
    KEY_CUSTOM_SMTP_HOST,
    KEY_MAIL_PROVIDER,
    KEY_SENDER_EMAIL,
    get_setting,
    set_setting,
)
from src.cv_builder.profile_editor import write_profile
from src.ui import onboarding_data as od
from src.ui.jobs import SmtpLoginTestJobResult
from src.ui.onboarding_pages import PAGE_FACTORIES
from src.ui.onboarding_pages.mail import MailPage, guide_text
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


@pytest.fixture
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
def infos(monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda parent, title, text, *a, **k: shown.append(text) or 0
    )
    return shown


@pytest.fixture
def test_job(monkeypatch):
    """Sahte bağlantı testi: `calls` çağrı argümanlarını, `result` dönecek sonucu tutar."""

    class Fake:
        calls: list = []
        result = SmtpLoginTestJobResult(email="ben@gmail.com", ok=True, imap_ok=True)

    Fake.calls = []

    def _job(profile_path, *, on_progress, token, **kwargs):
        Fake.calls.append(kwargs)
        return Fake.result

    monkeypatch.setattr("src.ui.onboarding_pages.mail.smtp_login_test_job", _job)
    return Fake


def _make_wizard(qtbot, engine, tmp_path) -> OnboardingWizard:
    wizard = OnboardingWizard(
        engine, tmp_path / "master_profile.json", page_factories={od.PAGE_MAIL: MailPage}
    )
    qtbot.addWidget(wizard)
    wizard.show()
    return wizard


def _page(wizard) -> MailPage:
    return wizard.page(od.PAGE_ORDER.index(od.PAGE_MAIL))


def _finish(wizard) -> None:
    wizard.button(QWizard.FinishButton).click()


def _run_test(qtbot, page) -> None:
    page.mail_group.test_button.click()
    qtbot.waitUntil(lambda: page._worker is None, timeout=2000)


def test_mail_page_is_registered():
    assert PAGE_FACTORIES[od.PAGE_MAIL] is MailPage


def test_no_password_is_blocked_and_nothing_is_written(
    qtbot, engine, tmp_path, session, keyring_store, warnings
):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    page.mail_group.sender_email_edit.setText("ben@gmail.com")

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert "şifresini kaydedin" in warnings[0]
    session.expire_all()
    assert get_setting(session, KEY_SENDER_EMAIL) is None


def test_password_without_successful_test_is_blocked(
    qtbot, engine, tmp_path, keyring_store, warnings
):
    secrets.set_mail_password("abcd efgh ijkl mnop")
    wizard = _make_wizard(qtbot, engine, tmp_path)

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert "Bağlantıyı test et" in warnings[0]


def test_successful_test_allows_finish_and_saves_settings(
    qtbot, engine, tmp_path, session, keyring_store, test_job, infos
):
    secrets.set_mail_password("abcd efgh ijkl mnop")
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    page.mail_group.sender_email_edit.setText("ben@gmail.com")
    page.mail_group.sender_name_edit.setText("Ben")

    _run_test(qtbot, page)
    _finish(wizard)

    assert wizard.result() == QDialog.Accepted
    assert len(test_job.calls) == 1
    assert test_job.calls[0]["sender_email"] == "ben@gmail.com"  # EKRANDAKİ değer
    session.expire_all()
    assert get_setting(session, KEY_SENDER_EMAIL) == "ben@gmail.com"
    assert get_setting(session, KEY_MAIL_PROVIDER) == "gmail"
    assert "ben@gmail.com" in infos[0]


def test_imap_failure_does_not_count_as_tested(
    qtbot, engine, tmp_path, keyring_store, test_job, warnings
):
    secrets.set_mail_password("abcd efgh ijkl mnop")
    test_job.result = SmtpLoginTestJobResult(
        email="ben@gmail.com", ok=True, imap_ok=False, imap_error="IMAP kapalı"
    )
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)

    _run_test(qtbot, page)
    assert page.connection_tested_ok() is False
    warnings.clear()
    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert "Bağlantıyı test et" in warnings[0]


def test_changing_fields_after_a_successful_test_invalidates_it(
    qtbot, engine, tmp_path, keyring_store, test_job, infos
):
    secrets.set_mail_password("abcd efgh ijkl mnop")
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    page.mail_group.sender_email_edit.setText("ben@gmail.com")
    _run_test(qtbot, page)
    assert page.connection_tested_ok() is True

    page.mail_group.sender_email_edit.setText("baska@gmail.com")
    assert page.connection_tested_ok() is False

    page.mail_group.sender_email_edit.setText("ben@gmail.com")
    assert page.connection_tested_ok() is True
    secrets.delete_mail_password()
    assert page.connection_tested_ok() is False  # şifre silindi


def test_inbox_interval_change_keeps_the_test_valid(
    qtbot, engine, tmp_path, keyring_store, test_job, infos
):
    secrets.set_mail_password("abcd efgh ijkl mnop")
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    _run_test(qtbot, page)

    page.mail_group.inbox_check_minutes_spin.setValue(15)

    assert page.connection_tested_ok() is True


def test_invalid_sender_email_is_blocked(qtbot, engine, tmp_path, keyring_store, test_job, infos, warnings):
    secrets.set_mail_password("abcd efgh ijkl mnop")
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    page.mail_group.sender_email_edit.setText("gecersiz")
    _run_test(qtbot, page)

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert "geçerli görünmüyor" in warnings[0]


def test_custom_provider_requires_server_fields(
    qtbot, engine, tmp_path, keyring_store, test_job, infos, warnings
):
    secrets.set_mail_password("abcd efgh ijkl mnop")
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    group = page.mail_group
    group.mail_provider_combo.setCurrentIndex(group.mail_provider_combo.findData("custom"))
    _run_test(qtbot, page)

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert "Özel SMTP sunucusu boş olamaz" in warnings[0]


def test_custom_provider_values_are_saved(qtbot, engine, tmp_path, session, keyring_store, test_job, infos):
    secrets.set_mail_password("abcd efgh ijkl mnop")
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    group = page.mail_group
    group.mail_provider_combo.setCurrentIndex(group.mail_provider_combo.findData("custom"))
    group.custom_smtp_host_edit.setText("smtp.firma.com")
    group.custom_smtp_port_spin.setValue(587)
    group.custom_imap_host_edit.setText("imap.firma.com")
    group.custom_imap_port_spin.setValue(993)
    _run_test(qtbot, page)

    _finish(wizard)

    assert wizard.result() == QDialog.Accepted
    session.expire_all()
    assert get_setting(session, KEY_CUSTOM_SMTP_HOST) == "smtp.firma.com"


def test_guide_follows_the_provider(qtbot, engine, tmp_path, keyring_store):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    group = page.mail_group
    presets = load_mail_provider_presets()

    group.mail_provider_combo.setCurrentIndex(group.mail_provider_combo.findData("gmail"))
    assert page.guide_label.text() == guide_text(presets["gmail"])
    assert page.guide_label.text().startswith("1. ")
    assert "apppasswords" in page.guide_label.text()

    group.mail_provider_combo.setCurrentIndex(group.mail_provider_combo.findData("yandex"))
    assert page.guide_label.text() == guide_text(presets["yandex"])
    assert "Yandex" in page.guide_label.text()

    group.mail_provider_combo.setCurrentIndex(group.mail_provider_combo.findData("custom"))
    assert "SMTP/IMAP" in page.guide_label.text()


def test_guide_text_falls_back_to_hint_then_generic():
    presets = load_mail_provider_presets()
    outlook = presets["outlook"]
    assert outlook.password_guide_tr is None
    assert guide_text(outlook) == "Bu sağlayıcı için ayrıntılı rehber yok."
    gmail_without_guide = presets["gmail"].model_copy(update={"password_guide_tr": None})
    assert guide_text(gmail_without_guide) == presets["gmail"].password_hint_tr
    assert guide_text(None) == "Bu sağlayıcı için ayrıntılı rehber yok."


def test_sender_defaults_to_profile_email_when_empty(qtbot, engine, tmp_path, keyring_store):
    write_profile(tmp_path / "master_profile.json", build_sample_profile())

    wizard = _make_wizard(qtbot, engine, tmp_path)

    assert _page(wizard).mail_group.sender_email_edit.text() == "test@example.com"


def test_saved_sender_is_not_replaced_by_profile_email(qtbot, engine, tmp_path, session, keyring_store):
    write_profile(tmp_path / "master_profile.json", build_sample_profile())
    set_setting(session, KEY_SENDER_EMAIL, "kayitli@gmail.com")
    session.commit()

    wizard = _make_wizard(qtbot, engine, tmp_path)

    assert _page(wizard).mail_group.sender_email_edit.text() == "kayitli@gmail.com"


def test_broken_profile_does_not_block_the_page(qtbot, engine, tmp_path, keyring_store):
    (tmp_path / "master_profile.json").write_text("{bozuk", encoding="utf-8")

    wizard = _make_wizard(qtbot, engine, tmp_path)

    assert _page(wizard).mail_group.sender_email_edit.text() == ""
    assert (tmp_path / "master_profile.json").read_text(encoding="utf-8") == "{bozuk"
