import json
import logging
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, field_validator, model_validator

from src.core.tags import TAG_VOCABULARY

logger = logging.getLogger(__name__)

# Dates are "YYYY" or "YYYY-MM" — never a day, because CVs do not carry one.
YEAR_MONTH_PATTERN = re.compile(r"^\d{4}(-(0[1-9]|1[0-2]))?$")


def check_year_month(value: Optional[str]) -> Optional[str]:
    """Validate a 'YYYY' or 'YYYY-MM' date string. None means 'Devam ediyor'."""
    if value is None:
        return None
    cleaned = value.strip()
    if not YEAR_MONTH_PATTERN.match(cleaned):
        raise ValueError(f"Tarih 'YYYY' veya 'YYYY-MM' olmalı, gelen: '{value}'")
    return cleaned


class PersonalInfo(BaseModel):
    name: str
    email: str
    phone: Optional[str] = None
    location: Optional[str] = None
    linkedin: Optional[str] = None
    # Mailde "Ben {name}, {intro}." cümlesinde kullanılır; CV PDF'inde görünmez.
    intro: Optional[str] = None


class Language(BaseModel):
    """Not a ProfileItem: languages have no id/tags, so they are never reordered."""

    name: str
    level: str


class ProfileItem(BaseModel):
    id: str
    tags: list[str]

    @field_validator("id")
    @classmethod
    def id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("id cannot be empty")
        return v.strip()

    @field_validator("tags")
    @classmethod
    def tags_not_empty(cls, v: list[str]) -> list[str]:
        cleaned = [tag.strip() for tag in v if tag.strip()]
        invalid = [tag for tag in cleaned if tag not in TAG_VOCABULARY]
        if invalid:
            raise ValueError(
                f"Geçersiz etiket(ler): {invalid}. "
                f"Kabul edilen etiketler: {sorted(TAG_VOCABULARY)}"
            )
        return cleaned


class Experience(ProfileItem):
    company: str
    title: str
    department: Optional[str] = None
    note: Optional[str] = None
    topics: list[str] = []
    start_date: str
    end_date: Optional[str] = None
    bullets: list[str] = []

    @field_validator("start_date", "end_date")
    @classmethod
    def valid_dates(cls, v: Optional[str]) -> Optional[str]:
        return check_year_month(v)


class Education(ProfileItem):
    school: str
    field: str
    degree: str
    gpa: Optional[str] = None
    show_gpa: bool = True
    start_date: str
    end_date: Optional[str] = None
    expected: bool = False

    @field_validator("start_date", "end_date")
    @classmethod
    def valid_dates(cls, v: Optional[str]) -> Optional[str]:
        return check_year_month(v)


class Project(ProfileItem):
    name: str
    date: Optional[str] = None
    bullets: list[str] = []
    technologies: list[str]

    @field_validator("date")
    @classmethod
    def valid_date(cls, v: Optional[str]) -> Optional[str]:
        return check_year_month(v)


class Skill(ProfileItem):
    name: str
    level: Optional[str] = None
    # Groups skills under a heading in the CV ("Programlama Dilleri" etc.).
    category: Optional[str] = None


class Achievement(ProfileItem):
    title: str
    description: str


DEFAULT_SUMMARY_KEY = "default"


class MasterProfile(BaseModel):
    personal_info: PersonalInfo
    summaries: dict[str, str]
    experience: list[Experience] = []
    education: list[Education] = []
    projects: list[Project] = []
    skills: list[Skill] = []
    achievements: list[Achievement] = []
    languages: list[Language] = []

    @field_validator("summaries")
    @classmethod
    def check_summaries(cls, v: dict[str, str]) -> dict[str, str]:
        if DEFAULT_SUMMARY_KEY not in v:
            raise ValueError(f"summaries içinde '{DEFAULT_SUMMARY_KEY}' anahtarı zorunlu")
        invalid = [
            key for key in v if key != DEFAULT_SUMMARY_KEY and key not in TAG_VOCABULARY
        ]
        if invalid:
            raise ValueError(
                f"Geçersiz özet anahtar(lar)ı: {invalid}. "
                f"Kabul edilenler: '{DEFAULT_SUMMARY_KEY}' veya {sorted(TAG_VOCABULARY)}"
            )
        # Boş GENEL özete izin verilir: ilk açılış sihirbazı (Adım 10.2b) kullanıcı adına özet
        # UYDURMAZ (CLAUDE.md kural 1/9), şablon boş özeti hiç basmaz (`{% if summary %}`).
        # Etikete özel bir özet varsa boş olamaz.
        empty = [
            key for key, text in v.items() if key != DEFAULT_SUMMARY_KEY and not text.strip()
        ]
        if empty:
            raise ValueError(f"Boş özet metni: {empty}")
        return v

    @model_validator(mode="after")
    def check_unique_ids(self) -> "MasterProfile":
        all_items = (
            self.experience
            + self.education
            + self.projects
            + self.skills
            + self.achievements
        )
        ids = [item.id for item in all_items]
        duplicates = [id_ for id_ in ids if ids.count(id_) > 1]
        if duplicates:
            raise ValueError(f"Duplicate IDs found: {set(duplicates)}")
        return self


def load_profile(path: Path) -> MasterProfile:
    """Load and validate a profile from a JSON file.

    Args:
        path: Path to the master_profile.json file

    Returns:
        Validated MasterProfile instance

    Raises:
        FileNotFoundError: If the file does not exist
        ValueError: If the profile is invalid
        json.JSONDecodeError: If the JSON is malformed
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        profile = MasterProfile.model_validate(data)
        logger.info(f"Profile loaded successfully from {path}")
        return profile
    except FileNotFoundError:
        logger.error(f"Profile file not found: {path}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in profile file {path}: {e}")
        raise ValueError(f"Invalid JSON: {e}") from e
    except Exception as e:
        logger.error(f"Failed to load profile from {path}: {e}")
        raise
