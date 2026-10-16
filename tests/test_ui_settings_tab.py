"""src/ui/settings_tab.py testleri (qtbot; gerçek keyring/SMTP/LLM yok).

Mantığın ağırlığı `test_ui_settings_data.py`'de (Qt YOK); burada yalnızca widget'ın form'u
doğru okuyup yazdığı, onay/uyarı diyaloglarının doğru sırayla çağrıldığı ve keyring
fonksiyonlarının doğru argümanlarla tetiklendiği kontrol ediliyor.
"""
import pytest
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QLineEdit, QMessageBox, QPlainTextEdit

from src.core import secrets
from src.core.db import get_session
from src.core.legal import record_consent
from src.core.update_check import UpdateInfo
from src.core.version import __version__
from src.core.professions import load_profession_presets
from src.core.settings import (
    CV_MODE_ATS,
    CV_MODE_OWN_PDF,
    KEY_APPLICATION_TARGET,
    KEY_CV_MODE,
    KEY_DAILY_LIMIT,
    KEY_INBOX_CHECK_MINUTES,
    KEY_MIN_DELAY_SECONDS,
    KEY_PLACES_MONTHLY_LIMIT,
    KEY_PLACES_REQUESTS_MONTH_KEY,
    KEY_PLACES_REQUESTS_THIS_MONTH,
    KEY_PROFESSION,
    KEY_SEND_MODE,
    KEY_TEST_MODE,
    KEY_UPDATE_CHECK_ENABLED,
    KEY_USE_GOOGLE_PLACES,
    MODE_AUTO,
    MODE_MANUAL,
    get_setting,
    set_setting,
)
from src.cv_builder.mail_template import TARGET_JOB, TARGET_VOLUNTEER_INTERNSHIP
from src.cv_builder.own_cv import has_own_cv
from src.llm.providers import PROVIDER_NONE
from src.core.models import utcnow
from src.core.timeutil import pacific_month_key
from src.ui.jobs import (
    LlmTestJobResult,
    PlacesKeyTestJobResult,
    SmtpLoginTestJobResult,
    UpdateCheckJobResult,
)
from src.ui.settings_groups import updates_group
from src.ui.settings_groups.updates_group import OPEN_PAGE_TEXT
from src.ui.settings_data import TEST_MODE_OFF_VIA_SAVE_ERROR
from src.ui.settings_tab import (
    UNSAVED_CHANGES_DISCARD,
    UNSAVED_CHANGES_SAVE,
    UNSAVED_CHANGES_STAY,
    SettingsTab,
)
from src.ui.test_mode_data import (
    BACK_TO_TEST_BUTTON_TEXT,
    GO_LIVE_BUTTON_TEXT,
    GO_LIVE_CONFIRM_BUTTON_TEXT,
    NO_CONSENT_TITLE,
    TEST_MODE_OFF_LABEL,
    TEST_MODE_ON_LABEL,
)
from src.ui.test_mode_dialog import GoLiveDialog


