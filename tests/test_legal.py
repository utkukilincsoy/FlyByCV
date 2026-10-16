"""KVKK / 6563 bilgilendirmesi ve onayı (PLAN.md Adım 10.8b): onaysız GERÇEK gönderim yok.

`conftest.legal_consent_given` (autouse) gönderim geçidindeki `has_consent`'i sahteler; buradaki
testler gerçek fonksiyonu geri yükler.
"""
import pytest
from PySide6.QtWidgets import QDialog

from src.core import legal
from src.core.db import get_session
from src.core.models import STATUS_APPROVED
from src.core.settings import (
    KEY_LEGAL_CONSENT_AT,
    KEY_LEGAL_CONSENT_VERSION,
    KEY_TEST_MODE,
    get_setting,
    set_setting,
)
from src.sender.smtp_sender import MailAccount, MailIdentity, send_application
from src.ui import legal_dialog
from src.ui.legal_dialog import LegalConsentDialog, ensure_legal_consent
from tests.conftest import FakeTransport, build_application_row, build_sample_profile


@pytest.fixture
def real_consent_check(monkeypatch):
    monkeypatch.setattr("src.sender.smtp_sender.has_consent", legal.has_consent)


@pytest.fixture
def account() -> MailAccount:
    return MailAccount(identity=MailIdentity(email="ben@gmail.com", name="Ben"), password="x")


# --- çekirdek ---------------------------------------------------------------------------------


def test_no_consent_by_default(session):
    assert legal.has_consent(session) is False
    assert get_setting(session, KEY_LEGAL_CONSENT_VERSION) is None


def test_record_consent_stores_version_and_time(session):
    from datetime import datetime

    legal.record_consent(session, now=datetime(2026, 10, 1, 9, 30))

    assert legal.has_consent(session) is True
    assert get_setting(session, KEY_LEGAL_CONSENT_VERSION) == legal.LEGAL_TEXT_VERSION
    assert get_setting(session, KEY_LEGAL_CONSENT_AT) == "2026-10-01T09:30:00"


def test_consent_for_an_older_text_version_is_not_valid(session):
    set_setting(session, KEY_LEGAL_CONSENT_VERSION, "2000-01-01")
    assert legal.has_consent(session) is False


def test_legal_text_is_shipped_and_marked_as_not_legal_advice():
    text = legal.load_legal_text()
    assert "hukuki tavsiye değildir" in text
    assert "6698" in text and "6563" in text
    assert "yalnızca bu bilgisayarda" in text  # yerel veri açıklaması


def test_legal_text_discloses_the_update_check_and_the_version_was_bumped():
    """10.9d: yeni dış bağlantı (GitHub) metinde açıkça yazılı; sürüm artmadıysa mevcut kurulumlar
    değişen metni görmeden eski onayla devam ederdi."""
    text = legal.load_legal_text()
    assert "GitHub" in text and "yeni sürüm" in text.lower()
    assert "IP adresiniz" in text
    assert legal.LEGAL_TEXT_VERSION > "2026-09-30"


# --- gönderim geçidi --------------------------------------------------------------------------


def test_real_send_refused_without_consent(session, pdf_path, account, real_consent_check):
    set_setting(session, KEY_TEST_MODE, False)
    application, _ = build_application_row(
        session, domain="firma.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED
    )
    transport = FakeTransport()

    result = send_application(
        session, application, build_sample_profile(), auto_mode=False, transport=transport, account=account
    )

    assert result.refused_reason == legal.CONSENT_REQUIRED_REASON
    assert not result.sent
    assert transport.calls == []
    assert application.status == STATUS_APPROVED


def test_real_send_allowed_after_consent(session, pdf_path, account, real_consent_check):
    set_setting(session, KEY_TEST_MODE, False)
    legal.record_consent(session)
    application, _ = build_application_row(
        session, domain="firma.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED
    )
    transport = FakeTransport()

    result = send_application(
        session, application, build_sample_profile(), auto_mode=False, transport=transport, account=account
    )

    assert result.sent
    assert len(transport.calls) == 1


def test_test_mode_send_does_not_need_consent(session, pdf_path, account, real_consent_check):
    """Test modu kullanıcının KENDİ adresine gider, onay istemez."""
    set_setting(session, KEY_TEST_MODE, True)
    application, company = build_application_row(
        session, domain="firma.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED
    )
    transport = FakeTransport()

    result = send_application(
        session, application, build_sample_profile(), auto_mode=False, transport=transport, account=account
    )

    assert result.sent
    assert transport.calls[0][2] == [account.identity.email]


# --- onay penceresi ---------------------------------------------------------------------------


def test_dialog_accept_button_needs_checkbox(qtbot):
    dialog = LegalConsentDialog("# Başlık\n\nmetin")
    qtbot.addWidget(dialog)

    assert dialog._accept_button.isEnabled() is False
    dialog._checkbox.setChecked(True)
    assert dialog._accept_button.isEnabled() is True
    dialog._checkbox.setChecked(False)
    assert dialog._accept_button.isEnabled() is False


def test_dialog_accept_and_decline_results(qtbot):
    dialog = LegalConsentDialog("metin")
    qtbot.addWidget(dialog)
    dialog._checkbox.setChecked(True)
    dialog._accept_button.click()
    assert dialog.result() == QDialog.Accepted

    declined = LegalConsentDialog("metin")
    qtbot.addWidget(declined)
    declined._decline_button.click()
    assert declined.result() == QDialog.Rejected


def test_ensure_consent_asks_once_then_never_again(engine):
    shown: list[str] = []

    def accept(text: str) -> bool:
        shown.append(text)
        return True

    assert ensure_legal_consent(engine, ask=accept) is True
    assert len(shown) == 1
    assert "hukuki tavsiye değildir" in shown[0]
    with get_session(engine) as session:
        assert legal.has_consent(session) is True

    def must_not_ask(text: str) -> bool:
        raise AssertionError("onay varken tekrar sorulmamalı")

    assert ensure_legal_consent(engine, ask=must_not_ask) is True


def test_ensure_consent_declined_records_nothing_and_asks_again_next_time(engine):
    assert ensure_legal_consent(engine, ask=lambda text: False) is False
    with get_session(engine) as session:
        assert legal.has_consent(session) is False

    asked: list[str] = []
    ensure_legal_consent(engine, ask=lambda text: asked.append(text) or False)
    assert len(asked) == 1


def test_ensure_consent_with_missing_text_file_records_nothing(engine, monkeypatch):
    def broken() -> str:
        raise FileNotFoundError("kvkk_tr.md")

    monkeypatch.setattr(legal_dialog, "load_legal_text", broken)

    def must_not_ask(text: str) -> bool:
        raise AssertionError("metin yokken onay sorulamaz")

    assert ensure_legal_consent(engine, ask=must_not_ask) is False
    with get_session(engine) as session:
        assert legal.has_consent(session) is False
