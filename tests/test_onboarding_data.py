"""src/ui/onboarding_data.py testleri (Qt YOK -- saf Python; PLAN.md Adım 10.2a)."""
import pytest
from pydantic import ValidationError

from src.core.settings import (
    CV_MODE_ATS,
    CV_MODE_OWN_PDF,
    KEY_AUTOPILOT_CITY,
    KEY_CV_MODE,
    KEY_ONBOARDING_COMPLETED,
    KEY_ONBOARDING_STEP,
    KEY_PROFESSION,
    get_setting,
    set_setting,
)
from src.cv_builder.profile_editor import write_profile
from src.cv_builder.profile_loader import PersonalInfo, load_profile
from src.ui import onboarding_data as od
from tests.conftest import build_sample_profile

CITIES = {"06", "34"}
PROFESSIONS = {"yazilim", "ic-mimar"}


def test_page_order_has_nine_unique_pages_starting_with_welcome_ending_with_finish():
    assert len(od.PAGE_ORDER) == 9
    assert len(set(od.PAGE_ORDER)) == 9
    assert od.PAGE_ORDER[0] == od.PAGE_WELCOME
    assert od.PAGE_ORDER[-1] == od.PAGE_FINISH


# --- kaldığı yerden devam -------------------------------------------------------------------


def test_fresh_install_needs_onboarding_and_starts_at_first_page(session):
    assert od.is_onboarding_needed(session) is True
    assert od.resume_page_id(session) == od.PAGE_WELCOME


def test_saved_step_is_resumed(session):
    od.save_step(session, od.PAGE_CV)
    session.commit()
    assert od.resume_page_id(session) == od.PAGE_CV


def test_unknown_saved_step_falls_back_to_first_page(session):
    from src.core.settings import set_setting

    set_setting(session, KEY_ONBOARDING_STEP, "artik-olmayan-sayfa")
    assert od.resume_page_id(session) == od.PAGE_WELCOME


def test_pick_start_page_uses_resume_page_when_available():
    available = {od.PAGE_WELCOME, od.PAGE_PERSONAL, od.PAGE_CV}
    assert od.pick_start_page(od.PAGE_PERSONAL, available) == od.PAGE_PERSONAL


def test_pick_start_page_skips_forward_when_saved_page_not_built_yet():
    available = {od.PAGE_WELCOME, od.PAGE_CV}
    assert od.pick_start_page(od.PAGE_PERSONAL, available) == od.PAGE_CV


def test_pick_start_page_falls_back_to_last_available_when_saved_page_is_past_all():
    available = {od.PAGE_WELCOME, od.PAGE_PERSONAL}
    assert od.pick_start_page(od.PAGE_FINISH, available) == od.PAGE_PERSONAL


def test_pick_start_page_without_pages_raises():
    with pytest.raises(ValueError):
        od.pick_start_page(od.PAGE_WELCOME, set())


def test_save_step_rejects_unknown_page(session):
    with pytest.raises(ValueError):
        od.save_step(session, "yok")


def test_complete_onboarding_marks_done_and_clears_step(session):
    od.save_step(session, od.PAGE_MAIL)
    od.complete_onboarding(session)
    session.commit()
    assert od.is_onboarding_needed(session) is False
    assert get_setting(session, KEY_ONBOARDING_COMPLETED) is True
    assert get_setting(session, KEY_ONBOARDING_STEP) is None


# --- ortak yazma yardımcıları (10.2f) --------------------------------------------------------


def test_save_settings_fields_writes_by_form_field_name(session):
    od.save_settings_fields(session, {"autopilot_city": "06", "profession": "yazilim"})
    session.commit()

    assert get_setting(session, KEY_AUTOPILOT_CITY) == "06"
    assert get_setting(session, KEY_PROFESSION) == "yazilim"


def test_save_settings_fields_rejects_unknown_field(session):
    with pytest.raises(KeyError):
        od.save_settings_fields(session, {"yok_boyle_alan": 1})


