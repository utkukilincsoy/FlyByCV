"""data/master_profile.json'ı yükleyip data/profil_kontrol.md raporunu üretir."""
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.cv_builder.profile_loader import MasterProfile, ProfileItem, load_profile  # noqa: E402

logger = logging.getLogger(__name__)

PROFILE_PATH = PROJECT_ROOT / "data" / "master_profile.json"
REPORT_PATH = PROJECT_ROOT / "data" / "profil_kontrol.md"


def format_date(value: str | None) -> str:
    return value if value else "-"


def format_end_date(value: str | None) -> str:
    return value if value else "Devam ediyor"


def format_tags(tags: list[str]) -> str:
    return ", ".join(tags) if tags else "-"


def escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def item_label(item: ProfileItem) -> str:
    for attr in ("company", "school", "name", "title"):
        value = getattr(item, attr, None)
        if value:
            return f"{item.id} ({value})"
    return item.id


def render_personal_info(profile: MasterProfile) -> str:
    info = profile.personal_info
    lines = [
        "## Kişisel Bilgiler",
        f"- **Ad:** {info.name}",
        f"- **E-posta:** {info.email}",
        f"- **Telefon:** {info.phone or '-'}",
        f"- **Konum:** {info.location or '-'}",
        f"- **LinkedIn:** {info.linkedin or '-'}",
    ]
    return "\n".join(lines)


def render_summaries(profile: MasterProfile) -> str:
    lines = ["## Özetler"]
    for key, text in profile.summaries.items():
        lines.append(f"- **{key}:** {escape_cell(text)}")
    return "\n".join(lines)


def render_experience(profile: MasterProfile) -> str:
    """Full detail, not a preview: this report is compared word by word against the CV."""
    lines = ["## Deneyimler"]
    for exp in profile.experience:
        header = exp.company
        if exp.department:
            header += f" — {exp.department}"
        lines.append(f"### {exp.id}: {header}")
        title = exp.title
        if exp.note:
            title += f" ({exp.note})"
        lines.append(f"- **Unvan:** {title}")
        lines.append(
            f"- **Tarih:** {format_date(exp.start_date)} – {format_end_date(exp.end_date)}"
        )
        if exp.topics:
            lines.append(f"- **Konular:** {', '.join(exp.topics)}")
        lines.append(f"- **Etiketler:** {format_tags(exp.tags)}")
        lines.append("- **Maddeler:**")
        for bullet in exp.bullets:
            lines.append(f"  - {bullet}")
    return "\n".join(lines)


def render_education(profile: MasterProfile) -> str:
    lines = ["## Eğitim"]
    for edu in profile.education:
        lines.append(f"### {edu.id}: {edu.school}")
        lines.append(f"- **Bölüm:** {edu.field}")
        degree = edu.degree
        if edu.gpa:
            visible = "görünür" if edu.show_gpa else "gizli"
            degree += f" — GNO: {edu.gpa} ({visible})"
        lines.append(f"- **Derece:** {degree}")
        end = format_end_date(edu.end_date)
        if edu.expected:
            end += " (Beklenen)"
        lines.append(f"- **Tarih:** {format_date(edu.start_date)} – {end}")
        lines.append(f"- **Etiketler:** {format_tags(edu.tags)}")
    return "\n".join(lines)


def render_projects(profile: MasterProfile) -> str:
    lines = ["## Projeler"]
    for proj in profile.projects:
        lines.append(f"### {proj.id}: {proj.name}")
        lines.append(f"- **Tarih:** {format_date(proj.date)}")
        lines.append(f"- **Teknolojiler:** {', '.join(proj.technologies) or '-'}")
        lines.append(f"- **Etiketler:** {format_tags(proj.tags)}")
        lines.append("- **Maddeler:**")
        for bullet in proj.bullets:
            lines.append(f"  - {bullet}")
    return "\n".join(lines)


def render_languages(profile: MasterProfile) -> str:
    lines = ["## Diller"]
    for language in profile.languages:
        lines.append(f"- {language.name} — {language.level}")
    return "\n".join(lines)


def render_skills_list(profile: MasterProfile) -> str:
    lines = ["## Yetenekler"]
    for skill in profile.skills:
        level = f" — {skill.level}" if skill.level else ""
        lines.append(f"- {skill.name}{level} ({format_tags(skill.tags)})")
    return "\n".join(lines)


def collect_tag_index(profile: MasterProfile) -> dict[str, list[str]]:
    tag_index: dict[str, list[str]] = {}
    all_items: list[ProfileItem] = (
        profile.experience
        + profile.education
        + profile.projects
        + profile.skills
        + profile.achievements
    )
    for item in all_items:
        for tag in item.tags:
            tag_index.setdefault(tag, []).append(item_label(item))
    return tag_index


def render_tag_summary(profile: MasterProfile) -> str:
    lines = ["## Etiket Özeti"]
    tag_index = collect_tag_index(profile)
    for tag in sorted(tag_index):
        lines.append(f"### {tag}")
        for label in tag_index[tag]:
            lines.append(f"- {label}")
    return "\n".join(lines)


def build_report(profile: MasterProfile) -> str:
    sections = [
        render_personal_info(profile),
        render_summaries(profile),
        render_experience(profile),
        render_education(profile),
        render_projects(profile),
        render_skills_list(profile),
        render_languages(profile),
        render_tag_summary(profile),
    ]
    return "\n\n".join(sections) + "\n"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    profile = load_profile(PROFILE_PATH)
    report = build_report(profile)
    REPORT_PATH.write_text(report, encoding="utf-8")
    logger.info(f"Rapor yazıldı: {REPORT_PATH}")


if __name__ == "__main__":
    main()
