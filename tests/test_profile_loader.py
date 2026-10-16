import json
import pytest
from pathlib import Path
from pydantic import ValidationError

from src.cv_builder.profile_loader import (
    MasterProfile,
    PersonalInfo,
    Experience,
    Education,
    Language,
    Project,
    Skill,
    Achievement,
    load_profile,
)

SUMMARIES = {"default": "Varsayılan özet"}
PERSONAL = PersonalInfo(name="Test", email="test@example.com")


def test_load_example_profile(tmp_path):
    """Test loading the example profile."""
    profile_path = Path("data/master_profile.example.json")
    profile = load_profile(profile_path)

    assert profile.personal_info.name == "Gülşah Çiçek"
    assert profile.personal_info.email == "gulsah.cicek@example.com"
    assert len(profile.experience) == 1
    assert len(profile.education) == 1
    assert len(profile.projects) == 1
    assert len(profile.skills) == 3
    assert len(profile.achievements) == 1
    assert len(profile.languages) == 2
    assert "default" in profile.summaries


def test_summaries_default_required():
    with pytest.raises(ValidationError, match="default"):
        MasterProfile(personal_info=PERSONAL, summaries={"yapay-zeka": "YZ özeti"})


def test_summaries_missing_rejected():
    with pytest.raises(ValidationError):
        MasterProfile(personal_info=PERSONAL)


def test_summaries_invalid_key_rejected():
    with pytest.raises(ValidationError, match="uydurma-anahtar"):
        MasterProfile(
            personal_info=PERSONAL,
            summaries={"default": "Özet", "uydurma-anahtar": "Metin"},
        )


def test_summaries_empty_tag_text_rejected():
    with pytest.raises(ValidationError, match="Boş özet"):
        MasterProfile(
            personal_info=PERSONAL, summaries={"default": "Özet", "backend": "   "}
        )


def test_summaries_empty_default_accepted():
    """İlk açılış sihirbazı boş genel özetli bir profil yazar (Adım 10.2b); uydurma özet yok."""
    profile = MasterProfile(personal_info=PERSONAL, summaries={"default": ""})
    assert profile.summaries == {"default": ""}


def test_summaries_valid_tag_keys_accepted():
    profile = MasterProfile(
        personal_info=PERSONAL,
        summaries={"default": "Özet", "yapay-zeka": "YZ", "backend": "Backend"},
    )
    assert set(profile.summaries) == {"default", "yapay-zeka", "backend"}


def test_personal_info_required_fields():
    """Test that PersonalInfo requires name and email."""
    with pytest.raises(ValidationError):
        PersonalInfo(name="Test")

    with pytest.raises(ValidationError):
        PersonalInfo(email="test@example.com")

    info = PersonalInfo(name="Test User", email="test@example.com")
    assert info.name == "Test User"


def test_profile_item_id_validation():
    """Test that ProfileItem validates non-empty ids."""
    with pytest.raises(ValidationError):
        Skill(id="", tags=["backend"], name="Test")

    with pytest.raises(ValidationError):
        Skill(id="   ", tags=["backend"], name="Test")

    skill = Skill(id="skill_001", tags=["backend"], name="Test")
    assert skill.id == "skill_001"


def test_profile_item_tags_filtering():
    """Test that empty tags are filtered out."""
    skill = Skill(id="skill_001", tags=["backend", "  ", ""], name="Test")
    assert skill.tags == ["backend"]


def test_profile_item_invalid_tag_rejected():
    """Test that a tag outside TAG_VOCABULARY is rejected with the tag name in the error."""
    with pytest.raises(ValidationError) as exc_info:
        Skill(id="skill_001", tags=["backend", "uydurma-etiket"], name="Test")
    assert "uydurma-etiket" in str(exc_info.value)


def test_profile_item_valid_tags_from_vocabulary():
    """Test that all TAG_VOCABULARY entries are individually accepted."""
    from src.core.tags import TAG_VOCABULARY

    for tag in TAG_VOCABULARY:
        skill = Skill(id="skill_001", tags=[tag], name="Test")
        assert skill.tags == [tag]


def test_duplicate_ids_detection():
    """Test that MasterProfile detects duplicate IDs."""
    with pytest.raises(ValidationError) as exc_info:
        MasterProfile(
            personal_info=PersonalInfo(name="Test", email="test@example.com"),
            summaries=SUMMARIES,
            skills=[
                Skill(id="skill_001", tags=["backend"], name="Skill 1"),
                Skill(id="skill_001", tags=["backend"], name="Skill 2"),
            ],
        )
    assert "Duplicate IDs" in str(exc_info.value)


def test_experience_required_fields():
    """Test Experience requires all key fields."""
    experience = Experience(
        id="exp_001",
        tags=["backend"],
        company="TechCorp",
        title="Developer",
        start_date="2021-01",
        bullets=["Test work"],
    )
    assert experience.company == "TechCorp"
    assert experience.title == "Developer"
    assert experience.department is None
    assert experience.note is None
    assert experience.topics == []
    assert experience.end_date is None  # "Devam ediyor"


def test_experience_optional_fields():
    """Test Experience department, note and topics."""
    experience = Experience(
        id="exp_001",
        tags=["ag-yonetimi"],
        company="TechCorp",
        title="Stajyer",
        department="Teknik",
        note="20 Gün Zorunlu",
        topics=["CCNA", "OSPF"],
        start_date="2026-01",
        end_date="2026-09",
        bullets=["Madde 1", "Madde 2"],
    )
    assert experience.department == "Teknik"
    assert experience.note == "20 Gün Zorunlu"
    assert experience.topics == ["CCNA", "OSPF"]
    assert len(experience.bullets) == 2