def test_load_existing_profile_is_none_for_a_fresh_install(tmp_path):
    assert od.load_existing_profile(tmp_path / "master_profile.json") is None


def test_load_existing_profile_raises_for_a_corrupt_file(tmp_path):
    path = tmp_path / "master_profile.json"
    path.write_text("{ bozuk", encoding="utf-8")
    with pytest.raises(ValueError):
        od.load_existing_profile(path)


def test_save_personal_info_creates_a_minimal_profile_without_inventing_content(tmp_path):
    path = tmp_path / "data" / "master_profile.json"

    wrote = od.save_personal_info(path, PersonalInfo(name="Ayşe Kaya", email="ayse@example.com"))

    profile = load_profile(path)
    assert wrote is True
    assert profile.personal_info.name == "Ayşe Kaya"
    assert profile.summaries == {"default": ""}
    assert profile.experience == profile.education == profile.projects == profile.skills == []


def test_save_personal_info_changes_only_personal_info_and_backs_up(tmp_path):
    path = tmp_path / "master_profile.json"
    original = build_sample_profile()
    write_profile(path, original)

    new_info = original.personal_info.model_copy(update={"name": "Yeni Ad", "phone": "0555"})
    assert od.save_personal_info(path, new_info) is True

    saved = load_profile(path)
    assert saved.personal_info == new_info
    assert saved.experience == original.experience
    assert saved.summaries == original.summaries
    assert saved.achievements == original.achievements
    assert len(list(tmp_path.glob("master_profile.json.bak-*"))) == 1


def test_save_personal_info_does_not_touch_the_file_when_nothing_changed(tmp_path):
    path = tmp_path / "master_profile.json"
    original = build_sample_profile()
    write_profile(path, original)
    before = path.read_bytes()

    assert od.save_personal_info(path, original.personal_info) is False

    assert path.read_bytes() == before
    assert list(tmp_path.glob("master_profile.json.bak-*")) == []


def test_save_personal_info_refuses_to_overwrite_a_corrupt_profile(tmp_path):
    path = tmp_path / "master_profile.json"
    path.write_text("{ bozuk", encoding="utf-8")

    with pytest.raises(ValueError):
        od.save_personal_info(path, PersonalInfo(name="A", email="a@b.co"))

    assert path.read_text(encoding="utf-8") == "{ bozuk"


# --- CV sayfası yardımcıları (10.2h) ---------------------------------------------------------


def _sections(profile):
    return dict(
        experience=list(profile.experience),
        education=list(profile.education),
        projects=list(profile.projects),
        skills=list(profile.skills),
    )


def test_save_profile_sections_writes_only_the_four_lists(tmp_path):
    path = tmp_path / "master_profile.json"
    original = build_sample_profile()
    write_profile(path, original)
    sections = _sections(original)
    sections["skills"] = sections["skills"][:1]

    assert od.save_profile_sections(path, **sections) is True

    saved = load_profile(path)
    assert [skill.id for skill in saved.skills] == ["skill_1"]
    assert saved.experience == original.experience
    assert saved.personal_info == original.personal_info
    assert saved.summaries == original.summaries
    assert saved.achievements == original.achievements
    assert saved.languages == original.languages


def test_save_profile_sections_is_a_no_op_for_identical_lists(tmp_path):
    path = tmp_path / "master_profile.json"
    original = build_sample_profile()
    write_profile(path, original)
    before = path.read_bytes()

    assert od.save_profile_sections(path, **_sections(original)) is False

    assert path.read_bytes() == before
    assert list(tmp_path.glob("master_profile.json.bak-*")) == []


def test_save_profile_sections_needs_an_existing_profile(tmp_path):
    with pytest.raises(FileNotFoundError):
        od.save_profile_sections(tmp_path / "yok.json", **_sections(build_sample_profile()))


def test_merge_profile_sections_rejects_duplicate_ids():
    original = build_sample_profile()
    sections = _sections(original)
    sections["experience"] = [original.experience[0], original.experience[0]]

    with pytest.raises(ValidationError):
        od.merge_profile_sections(original, **sections)


