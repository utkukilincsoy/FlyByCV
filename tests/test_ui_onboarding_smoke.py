"""Kurulum sihirbazının baştan sona duman testi: KAYITLI TÜM SAYFALAR, taze kurulumda, "İleri"/"Bitir" düğmeleriyle
(PLAN.md Adım 10.2; qtbot).

Tek tek sayfa testleri kendi sayfalarını sınar; burası dokuz sayfanın BİRLİKTE, gerçek sırayla ve gerçek veri
katmanıyla (kendi PDF'i, gerçek mail şablonu, gerçek `send_self_test`) kullanıcı gibi geçilebildiğini sınar. Dışarıya
çıkan tek şeyler sahte: şifre kasası, mail bağlantı testi ve SMTP gönderimi (`FakeTransport`); ağa hiç çıkılmaz.
"""
from functools import partial

import pytest
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox, QWizard
from sqlmodel import func, select

from src.core import paths, secrets
from src.core.db import get_session
from src.core.legal import has_consent
from src.core.models import Application, Company, Setting
from src.core.settings import (
    CV_MODE_OWN_PDF,
    KEY_APPLICATION_TARGET,
    KEY_AUTOPILOT_CITY,
    KEY_CV_MODE,
    KEY_ONBOARDING_COMPLETED,
    KEY_ONBOARDING_STEP,
    KEY_PROFESSION,
    KEY_SENDER_EMAIL,
    KEY_TEST_MODE,
    get_setting,
)
from src.cv_builder.profile_loader import load_profile
from src.sender.smtp_sender import HEADER_INTENDED_TO
from src.ui import onboarding_data as od
from src.ui.jobs import SmtpLoginTestJobResult, self_test_job
from src.ui.onboarding_pages import PAGE_FACTORIES
from src.ui.onboarding_wizard import OnboardingWizard
from tests.conftest import FakeTransport, build_mail_account

PDF_BYTES = b"%PDF-1.4 benim cv"
EMAIL = "ben@gmail.com"


class _FakeKeyring:
    def __init__(self) -> None:
        self.store: dict = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        del self.store[(service, username)]


@pytest.fixture
def transport():
    return FakeTransport()


@pytest.fixture
def dialogs(monkeypatch, tmp_path, transport):
    """Kullanıcıyı ve dış dünyayı sahtelenmiş hâlde kurar; açılan `QMessageBox` metinleri `shown`a toplanır."""
    monkeypatch.setattr(secrets, "keyring", _FakeKeyring())
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "home"))

    source = tmp_path / "benim_cv.pdf"
    source.write_bytes(PDF_BYTES)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), "PDF"))

    shown: list[str] = []
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(
            QMessageBox, name, lambda parent, title, text, *a, **k: shown.append(text) or 0
        )
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)  # şablon önerisi: Hayır

    monkeypatch.setattr(
        "src.ui.onboarding_pages.mail.smtp_login_test_job",
        lambda profile_path, *, on_progress, token, **kwargs: SmtpLoginTestJobResult(
            email=EMAIL, ok=True, imap_ok=True
        ),
    )
    # Test maili: GERÇEK iş ve gerçek `send_self_test`; yalnızca SMTP ve hesap sahte.
    monkeypatch.setattr(
        "src.ui.onboarding_pages.self_test.self_test_job",
        partial(self_test_job, transport=transport, account=build_mail_account(email=EMAIL)),
    )
    return shown


def _wait_idle(qtbot, page) -> None:
    qtbot.waitUntil(lambda: page._worker is None, timeout=5000)


def _page(wizard, page_id):
    return wizard.page(od.PAGE_ORDER.index(page_id))


def _next(wizard) -> None:
    wizard.button(QWizard.NextButton).click()


def _do_welcome(qtbot, wizard) -> None:
    _page(wizard, od.PAGE_WELCOME)._panel.checkbox.setChecked(True)


def _do_personal(qtbot, wizard) -> None:
    page = _page(wizard, od.PAGE_PERSONAL)
    page.personal_group.name_edit.setText("Ayşe Kaya")
    page.personal_group.email_edit.setText(EMAIL)
    page.city_combo.setCurrentIndex(page.city_combo.findData("06"))


