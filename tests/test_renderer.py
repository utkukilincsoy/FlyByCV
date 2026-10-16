import pytest
from pypdf import PdfReader

from src.cv_builder.profile_loader import MasterProfile
from src.cv_builder.renderer import (
    company_folder_name,
    cv_attachment_name,
    cv_filename,
    format_date,
    format_range,
    render_html,
    render_pdf,
    to_ascii,
)
from src.cv_builder.tag_matcher import TailoredCV, tailor
from src.cv_builder.validator import ValidationError
from tests.conftest import build_sample_profile


def _untailored(profile: MasterProfile) -> TailoredCV:
    return tailor(profile, [], enabled=False)


def _pdf_text(path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() for page in reader.pages)


def _render(profile: MasterProfile, tmp_path, tailored: TailoredCV | None = None):
    out_path = tmp_path / "cv.pdf"
    return render_pdf(profile, tailored or _untailored(profile), out_path)


@pytest.fixture(scope="module")
def main_cv(tmp_path_factory):
    """Ana CV bir kez üretilir; PDF üretimi yavaş olduğu için paylaşılır."""
    profile = build_sample_profile()
    out_path = tmp_path_factory.mktemp("cv") / "main.pdf"
    return render_pdf(profile, _untailored(profile), out_path)


# --- Tarih biçimlendirme ---


def test_format_date_turkish_month():
    assert format_date("2026-01") == "Ocak 2026"
    assert format_date("2026-09") == "Eylül 2026"


def test_format_date_year_only():
    assert format_date("2020") == "2020"


def test_format_range_full():
    assert format_range("2026-01", "2026-09") == "Ocak 2026 – Eylül 2026"


def test_format_range_ongoing():
    assert format_range("2026-01", None) == "Ocak 2026 – Devam ediyor"


def test_format_range_expected():
    assert format_range("2023", "2028", expected=True) == "2023 – 2028 (Beklenen)"


# --- Dosya adı ---


def test_to_ascii_turkish_characters():
    assert to_ascii("Gülşah Çiçek") == "Gulsah Cicek"
    assert to_ascii("ğşıİÖÇÜ") == "gsiIOCU"


def test_cv_filename_general(sample_profile):
    assert cv_filename(sample_profile) == "Test_Kisi_CV_genel.pdf"


def test_cv_filename_with_domain(sample_profile):
    name = cv_filename(sample_profile, "https://www.Firma.com/iletisim")
    assert name == "Test_Kisi_CV_firma.com.pdf"


def test_cv_attachment_name_has_no_company_info(sample_profile):
    """Mail eki her şirkete aynı adla gider: şirket/alan adı içermez."""
    assert cv_attachment_name(sample_profile) == "Test_Kisi_CV.pdf"


def test_company_folder_name_normalizes_domain():
    assert company_folder_name("https://www.Firma.com/iletisim") == "firma.com"


# --- PDF içeriği ---


def test_pdf_contains_all_titles(main_cv, sample_profile):
    text = _pdf_text(main_cv)
    assert sample_profile.personal_info.name in text
    for expected in ("Şirket", "Unvan", "Okul", "Bölüm", "Lisans"):
        assert expected in text
    for project in sample_profile.projects:
        assert project.name in text
    for skill in sample_profile.skills:
        assert skill.name in text
    for achievement in sample_profile.achievements:
        assert achievement.title in text


def test_pdf_contains_dates(main_cv):
    text = _pdf_text(main_cv)
    assert "2020 – Devam ediyor" in text  # deneyim: bitiş tarihi yok
    assert "Eylül 2016 – Devam ediyor" in text  # eğitim: YYYY-MM


def test_pdf_section_headings(main_cv):
    text = _pdf_text(main_cv)
    for heading in ("ÖZET", "EĞİTİM", "DENEYİM", "PROJELER", "YETENEKLER", "DİLLER"):
        assert heading in text


def test_pdf_metadata_title(main_cv, sample_profile):
    reader = PdfReader(str(main_cv))
    assert reader.metadata.title == f"{sample_profile.personal_info.name} – CV"