class _FakeKeyring:
    """`tests/test_secrets.py::_FakeKeyring` ile aynı; gerçek kasaya hiç dokunmaz."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str):
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        from keyring.errors import PasswordDeleteError

        if (service, username) not in self.store:
            raise PasswordDeleteError("kayıt yok")
        del self.store[(service, username)]


def _make_tab(qtbot, engine, tmp_path, monkeypatch) -> SettingsTab:
    monkeypatch.setattr(secrets, "keyring", _FakeKeyring())
    profile_path = tmp_path / "master_profile.json"  # bu testlerde okunmuyor (profil yok)
    tab = SettingsTab(engine, profile_path)
    qtbot.addWidget(tab)
    return tab


def _stub_clicked_button(monkeypatch, text: str) -> None:
    """`box.exec()`'i atlar, `box.clickedButton()`'ı METNİYLE eşleşen butona sabitler.

    `QMessageBox.buttons()`'ın sırası eklenme sırasıyla AYNI DEĞİL (Qt, rol'e göre platforma
    özgü bir sırayla düzenliyor) -- bu yüzden indeksle değil metinle seçiyoruz.
    """

    def _clicked(self):
        for button in self.buttons():
            if button.text() == text:
                return button
        raise AssertionError(f"'{text}' metinli buton bulunamadı: {[b.text() for b in self.buttons()]}")

    monkeypatch.setattr(QMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(QMessageBox, "clickedButton", _clicked)


def _deny_all_dialogs(monkeypatch) -> None:
    """Beklenmeyen bir onay diyaloğu açılırsa test hemen patlasın diye 'Hayır/Vazgeç' döner."""
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)


# ---------------------------------------------------------------------------
# Yükleme / form
# ---------------------------------------------------------------------------


def test_settings_tab_loads_defaults(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    assert tab._daily_limit_spin.value() == 20
    assert tab.form().test_mode is True
    assert tab._send_mode_combo.currentData() == MODE_MANUAL
    assert tab._inbox_check_minutes_spin.value() == 30
    assert tab.is_dirty() is False


def test_settings_tab_reload_picks_up_external_change(qtbot, engine, tmp_path, monkeypatch, session):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    set_setting(session, KEY_DAILY_LIMIT, 9)
    session.commit()
    tab.reload()

    assert tab._daily_limit_spin.value() == 9
    assert tab.is_dirty() is False


def test_editing_a_field_marks_dirty(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    tab._daily_limit_spin.setValue(tab._daily_limit_spin.value() + 1)

    assert tab.is_dirty() is True


# ---------------------------------------------------------------------------
# Hedef / Meslek / Mail dili — şablon önerisi (PLAN.md Adım 10.4)
# ---------------------------------------------------------------------------


def test_reload_never_prompts_to_overwrite_template_even_when_target_and_profession_set(
    qtbot, engine, tmp_path, monkeypatch, session
):
    """Kaydedilmiş hedef/meslek, o anki mail şablonundan FARKLI bir öneriye işaret etse bile
    yalnızca sekmeyi açmak/`reload()` hiçbir onay penceresi TETİKLEMEMELİ (kullanıcının kendi
    şablonu sormadan ezilmez, bkz. `_set_form`'daki `blockSignals`)."""
    set_setting(session, KEY_APPLICATION_TARGET, TARGET_VOLUNTEER_INTERNSHIP)
    set_setting(session, KEY_PROFESSION, "yazilim")
    session.commit()

    def _fail_if_asked(*_args, **_kwargs):
        raise AssertionError("reload() sırasında beklenmeyen bir onay penceresi açıldı")

    monkeypatch.setattr(QMessageBox, "question", _fail_if_asked)

    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab.reload()

    assert tab._target_combo.currentData() == TARGET_VOLUNTEER_INTERNSHIP
    assert tab._profession_combo.currentData() == "yazilim"
    assert tab.is_dirty() is False


def test_changing_target_prompts_and_applies_suggestion_on_yes(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab._profession_combo.setCurrentIndex(tab._profession_combo.findData("yazilim"))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)

    tab._target_combo.setCurrentIndex(tab._target_combo.findData(TARGET_VOLUNTEER_INTERNSHIP))

    expected = load_profession_presets()["yazilim"].default_mail_templates[TARGET_VOLUNTEER_INTERNSHIP]
    assert tab._mail_subject_edit.text() == expected.subject
    assert tab._mail_body_edit.toPlainText() == expected.body


def test_changing_target_prompts_and_keeps_current_template_on_no(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    original_subject = tab._mail_subject_edit.text()
    original_body = tab._mail_body_edit.toPlainText()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)

    tab._target_combo.setCurrentIndex(tab._target_combo.findData(TARGET_JOB))

    assert tab._mail_subject_edit.text() == original_subject
    assert tab._mail_body_edit.toPlainText() == original_body


def test_changing_target_does_not_prompt_when_suggestion_already_matches_current_text(
    qtbot, engine, tmp_path, monkeypatch
):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab._profession_combo.setCurrentIndex(tab._profession_combo.findData("yazilim"))
    expected = load_profession_presets()["yazilim"].default_mail_templates[TARGET_JOB]
    tab._mail_subject_edit.setText(expected.subject)
    tab._mail_body_edit.setPlainText(expected.body)

    def _fail_if_asked(*_args, **_kwargs):
        raise AssertionError("metin zaten öneriyle aynıyken onay istenmemeli")

    monkeypatch.setattr(QMessageBox, "question", _fail_if_asked)

    tab._target_combo.setCurrentIndex(tab._target_combo.findData(TARGET_JOB))  # tetikler ama farksız

    assert tab._mail_subject_edit.text() == expected.subject


# ---------------------------------------------------------------------------
# Kaydet / Vazgeç / Varsayılanlara dön
# ---------------------------------------------------------------------------


def test_save_writes_changed_setting_and_emits_signal(qtbot, engine, tmp_path, monkeypatch, session):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    _deny_all_dialogs(monkeypatch)  # bu senaryoda hiçbir onay TETİKLENMEMELİ

    tab._daily_limit_spin.setValue(7)

    with qtbot.waitSignal(tab.settings_saved, timeout=1000):
        tab._on_save()

    assert get_setting(session, KEY_DAILY_LIMIT) == 7
    assert tab.is_dirty() is False


def test_cancel_discards_unsaved_edit(qtbot, engine, tmp_path, monkeypatch, session):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    tab._daily_limit_spin.setValue(7)
    tab._on_cancel()

    assert tab._daily_limit_spin.value() == 20
    assert tab.is_dirty() is False
    assert get_setting(session, KEY_DAILY_LIMIT) == 20


def test_save_with_validation_error_does_not_write_and_shows_critical(
    qtbot, engine, tmp_path, monkeypatch, session
):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    calls = []
    monkeypatch.setattr(
        QMessageBox, "critical", lambda *a, **k: calls.append(a) or QMessageBox.Ok
    )
    tab._min_delay_spin.setValue(15)
    tab._max_delay_spin.setValue(10)

    tab._on_save()

    assert len(calls) == 1
    assert get_setting(session, KEY_MIN_DELAY_SECONDS) == 60  # varsayılan, hiç yazılmadı
    assert tab.is_dirty() is True


def test_restore_defaults_only_touches_widgets_not_database(qtbot, engine, tmp_path, monkeypatch, session):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab._daily_limit_spin.setValue(7)
    # saatlik limit varsayılanı (20) günlük limitten (7) büyük -- `_on_save()` bir uyarı
    # gösterir (bkz. `form_warnings`); testin konusu bu uyarı değil, gerçek bir dialog
    # açılıp asılı kalmasın diye kapatılıyor.
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    tab._on_save()

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    tab._on_restore_defaults()

    assert tab._daily_limit_spin.value() == 20  # widget varsayılana döndü
    assert get_setting(session, KEY_DAILY_LIMIT) == 7  # DB'ye DOKUNULMADI
    assert tab.is_dirty() is True  # Kaydet'e basılmadı


def test_restore_defaults_declined_leaves_widgets_untouched(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab._daily_limit_spin.setValue(7)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    tab._on_restore_defaults()

    assert tab._daily_limit_spin.value() == 7


# ---------------------------------------------------------------------------
# Test modu: "Gerçek gönderime geç" / "Test moduna dön" düğmeleri (yazılı onay YOK)
# ---------------------------------------------------------------------------


def _grant_consent(session) -> None:
    record_consent(session)
    session.commit()


def _stub_go_live_dialog(monkeypatch, result) -> list[int]:
    """`GoLiveDialog.exec()`'i atlar, `result` döner; kaç kez açıldığını sayar."""
    opened: list[int] = []

    def _exec(self):
        opened.append(1)
        return result

    monkeypatch.setattr(GoLiveDialog, "exec", _exec)
    return opened


def test_test_mode_widgets_show_on_state_and_go_live_button(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    assert tab._test_mode_label.text() == TEST_MODE_ON_LABEL
    assert tab._test_mode_button.text() == GO_LIVE_BUTTON_TEXT
    assert tab.form().test_mode is True


def test_go_live_without_consent_is_blocked_and_asks_nothing(
    qtbot, engine, tmp_path, monkeypatch, session
):
    """KVKK onayı yoksa geçilemez: bilgi kutusu çıkar, onay penceresi HİÇ açılmaz, ayar değişmez."""
    monkeypatch.setattr("src.ui.test_mode_data.has_consent", lambda session: False)
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    opened = _stub_go_live_dialog(monkeypatch, QDialog.Accepted)
    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda parent, title, text: shown.append(title))
    )

    tab._test_mode_button.click()

    assert shown == [NO_CONSENT_TITLE]
    assert opened == []
    session.expire_all()
    assert get_setting(session, KEY_TEST_MODE) is True
    assert tab._test_mode_label.text() == TEST_MODE_ON_LABEL


def test_go_live_cancelled_in_dialog_keeps_test_mode(qtbot, engine, tmp_path, monkeypatch, session):
    _grant_consent(session)
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    opened = _stub_go_live_dialog(monkeypatch, QDialog.Rejected)

    tab._test_mode_button.click()

    assert opened == [1]
    session.expire_all()
    assert get_setting(session, KEY_TEST_MODE) is True
    assert tab.form().test_mode is True


def test_go_live_confirmed_writes_setting_and_updates_screen(
    qtbot, engine, tmp_path, monkeypatch, session
):
    _grant_consent(session)
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    _stub_go_live_dialog(monkeypatch, QDialog.Accepted)

    with qtbot.waitSignal(tab.settings_saved, timeout=1000):
        tab._test_mode_button.click()

    session.expire_all()
    assert get_setting(session, KEY_TEST_MODE) is False
    assert tab._test_mode_label.text() == TEST_MODE_OFF_LABEL
    assert tab._test_mode_button.text() == BACK_TO_TEST_BUTTON_TEXT
    assert tab.is_dirty() is False  # anında yazıldı, "kaydedilmemiş değişiklik" olarak kalmadı


def test_back_to_test_mode_needs_no_confirmation(qtbot, engine, tmp_path, monkeypatch, session):
    set_setting(session, KEY_TEST_MODE, False)
    session.commit()
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    opened = _stub_go_live_dialog(monkeypatch, QDialog.Rejected)
    _deny_all_dialogs(monkeypatch)  # hiçbir onay TETİKLENMEMELİ

    assert tab._test_mode_button.text() == BACK_TO_TEST_BUTTON_TEXT
    with qtbot.waitSignal(tab.settings_saved, timeout=1000):
        tab._test_mode_button.click()

    assert opened == []
    session.expire_all()
    assert get_setting(session, KEY_TEST_MODE) is True
    assert tab._test_mode_button.text() == GO_LIVE_BUTTON_TEXT


def test_go_live_keeps_other_unsaved_edits(qtbot, engine, tmp_path, monkeypatch, session):
    _grant_consent(session)
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    _stub_go_live_dialog(monkeypatch, QDialog.Accepted)
    tab._daily_limit_spin.setValue(7)

    tab._test_mode_button.click()

    assert tab._daily_limit_spin.value() == 7
    assert tab.is_dirty() is True  # yalnızca günlük limit kirli; test modu değil
    session.expire_all()
    assert get_setting(session, KEY_DAILY_LIMIT) != 7


def test_save_cannot_turn_test_mode_off(qtbot, engine, tmp_path, monkeypatch, session):
    """Kaydet yolu, düğmenin KVKK/onay kapısını atlayıp test modunu kapatamaz."""
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab._sending_group.set_test_mode(False)  # ekranı zorla "gerçek"e çevir (düğme kapısını atlayarak)
    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "critical", staticmethod(lambda parent, title, text: shown.append(text))
    )

    tab._on_save()

    assert shown == [TEST_MODE_OFF_VIA_SAVE_ERROR]
    session.expire_all()
    assert get_setting(session, KEY_TEST_MODE) is True