def _do_target(qtbot, wizard) -> None:
    page = _page(wizard, od.PAGE_TARGET)
    page.target_combo.setCurrentIndex(page.target_combo.findData("internship"))
    page.profession_combo.setCurrentIndex(page.profession_combo.findData("yazilim"))


def _do_cv(qtbot, wizard) -> None:
    _page(wizard, od.PAGE_CV).cv_group.on_select_own_cv()  # taze kurulumda mod başta "kendi PDF'i"


def _do_mail(qtbot, wizard) -> None:
    secrets.set_mail_password("abcd efgh ijkl mnop")
    page = _page(wizard, od.PAGE_MAIL)
    page.mail_group.refresh_password_status()
    page.mail_group.test_button.click()
    _wait_idle(qtbot, page)


def _do_sources(qtbot, wizard) -> None:
    """Şehir kişisel bilgi sayfasından, OpenStreetMap taze kurulumda varsayılan açık geldi: dokunulmaz."""


def _do_template(qtbot, wizard) -> None:
    """Varsayılan şablon kabul edilir (öneri sorusuna Hayır denmişti)."""


def _do_self_test(qtbot, wizard) -> None:
    page = _page(wizard, od.PAGE_SELF_TEST)
    page.send_button.click()
    _wait_idle(qtbot, page)
    page.confirm_check.setChecked(True)


#: Son sayfa (bitiş) hariç, sırayla her sayfada yapılacak kullanıcı eylemi.
ACTIONS = {
    od.PAGE_WELCOME: _do_welcome,
    od.PAGE_PERSONAL: _do_personal,
    od.PAGE_TARGET: _do_target,
    od.PAGE_CV: _do_cv,
    od.PAGE_MAIL: _do_mail,
    od.PAGE_SOURCES: _do_sources,
    od.PAGE_TEMPLATE: _do_template,
    od.PAGE_SELF_TEST: _do_self_test,
}


def _make_wizard(qtbot, file_engine, tmp_path) -> OnboardingWizard:
    wizard = OnboardingWizard(file_engine, tmp_path / "master_profile.json")
    qtbot.addWidget(wizard)
    wizard.show()
    return wizard


def _walk(qtbot, wizard, *, until: str) -> None:
    """Kayıtlı sayfaları sırayla geçer; `until` sayfasına GELİNCE (onun eylemi yapılmadan) durur."""
    for page_id in wizard.page_ids:
        assert wizard.current_page_id() == page_id
        if page_id == until:
            return
        ACTIONS[page_id](qtbot, wizard)
        _next(wizard)
        assert wizard.current_page_id() != page_id, f"{page_id} sayfası geçilemedi"
    raise AssertionError(f"{until} sayfasına gelinemedi")


def test_all_nine_pages_are_registered_in_order():
    assert len(od.PAGE_ORDER) == 9
    assert set(PAGE_FACTORIES) == set(od.PAGE_ORDER)


def test_fresh_install_walks_through_every_page_to_finish(
    qtbot, file_engine, tmp_path, dialogs, transport
):
    wizard = _make_wizard(qtbot, file_engine, tmp_path)
    assert wizard.page_ids == list(od.PAGE_ORDER)  # dokuz sayfanın hepsi, PAGE_ORDER sırasıyla

    _walk(qtbot, wizard, until=od.PAGE_FINISH)

    finish = _page(wizard, od.PAGE_FINISH)
    assert wizard.button(QWizard.FinishButton).text() == "Otopilot'a git"
    assert "Ayşe Kaya" in [label.text() for label in finish.findChildren(type(finish.test_mode_label))]
    assert "AÇIK" in finish.test_mode_label.text()

    wizard.button(QWizard.FinishButton).click()

    assert wizard.result() == QDialog.Accepted
    with get_session(file_engine) as session:
        # Kurulum durumu
        assert get_setting(session, KEY_ONBOARDING_COMPLETED) is True
        assert get_setting(session, KEY_ONBOARDING_STEP) is None
        assert has_consent(session) is True
        assert get_setting(session, KEY_APPLICATION_TARGET) == "internship"
        assert get_setting(session, KEY_PROFESSION) == "yazilim"
        assert get_setting(session, KEY_AUTOPILOT_CITY) == "06"
        assert get_setting(session, KEY_CV_MODE) == CV_MODE_OWN_PDF
        assert get_setting(session, KEY_SENDER_EMAIL) == EMAIL
        # Test modu AÇIK kaldı ve sihirbaz o ayara hiç dokunmadı
        assert get_setting(session, KEY_TEST_MODE) is True
        assert session.get(Setting, KEY_TEST_MODE) is None
        # Test maili veritabanına HİÇBİR kayıt açmadı
        assert session.exec(select(func.count()).select_from(Company)).one() == 0
        assert session.exec(select(func.count()).select_from(Application)).one() == 0

    assert load_profile(tmp_path / "master_profile.json").personal_info.name == "Ayşe Kaya"
    assert paths.own_cv_path().read_bytes() == PDF_BYTES

    # Test maili: tam bir kez, yalnızca kendi adresine, kendi PDF'iyle, [KURULUM TESTİ] önekiyle
    (account, message, recipients), = transport.calls
    assert recipients == [EMAIL] and message["To"] == EMAIL
    assert message["Subject"].startswith("[KURULUM TESTİ] ")
    assert message[HEADER_INTENDED_TO] == EMAIL
    assert next(message.iter_attachments()).get_content() == PDF_BYTES


