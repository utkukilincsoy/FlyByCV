"""Kullanıcının Ayarlar'da yazdığı mail şablonuna profil/başvuru bilgilerini yerleştirir.

Şablon metni kullanıcıya aittir (CLAUDE.md kural 1) — burada hiçbir cümle üretilmez, yalnızca
`PLACEHOLDERS`'taki yer tutucular değiştirilir. Basit `str.replace` kullanılır (Jinja/`str.format`
DEĞİL): kullanıcının şablonunda başka bir `{` geçerse kod gibi çalıştırılıp/hata fırlatıp arayüzü
bozmasın (PLAN.md Adım 10.4a).

`{ad_soyad}`/`{sirket_adi}`/`{okul}`/`{bolum}`/`{sehir}`/`{telefon}`/`{eposta}`/`{linkedin}`
doğrudan `master_profile.json`'dan gelir (kural 9 — birebir, yeniden yazılmaz). `{hedef}`/
`{meslek}` ise LLM'in ÜRETMEDİĞİ, koddaki sabit bir sözlükten (`TARGET_LABELS`) veya seçili
meslek paketinin `name_tr`'inden okunan sabit metinlerdir — CV/başvuru içeriği değil, yalnızca
"hangi hedef/meslek için yazıldığı" bilgisidir. `{giris_cumlesi}` (LLM'in şirkete özel tek
cümlesi) HENÜZ bağlanmadı — `src/llm/*`'teki `company_focus_phrase()`/`phrase_validator.py`
silinmedi, o yer tutucu geri geldiğinde yeniden bağlanacak.
"""
import logging
import re
from typing import Optional

from src.cv_builder.profile_loader import Education, MasterProfile

logger = logging.getLogger(__name__)

PLACEHOLDER_AD_SOYAD = "{ad_soyad}"
PLACEHOLDER_SIRKET_ADI = "{sirket_adi}"
PLACEHOLDER_OKUL = "{okul}"
PLACEHOLDER_BOLUM = "{bolum}"
PLACEHOLDER_HEDEF = "{hedef}"
PLACEHOLDER_MESLEK = "{meslek}"
PLACEHOLDER_SEHIR = "{sehir}"
PLACEHOLDER_TELEFON = "{telefon}"
PLACEHOLDER_EPOSTA = "{eposta}"
PLACEHOLDER_LINKEDIN = "{linkedin}"

PLACEHOLDERS: tuple[str, ...] = (
    PLACEHOLDER_AD_SOYAD,
    PLACEHOLDER_SIRKET_ADI,
    PLACEHOLDER_OKUL,
    PLACEHOLDER_BOLUM,
    PLACEHOLDER_HEDEF,
    PLACEHOLDER_MESLEK,
    PLACEHOLDER_SEHIR,
    PLACEHOLDER_TELEFON,
    PLACEHOLDER_EPOSTA,
    PLACEHOLDER_LINKEDIN,
)

#: "Hedef" (başvuru türü) — `Company`/`ProfessionPreset.default_mail_templates` ile aynı 4
#: anahtar (bkz. `src/core/professions.py::REQUIRED_TARGETS`, TEK kaynak burası).
TARGET_JOB = "job"
TARGET_INTERNSHIP = "internship"
TARGET_VOLUNTEER_INTERNSHIP = "volunteer_internship"
TARGET_PART_TIME = "part_time"
TARGETS: tuple[str, ...] = (TARGET_JOB, TARGET_INTERNSHIP, TARGET_VOLUNTEER_INTERNSHIP, TARGET_PART_TIME)

LANGUAGE_TR = "tr"
LANGUAGE_EN = "en"
LANGUAGES: tuple[str, ...] = (LANGUAGE_TR, LANGUAGE_EN)

#: `{hedef}` yer tutucusunun değeri — LLM'den DEĞİL, sabit bir çeviri sözlüğünden gelir (yalnızca
#: 4x2 = 8 sabit kelime öbeği, uydurma riski yok). Mail dili İngilizce seçilse bile `{hedef}`
#: gövdeye doğru dilde otursun diye `language`'a göre seçilir.
TARGET_LABELS: dict[str, dict[str, str]] = {
    LANGUAGE_TR: {
        TARGET_JOB: "iş",
        TARGET_INTERNSHIP: "staj",
        TARGET_VOLUNTEER_INTERNSHIP: "gönüllü staj",
        TARGET_PART_TIME: "yarı zamanlı iş",
    },
    LANGUAGE_EN: {
        TARGET_JOB: "job",
        TARGET_INTERNSHIP: "internship",
        TARGET_VOLUNTEER_INTERNSHIP: "volunteer internship",
        TARGET_PART_TIME: "part-time job",
    },
}

_PLACEHOLDER_PATTERN = re.compile(r"\{[^{}]*\}")


def _current_education(profile: MasterProfile) -> Optional[Education]:
    """{okul}/{bolum} için kullanılacak eğitim kaydı.

    Devam eden (`expected=True`) ilk kayıt tercih edilir; yoksa listedeki ilk kayıt (kullanıcı
    profilinde en güncel/en yüksek eğitimi başa koyar); hiç eğitim yoksa `None`.
    """
    for education in profile.education:
        if education.expected:
            return education
    return profile.education[0] if profile.education else None


def _placeholder_values(
    profile: MasterProfile,
    company_name: str,
    *,
    target: Optional[str] = None,
    language: str = LANGUAGE_TR,
    profession_name: Optional[str] = None,
) -> dict[str, str]:
    education = _current_education(profile)
    personal = profile.personal_info
    return {
        PLACEHOLDER_AD_SOYAD: personal.name,
        PLACEHOLDER_SIRKET_ADI: company_name,
        PLACEHOLDER_OKUL: education.school if education else "",
        PLACEHOLDER_BOLUM: education.field if education else "",
        PLACEHOLDER_HEDEF: TARGET_LABELS.get(language, TARGET_LABELS[LANGUAGE_TR]).get(target, ""),
        PLACEHOLDER_MESLEK: profession_name or "",
        PLACEHOLDER_SEHIR: personal.location or "",
        PLACEHOLDER_TELEFON: personal.phone or "",
        PLACEHOLDER_EPOSTA: personal.email or "",
        PLACEHOLDER_LINKEDIN: personal.linkedin or "",
    }


def fill_mail_template(
    subject_template: str,
    body_template: str,
    profile: MasterProfile,
    company_name: str,
    *,
    target: Optional[str] = None,
    language: str = LANGUAGE_TR,
    profession_name: Optional[str] = None,
) -> tuple[str, str]:
    """(konu, gövde) döner: yer tutucular değiştirilir, başka hiçbir şey eklenmez/çıkarılmaz.

    `target`/`language`/`profession_name` yalnızca `{hedef}`/`{meslek}` için kullanılır; hiçbiri
    verilmezse (eski çağrı biçimi) bu iki yer tutucu boş metinle değişir.
    """
    values = _placeholder_values(
        profile, company_name, target=target, language=language, profession_name=profession_name
    )
    subject = subject_template
    body = body_template
    for placeholder, value in values.items():
        subject = subject.replace(placeholder, value)
        body = body.replace(placeholder, value)
    logger.debug(f"Mail metni şablondan üretildi: {company_name}")
    return subject.strip(), body.strip()


def find_unknown_placeholders(text: str) -> list[str]:
    """Metindeki `{...}` yer tutucularından `PLACEHOLDERS`'ta olmayanları döner (Ayarlar
    doğrulaması için — PLAN.md Adım 10.4b)."""
    found = set(_PLACEHOLDER_PATTERN.findall(text))
    return sorted(found - set(PLACEHOLDERS))
