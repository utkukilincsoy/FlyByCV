"""src/ui/profile_data.py testleri (Qt YOK -- saf Python)."""
import pytest

from src.cv_builder.profile_loader import Experience, PersonalInfo
from src.ui.profile_data import (
    all_item_ids,
    collect_profile,
    education_summary,
    experience_summary,
    format_comma_list,
    generate_item_id,
    parse_comma_list,
    parse_lines,
    personal_info_errors,
    project_summary,
    skill_summary,
)
from tests.conftest import build_sample_profile


def test_parse_lines_strips_and_drops_empty():
    text = "Birinci satır\n\n  İkinci satır  \n\n"
    assert parse_lines(text) == ["Birinci satır", "İkinci satır"]


def test_parse_comma_list_strips_and_drops_empty():
    assert parse_comma_list("Python, , REST API ,") == ["Python", "REST API"]


def test_format_comma_list_round_trips():
    assert format_comma_list(parse_comma_list("Python, REST API")) == "Python, REST API"


def test_all_item_ids_covers_every_category():
    profile = build_sample_profile()
    ids = all_item_ids(profile)
    assert "exp_1" in ids
    assert "edu_1" in ids
    assert "proj_1" in ids
    assert "skill_1" in ids
    assert "ach_1" in ids  # achievements bu sekmede düzenlenmiyor ama çarpışmamalı


def test_generate_item_id_never_collides():
    existing = {"exp_aaaaaaaa"}
    new_id = generate_item_id("experience", existing)
    assert new_id not in existing
    assert new_id.startswith("exp_")


def test_generate_item_id_uses_category_prefix():
    assert generate_item_id("education", set()).startswith("edu_")
    assert generate_item_id("projects", set()).startswith("proj_")
    assert generate_item_id("skills", set()).startswith("skill_")


def test_summary_functions_are_readable():
    profile = build_sample_profile()
    assert "Şirket" in experience_summary(profile.experience[0])
    assert "Okul" in education_summary(profile.education[0])
    assert project_summary(profile.projects[0]) == "proj_1"
    assert "Python" in skill_summary(profile.skills[0])


def test_experience_summary_shows_devam_ediyor_when_end_date_missing():
    item = Experience(
        id="exp_x", tags=[], company="ACME", title="Stajyer", start_date="2024-01"
    )
    assert "devam ediyor" in experience_summary(item)


def test_personal_info_errors_rejects_blank_name_and_email():
    errors = personal_info_errors("   ", "")
    assert len(errors) == 2


def test_personal_info_errors_empty_when_filled():
    assert personal_info_errors("Ali Veli", "ali@example.com") == []


def test_collect_profile_preserves_untouched_sections():
    profile = build_sample_profile()
    new_info = PersonalInfo(name="Yeni Ad", email="yeni@example.com")

    result = collect_profile(
        profile,
        personal_info=new_info,
        experience=profile.experience,
        education=profile.education,
        projects=profile.projects,
        skills=profile.skills,
    )

    assert result.personal_info.name == "Yeni Ad"
    assert result.summaries == profile.summaries
    assert result.achievements == profile.achievements
    assert result.languages == profile.languages


def test_collect_profile_rejects_duplicate_ids_across_categories():
    profile = build_sample_profile()
    # experience id'sini eğitimdeki id ile çakıştır -- `check_unique_ids` tüm profili kapsar.
    clashing = profile.experience[0].model_copy(update={"id": profile.education[0].id})

    with pytest.raises(Exception):
        collect_profile(
            profile,
            personal_info=profile.personal_info,
            experience=[clashing],
            education=profile.education,
            projects=profile.projects,
            skills=profile.skills,
        )
