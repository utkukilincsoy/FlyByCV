"""TailoredCV + MasterProfile -> HTML -> A4 PDF.

Metinler profilden birebir okunur; burada hiçbir CV cümlesi üretilmez.
Şablonlar: templates/cv.html, templates/cv.css (CLAUDE.md'deki ATS kuralları).
"""
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, TypeVar
from urllib.parse import quote

from playwright.sync_api import sync_playwright

from src.core.models import normalize_domain
from src.cv_builder.profile_loader import (
    Education,
    Experience,
    MasterProfile,
    Project,
    ProfileItem,
    Skill,
)
from src.cv_builder.tag_matcher import TailoredCV
from src.cv_builder.templating import environment
from src.cv_builder.validator import validate

logger = logging.getLogger(__name__)

ItemT = TypeVar("ItemT", bound=ProfileItem)

CV_TEMPLATE = "cv.html"

TURKISH_MONTHS: tuple[str, ...] = (
    "Ocak",
    "Şubat",
    "Mart",
    "Nisan",
    "Mayıs",
    "Haziran",
    "Temmuz",
    "Ağustos",
    "Eylül",
    "Ekim",
    "Kasım",
    "Aralık",
)

ONGOING_LABEL = "Devam ediyor"
EXPECTED_LABEL = "(Beklenen)"
DATE_SEPARATOR = " – "
DETAIL_SEPARATOR = " – "
GENERAL_SUFFIX = "genel"

# ASCII transliteration for file names; NFKD alone turns "İ" into "I" + a combining dot.
_ASCII_MAP = str.maketrans(
    {
        "ğ": "g", "Ğ": "G",
        "ş": "s", "Ş": "S",
        "ı": "i", "İ": "I",
        "ö": "o", "Ö": "O",
        "ç": "c", "Ç": "C",
        "ü": "u", "Ü": "U",
    }
)


@dataclass(frozen=True)
class SkillGroup:
    """One line of the skills section: a category heading and its skills."""

    category: Optional[str]
    items: str


def format_date(value: Optional[str]) -> str:
    """'2026-01' -> 'Ocak 2026', '2020' -> '2020', None -> ''."""
    if not value:
        return ""
    parts = value.split("-")
    year = parts[0]
    if len(parts) == 1:
        return year
    return f"{TURKISH_MONTHS[int(parts[1]) - 1]} {year}"


def format_range(
    start: Optional[str], end: Optional[str], expected: bool = False
) -> str:
    """'Ocak 2026 – Eylül 2026', bitiş yoksa '… – Devam ediyor'."""
    start_text = format_date(start)
    if end:
        end_text = format_date(end)
        if expected:
            end_text = f"{end_text} {EXPECTED_LABEL}"
    else:
        end_text = ONGOING_LABEL
    if not start_text:
        return end_text
    return f"{start_text}{DATE_SEPARATOR}{end_text}"


def to_ascii(text: str) -> str:
    """Türkçe karakterleri ASCII karşılıklarına çevirir (dosya adları için)."""
    translated = text.translate(_ASCII_MAP)
    decomposed = unicodedata.normalize("NFKD", translated)
    return decomposed.encode("ascii", "ignore").decode("ascii")


def _sanitize_filename_part(text: str, keep_dots: bool = False) -> str:
    """Keep letters, digits, '_' and '-' (plus '.' for domains)."""
    allowed = r"\w.-" if keep_dots else r"\w-"
    cleaned = re.sub(rf"[^{allowed}]", "", to_ascii(text).replace(" ", "_"), flags=re.ASCII)
    return cleaned.strip("._-")


def cv_filename(profile: MasterProfile, domain: Optional[str] = None) -> str:
    """'Gulsah_Cicek_CV_firma.com.pdf' — ana CV için sonek '_genel'."""
    name_part = _sanitize_filename_part(profile.personal_info.name) or "CV"
    suffix = ""
    if domain:
        suffix = _sanitize_filename_part(normalize_domain(domain), keep_dots=True)
    return f"{name_part}_CV_{suffix or GENERAL_SUFFIX}.pdf"


def cv_attachment_name(profile: MasterProfile) -> str:
    """Maile eklenecek PDF'in adı: 'Gulsah_Cicek_CV.pdf'.

    Şirket adı veya alan adı içermez: her şirkete aynı adla gider, toplu gönderim
    izlenimi vermesin. Dosyalar bunun yerine alan adı adlı klasörlere ayrılır.
    """
    name_part = _sanitize_filename_part(profile.personal_info.name) or "CV"
    return f"{name_part}_CV.pdf"


def company_folder_name(domain: str) -> str:
    """Şirketin çıktı klasörünün adı: 'https://www.Firma.com/iletisim' -> 'firma.com'."""
    return _sanitize_filename_part(normalize_domain(domain), keep_dots=True)