def test_go_live_dialog_confirm_button_needs_the_checkbox(qtbot):
    dialog = GoLiveDialog()
    qtbot.addWidget(dialog)

    assert dialog.confirm_button.isEnabled() is False
    dialog.ack_checkbox.setChecked(True)
    assert dialog.confirm_button.isEnabled() is True
    dialog.ack_checkbox.setChecked(False)
    assert dialog.confirm_button.isEnabled() is False


def test_go_live_dialog_has_no_typed_word_field(qtbot):
    dialog = GoLiveDialog()
    qtbot.addWidget(dialog)

    assert dialog.findChildren(QLineEdit) == []
    assert dialog.confirm_button.text() == GO_LIVE_CONFIRM_BUTTON_TEXT


# ---------------------------------------------------------------------------
# Manuel -> Otomatik: Evet/Hayır onayı
# ---------------------------------------------------------------------------


def test_switching_to_auto_mode_declined_does_not_save(qtbot, engine, tmp_path, monkeypatch, session):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    index = tab._send_mode_combo.findData(MODE_AUTO)
    tab._send_mode_combo.setCurrentIndex(index)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    tab._on_save()

    assert get_setting(session, KEY_SEND_MODE) == MODE_MANUAL


def test_switching_to_auto_mode_confirmed_saves(qtbot, engine, tmp_path, monkeypatch, session):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    index = tab._send_mode_combo.findData(MODE_AUTO)
    tab._send_mode_combo.setCurrentIndex(index)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    with qtbot.waitSignal(tab.settings_saved, timeout=1000):
        tab._on_save()

    assert get_setting(session, KEY_SEND_MODE) == MODE_AUTO


