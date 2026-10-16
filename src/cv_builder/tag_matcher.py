"""Şirket etiketlerine göre profil maddelerini sıralar (LLM'siz, saf mantık).

Çıktı yalnızca ID listeleri ve özet anahtarıdır; hiçbir metin üretilmez.
"""
import logging
from dataclasses import dataclass
from typing import Optional

from src.cv_builder.profile_loader import (
    DEFAULT_SUMMARY_KEY,
    MasterProfile,
    ProfileItem,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TailoredCV:
    summary_key: str
    experience_ids: list[str]
    education_ids: list[str]
    project_ids: list[str]
    skill_ids: list[str]
    achievement_ids: list[str]
    #: Şirket etiketlerinden profille (deneyim/eğitim/proje/yetenek/başarı/özet) eşleşenler.
    #: CV sıralaması (proje/yetenek) ve `Application.matched_tags` raporlaması için kullanılır.
    matched_tags: list[str]
    #: `matched_tags`'in bir ALT KÜMESİ: mail cümlesinde ("... ilgi alanlarımla örtüştüğü
    #: için") kullanılabilecek etiketler. Yalnızca deneyim, proje, eğitim ve özet
    #: etiketlerinden gelir — yetenek (skill) ve başarı (achievement) etiketleri KATILMAZ.
    #: Gerekçe: bir programlama dili/aracı bilmek "ilgi alanım bu" demek değildir, o bilgi
    #: zaten CV'de yetenek sıralamasıyla (`skill_ids`) gösteriliyor. bkz. `_find_interest_tags`.
    interest_tags: list[str]


def _ids(items: list[ProfileItem]) -> list[str]:
    return [item.id for item in items]


def _normalize_tags(tags: list[str]) -> list[str]:
    """Strip, drop empties and duplicates while keeping order."""
    result: list[str] = []
    for tag in tags:
        cleaned = tag.strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _match_count(item: ProfileItem, tags: set[str]) -> int:
    return len(set(item.tags) & tags)


def _pick_summary(profile: MasterProfile, company_tags: list[str]) -> str:
    for tag in company_tags:
        if tag in profile.summaries:
            return tag
    return DEFAULT_SUMMARY_KEY


def _find_matched_tags(profile: MasterProfile, company_tags: list[str]) -> list[str]:
    profile_tags: set[str] = set(profile.summaries) - {DEFAULT_SUMMARY_KEY}
    all_items: list[ProfileItem] = (
        profile.experience
        + profile.education
        + profile.projects
        + profile.skills
        + profile.achievements
    )
    for item in all_items:
        profile_tags.update(item.tags)
    return [tag for tag in company_tags if tag in profile_tags]


def _find_interest_tags(profile: MasterProfile, company_tags: list[str]) -> list[str]:
    """`matched_tags`'in mail cümlesinde kullanılabilecek alt kümesi.

    Yalnızca deneyim, proje, eğitim ve özet (summaries) etiketlerine bakar — `_find_matched_tags`
    ile aynı mantık, sadece yetenek ve başarı maddeleri havuza KATILMAZ.
    """
    profile_tags: set[str] = set(profile.summaries) - {DEFAULT_SUMMARY_KEY}
    interest_items: list[ProfileItem] = profile.experience + profile.education + profile.projects
    for item in interest_items:
        profile_tags.update(item.tags)
    return [tag for tag in company_tags if tag in profile_tags]


def _untailored(profile: MasterProfile) -> TailoredCV:
    return TailoredCV(
        summary_key=DEFAULT_SUMMARY_KEY,
        experience_ids=_ids(profile.experience),
        education_ids=_ids(profile.education),
        project_ids=_ids(profile.projects),
        skill_ids=_ids(profile.skills),
        achievement_ids=_ids(profile.achievements),
        matched_tags=[],
        interest_tags=[],
    )


def _rank_projects(
    profile: MasterProfile, tags: set[str], max_projects: Optional[int]
) -> list[str]:
    # sorted() is stable: equal match counts keep their original order.
    ranked = sorted(profile.projects, key=lambda p: -_match_count(p, tags))
    if max_projects is not None:
        ranked = ranked[:max_projects]
    return _ids(ranked)


def _rank_skills(profile: MasterProfile, tags: set[str]) -> list[str]:
    matched = [s for s in profile.skills if _match_count(s, tags) > 0]
    rest = [s for s in profile.skills if _match_count(s, tags) == 0]
    return _ids(matched + rest)


def tailor(
    profile: MasterProfile,
    company_tags: list[str],
    enabled: bool,
    max_projects: Optional[int] = None,
) -> TailoredCV:
    """Order profile item IDs for a company. Experience/education order never changes."""
    if max_projects is not None and max_projects < 0:
        raise ValueError("max_projects negatif olamaz")

    tags = _normalize_tags(company_tags)
    if not enabled or not tags:
        return _untailored(profile)

    matched_tags = _find_matched_tags(profile, tags)
    if not matched_tags:
        logger.info("Şirket etiketlerinden hiçbiri profille eşleşmedi, ana CV kullanılıyor")
        return _untailored(profile)

    tag_set = set(matched_tags)
    return TailoredCV(
        summary_key=_pick_summary(profile, tags),
        experience_ids=_ids(profile.experience),
        education_ids=_ids(profile.education),
        project_ids=_rank_projects(profile, tag_set, max_projects),
        skill_ids=_rank_skills(profile, tag_set),
        achievement_ids=_ids(profile.achievements),
        matched_tags=matched_tags,
        interest_tags=_find_interest_tags(profile, tags),
    )
