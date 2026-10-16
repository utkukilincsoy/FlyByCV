from dataclasses import replace

import pytest

from src.cv_builder.tag_matcher import TailoredCV, tailor
from src.cv_builder.validator import ValidationError, validate


@pytest.fixture
def base(sample_profile) -> TailoredCV:
    """A valid, untailored result to mutate in each test."""
    return tailor(sample_profile, [], enabled=False)


def test_valid_output_passes(sample_profile, base):
    validate(base, sample_profile)


def test_unknown_id(sample_profile, base):
    bad = replace(base, project_ids=base.project_ids + ["proj_999"])
    with pytest.raises(ValidationError, match="proj_999"):
        validate(bad, sample_profile)


def test_id_in_wrong_section(sample_profile, base):
    bad = replace(base, project_ids=["skill_1"])
    with pytest.raises(ValidationError, match="skill_1"):
        validate(bad, sample_profile)


def test_duplicate_id(sample_profile, base):
    bad = replace(base, skill_ids=base.skill_ids + ["skill_1"])
    with pytest.raises(ValidationError, match="tekrar"):
        validate(bad, sample_profile)


def test_experience_order_changed(sample_profile, base):
    bad = replace(base, experience_ids=["exp_2", "exp_1"])
    with pytest.raises(ValidationError, match="deneyim: sıra"):
        validate(bad, sample_profile)


def test_education_order_changed(sample_profile, base):
    bad = replace(base, education_ids=["edu_2", "edu_1"])
    with pytest.raises(ValidationError, match="eğitim: sıra"):
        validate(bad, sample_profile)


def test_experience_dropped(sample_profile, base):
    bad = replace(base, experience_ids=["exp_1"])
    with pytest.raises(ValidationError, match="deneyim: düşürülmüş.*exp_2"):
        validate(bad, sample_profile)


def test_education_dropped(sample_profile, base):
    bad = replace(base, education_ids=["edu_2"])
    with pytest.raises(ValidationError, match="eğitim: düşürülmüş.*edu_1"):
        validate(bad, sample_profile)


def test_unknown_summary_key(sample_profile, base):
    bad = replace(base, summary_key="siber-guvenlik")
    with pytest.raises(ValidationError, match="Özet"):
        validate(bad, sample_profile)


def test_skill_missing(sample_profile, base):
    bad = replace(base, skill_ids=["skill_1", "skill_2"])
    with pytest.raises(ValidationError, match="yetenekler: düşürülmüş.*skill_3"):
        validate(bad, sample_profile)


def test_skill_reordered_passes(sample_profile, base):
    validate(replace(base, skill_ids=["skill_3", "skill_1", "skill_2"]), sample_profile)


def test_achievement_order_changed(sample_profile, base):
    bad = replace(base, achievement_ids=["ach_2", "ach_1"])
    with pytest.raises(ValidationError, match="başarılar: sıra"):
        validate(bad, sample_profile)


def test_achievement_dropped(sample_profile, base):
    bad = replace(base, achievement_ids=["ach_1"])
    with pytest.raises(ValidationError, match="başarılar: düşürülmüş"):
        validate(bad, sample_profile)


def test_projects_over_max(sample_profile, base):
    bad = replace(base, project_ids=["proj_3", "proj_2", "proj_1"])
    with pytest.raises(ValidationError, match="en fazla 2"):
        validate(bad, sample_profile, max_projects=2)


def test_missing_projects_pass(sample_profile, base):
    partial = replace(base, project_ids=["proj_3"])
    validate(partial, sample_profile)
    validate(partial, sample_profile, max_projects=2)


def test_full_project_list_passes_even_over_max(sample_profile, base):
    # Untailored output (all projects) must not be rejected when max_projects is set.
    validate(base, sample_profile, max_projects=2)
    reordered = replace(base, project_ids=["proj_4", "proj_3", "proj_2", "proj_1"])
    validate(reordered, sample_profile, max_projects=2)