def test_inbox_check_minutes_zero_shows_special_value_text(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    tab._inbox_check_minutes_spin.setValue(0)

    assert tab._inbox_check_minutes_spin.specialValueText() == "kapalı"
    assert tab.form().inbox_check_minutes == 0


def test_save_writes_inbox_check_minutes(qtbot, engine, tmp_path, monkeypatch, session):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    _deny_all_dialogs(monkeypatch)

    tab._inbox_check_minutes_spin.setValue(15)

    with qtbot.waitSignal(tab.settings_saved, timeout=1000):
        tab._on_save()

    assert get_setting(session, KEY_INBOX_CHECK_MINUTES) == 15


def test_send_mode_description_updates_with_selection(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    index = tab._send_mode_combo.findData(MODE_AUTO)
    tab._send_mode_combo.setCurrentIndex(index)

    assert "otomatik" in tab._send_mode_desc_label.text().lower()


# ---------------------------------------------------------------------------
# Kaydedilmemiş değişiklik uyarısı (confirm_leave)
# ---------------------------------------------------------------------------


def test_confirm_leave_true_when_not_dirty(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    assert tab.confirm_leave() is True


def test_confirm_leave_discard_resets_widgets(qtbot, engine, tmp_path, monkeypatch, session):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab._daily_limit_spin.setValue(7)

    _stub_clicked_button(monkeypatch, UNSAVED_CHANGES_DISCARD)

    result = tab.confirm_leave()

    assert result is True
    assert tab._daily_limit_spin.value() == 20
    assert get_setting(session, KEY_DAILY_LIMIT) == 20


def test_confirm_leave_stay_keeps_edit_and_returns_false(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab._daily_limit_spin.setValue(7)

    _stub_clicked_button(monkeypatch, UNSAVED_CHANGES_STAY)

    result = tab.confirm_leave()

    assert result is False
    assert tab._daily_limit_spin.value() == 7


def test_confirm_leave_save_writes_and_returns_true(qtbot, engine, tmp_path, monkeypatch, session):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab._daily_limit_spin.setValue(7)

    # `_on_save()` içindeki `QMessageBox.critical` çağrılmayacak (test modu/gönderim modu
    # değişmedi, hata da yok); saatlik limit varsayılanı (20) günlük limitten (7) büyük
    # olduğu için `warning` ÇAĞRILIR (bkz. `form_warnings`) -- testin konusu bu olmadığından
    # gerçek bir dialog açılıp asılı kalmasın diye kapatılıyor.
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    _stub_clicked_button(monkeypatch, UNSAVED_CHANGES_SAVE)

    result = tab.confirm_leave()

    assert result is True
    assert get_setting(session, KEY_DAILY_LIMIT) == 7
    assert tab.is_dirty() is False


# ---------------------------------------------------------------------------
# API anahtarı / Gmail şifresi kaydet-sil (keyring sahte)
# ---------------------------------------------------------------------------


def test_save_api_key_updates_status_label(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    assert tab._key_status_labels["claude"].text() == "kayıtlı değil"

    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("sk-ant-test-123", True))
    )
    tab._on_save_api_key("claude")

    assert tab._key_status_labels["claude"].text() == "kayıtlı ✓"
    assert secrets.get_api_key("claude") == "sk-ant-test-123"


def test_save_api_key_cancelled_input_dialog_does_nothing(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))
    tab._on_save_api_key("claude")

    assert tab._key_status_labels["claude"].text() == "kayıtlı değil"


def test_save_api_key_empty_value_shows_warning_and_does_not_save(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("   ", True)))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.Ok)

    tab._on_save_api_key("claude")

    assert len(warnings) == 1
    assert tab._key_status_labels["claude"].text() == "kayıtlı değil"


def test_delete_api_key_updates_status_label(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    secrets.set_api_key("claude", "sk-ant-test-123")
    tab._refresh_key_status("claude")
    assert tab._key_status_labels["claude"].text() == "kayıtlı ✓"

    tab._on_delete_api_key("claude")

    assert tab._key_status_labels["claude"].text() == "kayıtlı değil"
    assert secrets.get_api_key("claude") is None


def test_delete_api_key_when_nothing_saved_shows_information(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a) or QMessageBox.Ok)

    tab._on_delete_api_key("claude")

    assert len(infos) == 1


def test_save_mail_password_strips_internal_whitespace(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("abcd efgh ijkl mnop", True))
    )
    tab._on_save_mail_password()

    assert tab._gmail_status_label.text() == "kayıtlı ✓"
    assert secrets.get_mail_password() == "abcdefghijklmnop"


