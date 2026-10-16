"""src/ui/settings_data.py testleri (Qt YOK -- saf Python, widget gerektirmez)."""
from dataclasses import replace
from pathlib import Path

import pytest

from src.core.settings import (
    DEFAULTS,
    KEY_AUTOPILOT_QUERIES,
    KEY_BOUNCE_MIN_SAMPLE,
    KEY_BOUNCE_PAUSE_RATE,
    KEY_DISCOVERY_STATE,
    KEY_INBOX_LAST_CHECK_AT,
    KEY_LEGAL_CONSENT_AT,
    KEY_LEGAL_CONSENT_VERSION,
    KEY_PAUSE_REASON,
    KEY_PLACES_REQUESTS_MONTH_KEY,
    KEY_PLACES_REQUESTS_THIS_MONTH,
    KEY_ONBOARDING_COMPLETED,
    KEY_ONBOARDING_STEP,
    KEY_SCHEMA_VERSION,
    KEY_SENDING_PAUSED,
    KEY_UI_THEME,
    KEY_UPDATE_CHECK_ENABLED,
    KEY_UPDATE_LAST_CHECK_AT,
    KEY_UPDATE_SKIPPED_VERSION,
    MODE_AUTO,
    MODE_MANUAL,
    get_setting,
    set_setting,
)
from src.core.professions import load_profession_presets
from src.core.mail_template_presets import load_generic_mail_templates
from src.cv_builder.mail_template import LANGUAGE_EN, LANGUAGE_TR, TARGET_JOB, TARGET_VOLUNTEER_INTERNSHIP
from src.ui.settings_data import (
    AUTO_MODE_CONFIRM_TITLE,
    FORM_KEYS,
    PLACES_KEY_GUIDE_TR,
    PLACES_ON_CONFIRM_TITLE,
    TEST_MODE_OFF_VIA_SAVE_ERROR,
    SettingsForm,
    apply_form,
    default_form,
    describe_changes,
    form_warnings,
    format_places_usage,
    format_secret_status,
    plan_save,
    read_form,
    sender_name_hint,
    suggested_mail_template,
    validate_form,
)

EXAMPLE_PROFILE_PATH = Path("data/master_profile.example.json")


# ---------------------------------------------------------------------------
# read_form / default_form / apply_form
# ---------------------------------------------------------------------------


def test_read_form_returns_defaults_on_empty_db(session):
    form = read_form(session)
    assert form == default_form()
    assert form.daily_limit == DEFAULTS["daily_limit"]
    assert form.max_projects is None


def test_read_form_reflects_written_settings(session):
    set_setting(session, "daily_limit", 7)
    set_setting(session, "sender_email", "ben@gmail.com")
    session.commit()

    form = read_form(session)
    assert form.daily_limit == 7
    assert form.sender_email == "ben@gmail.com"


