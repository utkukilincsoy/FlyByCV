"""Profil sekmesinin veri katmanı (PySide6 İÇERMEZ) -- Faz 10 Adım 10.3d.

`src/ui/settings_data.py`/`company_data.py` ile aynı kalıp: id üretimi, liste özetleri,
metin<->liste dönüşümleri ve TAM profil doğrulaması burada saf Python'la test edilir;
Qt widget'ları (`src/ui/profile_tab.py`, `profile_dialogs.py`) yalnızca bu fonksiyonları
çağıran ince bir kabuktur.

Tekil madde alanları (id/tags dışında) pydantic modellerinin KENDİ doğrulayıcılarında
kontrol edilir (ör. tarih biçimi, boş olmayan başlık) -- burada AYRI bir doğrulayıcı
YOK; `collect_profile()` tüm profili `MasterProfile.model_validate` ile yeniden
doğrulayarak (ör. `check_unique_ids`) kaydetmeden önceki TEK kapıyı oluşturur.
"""
import uuid

from src.core.tags import TAG_VOCABULARY
from src.cv_builder.profile_loader import (
    DEFAULT_SUMMARY_KEY,
    Education,
    Experience,
    MasterProfile,
    PersonalInfo,
    Project,
    Skill,
)

#: Kategori -> yeni id üretirken kullanılacak önek (`exp_a1b2c3d4` gibi, örnek profildeki
#: `exp_001` kalıbına yakın ama çakışma riski olmayan rastgele bir sondan).
ID_PREFIXES: dict[str, str] = {
    "experience": "exp",
    "education": "edu",
    "projects": "proj",
    "skills": "skill",
}

#: Ayarlar'daki "CV modu" birleştirmesiyle aynı ilke: arayüzde gösterilecek Türkçe bölüm adları.
SECTION_LABELS_TR: dict[str, str] = {
    "experience": "Deneyim",
    "education": "Eğitim",
    "projects": "Projeler",
    "skills": "Yetenekler",
}


def all_item_ids(profile: MasterProfile) -> set[str]:
    """Profildeki TÜM maddelerin (bu sekmede düzenlenmeyen `achievements` dahil) id'leri --
    yeni bir id üretirken hiçbirine çarpmamalı (`MasterProfile.check_unique_ids`)."""
    items = (
        list(profile.experience)
        + list(profile.education)
        + list(profile.projects)
        + list(profile.skills)
        + list(profile.achievements)
    )
    return {item.id for item in items}


def generate_item_id(category: str, existing_ids: set[str]) -> str:
    """`existing_ids`e çarpmayan yeni bir id üretir (kullanıcı id'yle hiç uğraşmaz)."""
    prefix = ID_PREFIXES[category]
    while True:
        candidate = f"{prefix}_{uuid.uuid4().hex[:8]}"
        if candidate not in existing_ids:
            return candidate


def parse_lines(text: str) -> list[str]:
    """Çok satırlı bir metin kutusunu satır satır listeye çevirir; boş satırlar atılır."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def format_lines(items: list[str]) -> str:
    return "\n".join(items)


def parse_comma_list(text: str) -> list[str]:
    """Virgülle ayrılmış bir metni listeye çevirir (etiketler, konular, teknolojiler...).

    Etiketler için bilinmeyen/boş değer burada elenmez -- `ProfileItem.tags_not_empty`
    (profile_loader.py) geçersiz etiket varsa kabul edilen listeyi de içeren Türkçe bir
    `ValueError` fırlatır, dialoglar bunu olduğu gibi gösterir.
    """
    return [item.strip() for item in text.split(",") if item.strip()]


def format_comma_list(items: list[str]) -> str:
    return ", ".join(items)


#: Dialoglarda etiket alanının altında gösterilecek ipucu metni.
TAG_HINT_TR = f"Kabul edilen etiketler: {', '.join(sorted(TAG_VOCABULARY))}"


def experience_summary(item: Experience) -> str:
    date_range = f"{item.start_date} – {item.end_date or 'devam ediyor'}"
    return f"{item.company} — {item.title} ({date_range})"


def education_summary(item: Education) -> str:
    date_range = f"{item.start_date} – {item.end_date or 'devam ediyor'}"
    return f"{item.school} — {item.degree}, {item.field} ({date_range})"


def project_summary(item: Project) -> str:
    return item.name + (f" ({item.date})" if item.date else "")


def skill_summary(item: Skill) -> str:
    parts = [item.name]
    if item.level:
        parts.append(f"({item.level})")
    if item.category:
        parts.append(f"— {item.category}")
    return " ".join(parts)


def personal_info_errors(name: str, email: str) -> list[str]:
    """Kaydetmeyi engelleyen Türkçe hatalar -- pydantic şeması `str` tipini zorunlu kılar
    ama BOŞ olmayı yasaklamaz; kişisel bilgiler için bu ikisi anlamsız bir profil üretir."""
    errors: list[str] = []
    if not name.strip():
        errors.append("Ad soyad boş olamaz.")
    if not email.strip():
        errors.append("E-posta boş olamaz.")
    return errors


def new_minimal_profile(personal_info: PersonalInfo) -> MasterProfile:
    """Yeni kurulumda (profil dosyası yok) yalnızca kişisel bilgilerden oluşan boş bir profil.

    Özet, deneyim vb. BOŞ kalır -- CV metni kullanıcı adına uydurulmaz (CLAUDE.md kural 1/9);
    kullanıcı bunları kendi PDF'iyle (`own_pdf` modu) ya da Profil sekmesinde kendisi doldurur.
    """
    return MasterProfile.model_validate(
        {
            "personal_info": personal_info.model_dump(mode="python"),
            "summaries": {DEFAULT_SUMMARY_KEY: ""},
        }
    )


def collect_profile(
    loaded: MasterProfile,
    *,
    personal_info: PersonalInfo,
    experience: list[Experience],
    education: list[Education],
    projects: list[Project],
    skills: list[Skill],
) -> MasterProfile:
    """Düzenlenen bölümleri `loaded` profiliyle birleştirip TAM ŞEMA DOĞRULAMASINDAN geçirir.

    `summaries`/`achievements`/`languages` bu sekmede düzenlenmez, `loaded`den AYNEN gelir.
    `MasterProfile.model_validate` (pydantic) `check_unique_ids` dahil TÜM modelin kurallarını
    yeniden çalıştırır -- kaydetmeden önceki TEK doğrulama kapısı burasıdır; geçemezse
    `pydantic.ValidationError` fırlatır, çağıran (`profile_tab.py`) bunu yakalayıp gösterir.
    """
    data = loaded.model_dump(mode="python")
    data["personal_info"] = personal_info.model_dump(mode="python")
    data["experience"] = [item.model_dump(mode="python") for item in experience]
    data["education"] = [item.model_dump(mode="python") for item in education]
    data["projects"] = [item.model_dump(mode="python") for item in projects]
    data["skills"] = [item.model_dump(mode="python") for item in skills]
    return MasterProfile.model_validate(data)


def format_validation_error(error: Exception) -> str:
    """`pydantic.ValidationError`i okunur, Türkçe ağırlıklı bir metne çevirir.

    Alan yollarındaki İngilizce kısımlar (`experience.0.tags`) ve pydantic'in kendi
    "Value error," ön eki KALIR -- tam bir çeviri katmanı bu adımın kapsamı dışında; asıl
    bilgi (ör. "Geçersiz etiket(ler): [...]") zaten `profile_loader.py`deki Türkçe
    doğrulayıcılardan geliyor.
    """
    return str(error)