def test_delete_mail_password_updates_status_label(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    secrets.set_mail_password("gizli-sifre-1234")
    tab._gmail_status_label.setText(secrets.get_mail_password() and "kayıtlı ✓" or "kayıtlı değil")

    tab._on_delete_mail_password()

    assert tab._gmail_status_label.text() == "kayıtlı değil"
    assert secrets.get_mail_password() is None


# ---------------------------------------------------------------------------
# "Bağlantıyı test et" -- LLM ve Gmail (ağa çıkan iş `JobWorker` üzerinden sahte bir
# `llm_test_job`/`smtp_login_test_job` ile taklit edilir; gerçek ağ/SMTP hiç YOK).
# ---------------------------------------------------------------------------


def _run_worker(qtbot, tab) -> None:
    """Tıklamayla başlayan işçinin bitmesini bekler.

    `qtbot.waitSignal` KULLANILMAZ: sahte iş fonksiyonu neredeyse anında bittiği için işçi
    `waitSignal`'in kendi bağlantısını kurmasından ÖNCE `finished_ok`'u yayabilir -- böyle bir
    yarış durumunda `waitSignal` sinyali asla görmez ve zaman aşımına uğrar (iş aslında doğru
    bitmiş olsa bile). `waitUntil` yoklama yaptığı için bu yarışa girmez.
    """
    assert tab._worker is not None
    qtbot.waitUntil(lambda: tab._worker is None, timeout=2000)


def test_llm_test_uses_current_unsaved_form_not_saved_setting(qtbot, engine, tmp_path, monkeypatch):
    """Buton EKRANDAKİ seçimi test etmeli, Kaydet'e basılmamış olsa bile."""
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    index = tab._llm_provider_combo.findData("claude")
    tab._llm_provider_combo.setCurrentIndex(index)
    tab._llm_model_claude_edit.setText("claude-opus-5")
    assert tab.is_dirty() is True  # bilerek KAYDEDİLMEDİ

    calls = []

    def _fake_job(provider, model, *, on_progress, token):
        calls.append((provider, model))
        return LlmTestJobResult(provider=provider, tags=["yapay-zeka"])

    monkeypatch.setattr("src.ui.settings_tab.llm_test_job", _fake_job)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)

    tab._on_test_llm_connection()
    _run_worker(qtbot, tab)

    assert calls == [("claude", "claude-opus-5")]


def test_llm_test_none_provider_shows_keyword_notice(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    index = tab._llm_provider_combo.findData(PROVIDER_NONE)
    tab._llm_provider_combo.setCurrentIndex(index)

    infos = []
    monkeypatch.setattr(
        "src.ui.settings_tab.llm_test_job",
        lambda provider, model, *, on_progress, token: LlmTestJobResult(
            provider=provider, tags=["yapay-zeka"]
        ),
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a) or QMessageBox.Ok)

    tab._on_test_llm_connection()
    _run_worker(qtbot, tab)

    assert len(infos) == 1
    assert "ağa hiç çıkılmadı" in infos[0][2].lower()


def test_llm_test_no_key_shows_warning(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    index = tab._llm_provider_combo.findData("gemini")
    tab._llm_provider_combo.setCurrentIndex(index)

    warnings = []
    monkeypatch.setattr(
        "src.ui.settings_tab.llm_test_job",
        lambda provider, model, *, on_progress, token: LlmTestJobResult(
            provider=provider, no_key=True
        ),
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.Ok)

    tab._on_test_llm_connection()
    _run_worker(qtbot, tab)

    assert len(warnings) == 1
    assert "anahtar" in warnings[0][2].lower()


def test_llm_test_error_shows_warning_with_message(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    index = tab._llm_provider_combo.findData("groq")
    tab._llm_provider_combo.setCurrentIndex(index)

    warnings = []
    monkeypatch.setattr(
        "src.ui.settings_tab.llm_test_job",
        lambda provider, model, *, on_progress, token: LlmTestJobResult(
            provider=provider, error="AuthenticationError | status_code=401 | yetkisiz"
        ),
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.Ok)

    tab._on_test_llm_connection()
    _run_worker(qtbot, tab)

    assert len(warnings) == 1
    assert "401" in warnings[0][2]


def test_llm_test_guards_against_double_start(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    calls = []
    monkeypatch.setattr(
        "src.ui.settings_tab.llm_test_job",
        lambda provider, model, *, on_progress, token: calls.append(1)
        or LlmTestJobResult(provider=provider),
    )
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a) or QMessageBox.Ok)

    tab._on_test_llm_connection()
    tab._on_test_llm_connection()  # hâlâ sürüyor -- ikinci tıklama "İş sürüyor" ile reddedilmeli
    _run_worker(qtbot, tab)

    assert len(calls) == 1  # iş yalnızca bir kez BAŞLADI
    # "İş sürüyor" uyarısı (ikinci tıklama) + sonuç kutusu (iş bitince): toplam 2.
    assert len(infos) == 2


def test_smtp_test_uses_current_unsaved_sender_email(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab._sender_email_edit.setText("ekrandaki@gmail.com")
    assert tab.is_dirty() is True  # bilerek KAYDEDİLMEDİ

    calls = []

    def _fake_job(profile_path, *, sender_email, sender_name, on_progress, token, **_kwargs):
        calls.append(sender_email)
        return SmtpLoginTestJobResult(email=sender_email, ok=True, imap_ok=True)

    monkeypatch.setattr("src.ui.settings_tab.smtp_login_test_job", _fake_job)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)

    tab._on_test_smtp_connection()
    _run_worker(qtbot, tab)

    assert calls == ["ekrandaki@gmail.com"]


def test_smtp_test_success_mentions_no_mail_sent(qtbot, engine, tmp_path, monkeypatch):
    """SMTP VE IMAP ikisi de başarılıysa "başarılı" başlığıyla, ikisi de ayrı ayrı gösterilir."""
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab._sender_email_edit.setText("ben@gmail.com")

    infos = []
    monkeypatch.setattr(
        "src.ui.settings_tab.smtp_login_test_job",
        lambda profile_path, *, sender_email, sender_name, on_progress, token, **_kwargs: SmtpLoginTestJobResult(
            email=sender_email, ok=True, imap_ok=True
        ),
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a) or QMessageBox.Ok)

    tab._on_test_smtp_connection()
    _run_worker(qtbot, tab)

    assert len(infos) == 1
    text = infos[0][2].lower()
    assert "gönderilmedi" in text
    assert "smtp" in text
    assert "imap" in text