def test_education_required_fields():
    """Test Education requires all key fields."""
    education = Education(
        id="edu_001",
        tags=["backend"],
        school="Test University",
        field="Computer Science",
        degree="Lisans",
        start_date="2017",
    )
    assert education.school == "Test University"
    assert education.gpa is None
    assert education.show_gpa is True
    assert education.expected is False


def test_education_gpa_and_expected():
    """Test Education gpa visibility and expected graduation."""
    education = Education(
        id="edu_001",
        tags=["backend"],
        school="Test University",
        field="Computer Science",
        degree="Lisans",
        gpa="2.50/4.00",
        show_gpa=False,
        start_date="2023",
        end_date="2028",
        expected=True,
    )
    assert education.gpa == "2.50/4.00"
    assert education.show_gpa is False
    assert education.expected is True


@pytest.mark.parametrize("value", ["2023", "2023-01", "2023-12"])
def test_valid_date_formats(value):
    """Test that YYYY and YYYY-MM are accepted."""
    project = Project(id="p", tags=[], name="P", date=value, technologies=[])
    assert project.date == value


@pytest.mark.parametrize("value", ["2023-01-01", "2023-13", "23", "2023-1", "Ocak 2023"])
def test_invalid_date_formats_rejected(value):
    """Test that day-level and malformed dates are rejected."""
    with pytest.raises(ValidationError, match="YYYY"):
        Project(id="p", tags=[], name="P", date=value, technologies=[])


def test_project_fields():
    """Test Project technologies, date and bullets."""
    project = Project(
        id="proj_001",
        tags=["yapay-zeka"],
        name="Test Project",
        date="2026-09",
        bullets=["Madde 1"],
        technologies=["Python", "Claude API"],
    )
    assert project.technologies == ["Python", "Claude API"]
    assert project.date == "2026-09"
    assert project.bullets == ["Madde 1"]


def test_project_date_optional():
    """Test that a project without a date is allowed."""
    project = Project(id="proj_001", tags=[], name="Test", technologies=[])
    assert project.date is None


def test_personal_info_linkedin():
    """Test PersonalInfo linkedin field."""
    info = PersonalInfo(name="Test", email="t@example.com", linkedin="linkedin.com/in/test")
    assert info.linkedin == "linkedin.com/in/test"
    assert PersonalInfo(name="Test", email="t@example.com").linkedin is None


def test_language_model():
    """Test Language has no id or tags, so it never joins ordering."""
    language = Language(name="Türkçe", level="Anadil")
    assert language.name == "Türkçe"
    assert language.level == "Anadil"
    assert not hasattr(language, "id")
    assert not hasattr(language, "tags")


def test_languages_default_empty():
    """Test that languages defaults to an empty list."""
    profile = MasterProfile(personal_info=PERSONAL, summaries=SUMMARIES)
    assert profile.languages == []


def test_skill_optional_level():
    """Test Skill with and without level."""
    skill_with_level = Skill(
        id="skill_001", tags=["backend"], name="Python", level="İleri"
    )
    assert skill_with_level.level == "İleri"

    skill_without_level = Skill(id="skill_002", tags=["backend"], name="Rust")
    assert skill_without_level.level is None


def test_master_profile_complete():
    """Test creating a complete MasterProfile."""
    profile = MasterProfile(
        personal_info=PersonalInfo(
            name="Test User",
            email="test@example.com",
            location="İstanbul",
        ),
        summaries=SUMMARIES,
        experience=[
            Experience(
                id="exp_001",
                tags=["backend"],
                company="Company A",
                title="Developer",
                start_date="2020",
                bullets=["Work description"],
            )
        ],
        education=[
            Education(
                id="edu_001",
                tags=["backend"],
                school="University",
                field="Computer Science",
                degree="Lisans",
                start_date="2016",
                end_date="2020",
            )
        ],
        skills=[
            Skill(id="skill_001", tags=["backend"], name="Python", level="İleri"),
            Skill(id="skill_002", tags=["backend"], name="SQL"),
        ],
        languages=[Language(name="Türkçe", level="Anadil")],
    )
    assert profile.personal_info.name == "Test User"
    assert len(profile.experience) == 1
    assert len(profile.education) == 1
    assert len(profile.skills) == 2
    assert len(profile.languages) == 1


def test_load_nonexistent_file():
    """Test loading a nonexistent profile file."""
    with pytest.raises(FileNotFoundError):
        load_profile(Path("nonexistent/path.json"))


def test_load_malformed_json(tmp_path):
    """Test loading a file with invalid JSON."""
    bad_json_file = tmp_path / "bad.json"
    bad_json_file.write_text("{invalid json}")

    with pytest.raises(ValueError) as exc_info:
        load_profile(bad_json_file)
    assert "Invalid JSON" in str(exc_info.value)


def test_load_invalid_profile(tmp_path):
    """Test loading a file with valid JSON but invalid profile schema."""
    invalid_file = tmp_path / "invalid.json"
    invalid_file.write_text('{"personal_info": {"name": "Test"}}')

    with pytest.raises(ValidationError):
        load_profile(invalid_file)