def test_form_keys_cover_every_setting_except_sending_paused():
    """Yeni bir ayar eklenip burası güncellenmezse bu test kırmızı olmalı.

    İstisnalar bilinçli: `KEY_SENDING_PAUSED`'ın sahibi durum şeridi (Not 24, PLAN.md Faz 5).
    `KEY_INBOX_LAST_CHECK_AT` kullanıcının DÜZENLEDİĞİ bir ayar değil,
    `src/inbox/service.py::check_inbox()`nun kendiliğinden yazdığı bir zaman damgası (Adım
    7.7, PLAN.md Faz 7) — forma hiç girmez. `KEY_INBOX_CHECK_MINUTES` ise Adım 7.7'de forma
    bağlandı (bkz. `inbox_check_minutes`).

    Faz 9 (Otopilot) ayarları henüz forma bağlanmadı, bilinçli olarak: `autopilot_queries`
    Adım 9.3'te (Google Places) Ayarlar sekmesine eklenecek (`autopilot_city` Adım 10.6c'de
    forma bağlandı); `use_google_places` ve `places_monthly_limit` Adım 10.6g'de forma
    bağlandı; `discovery_state`/`pause_reason` kullanıcının düzenlediği bir ayar DEĞİL, Otopilot'un
    kendiliğinden yazdığı iç durum (yukarıdaki `KEY_INBOX_LAST_CHECK_AT` ile aynı gerekçe);
    `bounce_pause_rate`/`bounce_min_sample` Adım 9.4'ün güvenlik freni eşikleri, şimdilik
    yalnızca kod içinde sabit. `schema_version` (Faz 10 Adım 10.1b) kullanıcının DÜZENLEDİĞİ
    bir ayar değil, `core/settings_migration.py`'nin kendi yazdığı bir işaretçi; `onboarding_*`
    (Adım 10.2a) sihirbazın kendi yazdığı durum. `update_check_enabled` forma Adım 10.9d'de
    ("Güncellemeler" grubu) bağlandı; `update_last_check_at`/`update_skipped_version` (Adım 10.9c)
    kontrolün kendi yazdığı durum.
    """
    exempt = {
        KEY_SENDING_PAUSED,
        KEY_INBOX_LAST_CHECK_AT,
        KEY_PLACES_REQUESTS_THIS_MONTH,
        KEY_PLACES_REQUESTS_MONTH_KEY,
        KEY_AUTOPILOT_QUERIES,
        KEY_DISCOVERY_STATE,
        KEY_PAUSE_REASON,
        KEY_BOUNCE_PAUSE_RATE,
        KEY_BOUNCE_MIN_SAMPLE,
        KEY_LEGAL_CONSENT_VERSION,
        KEY_LEGAL_CONSENT_AT,
        KEY_ONBOARDING_COMPLETED,
        KEY_ONBOARDING_STEP,
        KEY_UPDATE_LAST_CHECK_AT,
        KEY_UPDATE_SKIPPED_VERSION,
        KEY_SCHEMA_VERSION,
        KEY_UI_THEME,  # "Görünüm" grubu seçimi anında kaydeder, forma girmez
    }
    assert set(FORM_KEYS.values()) | exempt == set(DEFAULTS)


def test_apply_form_only_writes_changed_keys(session):
    old = read_form(session)
    new = replace(old, daily_limit=old.daily_limit + 1)

    changed = apply_form(session, new)

    assert changed == ("daily_limit",)
    assert get_setting(session, "daily_limit") == old.daily_limit + 1
    # Değişmeyen bir ayarın satırı hiç oluşmamalı.
    from src.core.models import Setting

    assert session.get(Setting, "hourly_limit") is None


def test_apply_form_writes_nothing_when_unchanged(session):
    form = read_form(session)
    changed = apply_form(session, form)
    assert changed == ()


def test_apply_form_round_trips_none(session):
    old = read_form(session)
    new = replace(old, max_projects=5)
    apply_form(session, new)
    session.commit()

    back_to_none = replace(read_form(session), max_projects=None)
    changed = apply_form(session, back_to_none)

    assert changed == ("max_projects",)
    assert get_setting(session, "max_projects") is None


# ---------------------------------------------------------------------------
# validate_form
# ---------------------------------------------------------------------------


def _valid_form(**overrides) -> SettingsForm:
    base = default_form()
    return replace(base, **overrides) if overrides else base


def test_validate_form_accepts_defaults():
    assert validate_form(_valid_form()) == ()


def test_validate_form_rejects_min_delay_over_max_delay():
    form = _valid_form(min_delay_seconds=150, max_delay_seconds=100)
    errors = validate_form(form)
    assert any("en az bekleme" in e.lower() for e in errors)


def test_validate_form_rejects_zero_daily_limit():
    form = _valid_form(daily_limit=0)
    errors = validate_form(form)
    assert any("günlük limit" in e.lower() for e in errors)


def test_validate_form_rejects_zero_hourly_limit():
    form = _valid_form(hourly_limit=0)
    errors = validate_form(form)
    assert any("saatlik limit" in e.lower() for e in errors)


def test_validate_form_rejects_malformed_work_start():
    form = _valid_form(work_start="9:00pm")
    errors = validate_form(form)
    assert any("mesai saatleri" in e.lower() for e in errors)


def test_validate_form_rejects_work_start_after_work_end():
    form = _valid_form(work_start="19:00", work_end="09:00")
    errors = validate_form(form)
    assert any("mesai başlangıcı" in e.lower() for e in errors)


def test_validate_form_rejects_empty_mail_template_subject():
    form = _valid_form(mail_template_subject="   ")
    errors = validate_form(form)
    assert any("mail konusu" in e.lower() for e in errors)


def test_validate_form_rejects_empty_mail_template_body():
    form = _valid_form(mail_template_body="   ")
    errors = validate_form(form)
    assert any("mail gövdesi" in e.lower() for e in errors)