def test_smtp_test_imap_failure_shown_separately_from_smtp_success(qtbot, engine, tmp_path, monkeypatch):
    """SMTP başarılı ama IMAP başarısızsa ikisi AYRI AYRI gösterilir, sonuç uyarı olarak çıkar."""
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab._sender_email_edit.setText("ben@gmail.com")

    warnings = []
    monkeypatch.setattr(
        "src.ui.settings_tab.smtp_login_test_job",
        lambda profile_path, *, sender_email, sender_name, on_progress, token, **_kwargs: SmtpLoginTestJobResult(
            email=sender_email, ok=True, imap_ok=False, imap_error="IMAP: kimlik doğrulama başarısız"
        ),
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.Ok)

    tab._on_test_smtp_connection()
    _run_worker(qtbot, tab)

    assert len(warnings) == 1
    text = warnings[0][2]
    assert "IMAP: kimlik doğrulama başarısız" in text
    assert "SMTP" in text and "başarılı" in text.lower()


def test_smtp_test_no_account_shows_warning(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    warnings = []
    monkeypatch.setattr(
        "src.ui.settings_tab.smtp_login_test_job",
        lambda profile_path, *, sender_email, sender_name, on_progress, token, **_kwargs: SmtpLoginTestJobResult(
            no_account=True
        ),
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.Ok)

    tab._on_test_smtp_connection()
    _run_worker(qtbot, tab)

    assert len(warnings) == 1
    assert "uygulama şifresi" in warnings[0][2].lower()


def test_smtp_test_error_shows_warning_with_message(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    warnings = []
    monkeypatch.setattr(
        "src.ui.settings_tab.smtp_login_test_job",
        lambda profile_path, *, sender_email, sender_name, on_progress, token, **_kwargs: SmtpLoginTestJobResult(
            email="ben@gmail.com", error="535 auth failed", imap_ok=True
        ),
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.Ok)

    tab._on_test_smtp_connection()
    _run_worker(qtbot, tab)

    assert len(warnings) == 1
    assert "535" in warnings[0][2]


def test_smtp_test_profile_error_before_account_shown_as_single_message(
    qtbot, engine, tmp_path, monkeypatch
):
    """Profil okunamadığı için hesaba hiç bakılamadıysa SMTP/IMAP ayrımı yapılmaz, tek hata gösterilir."""
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    warnings = []
    monkeypatch.setattr(
        "src.ui.settings_tab.smtp_login_test_job",
        lambda profile_path, *, sender_email, sender_name, on_progress, token, **_kwargs: SmtpLoginTestJobResult(
            error="Profil okunamadı: ..."
        ),
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.Ok)

    tab._on_test_smtp_connection()
    _run_worker(qtbot, tab)

    assert len(warnings) == 1
    assert warnings[0][2] == "Profil okunamadı: ..."


# ---------------------------------------------------------------------------
# CV modu (Faz 10 Adım 10.3a)
# ---------------------------------------------------------------------------


def test_cv_mode_defaults_to_ats_with_tailoring_controls_visible(qtbot, engine, tmp_path, monkeypatch):
    """`isHidden()` kullanılır (`isVisible()` DEĞİL): pencere hiç `show()` edilmediği için
    `isVisible()` ebeveyn zincirine bakıp her zaman `False` döner -- burada test edilen tek
    şey `setVisible()` çağrılarımız, ekranda gerçekten gösterilip gösterilmediği değil."""
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    assert tab._cv_mode_combo.currentData() == CV_MODE_ATS
    assert tab._tailoring_check.isHidden() is False
    assert tab._max_projects_spin.isHidden() is False
    assert tab._own_cv_row_widget.isHidden() is True


def test_switching_cv_mode_to_own_pdf_hides_tailoring_controls(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    tab._cv_mode_combo.setCurrentIndex(tab._cv_mode_combo.findData(CV_MODE_OWN_PDF))

    assert tab._tailoring_check.isHidden() is True
    assert tab._max_projects_spin.isHidden() is True
    assert tab._own_cv_row_widget.isHidden() is False


def test_select_own_cv_copies_file_immediately(qtbot, engine, tmp_path, monkeypatch):
    """Dosya seçimi "Kaydet"i beklemeden hemen kopyalanmalı (keyring anahtarlarıyla aynı ilke)."""
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "home"))
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    source = tmp_path / "benim_cv.pdf"
    source.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), "PDF"))

    tab._on_select_own_cv()

    assert has_own_cv() is True
    assert "Kayıtlı" in tab._own_cv_status_label.text()


def test_remove_own_cv(qtbot, engine, tmp_path, monkeypatch):
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "home"))
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    source = tmp_path / "benim_cv.pdf"
    source.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), "PDF"))
    tab._on_select_own_cv()
    assert has_own_cv() is True

    tab._on_remove_own_cv()

    assert has_own_cv() is False
    assert "seçilmedi" in tab._own_cv_status_label.text()


def test_save_rejects_own_pdf_mode_without_uploaded_file(qtbot, engine, tmp_path, monkeypatch):
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "home"))
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab._cv_mode_combo.setCurrentIndex(tab._cv_mode_combo.findData(CV_MODE_OWN_PDF))

    errors = []
    monkeypatch.setattr(
        QMessageBox, "critical", lambda *a, **k: errors.append(a) or QMessageBox.Ok
    )

    tab._on_save()

    assert len(errors) == 1
    assert "PDF" in errors[0][2]
    with get_session(engine) as session:
        assert get_setting(session, KEY_CV_MODE) == CV_MODE_ATS  # kaydedilmedi


def test_save_accepts_own_pdf_mode_with_uploaded_file(qtbot, engine, tmp_path, monkeypatch):
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "home"))
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    source = tmp_path / "benim_cv.pdf"
    source.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), "PDF"))
    tab._on_select_own_cv()
    tab._cv_mode_combo.setCurrentIndex(tab._cv_mode_combo.findData(CV_MODE_OWN_PDF))
    _deny_all_dialogs(monkeypatch)

    tab._on_save()

    with get_session(engine) as session:
        assert get_setting(session, KEY_CV_MODE) == CV_MODE_OWN_PDF