def _order(items: list[ItemT], ids: list[str]) -> list[ItemT]:
    """Reorder profile items by the given ID list; unknown IDs are skipped."""
    by_id = {item.id: item for item in items}
    return [by_id[id_] for id_ in ids if id_ in by_id]


def _join(parts: list[str], separator: str = DETAIL_SEPARATOR) -> str:
    return separator.join(part for part in parts if part)


def _linkedin_url(value: str) -> str:
    url = value if value.startswith(("http://", "https://")) else f"https://{value}"
    # Görünen metin okunur (ı, ç vb.) kalsın; href'teki Türkçe karakterler
    # PDF görüntüleyicilerin geçerli bir bağlantı olarak tanıması için
    # percent-encoded olmalı (örn. ı -> %C4%B1, ç -> %C3%A7).
    return quote(url, safe=":/.-_~")


def _contacts(profile: MasterProfile) -> list[dict[str, Optional[str]]]:
    info = profile.personal_info
    contacts: list[dict[str, Optional[str]]] = []
    if info.location:
        contacts.append({"text": info.location, "href": None})
    if info.phone:
        contacts.append({"text": info.phone, "href": None})
    contacts.append({"text": info.email, "href": f"mailto:{info.email}"})
    if info.linkedin:
        contacts.append({"text": info.linkedin, "href": _linkedin_url(info.linkedin)})
    return contacts


def _education_context(items: list[Education]) -> list[dict[str, str]]:
    result = []
    for edu in items:
        gpa = f"GNO: {edu.gpa}" if edu.gpa and edu.show_gpa else ""
        result.append(
            {
                "school": edu.school,
                "detail": _join([f"{edu.field} {edu.degree}".strip(), gpa]),
                "dates": format_range(edu.start_date, edu.end_date, edu.expected),
            }
        )
    return result


def _experience_context(items: list[Experience]) -> list[dict[str, object]]:
    result = []
    for exp in items:
        title = f"{exp.title} ({exp.note})" if exp.note else exp.title
        result.append(
            {
                "company": exp.company,
                "department": exp.department or "",
                "detail": _join([title, ", ".join(exp.topics)]),
                "dates": format_range(exp.start_date, exp.end_date),
                "bullets": exp.bullets,
            }
        )
    return result


def _project_context(items: list[Project]) -> list[dict[str, object]]:
    return [
        {
            "name": project.name,
            "detail": ", ".join(project.technologies),
            "dates": format_date(project.date),
            "bullets": project.bullets,
        }
        for project in items
    ]


def _skill_groups(skills: list[Skill]) -> list[SkillGroup]:
    """Group skills by category, keeping the given order.

    A dict keeps insertion order, so a category appears where its first
    skill appears — after tailoring the most relevant category comes first.
    """
    grouped: dict[Optional[str], list[str]] = {}
    for skill in skills:
        label = f"{skill.name} ({skill.level})" if skill.level else skill.name
        grouped.setdefault(skill.category, []).append(label)
    return [SkillGroup(category, ", ".join(names)) for category, names in grouped.items()]


def _build_context(profile: MasterProfile, tailored: TailoredCV) -> dict[str, object]:
    return {
        "title": f"{profile.personal_info.name} – CV",
        "name": profile.personal_info.name,
        "contacts": _contacts(profile),
        "summary": profile.summaries[tailored.summary_key],
        "education": _education_context(
            _order(profile.education, tailored.education_ids)
        ),
        "experience": _experience_context(
            _order(profile.experience, tailored.experience_ids)
        ),
        "projects": _project_context(_order(profile.projects, tailored.project_ids)),
        "achievements": _order(profile.achievements, tailored.achievement_ids),
        "skill_groups": _skill_groups(_order(profile.skills, tailored.skill_ids)),
        "languages": ", ".join(
            f"{language.name} ({language.level})" for language in profile.languages
        ),
    }


def render_html(profile: MasterProfile, tailored: TailoredCV) -> str:
    """Doğrulanmış sıralamadan CV HTML'i üretir (metin profilden birebir gelir)."""
    validate(tailored, profile)
    template = environment().get_template(CV_TEMPLATE)
    return template.render(**_build_context(profile, tailored))


def render_pdf(profile: MasterProfile, tailored: TailoredCV, out_path: Path) -> Path:
    """CV'yi A4 PDF olarak yazar ve dosya yolunu döndürür.

    Raises:
        ValidationError: Sıralama ana profille tutarsızsa (PDF üretilmez).
    """
    html = render_html(profile, tailored)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            page.emulate_media(media="print")
            page.pdf(
                path=str(out_path),
                format="A4",
                print_background=True,
                display_header_footer=False,
                margin={
                    "top": "1.3cm",
                    "bottom": "1.3cm",
                    "left": "1.5cm",
                    "right": "1.5cm",
                },
            )
        finally:
            browser.close()

    logger.info(f"CV PDF üretildi: {out_path}")
    return out_path