def test_pdf_fits_two_pages(main_cv):
    assert len(PdfReader(str(main_cv)).pages) <= 2


def test_pdf_keeps_turkish_characters(sample_profile, tmp_path):
    summary = "Çalışkan bir mühendis: ğ ş ı İ ö ç ü Ğ Ş Ö Ç Ü."
    profile = sample_profile.model_copy(update={"summaries": {"default": summary}})
    text = _pdf_text(_render(profile, tmp_path))
    assert summary in text


def test_linkedin_href_is_percent_encoded_but_text_stays_readable(sample_profile):
    info = sample_profile.personal_info.model_copy(
        update={"linkedin": "www.linkedin.com/in/gülşahçiçek"}
    )
    profile = sample_profile.model_copy(update={"personal_info": info})
    html = render_html(profile, _untailored(profile))
    assert '>www.linkedin.com/in/gülşahçiçek<' in html
    assert 'href="https://www.linkedin.com/in/g%C3%BCl%C5%9Fah%C3%A7i%C3%A7ek"' in html


def test_pdf_escapes_html(sample_profile, tmp_path):
    bullet = "<b>kalın değil</b> & düz metin"
    experience = [sample_profile.experience[0].model_copy(update={"bullets": [bullet]})]
    profile = sample_profile.model_copy(update={"experience": experience})
    assert bullet in _pdf_text(_render(profile, tmp_path))


# --- GNO görünürlüğü ---


def test_hidden_gpa_is_not_printed(sample_profile, tmp_path):
    visible = sample_profile.education[0].model_copy(
        update={"gpa": "3.20/4.00", "show_gpa": True}
    )
    hidden = sample_profile.education[1].model_copy(
        update={"gpa": "2.50/4.00", "show_gpa": False}
    )
    profile = sample_profile.model_copy(update={"education": [visible, hidden]})
    text = _pdf_text(_render(profile, tmp_path))
    assert "3.20/4.00" in text
    assert "2.50/4.00" not in text


# --- Özelleştirme ---


def test_tailored_pdf_project_order_matches(sample_profile, tmp_path):
    tailored = tailor(sample_profile, ["yapay-zeka"], enabled=True)
    text = _pdf_text(_render(sample_profile, tmp_path, tailored))
    positions = [text.index(id_) for id_ in tailored.project_ids]
    assert positions == sorted(positions)


def _with_categories(profile: MasterProfile) -> MasterProfile:
    categories = {
        "skill_1": "Programlama Dilleri",
        "skill_2": "Yapay Zeka",
        "skill_3": "Veri Bilimi",
    }
    skills = [s.model_copy(update={"category": categories[s.id]}) for s in profile.skills]
    return profile.model_copy(update={"skills": skills})


def test_matching_skill_category_comes_first(sample_profile):
    profile = _with_categories(sample_profile)
    html = render_html(profile, tailor(profile, ["yapay-zeka"], enabled=True))
    assert html.index("Yapay Zeka") < html.index("Programlama Dilleri")


def test_skill_categories_keep_profile_order_when_untailored(sample_profile):
    profile = _with_categories(sample_profile)
    html = render_html(profile, _untailored(profile))
    assert html.index("Programlama Dilleri") < html.index("Yapay Zeka")


def test_skills_without_category_are_printed_plainly(main_cv, sample_profile):
    """Kategorisiz yetenekler tek satırda, başlıksız basılır."""
    text = _pdf_text(main_cv)
    assert ", ".join(skill.name for skill in sample_profile.skills) in text


# --- Doğrulama ---


def test_invalid_tailored_cv_raises_and_writes_nothing(sample_profile, tmp_path):
    broken = _untailored(sample_profile)
    broken = TailoredCV(
        summary_key=broken.summary_key,
        experience_ids=["olmayan_id"],
        education_ids=broken.education_ids,
        project_ids=broken.project_ids,
        skill_ids=broken.skill_ids,
        achievement_ids=broken.achievement_ids,
        matched_tags=[],
        interest_tags=[],
    )
    out_path = tmp_path / "cv.pdf"
    with pytest.raises(ValidationError):
        render_pdf(sample_profile, broken, out_path)
    assert not out_path.exists()