# ---------------------------------------------------------------------------
# Google Places bölümü (10.6g): varsayılan KAPALI, anahtar kaydet/test/sil, rehber
# ---------------------------------------------------------------------------


def _save_places_key(monkeypatch, tab, key="AIza-test-key"):
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (key, True)))
    tab._on_save_places_key()


def test_places_defaults_off_900_and_no_key(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    assert tab._use_places_check.isChecked() is False
    assert tab._places_limit_spin.value() == 900
    assert tab._places_limit_spin.maximum() == 2000  # kodda sabit tavan
    assert tab._key_status_labels["google_places"].text() == "kayıtlı değil"
    assert "0 / 900" in tab._places_usage_label.text()
    assert tab.is_dirty() is False


def test_places_usage_label_shows_counter_of_current_month(qtbot, engine, tmp_path, monkeypatch, session):
    set_setting(session, KEY_PLACES_REQUESTS_MONTH_KEY, pacific_month_key(utcnow()))
    set_setting(session, KEY_PLACES_REQUESTS_THIS_MONTH, 37)
    session.commit()

    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    assert "37 / 900" in tab._places_usage_label.text()


def test_save_places_key_goes_to_keyring_and_never_into_form_or_database(
    qtbot, engine, tmp_path, monkeypatch, session
):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)

    _save_places_key(monkeypatch, tab, "AIza-secret")

    assert secrets.get_api_key("google_places") == "AIza-secret"
    assert tab._key_status_labels["google_places"].text() == "kayıtlı ✓"
    assert tab._has_places_key is True
    assert tab.is_dirty() is False  # anahtar forma girmez
    assert "AIza-secret" not in str(tab.form())


def test_key_alone_does_not_enable_places_source(qtbot, engine, tmp_path, monkeypatch):
    """Anahtar kayıtlı olsa da düğme kapalıyken Places kaynak özetinde görünmez (habersiz para yok)."""
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab._city_combo.setCurrentIndex(tab._city_combo.findData("06"))
    tab._profession_combo.setCurrentIndex(tab._profession_combo.findData("yazilim"))

    _save_places_key(monkeypatch, tab)
    assert "Google Places" not in tab._sources_hint_label.text()

    tab._use_places_check.setChecked(True)
    assert "Google Places" in tab._sources_hint_label.text()


def test_delete_places_key_updates_status_and_sources(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab._city_combo.setCurrentIndex(tab._city_combo.findData("06"))
    tab._profession_combo.setCurrentIndex(tab._profession_combo.findData("yazilim"))
    _save_places_key(monkeypatch, tab)
    tab._use_places_check.setChecked(True)
    assert "Google Places" in tab._sources_hint_label.text()

    tab._on_delete_places_key()

    assert secrets.get_api_key("google_places") is None
    assert tab._key_status_labels["google_places"].text() == "kayıtlı değil"
    assert tab._has_places_key is False
    assert "Google Places" not in tab._sources_hint_label.text()


def test_delete_places_key_when_none_saved_shows_information(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a) or QMessageBox.Ok)

    tab._on_delete_places_key()

    assert len(infos) == 1 and "Google Places" in infos[0][2]


def test_turning_places_on_declined_does_not_save(qtbot, engine, tmp_path, monkeypatch, session):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab._use_places_check.setChecked(True)
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No
    )

    tab._on_save()

    assert len(asked) == 1 and "FATURA" in asked[0]
    assert get_setting(session, KEY_USE_GOOGLE_PLACES) is False


def test_turning_places_on_confirmed_saves_flag_and_limit(qtbot, engine, tmp_path, monkeypatch, session):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab._use_places_check.setChecked(True)
    tab._places_limit_spin.setValue(300)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)

    with qtbot.waitSignal(tab.settings_saved, timeout=1000):
        tab._on_save()

    assert get_setting(session, KEY_USE_GOOGLE_PLACES) is True
    assert get_setting(session, KEY_PLACES_MONTHLY_LIMIT) == 300


def test_restore_defaults_turns_places_off_in_widgets(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab._use_places_check.setChecked(True)
    tab._places_limit_spin.setValue(1500)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)

    tab._on_restore_defaults()

    assert tab._use_places_check.isChecked() is False
    assert tab._places_limit_spin.value() == 900


def test_places_key_test_no_key_shows_warning(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    warnings = []
    monkeypatch.setattr(
        "src.ui.settings_tab.places_key_test_job",
        lambda *, on_progress, token: PlacesKeyTestJobResult(no_key=True),
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.Ok)

    tab._on_test_places_key()
    _run_worker(qtbot, tab)

    assert len(warnings) == 1 and "anahtar" in warnings[0][2].lower()


def test_places_key_test_error_shows_message_and_success_says_free_id_only(
    qtbot, engine, tmp_path, monkeypatch
):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    warnings, infos = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a) or QMessageBox.Ok)

    monkeypatch.setattr(
        "src.ui.settings_tab.places_key_test_job",
        lambda *, on_progress, token: PlacesKeyTestJobResult(error="Anahtar reddedildi (403)"),
    )
    tab._on_test_places_key()
    _run_worker(qtbot, tab)
    assert len(warnings) == 1 and "403" in warnings[0][2]

    monkeypatch.setattr(
        "src.ui.settings_tab.places_key_test_job",
        lambda *, on_progress, token: PlacesKeyTestJobResult(),
    )
    tab._on_test_places_key()
    _run_worker(qtbot, tab)
    assert len(infos) == 1 and "places.id" in infos[0][2]


# --- Güncellemeler grubu (10.9d) -----------------------------------------------------------------


class _FakeDesktopServices:
    opened: list = []

    @staticmethod
    def openUrl(url) -> bool:
        _FakeDesktopServices.opened.append(url.toString())
        return True


