from src.cv_builder.mail_template import (
    LANGUAGE_EN,
    LANGUAGE_TR,
    PLACEHOLDERS,
    TARGET_INTERNSHIP,
    TARGET_JOB,
    TARGET_PART_TIME,
    TARGET_VOLUNTEER_INTERNSHIP,
    fill_mail_template,
    find_unknown_placeholders,
)
from src.cv_builder.profile_loader import MasterProfile, PersonalInfo


def _profile(sample_profile: MasterProfile, **education_overrides) -> MasterProfile:
    """Örnek profili verilen `education` listesiyle kopyalar (verilmezse dokunmaz)."""
    if "education" not in education_overrides:
        return sample_profile
    return sample_profile.model_copy(update=education_overrides)


# --- fill_mail_template: temel değişim ---


def test_replaces_all_four_placeholders(sample_profile):
    subject, body = fill_mail_template(
        "{sirket_adi} – Başvuru",
        "Ben {ad_soyad}, {okul} {bolum} bölümü öğrencisiyim.",
        sample_profile,
        "Örnek A.Ş.",
    )
    assert subject == "Örnek A.Ş. – Başvuru"
    assert body == "Ben Test Kişi, Okul Bölüm bölümü öğrencisiyim."


def test_unrelated_text_is_untouched(sample_profile):
    subject, body = fill_mail_template("Sabit konu", "Sabit gövde metni.", sample_profile, "A.Ş.")
    assert subject == "Sabit konu"
    assert body == "Sabit gövde metni."


def test_repeated_placeholder_is_replaced_everywhere(sample_profile):
    _, body = fill_mail_template("", "{ad_soyad} - {ad_soyad}", sample_profile, "A.Ş.")
    assert body == "Test Kişi - Test Kişi"


def test_result_is_stripped(sample_profile):
    subject, body = fill_mail_template("  {sirket_adi}  ", "  {ad_soyad}  ", sample_profile, "A.Ş.")
    assert subject == "A.Ş."
    assert body == "Test Kişi"


def test_unknown_brace_in_template_does_not_raise(sample_profile):
    """Kullanıcı şablonunda `{` geçerse (Jinja/str.format olmadığı için) patlamamalı."""
    subject, body = fill_mail_template(
        "{sirket_adi}", "Merhaba {bilmedigim_alan}, {ad_soyad}", sample_profile, "A.Ş."
    )
    assert subject == "A.Ş."
    assert body == "Merhaba {bilmedigim_alan}, Test Kişi"


# --- eğitim seçimi (okul/bölüm) ---


def test_uses_expected_true_education_when_present(sample_profile):
    education = sample_profile.education
    updated = [
        education[0].model_copy(update={"expected": False}),
        education[1].model_copy(update={"expected": True, "school": "Devam Eden Okul", "field": "Devam Eden Bölüm"}),
    ]
    profile = _profile(sample_profile, education=updated)

    _, body = fill_mail_template("", "{okul} - {bolum}", profile, "A.Ş.")
    assert body == "Devam Eden Okul - Devam Eden Bölüm"


def test_falls_back_to_first_education_when_none_expected(sample_profile):
    updated = [e.model_copy(update={"expected": False}) for e in sample_profile.education]
    profile = _profile(sample_profile, education=updated)

    _, body = fill_mail_template("", "{okul} - {bolum}", profile, "A.Ş.")
    assert body == f"{updated[0].school} - {updated[0].field}"


def test_empty_education_list_leaves_placeholders_blank(sample_profile):
    profile = _profile(sample_profile, education=[])
    _, body = fill_mail_template("", "önce:{okul}:sonra", profile, "A.Ş.")
    assert body == "önce::sonra"


# --- yeni yer tutucular: {hedef}/{meslek}/{sehir}/{telefon}/{eposta}/{linkedin} ---


def test_profile_fields_are_filled_directly_from_profile(sample_profile):
    profile = sample_profile.model_copy(
        update={
            "personal_info": PersonalInfo(
                name="Test Kişi",
                email="test@example.com",
                phone="+90 555 000 00 00",
                location="Ankara",
                linkedin="linkedin.com/in/testkisi",
            )
        }
    )
    _, body = fill_mail_template(
        "", "{sehir} {telefon} {eposta} {linkedin}", profile, "A.Ş."
    )
    assert body == "Ankara +90 555 000 00 00 test@example.com linkedin.com/in/testkisi"


def test_profile_fields_blank_when_not_set(sample_profile):
    _, body = fill_mail_template(
        "", "önce:{sehir}:{telefon}:{linkedin}:sonra", sample_profile, "A.Ş."
    )
    assert body == "önce::::sonra"


def test_hedef_placeholder_uses_target_and_language(sample_profile):
    _, body_tr = fill_mail_template(
        "", "{hedef}", sample_profile, "A.Ş.", target=TARGET_VOLUNTEER_INTERNSHIP
    )
    assert body_tr == "gönüllü staj"

    _, body_en = fill_mail_template(
        "", "{hedef}", sample_profile, "A.Ş.", target=TARGET_INTERNSHIP, language=LANGUAGE_EN
    )
    assert body_en == "internship"

    _, body_part_time = fill_mail_template(
        "", "{hedef}", sample_profile, "A.Ş.", target=TARGET_PART_TIME, language=LANGUAGE_TR
    )
    assert body_part_time == "yarı zamanlı iş"


def test_hedef_placeholder_blank_when_target_not_given(sample_profile):
    _, body = fill_mail_template("", "önce:{hedef}:sonra", sample_profile, "A.Ş.")
    assert body == "önce::sonra"


def test_meslek_placeholder_uses_profession_name(sample_profile):
    _, body = fill_mail_template(
        "", "{meslek}", sample_profile, "A.Ş.", profession_name="Yazılım / Bilgisayar Mühendisliği"
    )
    assert body == "Yazılım / Bilgisayar Mühendisliği"


def test_meslek_placeholder_blank_when_not_given(sample_profile):
    _, body = fill_mail_template("", "önce:{meslek}:sonra", sample_profile, "A.Ş.")
    assert body == "önce::sonra"


def test_unused_target_still_leaves_job_label_out_of_other_languages(sample_profile):
    _, body = fill_mail_template(
        "", "{hedef}", sample_profile, "A.Ş.", target=TARGET_JOB, language=LANGUAGE_EN
    )
    assert body == "job"


# --- find_unknown_placeholders ---


def test_find_unknown_placeholders_returns_empty_for_known_ones():
    text = " ".join(PLACEHOLDERS)
    assert find_unknown_placeholders(text) == []


def test_find_unknown_placeholders_reports_unknown_token():
    assert find_unknown_placeholders("Merhaba {bilinmeyen}") == ["{bilinmeyen}"]


def test_find_unknown_placeholders_accepts_new_placeholders():
    text = "{hedef} {meslek} {sehir} {telefon} {eposta} {linkedin}"
    assert find_unknown_placeholders(text) == []


def test_find_unknown_placeholders_deduplicates_and_sorts():
    assert find_unknown_placeholders("{b} {a} {b}") == ["{a}", "{b}"]


def test_find_unknown_placeholders_ignores_plain_text():
    assert find_unknown_placeholders("Sayın Yetkili, iyi çalışmalar.") == []