def test_validate_form_rejects_unknown_placeholder_in_subject():
    form = _valid_form(mail_template_subject="{tarih} başvurusu")
    errors = validate_form(form)
    assert any("{tarih}" in e for e in errors)


def test_validate_form_rejects_unknown_placeholder_in_body():
    form = _valid_form(mail_template_body="Merhaba {bilinmeyen}")
    errors = validate_form(form)
    assert any("{bilinmeyen}" in e for e in errors)


def test_validate_form_accepts_known_placeholders():
    form = _valid_form(
        mail_template_subject="{sirket_adi} – Başvuru",
        mail_template_body=(
            "{ad_soyad}, {okul}, {bolum}, {hedef}, {meslek}, {sehir}, {telefon}, {eposta}, "
            "{linkedin}"
        ),
    )
    assert validate_form(form) == ()


def test_validate_form_rejects_unknown_cv_mode():
    form = _valid_form(cv_mode="pdf_dosyasi")
    errors = validate_form(form)
    assert any("cv modu" in e.lower() for e in errors)


def test_validate_form_rejects_own_pdf_without_uploaded_file(monkeypatch):
    monkeypatch.setattr("src.ui.settings_data.has_own_cv", lambda: False)
    form = _valid_form(cv_mode="own_pdf")
    errors = validate_form(form)
    assert any("pdf" in e.lower() and "yüklenmedi" in e.lower() for e in errors)


def test_validate_form_accepts_own_pdf_with_uploaded_file(monkeypatch):
    monkeypatch.setattr("src.ui.settings_data.has_own_cv", lambda: True)
    form = _valid_form(cv_mode="own_pdf")
    assert validate_form(form) == ()


def test_validate_form_rejects_unknown_application_target():
    form = _valid_form(application_target="oyle_boyle")
    errors = validate_form(form)
    assert any("hedef" in e.lower() for e in errors)


def test_validate_form_rejects_unknown_mail_language():
    form = _valid_form(mail_language="de")
    errors = validate_form(form)
    assert any("mail dili" in e.lower() for e in errors)


def test_validate_form_rejects_unknown_profession():
    form = _valid_form(profession="astronotluk")
    errors = validate_form(form)
    assert any("astronotluk" in e for e in errors)


def test_validate_form_accepts_none_target_and_profession():
    form = _valid_form(application_target=None, profession=None)
    assert validate_form(form) == ()


def test_validate_form_accepts_real_profession():
    form = _valid_form(profession="yazilim")
    assert validate_form(form) == ()


def test_validate_form_accepts_known_and_empty_city():
    assert validate_form(_valid_form(autopilot_city="06")) == ()
    assert validate_form(_valid_form(autopilot_city=None)) == ()


def test_validate_form_rejects_unknown_city_code():
    errors = validate_form(_valid_form(autopilot_city="99"))
    assert any("şehir" in error.lower() for error in errors)


def test_validate_form_does_not_lock_settings_when_city_file_is_unreadable(monkeypatch):
    """İl dosyası okunamazsa (boş sözlük) kayıtlı kod yüzünden Ayarlar kilitlenmemeli."""
    monkeypatch.setattr("src.ui.settings_data.load_cities", lambda: {})
    assert validate_form(_valid_form(autopilot_city="06")) == ()


def test_describe_changes_shows_city_name_not_code():
    old = _valid_form(autopilot_city=None)
    new = replace(old, autopilot_city="06")
    assert "Şehir: (boş) → Ankara" in describe_changes(old, new)


def test_validate_form_rejects_zero_max_projects():
    form = _valid_form(max_projects=0)
    errors = validate_form(form)
    assert any("proje say" in e.lower() for e in errors)


def test_validate_form_accepts_none_max_projects():
    form = _valid_form(max_projects=None)
    assert validate_form(form) == ()


def test_validate_form_rejects_malformed_sender_email():
    form = _valid_form(sender_email="gecersiz eposta @ gmail")
    errors = validate_form(form)
    assert any("e-posta" in e.lower() for e in errors)


def test_validate_form_accepts_empty_sender_email():
    form = _valid_form(sender_email=None)
    assert validate_form(form) == ()


def test_validate_form_rejects_negative_inbox_check_minutes():
    form = _valid_form(inbox_check_minutes=-1)
    errors = validate_form(form)
    assert any("gelen kutusu" in e.lower() for e in errors)


