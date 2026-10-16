"""TailoredCV çıktısını master profile karşı doğrular.

Hem LLM'siz tag_matcher hem de ileride LLM'den gelen sıralamalar buradan geçer.
Geçmezse çağıran taraf çıktıyı atıp ana CV'yi kullanmalıdır.
"""
import logging
from typing import Optional

from src.cv_builder.profile_loader import MasterProfile, ProfileItem
from src.cv_builder.tag_matcher import TailoredCV

logger = logging.getLogger(__name__)


class ValidationError(ValueError):
    """Özelleştirilmiş CV ana profille tutarsız."""


def _ids(items: list[ProfileItem]) -> list[str]:
    return [item.id for item in items]


def _sections(tailored: TailoredCV, profile: MasterProfile) -> dict[str, tuple[list[str], list[str]]]:
    """Section name -> (tailored IDs, original IDs)."""
    return {
        "deneyim": (tailored.experience_ids, _ids(profile.experience)),
        "eğitim": (tailored.education_ids, _ids(profile.education)),
        "projeler": (tailored.project_ids, _ids(profile.projects)),
        "yetenekler": (tailored.skill_ids, _ids(profile.skills)),
        "başarılar": (tailored.achievement_ids, _ids(profile.achievements)),
    }


def _check_ids_exist(name: str, ids: list[str], original: list[str]) -> None:
    unknown = [id_ for id_ in ids if id_ not in original]
    if unknown:
        raise ValidationError(f"{name}: profilde bu bölümde olmayan ID(ler): {unknown}")


def _check_no_duplicates(name: str, ids: list[str]) -> None:
    duplicates = sorted({id_ for id_ in ids if ids.count(id_) > 1})
    if duplicates:
        raise ValidationError(f"{name}: tekrar eden ID(ler): {duplicates}")


def _check_none_dropped(name: str, ids: list[str], original: list[str]) -> None:
    missing = [id_ for id_ in original if id_ not in ids]
    if missing:
        raise ValidationError(f"{name}: düşürülmüş madde(ler): {missing}")


def _check_order_unchanged(name: str, ids: list[str], original: list[str]) -> None:
    _check_none_dropped(name, ids, original)
    if ids != original:
        raise ValidationError(f"{name}: sıra değiştirilmiş, orijinal sıra korunmalı")


def _check_projects(ids: list[str], original: list[str], max_projects: Optional[int]) -> None:
    is_complete = set(ids) == set(original)
    if max_projects is not None and not is_complete and len(ids) > max_projects:
        raise ValidationError(
            f"projeler: {len(ids)} proje var, en fazla {max_projects} olmalı"
        )


def _check_summary(summary_key: str, profile: MasterProfile) -> None:
    if summary_key not in profile.summaries:
        raise ValidationError(f"Özet anahtarı profilde yok: '{summary_key}'")


def validate(
    tailored: TailoredCV,
    profile: MasterProfile,
    max_projects: Optional[int] = None,
) -> None:
    """Raise ValidationError if tailored is inconsistent with the profile."""
    sections = _sections(tailored, profile)
    for name, (ids, original) in sections.items():
        _check_ids_exist(name, ids, original)
        _check_no_duplicates(name, ids)

    _check_order_unchanged("deneyim", *sections["deneyim"])
    _check_order_unchanged("eğitim", *sections["eğitim"])
    _check_order_unchanged("başarılar", *sections["başarılar"])
    _check_none_dropped("yetenekler", *sections["yetenekler"])
    _check_projects(*sections["projeler"], max_projects)
    _check_summary(tailored.summary_key, profile)
    logger.debug("Özelleştirilmiş CV doğrulandı")