def test_each_page_blocks_until_its_requirement_is_met(qtbot, file_engine, tmp_path, dialogs):
    """Hiçbir sayfa boş/eksik girdiyle geçilemez (hoş geldin, kişisel, hedef, CV, mail, test maili)."""
    wizard = _make_wizard(qtbot, file_engine, tmp_path)

    for page_id in wizard.page_ids:
        if page_id in (od.PAGE_SOURCES, od.PAGE_TEMPLATE, od.PAGE_FINISH):
            ACTIONS.get(page_id, lambda *a: None)(qtbot, wizard)
            if page_id != od.PAGE_FINISH:
                _next(wizard)
            continue
        before = len(dialogs)
        _next(wizard)  # eylem YAPILMADAN
        assert wizard.current_page_id() == page_id, f"{page_id} boş girdiyle geçildi"
        assert len(dialogs) == before + 1  # kullanıcıya neden geçilemediği söylendi
        ACTIONS[page_id](qtbot, wizard)
        _next(wizard)
        assert wizard.current_page_id() != page_id


def test_closing_midway_resumes_at_the_saved_page_and_keeps_entered_data(
    qtbot, file_engine, tmp_path, dialogs
):
    wizard = _make_wizard(qtbot, file_engine, tmp_path)
    _walk(qtbot, wizard, until=od.PAGE_SOURCES)
    wizard.close()

    resumed = _make_wizard(qtbot, file_engine, tmp_path)

    assert resumed.current_page_id() == od.PAGE_SOURCES
    with get_session(file_engine) as session:
        assert get_setting(session, KEY_ONBOARDING_COMPLETED) in (None, False)  # henüz bitmedi
        assert get_setting(session, KEY_SENDER_EMAIL) == EMAIL  # önceki sayfaların girdisi kayıtlı
    assert load_profile(tmp_path / "master_profile.json").personal_info.name == "Ayşe Kaya"


def test_self_test_page_cannot_be_passed_without_sending_and_confirming(
    qtbot, file_engine, tmp_path, dialogs, transport
):
    wizard = _make_wizard(qtbot, file_engine, tmp_path)
    _walk(qtbot, wizard, until=od.PAGE_SELF_TEST)
    page = _page(wizard, od.PAGE_SELF_TEST)
    assert page.confirm_check.isEnabled() is False  # mail gönderilmeden kutu işaretlenemez

    _next(wizard)
    assert wizard.current_page_id() == od.PAGE_SELF_TEST and transport.calls == []

    page.send_button.click()
    _wait_idle(qtbot, page)
    assert len(transport.calls) == 1 and page.confirm_check.isEnabled() is True
    _next(wizard)
    assert wizard.current_page_id() == od.PAGE_SELF_TEST  # gönderildi ama "Mail geldi" işaretlenmedi

    page.confirm_check.setChecked(True)
    _next(wizard)
    assert wizard.current_page_id() == od.PAGE_FINISH