def test_validate_form_accepts_zero_inbox_check_minutes():
    """0 = otomatik kontrol kapalı, geçerli bir değer."""
    form = _valid_form(inbox_check_minutes=0)
    assert validate_form(form) == ()


def test_validate_form_rejects_unknown_send_mode():
    form = _valid_form(send_mode="yarim-otomatik")
    errors = validate_form(form)
    assert any("gönderim modu" in e.lower() for e in errors)


def test_validate_form_rejects_unknown_llm_provider():
    form = _valid_form(llm_provider="chatgpt")
    errors = validate_form(form)
    assert any("yapay zekâ sağlayıcısı" in e.lower() for e in errors)


def test_validate_form_rejects_empty_model_name_for_selected_provider():
    form = _valid_form(llm_provider="claude", llm_model_claude="  ")
    errors = validate_form(form)
    assert any("claude" in e.lower() and "model" in e.lower() for e in errors)


def test_validate_form_ignores_empty_model_name_when_provider_is_none():
    form = _valid_form(llm_provider="none", llm_model_claude="")
    assert validate_form(form) == ()


# ---------------------------------------------------------------------------
# form_warnings
# ---------------------------------------------------------------------------


def test_form_warnings_flags_high_daily_limit():
    form = _valid_form(daily_limit=31)
    warnings = form_warnings(form)
    assert any("gmail" in w.lower() for w in warnings)


def test_form_warnings_silent_at_thirty():
    form = _valid_form(daily_limit=30)
    assert form_warnings(form) == ()


def test_form_warnings_flags_hourly_over_daily():
    form = _valid_form(daily_limit=5, hourly_limit=10)
    warnings = form_warnings(form)
    assert any("saatlik limit" in w.lower() for w in warnings)


# ---------------------------------------------------------------------------
# plan_save
# ---------------------------------------------------------------------------


def test_plan_save_no_changes_has_no_confirmations():
    form = default_form()
    plan = plan_save(form, form)
    assert plan.errors == ()
    assert plan.confirmations == ()
    assert plan.changed_keys == ()


def test_plan_save_blocks_on_validation_errors_before_confirmations():
    old = default_form()
    new = replace(old, test_mode=False, daily_limit=0)  # test modu kapanıyor AMA form geçersiz
    plan = plan_save(old, new)
    assert plan.errors != ()
    assert plan.confirmations == ()


def test_plan_save_refuses_to_turn_test_mode_off():
    """Test modu yalnızca "Gerçek gönderime geç" düğmesiyle kapanır; Kaydet kapıyı atlayamaz."""
    old = default_form()
    assert old.test_mode is True
    new = replace(old, test_mode=False)
    plan = plan_save(old, new)

    assert plan.errors == (TEST_MODE_OFF_VIA_SAVE_ERROR,)
    assert plan.confirmations == ()
    assert plan.changed_keys == ()


def test_plan_save_allows_other_changes_while_test_mode_stays_off():
    """Zaten gerçek modda olan biri başka bir ayarı normal kaydedebilmeli."""
    old = replace(default_form(), test_mode=False)
    plan = plan_save(old, replace(old, daily_limit=old.daily_limit + 1))
    assert plan.errors == ()


def test_plan_save_no_confirmation_when_turning_test_mode_on():
    old = replace(default_form(), test_mode=False)
    new = replace(old, test_mode=True)
    plan = plan_save(old, new)
    assert plan.confirmations == ()


def test_plan_save_asks_yes_no_for_manual_to_auto():
    old = replace(default_form(), send_mode=MODE_MANUAL)
    new = replace(old, send_mode=MODE_AUTO)
    plan = plan_save(old, new)

    assert len(plan.confirmations) == 1
    confirmation = plan.confirmations[0]
    assert confirmation.title == AUTO_MODE_CONFIRM_TITLE


def test_plan_save_no_confirmation_for_auto_to_manual():
    old = replace(default_form(), send_mode=MODE_AUTO)
    new = replace(old, send_mode=MODE_MANUAL)
    plan = plan_save(old, new)
    assert plan.confirmations == ()


