import pytest

from src.cv_builder.profile_loader import Skill
from src.cv_builder.tag_matcher import tailor
from src.cv_builder.validator import validate

ORIGINAL_PROJECTS = ["proj_1", "proj_2", "proj_3", "proj_4"]
ORIGINAL_SKILLS = ["skill_1", "skill_2", "skill_3"]


def _with_extra_skill(profile, tag: str):
    """`profile` + sadece bu etikete sahip, başka hiçbir maddede geçmeyen bir yetenek."""
    extra = Skill(id="skill_extra", tags=[tag], name="Ekstra Araç")
    return profile.model_copy(update={"skills": profile.skills + [extra]})


def test_disabled_keeps_profile_order(sample_profile):
    result = tailor(sample_profile, ["yapay-zeka"], enabled=False, max_projects=1)

    assert result.summary_key == "default"
    assert result.experience_ids == ["exp_1", "exp_2"]
    assert result.education_ids == ["edu_1", "edu_2"]
    assert result.project_ids == ORIGINAL_PROJECTS
    assert result.skill_ids == ORIGINAL_SKILLS
    assert result.achievement_ids == ["ach_1", "ach_2"]
    assert result.matched_tags == []


@pytest.mark.parametrize("tags", [[], ["", "   "]])
def test_no_tags_same_as_disabled(sample_profile, tags):
    disabled = tailor(sample_profile, ["yapay-zeka"], enabled=False)
    assert tailor(sample_profile, tags, enabled=True, max_projects=1) == disabled


def test_tags_without_match_same_as_disabled(sample_profile):
    disabled = tailor(sample_profile, [], enabled=False)
    result = tailor(sample_profile, ["mobil", "oyun-gelistirme"], enabled=True, max_projects=1)
    assert result == disabled


def test_single_match(sample_profile):
    result = tailor(sample_profile, ["yapay-zeka"], enabled=True)

    assert result.summary_key == "yapay-zeka"
    assert result.project_ids == ["proj_2", "proj_3", "proj_1", "proj_4"]
    assert result.skill_ids == ["skill_2", "skill_1", "skill_3"]
    assert result.matched_tags == ["yapay-zeka"]


def test_multiple_matches_ranked_with_stable_ties(sample_profile):
    result = tailor(sample_profile, ["veri-bilimi", "yapay-zeka"], enabled=True)

    # proj_3 matches 2; proj_2 and proj_4 tie at 1 and keep original order.
    assert result.project_ids == ["proj_3", "proj_2", "proj_4", "proj_1"]
    assert result.skill_ids == ["skill_2", "skill_3", "skill_1"]
    # veri-bilimi has no summary, so the first matching summary key is used.
    assert result.summary_key == "yapay-zeka"
    assert result.matched_tags == ["veri-bilimi", "yapay-zeka"]


def test_matched_tags_excludes_unknown_and_duplicates(sample_profile):
    result = tailor(sample_profile, ["mobil", " backend ", "backend"], enabled=True)

    assert result.matched_tags == ["backend"]
    assert result.summary_key == "default"


def test_interest_tags_equal_matched_tags_for_experience_or_project_source(sample_profile):
    """Deneyim/proje/eğitim/özet kaynaklı eşleşmeler ilgi alanı sayılmalı (mail cümlesi)."""
    result = tailor(sample_profile, ["yapay-zeka"], enabled=True)
    assert result.interest_tags == ["yapay-zeka"]

    result = tailor(sample_profile, ["elektronik"], enabled=True)  # sadece edu_2'de var
    assert result.interest_tags == ["elektronik"]


def test_interest_tags_excludes_skill_only_match(sample_profile):
    """Yalnızca bir yetenekte geçen etiket `matched_tags`'te kalır ama `interest_tags`'te olmaz."""
    profile = _with_extra_skill(sample_profile, "veritabani")
    result = tailor(profile, ["veritabani"], enabled=True)

    assert result.matched_tags == ["veritabani"]  # CV sıralaması için hâlâ eşleşti
    assert result.interest_tags == []  # ama mail cümlesi için değil
    assert result.skill_ids[0] == "skill_extra"  # yetenek sıralaması yine etkilendi


def test_interest_tags_excludes_achievement_only_match(sample_profile):
    """Yalnızca bir başarıda geçen etiket ("arge", sadece ach_1'de) de ilgi alanı sayılmamalı."""
    result = tailor(sample_profile, ["arge"], enabled=True)

    assert result.matched_tags == ["arge"]
    assert result.interest_tags == []


def test_interest_tags_mixed_keeps_only_non_skill_matches(sample_profile):
    """Karışık eşleşmede yalnızca yetenek/başarı KAYNAKLI olan etiket elenmeli."""
    profile = _with_extra_skill(sample_profile, "veritabani")
    result = tailor(profile, ["veritabani", "yapay-zeka"], enabled=True)

    assert result.matched_tags == ["veritabani", "yapay-zeka"]
    assert result.interest_tags == ["yapay-zeka"]


def test_untailored_interest_tags_is_empty(sample_profile):
    """Özelleştirme kapalıyken veya eşleşme yokken interest_tags de boş olmalı."""
    assert tailor(sample_profile, ["yapay-zeka"], enabled=False).interest_tags == []
    assert tailor(sample_profile, ["oyun-gelistirme"], enabled=True).interest_tags == []


def test_max_projects_truncates(sample_profile):
    result = tailor(sample_profile, ["veri-bilimi", "yapay-zeka"], enabled=True, max_projects=2)
    assert result.project_ids == ["proj_3", "proj_2"]

    result = tailor(sample_profile, ["yapay-zeka"], enabled=True, max_projects=0)
    assert result.project_ids == []


def test_negative_max_projects_rejected(sample_profile):
    with pytest.raises(ValueError):
        tailor(sample_profile, ["yapay-zeka"], enabled=True, max_projects=-1)


def test_experience_and_education_order_never_changes(sample_profile):
    result = tailor(sample_profile, ["elektronik", "yapay-zeka"], enabled=True)

    assert result.experience_ids == ["exp_1", "exp_2"]
    assert result.education_ids == ["edu_1", "edu_2"]
    assert result.achievement_ids == ["ach_1", "ach_2"]


@pytest.mark.parametrize(
    "tags, enabled, max_projects",
    [
        (["yapay-zeka"], False, None),
        ([], True, 2),
        (["mobil"], True, 1),
        (["veri-bilimi", "yapay-zeka"], True, None),
        (["veri-bilimi", "yapay-zeka"], True, 2),
    ],
)
def test_tailor_output_passes_validator(sample_profile, tags, enabled, max_projects):
    result = tailor(sample_profile, tags, enabled=enabled, max_projects=max_projects)
    validate(result, sample_profile, max_projects=max_projects)
