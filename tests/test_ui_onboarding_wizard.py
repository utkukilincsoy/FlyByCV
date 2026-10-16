"""src/ui/onboarding_wizard.py iskelet testleri (PLAN.md Adım 10.2d; qtbot, SAHTE sayfalarla).

Gerçek sayfaların testleri kendi dosyalarında (ör. `test_ui_onboarding_welcome.py`).
"""
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox, QWizard

from src.core.settings import (
    KEY_ONBOARDING_COMPLETED,
    KEY_ONBOARDING_STEP,
    KEY_SENDER_NAME,
    get_setting,
    set_setting,
)
from src.ui import onboarding_data as od
from src.ui.onboarding_pages.base import OnboardingPage
from src.ui.onboarding_wizard import OnboardingWizard, run_onboarding_wizard


class FakePage(OnboardingPage):
    """Test sayfası: hata listesi dışarıdan verilir, kaydedildiğinde bir ayar yazar."""

    def __init__(self, page_id, engine, profile_path):
        super().__init__(engine, profile_path)
        self.page_id = page_id
        self.errors: list[str] = []
        self.saved = 0
        self.loaded = 0

    def load(self):
        self.loaded += 1

    def validation_errors(self):
        return list(self.errors)

    def save(self, session):
        self.saved += 1
        # Ayar anahtarları beyaz listeli; zararsız gerçek bir anahtara "son kaydeden sayfa" yazılır.
        set_setting(session, KEY_SENDER_NAME, self.page_id)


def _factories(*page_ids):
    return {
        page_id: (lambda engine, profile_path, _id=page_id: FakePage(_id, engine, profile_path))
        for page_id in page_ids
    }


def _make_wizard(qtbot, engine, tmp_path, *page_ids, reopen=False):
    ids = page_ids or (od.PAGE_WELCOME, od.PAGE_PERSONAL, od.PAGE_TARGET)
    wizard = OnboardingWizard(
        engine, tmp_path / "master_profile.json", reopen=reopen, page_factories=_factories(*ids)
    )
    qtbot.addWidget(wizard)
    wizard.show()
    return wizard


def _click(wizard, which):
    wizard.button(which).click()


@pytest.fixture(autouse=True)
def _no_real_dialogs(monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(text) or 0
    )
    return shown


@pytest.fixture
def warnings(_no_real_dialogs):
    return _no_real_dialogs


# --- sayfa dizilimi ---------------------------------------------------------------------------


def test_pages_follow_page_order_regardless_of_registration_order(qtbot, engine, tmp_path):
    wizard = _make_wizard(qtbot, engine, tmp_path, od.PAGE_TARGET, od.PAGE_WELCOME, od.PAGE_CV)
    assert wizard.page_ids == [od.PAGE_WELCOME, od.PAGE_TARGET, od.PAGE_CV]


def test_unregistered_pages_are_skipped_when_going_next(qtbot, engine, tmp_path):
    wizard = _make_wizard(qtbot, engine, tmp_path, od.PAGE_WELCOME, od.PAGE_CV)
    assert wizard.current_page_id() == od.PAGE_WELCOME
    _click(wizard, QWizard.NextButton)
    assert wizard.current_page_id() == od.PAGE_CV


def test_unknown_page_key_is_rejected(qtbot, engine, tmp_path):
    with pytest.raises(ValueError):
        OnboardingWizard(engine, tmp_path / "p.json", page_factories=_factories("yok"))


def test_wizard_without_pages_is_rejected(engine, tmp_path):
    with pytest.raises(ValueError):
        OnboardingWizard(engine, tmp_path / "p.json", page_factories={})


# --- geri / ileri / kayıt -------------------------------------------------------------------------


def test_next_saves_page_data_and_step(qtbot, engine, tmp_path, session):
    wizard = _make_wizard(qtbot, engine, tmp_path)

    _click(wizard, QWizard.NextButton)

    assert wizard.current_page_id() == od.PAGE_PERSONAL
    session.expire_all()
    assert get_setting(session, KEY_SENDER_NAME) == od.PAGE_WELCOME  # ilk sayfanın girdisi yazıldı
    assert get_setting(session, KEY_ONBOARDING_STEP) == od.PAGE_PERSONAL


def test_back_goes_to_previous_page_and_records_it(qtbot, engine, tmp_path, session):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    _click(wizard, QWizard.NextButton)
    _click(wizard, QWizard.NextButton)
    assert wizard.current_page_id() == od.PAGE_TARGET

    _click(wizard, QWizard.BackButton)

    assert wizard.current_page_id() == od.PAGE_PERSONAL
    session.expire_all()
    assert get_setting(session, KEY_ONBOARDING_STEP) == od.PAGE_PERSONAL


def test_no_back_button_on_first_page(qtbot, engine, tmp_path):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    assert wizard.button(QWizard.BackButton).isVisible() is False