def test_plan_save_confirmations_in_order():
    old = default_form()
    new = replace(old, send_mode=MODE_AUTO, use_google_places=True)
    plan = plan_save(old, new)

    assert [c.title for c in plan.confirmations] == [AUTO_MODE_CONFIRM_TITLE, PLACES_ON_CONFIRM_TITLE]


def test_plan_save_reports_changed_keys():
    old = default_form()
    new = replace(old, daily_limit=old.daily_limit + 1, hourly_limit=old.hourly_limit - 1)
    plan = plan_save(old, new)
    assert set(plan.changed_keys) == {"daily_limit", "hourly_limit"}


def test_plan_save_includes_warnings():
    old = default_form()
    new = replace(old, daily_limit=35)
    plan = plan_save(old, new)
    assert any("gmail" in w.lower() for w in plan.warnings)


# ---------------------------------------------------------------------------
# describe_changes / format_secret_status
# ---------------------------------------------------------------------------


def test_describe_changes_lists_only_changed_fields_in_turkish():
    old = default_form()
    new = replace(old, daily_limit=old.daily_limit + 5)
    lines = describe_changes(old, new)

    assert len(lines) == 1
    assert "Günlük limit" in lines[0]
    assert str(old.daily_limit) in lines[0]
    assert str(old.daily_limit + 5) in lines[0]


def test_describe_changes_formats_booleans_and_none_in_turkish():
    old = default_form()
    new = replace(old, tailoring_enabled=False, sender_email="a@b.com")
    lines = describe_changes(old, new)

    joined = "\n".join(lines)
    assert "Açık → Kapalı" in joined
    assert "(boş) → a@b.com" in joined


def test_describe_changes_formats_inbox_check_minutes_in_turkish():
    old = replace(default_form(), inbox_check_minutes=0)
    new = replace(old, inbox_check_minutes=45)
    lines = describe_changes(old, new)

    joined = "\n".join(lines)
    assert "kapalı → 45 dk" in joined


def test_describe_changes_empty_when_nothing_changed():
    form = default_form()
    assert describe_changes(form, form) == ()


def test_describe_changes_formats_target_and_language_in_turkish():
    old = default_form()
    new = replace(old, application_target=TARGET_VOLUNTEER_INTERNSHIP, mail_language=LANGUAGE_EN)
    lines = describe_changes(old, new)

    joined = "\n".join(lines)
    assert "(boş) → gönüllü staj" in joined
    assert "Türkçe → İngilizce" in joined


def test_format_secret_status():
    assert format_secret_status(True) == "kayıtlı ✓"
    assert format_secret_status(False) == "kayıtlı değil"


# ---------------------------------------------------------------------------
# sender_name_hint
# ---------------------------------------------------------------------------


def test_sender_name_hint_reads_profile_name():
    assert sender_name_hint(EXAMPLE_PROFILE_PATH) == "Gülşah Çiçek"


def test_sender_name_hint_missing_file_does_not_raise(tmp_path):
    missing = tmp_path / "olmayan.json"
    assert sender_name_hint(missing) == "(profil okunamadı)"


def test_sender_name_hint_invalid_json_does_not_raise(tmp_path):
    bad = tmp_path / "bozuk.json"
    bad.write_text('{"personal_info": {"name": "Test"}}')
    assert sender_name_hint(bad) == "(profil okunamadı)"


# ---------------------------------------------------------------------------
# suggested_mail_template
# ---------------------------------------------------------------------------

_PROFESSIONS = load_profession_presets()
_GENERIC_TEMPLATES = load_generic_mail_templates()


def test_suggested_template_none_when_no_target_selected():
    assert suggested_mail_template(None, "yazilim", LANGUAGE_TR, _PROFESSIONS, _GENERIC_TEMPLATES) is None


def test_suggested_template_uses_profession_package_in_turkish():
    suggestion = suggested_mail_template(
        TARGET_VOLUNTEER_INTERNSHIP, "yazilim", LANGUAGE_TR, _PROFESSIONS, _GENERIC_TEMPLATES
    )
    expected = _PROFESSIONS["yazilim"].default_mail_templates[TARGET_VOLUNTEER_INTERNSHIP]
    assert suggestion == expected


def test_suggested_template_falls_back_to_generic_when_no_profession_selected():
    suggestion = suggested_mail_template(
        TARGET_JOB, None, LANGUAGE_TR, _PROFESSIONS, _GENERIC_TEMPLATES
    )
    assert suggestion == _GENERIC_TEMPLATES[(TARGET_JOB, LANGUAGE_TR)]


