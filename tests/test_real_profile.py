"""Gerçek profil dosyası varsa şemaya uyduğunu doğrular (yoksa test atlanır)."""
from pathlib import Path

import pytest

from src.cv_builder.profile_loader import load_profile
from src.cv_builder.tag_matcher import tailor
from src.cv_builder.validator import validate

PROFILE_PATH = Path("data/master_profile.json")

pytestmark = pytest.mark.skipif(
    not PROFILE_PATH.exists(), reason="Gerçek profil yok (git'e girmez)"
)


@pytest.fixture
def real_profile():
    return load_profile(PROFILE_PATH)


def test_real_profile_loads(real_profile):
    assert real_profile.personal_info.name
    assert "default" in real_profile.summaries


def test_real_profile_has_intro(real_profile):
    """Mail gövdesindeki tanıtım cümlesi profilde dolu olmalı."""
    assert real_profile.personal_info.intro


def test_languages_are_not_skills(real_profile):
    """Languages live in their own section, never among skills."""
    skill_names = {skill.name for skill in real_profile.skills}
    for language in real_profile.languages:
        assert language.name not in skill_names


def test_languages_never_reordered(real_profile):
    """Languages carry no id, so they cannot appear in tailoring output."""
    original = [(lang.name, lang.level) for lang in real_profile.languages]
    result = tailor(real_profile, ["ag-yonetimi"], enabled=True, max_projects=1)

    all_ids = (
        result.experience_ids
        + result.education_ids
        + result.project_ids
        + result.skill_ids
        + result.achievement_ids
    )
    for name, _ in original:
        assert name not in all_ids
    assert [(lang.name, lang.level) for lang in real_profile.languages] == original


def test_real_profile_tailoring_validates(real_profile):
    result = tailor(real_profile, ["ag-yonetimi", "sistem-yonetimi"], enabled=True, max_projects=1)
    validate(result, real_profile, max_projects=1)
    assert result.matched_tags