def test_initial_cv_mode_recommends_own_pdf_only_on_a_fresh_wizard_with_no_choice(session):
    assert od.initial_cv_mode(session) == CV_MODE_OWN_PDF


def test_initial_cv_mode_keeps_an_explicit_choice_on_a_fresh_wizard(session):
    set_setting(session, KEY_CV_MODE, CV_MODE_ATS)
    session.commit()
    assert od.initial_cv_mode(session) == CV_MODE_ATS


def test_initial_cv_mode_never_overrides_an_onboarded_users_default(session):
    set_setting(session, KEY_ONBOARDING_COMPLETED, True)  # CV modu satırı yok = varsayılan ATS
    session.commit()
    assert od.initial_cv_mode(session) == CV_MODE_ATS


def test_cv_ats_mode_needs_a_readable_profile_but_own_pdf_does_not():
    assert any("profil dosyası okunamadı" in e for e in od.validate_cv("ats", False, False))
    assert od.validate_cv("ats", False, True) == []
    assert od.validate_cv("own_pdf", True, False) == []


# --- sayfa doğrulamaları --------------------------------------------------------------------


def test_welcome_requires_consent():
    assert od.validate_welcome(False)
    assert od.validate_welcome(True) == []


def test_personal_valid():
    assert od.validate_personal("Ayşe Yılmaz", "ayse@ornek.com", "34", CITIES) == []


@pytest.mark.parametrize(
    "name,email,city,fragment",
    [
        ("  ", "a@b.com", "34", "Ad soyad"),
        ("Ayşe", "", "34", "E-posta boş"),
        ("Ayşe", "ayse-ornek.com", "34", "geçerli görünmüyor"),
        ("Ayşe", "a@b.com", None, "şehir seçin"),
        ("Ayşe", "a@b.com", "99", "listede yok"),
    ],
)
def test_personal_errors(name, email, city, fragment):
    errors = od.validate_personal(name, email, city, CITIES)
    assert any(fragment in error for error in errors)


def test_target_valid_and_errors():
    assert od.validate_target("internship", "ic-mimar", PROFESSIONS) == []
    assert any("Ne aradığınızı" in e for e in od.validate_target(None, "ic-mimar", PROFESSIONS))
    assert any("Ne aradığınızı" in e for e in od.validate_target("hobi", "ic-mimar", PROFESSIONS))
    assert any("meslek seçin" in e for e in od.validate_target("job", None, PROFESSIONS))
    assert any("listede yok" in e for e in od.validate_target("job", "astronot", PROFESSIONS))


def test_cv_own_pdf_requires_uploaded_file_but_ats_does_not():
    assert od.validate_cv("own_pdf", has_own_cv=False)
    assert od.validate_cv("own_pdf", has_own_cv=True) == []
    assert od.validate_cv("ats", has_own_cv=False) == []


def test_mail_requires_password_then_successful_test():
    assert any("şifresini" in e for e in od.validate_mail(False, False))
    assert any("Bağlantıyı test et" in e for e in od.validate_mail(True, False))
    assert od.validate_mail(True, True) == []


GMAIL_VALUES = {
    "sender_email": "ben@gmail.com",
    "sender_name": None,
    "mail_provider": "gmail",
    "custom_smtp_host": None,
    "custom_smtp_port": None,
    "custom_smtp_security": "starttls",
    "custom_imap_host": None,
    "custom_imap_port": None,
    "inbox_check_minutes": 30,
}


def test_mail_account_valid_gmail_needs_password_and_test():
    assert od.validate_mail_account(GMAIL_VALUES, True, True) == []
    assert any("şifresini" in e for e in od.validate_mail_account(GMAIL_VALUES, False, False))
    assert any("Bağlantıyı test et" in e for e in od.validate_mail_account(GMAIL_VALUES, True, False))