def test_updates_group_shows_current_version_and_saves_the_toggle(
    qtbot, engine, tmp_path, monkeypatch, session
):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    assert __version__ in tab._updates_group.version_label.text()
    assert tab._check_updates_check.isChecked() is True  # varsayılan AÇIK
    assert tab.is_dirty() is False

    tab._check_updates_check.setChecked(False)
    assert tab.is_dirty() is True
    tab._on_save()

    session.expire_all()
    assert get_setting(session, KEY_UPDATE_CHECK_ENABLED) is False
    tab.reload()
    assert tab._check_updates_check.isChecked() is False


def test_check_now_runs_a_forced_check_and_announces_a_new_version(
    qtbot, engine, tmp_path, monkeypatch
):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    calls, announced, infos = [], [], []
    info = UpdateInfo(version="1.2.0", url="https://github.com/a/b/releases/latest")
    monkeypatch.setattr(
        "src.ui.settings_tab.update_check_job",
        lambda engine, *, force, on_progress, token: calls.append(force) or UpdateCheckJobResult(info=info),
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a) or QMessageBox.Ok)
    tab.update_available.connect(announced.append)
    # Sonuç kutusu `exec` ile açılır: "İndirme sayfasını aç" seçilmiş gibi davran.
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(
        QMessageBox,
        "clickedButton",
        lambda self: next(b for b in self.buttons() if b.text() == OPEN_PAGE_TEXT),
    )
    _FakeDesktopServices.opened = []
    monkeypatch.setattr(updates_group, "QDesktopServices", _FakeDesktopServices)

    tab._on_check_updates_now()
    _run_worker(qtbot, tab)

    assert calls == [True]  # "Şimdi kontrol et" = force
    assert announced == [info]
    assert _FakeDesktopServices.opened == ["https://github.com/a/b/releases/latest"]
    assert infos == []  # yeni sürüm varken "bulunamadı" kutusu açılmaz


def test_check_now_without_new_version_or_repo_says_so_without_announcing(
    qtbot, engine, tmp_path, monkeypatch
):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    announced, infos = [], []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a[2]) or QMessageBox.Ok)
    tab.update_available.connect(announced.append)

    monkeypatch.setattr(
        "src.ui.settings_tab.update_check_job",
        lambda engine, *, force, on_progress, token: UpdateCheckJobResult(info=None),
    )
    tab._on_check_updates_now()
    _run_worker(qtbot, tab)
    assert len(infos) == 1 and "bulunamadı" in infos[0]

    monkeypatch.setattr(
        "src.ui.settings_tab.update_check_job",
        lambda engine, *, force, on_progress, token: UpdateCheckJobResult(configured=False),
    )
    tab._on_check_updates_now()
    _run_worker(qtbot, tab)
    assert len(infos) == 2 and "etkin değil" in infos[1]
    assert announced == []


def test_places_guide_dialog_shows_step_by_step_guide(qtbot, engine, tmp_path, monkeypatch):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    shown = []
    monkeypatch.setattr(
        QDialog,
        "exec",
        lambda self: shown.append((self.windowTitle(), self.findChild(QPlainTextEdit).toPlainText())) or 0,
    )

    tab._on_show_places_guide()

    assert len(shown) == 1
    title, text = shown[0]
    assert "nasıl alınır" in title
    assert "Bütçe uyarısı faturayı DURDURMAZ" in text
    assert "GÜNLÜK İSTEK KOTASI" in text


def test_reopen_wizard_button_runs_wizard_in_reopen_mode_and_reloads(
    qtbot, engine, tmp_path, monkeypatch, session
):
    """Düğme sihirbazı `reopen=True` ile açar; kapanınca ekran veritabanından tazelenir."""
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    assert tab._wizard_button.text() == "Kurulum sihirbazını yeniden aç"
    calls = []

    def _fake_wizard(engine_, profile_path, **kwargs):
        calls.append(kwargs)
        set_setting(session, KEY_DAILY_LIMIT, 7)  # sihirbaz bir ayarı değiştirmiş gibi
        session.commit()
        return True

    monkeypatch.setattr("src.ui.settings_tab.run_onboarding_wizard", _fake_wizard)

    with qtbot.waitSignal(tab.settings_saved, timeout=1000):
        tab._wizard_button.click()

    assert len(calls) == 1 and calls[0]["reopen"] is True
    assert tab._daily_limit_spin.value() == 7


def test_reopen_wizard_completed_asks_the_main_window_for_the_autopilot_tab(
    qtbot, engine, tmp_path, monkeypatch
):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    monkeypatch.setattr("src.ui.settings_tab.run_onboarding_wizard", lambda *a, **k: True)

    with qtbot.waitSignal(tab.autopilot_requested, timeout=1000):
        tab._wizard_button.click()


def test_reopen_wizard_closed_early_does_not_ask_for_the_autopilot_tab(
    qtbot, engine, tmp_path, monkeypatch
):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    monkeypatch.setattr("src.ui.settings_tab.run_onboarding_wizard", lambda *a, **k: False)

    with qtbot.assertNotEmitted(tab.autopilot_requested):
        tab._wizard_button.click()


def test_reopen_wizard_button_stays_when_unsaved_changes_are_kept(
    qtbot, engine, tmp_path, monkeypatch
):
    tab = _make_tab(qtbot, engine, tmp_path, monkeypatch)
    tab._daily_limit_spin.setValue(tab._daily_limit_spin.value() + 1)
    _stub_clicked_button(monkeypatch, UNSAVED_CHANGES_STAY)
    monkeypatch.setattr(
        "src.ui.settings_tab.run_onboarding_wizard",
        lambda *a, **k: pytest.fail("Sihirbaz açılmamalıydı"),
    )

    tab._wizard_button.click()

    assert tab.is_dirty() is True