def test_suggested_template_falls_back_to_generic_for_unknown_profession():
    suggestion = suggested_mail_template(
        TARGET_JOB, "bilinmeyen_meslek", LANGUAGE_TR, _PROFESSIONS, _GENERIC_TEMPLATES
    )
    assert suggestion == _GENERIC_TEMPLATES[(TARGET_JOB, LANGUAGE_TR)]


def test_suggested_template_uses_generic_english_even_with_profession_selected():
    """Meslek paketleri yalnızca TR (bkz. `core/professions.py`); dil İngilizce'yken meslek
    seçili olsa bile genel İngilizce şablona düşülür."""
    suggestion = suggested_mail_template(
        TARGET_JOB, "yazilim", LANGUAGE_EN, _PROFESSIONS, _GENERIC_TEMPLATES
    )
    assert suggestion == _GENERIC_TEMPLATES[(TARGET_JOB, LANGUAGE_EN)]


# --- Google Places ayarları (10.6g) -------------------------------------------------------------


def test_places_defaults_are_off_and_900():
    form = default_form()
    assert form.use_google_places is False
    assert form.places_monthly_limit == 900


def test_validate_form_accepts_places_limit_up_to_hard_max():
    assert validate_form(_valid_form(places_monthly_limit=0)) == ()
    assert validate_form(_valid_form(places_monthly_limit=2000)) == ()


def test_validate_form_rejects_places_limit_above_hard_max_and_negative():
    above = validate_form(_valid_form(places_monthly_limit=2001))
    assert any("2000" in e and "Places" in e for e in above)
    negative = validate_form(_valid_form(places_monthly_limit=-1))
    assert any("negatif" in e for e in negative)


def test_plan_save_with_places_limit_above_hard_max_writes_nothing():
    old = default_form()
    plan = plan_save(old, replace(old, use_google_places=True, places_monthly_limit=5000))
    assert plan.errors != ()
    assert plan.confirmations == ()


def test_turning_places_on_asks_yes_no_confirmation_mentioning_cost():
    old = default_form()
    plan = plan_save(old, replace(old, use_google_places=True))
    assert [c.title for c in plan.confirmations] == [PLACES_ON_CONFIRM_TITLE]
    confirmation = plan.confirmations[0]
    assert "FATURA" in confirmation.text
    assert "DURDURMAZ" in confirmation.text  # bütçe uyarısı faturayı durdurmaz


def test_turning_places_off_or_changing_limit_needs_no_confirmation():
    on = replace(default_form(), use_google_places=True)
    assert plan_save(on, replace(on, use_google_places=False)).confirmations == ()
    assert plan_save(on, replace(on, places_monthly_limit=100)).confirmations == ()


def test_places_fields_round_trip_through_database(session):
    form = replace(read_form(session), use_google_places=True, places_monthly_limit=450)
    assert set(apply_form(session, form)) == {"use_google_places", "places_monthly_limit"}
    session.commit()
    assert read_form(session).use_google_places is True
    assert read_form(session).places_monthly_limit == 450


def test_format_places_usage_clips_to_hard_max():
    assert "3 / 900" in format_places_usage(3, 900)
    assert "5 / 2000" in format_places_usage(5, 99999)  # eski/elle bozulmuş ayar tavana kırpılır
    assert "0 / 0" in format_places_usage(0, -5)


def test_key_guide_has_required_warnings_and_steps():
    guide = PLACES_KEY_GUIDE_TR
    assert "Places API (New)" in guide
    assert "faturalandırma" in guide.lower()
    assert "günlük" in guide.lower() and "kota" in guide.lower()
    assert "Bütçe uyarısı faturayı DURDURMAZ" in guide
    assert "anahtarı kısıtla" in guide.lower()
    assert "2000" in guide


def test_update_check_enabled_is_on_by_default_and_round_trips_without_confirmation(session):
    assert default_form().update_check_enabled is True
    on = read_form(session)
    off = replace(on, update_check_enabled=False)

    assert plan_save(on, off).confirmations == ()  # kapatmak da açmak da onaysız
    assert apply_form(session, off) == ("update_check_enabled",)
    session.commit()
    assert get_setting(session, KEY_UPDATE_CHECK_ENABLED) is False
    assert read_form(session).update_check_enabled is False