def test_mail_account_field_errors_come_before_password_and_test():
    errors = od.validate_mail_account({**GMAIL_VALUES, "sender_email": "yanlis"}, False, False)
    assert errors == ["Gönderen e-posta adresi geçerli görünmüyor (örn. ad@gmail.com)."]


def test_mail_account_empty_sender_is_fine_it_falls_back_to_the_profile():
    assert od.validate_mail_account({**GMAIL_VALUES, "sender_email": None}, True, True) == []


def test_mail_account_unsupported_or_unknown_provider_is_rejected():
    assert any("desteklenmiyor" in e for e in od.validate_mail_account({**GMAIL_VALUES, "mail_provider": "outlook"}, True, True))
    assert any("Bilinmeyen" in e for e in od.validate_mail_account({**GMAIL_VALUES, "mail_provider": "yok"}, True, True))


def test_mail_account_custom_provider_needs_all_server_fields():
    errors = od.validate_mail_account({**GMAIL_VALUES, "mail_provider": "custom"}, True, True)
    assert len(errors) == 4  # SMTP sunucu+port, IMAP sunucu+port
    full = {
        **GMAIL_VALUES,
        "mail_provider": "custom",
        "custom_smtp_host": "smtp.firma.com",
        "custom_smtp_port": 587,
        "custom_imap_host": "imap.firma.com",
        "custom_imap_port": 993,
    }
    assert od.validate_mail_account(full, True, True) == []


def test_mail_test_signature_follows_connection_fields_and_password_only():
    base = od.mail_test_signature(GMAIL_VALUES, True)
    assert od.mail_test_signature({**GMAIL_VALUES, "inbox_check_minutes": 5}, True) == base
    assert od.mail_test_signature({**GMAIL_VALUES, "sender_email": "x@y.co"}, True) != base
    assert od.mail_test_signature({**GMAIL_VALUES, "mail_provider": "yandex"}, True) != base
    assert od.mail_test_signature(GMAIL_VALUES, False) != base


def test_self_test_requires_a_sent_mail_and_the_confirmation():
    assert "gönderin" in od.validate_self_test(False, False)[0]
    assert "gönderin" in od.validate_self_test(False, True)[0]  # kutu işaretli ama mail hiç gitmedi
    assert "onay kutusunu" in od.validate_self_test(True, False)[0]
    assert od.validate_self_test(True, True) == []


def test_self_test_signature_follows_account_cv_template_and_files():
    values = {**GMAIL_VALUES, "cv_mode": "own_pdf", "mail_template_subject": "K", "mail_template_body": "G"}
    base = od.self_test_signature(values, True, (1, 2), (3, 4))

    assert od.self_test_signature({**values, "inbox_check_minutes": 5}, True, (1, 2), (3, 4)) == base
    assert od.self_test_signature({**values, "sender_email": "x@y.co"}, True, (1, 2), (3, 4)) != base
    assert od.self_test_signature({**values, "cv_mode": "ats"}, True, (1, 2), (3, 4)) != base
    assert od.self_test_signature({**values, "mail_template_body": "Y"}, True, (1, 2), (3, 4)) != base
    assert od.self_test_signature(values, False, (1, 2), (3, 4)) != base
    assert od.self_test_signature(values, True, (9, 2), (3, 4)) != base  # profil dosyası değişti
    assert od.self_test_signature(values, True, (1, 2), None) != base  # kendi PDF'i silindi


def test_file_stamp_changes_with_content_and_is_none_for_missing_file(tmp_path):
    path = tmp_path / "a.pdf"
    assert od.file_stamp(path) is None
    path.write_bytes(b"1")
    first = od.file_stamp(path)
    path.write_bytes(b"22")
    assert od.file_stamp(path) != first


def test_self_test_cooldown_remaining():
    assert od.self_test_cooldown_remaining(None, 100.0, 30) == 0.0
    assert od.self_test_cooldown_remaining(100.0, 110.0, 30) == 20.0
    assert od.self_test_cooldown_remaining(100.0, 130.0, 30) == 0.0
    assert od.self_test_cooldown_remaining(100.0, 500.0, 30) == 0.0