def test_validation_error_blocks_next_shows_message_and_saves_nothing(
    qtbot, engine, tmp_path, session, warnings
):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    wizard.page(od.PAGE_ORDER.index(od.PAGE_WELCOME)).errors = ["Ad boş olamaz."]

    _click(wizard, QWizard.NextButton)

    assert wizard.current_page_id() == od.PAGE_WELCOME
    assert warnings == ["Ad boş olamaz."]
    session.expire_all()
    assert get_setting(session, KEY_SENDER_NAME) is None  # varsayılan: hiçbir şey yazılmadı
    assert get_setting(session, KEY_ONBOARDING_STEP) == od.PAGE_WELCOME  # yalnız başlangıç kaydı


# --- kaldığı yerden devam ---------------------------------------------------------------------


def test_fresh_wizard_starts_at_first_page(qtbot, engine, tmp_path):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    assert wizard.current_page_id() == od.PAGE_WELCOME


def test_wizard_resumes_at_saved_step_after_being_closed_midway(qtbot, engine, tmp_path):
    first = _make_wizard(qtbot, engine, tmp_path)
    _click(first, QWizard.NextButton)
    first.reject()  # kullanıcı yarıda kapattı

    second = _make_wizard(qtbot, engine, tmp_path)

    assert second.current_page_id() == od.PAGE_PERSONAL


def test_closing_midway_does_not_complete_onboarding(qtbot, engine, tmp_path, session):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    _click(wizard, QWizard.NextButton)

    wizard.reject()

    assert wizard.result() == QDialog.Rejected
    session.expire_all()
    assert od.is_onboarding_needed(session) is True


# --- bitiş ---------------------------------------------------------------------------------------


def test_finish_marks_onboarding_completed_and_clears_step(qtbot, engine, tmp_path, session):
    wizard = _make_wizard(qtbot, engine, tmp_path, od.PAGE_WELCOME, od.PAGE_PERSONAL)
    _click(wizard, QWizard.NextButton)
    assert wizard.button(QWizard.FinishButton).isVisible()

    _click(wizard, QWizard.FinishButton)

    assert wizard.result() == QDialog.Accepted
    session.expire_all()
    assert od.is_onboarding_needed(session) is False
    assert get_setting(session, KEY_ONBOARDING_STEP) is None
    assert get_setting(session, KEY_SENDER_NAME) == od.PAGE_PERSONAL  # son sayfa da kaydedildi


def test_finish_is_blocked_by_last_page_validation(qtbot, engine, tmp_path, session, warnings):
    wizard = _make_wizard(qtbot, engine, tmp_path, od.PAGE_WELCOME, od.PAGE_PERSONAL)
    _click(wizard, QWizard.NextButton)
    wizard.page(od.PAGE_ORDER.index(od.PAGE_PERSONAL)).errors = ["Eksik."]

    _click(wizard, QWizard.FinishButton)

    assert wizard.result() != QDialog.Accepted
    session.expire_all()
    assert od.is_onboarding_needed(session) is True


# --- startup bağlantısı ---------------------------------------------------------------------------


@pytest.mark.parametrize("dialog_result, expected", [(QDialog.Accepted, True), (QDialog.Rejected, False)])
def test_run_onboarding_wizard_returns_whether_it_was_completed(
    engine, tmp_path, monkeypatch, dialog_result, expected
):
    """`startup.WizardRunner` sözleşmesi: tamamlandıysa True, yarıda kapatıldıysa False."""
    monkeypatch.setattr(
        "src.ui.onboarding_wizard.PAGE_FACTORIES", _factories(od.PAGE_WELCOME)
    )
    monkeypatch.setattr(QWizard, "exec", lambda self: dialog_result)

    assert run_onboarding_wizard(engine, tmp_path / "master_profile.json") is expected


# --- yeniden açma (Ayarlar'dan) ------------------------------------------------------------------


@pytest.fixture
def onboarded(session):
    set_setting(session, KEY_ONBOARDING_COMPLETED, True)
    session.commit()


def test_reopen_starts_at_first_page_even_if_a_step_was_saved(qtbot, engine, tmp_path, session, onboarded):
    set_setting(session, KEY_ONBOARDING_STEP, od.PAGE_TARGET)
    session.commit()

    wizard = _make_wizard(qtbot, engine, tmp_path, reopen=True)

    assert wizard.current_page_id() == od.PAGE_WELCOME


def test_reopen_does_not_record_steps_and_never_unmarks_completion(
    qtbot, engine, tmp_path, session, onboarded
):
    wizard = _make_wizard(qtbot, engine, tmp_path, reopen=True)
    _click(wizard, QWizard.NextButton)

    wizard.reject()  # yarıda kapatıldı

    session.expire_all()
    assert get_setting(session, KEY_ONBOARDING_STEP) is None
    assert od.is_onboarding_needed(session) is False  # bir sonraki açılışta kendiliğinden açılmaz


def test_reopen_finish_keeps_onboarding_completed(qtbot, engine, tmp_path, session, onboarded):
    wizard = _make_wizard(qtbot, engine, tmp_path, od.PAGE_WELCOME, reopen=True)

    _click(wizard, QWizard.FinishButton)

    session.expire_all()
    assert wizard.result() == QDialog.Accepted
    assert od.is_onboarding_needed(session) is False
