"""İlk açılış sihirbazının veri katmanı (PySide6 İÇERMEZ) -- Faz 10 Adım 10.2a.

`settings_data.py`/`profile_data.py` ile aynı kalıp: sayfa sırası, her sayfanın doğrulaması ve
"kaldığı yerden devam" mantığı burada saf Python'la test edilir; sihirbazın Qt kabuğu
(`onboarding_wizard.py`, Adım 10.2d) yalnızca bu fonksiyonları çağırır.

Doğrulayıcılar ekrandaki ham değerleri alıp Türkçe hata listesi döner (boş liste = sayfa
geçilebilir). Şablon sayfasının (10.2k) doğrulaması o adıma, test maili (10.2l) sayfasının
"gönderildi" bilgisi de o adıma bırakıldı; burada yalnızca onay kutusunun kuralı var.
"""
from dataclasses import replace
from pathlib import Path
from typing import Any, Collection, Mapping, Optional, Sequence

from sqlmodel import Session

from src.company_finder.google_places import PLACES_HARD_MAX_MONTHLY
from src.core.custom_profession import CUSTOM_PROFESSION_ID, validate_custom_profession
from src.core.models import Setting
from src.core.settings import (
    CV_MODE_OWN_PDF,
    DEFAULT_MAIL_TEMPLATE_BODY,
    DEFAULT_MAIL_TEMPLATE_SUBJECT,
    KEY_CV_MODE,
    KEY_ONBOARDING_COMPLETED,
    KEY_ONBOARDING_STEP,
    get_setting,
    set_setting,
)
from src.cv_builder.mail_template import TARGETS
from src.cv_builder.profile_editor import write_profile
from src.cv_builder.profile_loader import (
    Education,
    Experience,
    MasterProfile,
    PersonalInfo,
    Project,
    Skill,
    load_profile,
)
from src.ui.profile_data import collect_profile, new_minimal_profile
from src.ui.settings_data import (
    FORM_KEYS,
    default_form,
    is_valid_email,
    validate_mail_provider,
    validate_mail_template,
)

PAGE_WELCOME = "welcome"
PAGE_PERSONAL = "personal"
PAGE_TARGET = "target"
PAGE_CV = "cv"
PAGE_MAIL = "mail"
PAGE_SOURCES = "sources"
PAGE_TEMPLATE = "template"
PAGE_SELF_TEST = "self_test"
PAGE_FINISH = "finish"

#: Sayfaların gösterileceği sıra (PLAN Adım 10.2: hoş geldin/KVKK → kişisel bilgi+şehir → hedef+
#: meslek → CV → mail hesabı → kaynaklar → şablon → test maili → bitiş).
PAGE_ORDER: tuple[str, ...] = (
    PAGE_WELCOME,
    PAGE_PERSONAL,
    PAGE_TARGET,
    PAGE_CV,
    PAGE_MAIL,
    PAGE_SOURCES,
    PAGE_TEMPLATE,
    PAGE_SELF_TEST,
    PAGE_FINISH,
)


# -- kaldığı yerden devam ----------------------------------------------------------------------


def is_onboarding_needed(session: Session) -> bool:
    """Sihirbaz açılmalı mı? Var olan kullanıcıda göç `onboarding_completed`'ı True yazar."""
    return not get_setting(session, KEY_ONBOARDING_COMPLETED)


def resume_page_id(session: Session) -> str:
    """Yarıda kapatılmışsa kaldığı sayfa, aksi halde (ya da kayıt bilinmiyorsa) ilk sayfa."""
    step = get_setting(session, KEY_ONBOARDING_STEP)
    return step if step in PAGE_ORDER else PAGE_ORDER[0]


def pick_start_page(resume_id: str, available_ids: Collection[str]) -> str:
    """Sihirbazın açılacağı sayfa: kayıtlı sayfa, yoksa (henüz yazılmamışsa) ondan sonraki ilk
    mevcut sayfa, o da yoksa son mevcut sayfa. `available_ids` boş olamaz."""
    ordered = [page_id for page_id in PAGE_ORDER if page_id in available_ids]
    if not ordered:
        raise ValueError("Sihirbazda gösterilecek sayfa yok")
    resume_index = PAGE_ORDER.index(resume_id)
    for page_id in ordered:
        if PAGE_ORDER.index(page_id) >= resume_index:
            return page_id
    return ordered[-1]


def save_step(session: Session, page_id: str) -> None:
    """Sihirbazın şu an hangi sayfada olduğunu yazar (`commit` çağırana aittir)."""
    if page_id not in PAGE_ORDER:
        raise ValueError(f"Bilinmeyen sihirbaz sayfası: {page_id!r}")
    set_setting(session, KEY_ONBOARDING_STEP, page_id)


def complete_onboarding(session: Session) -> None:
    """Sihirbaz bitti: bir daha otomatik açılmaz, kaldığı yer temizlenir (`commit` çağırana ait)."""
    set_setting(session, KEY_ONBOARDING_COMPLETED, True)
    set_setting(session, KEY_ONBOARDING_STEP, None)


# -- sayfaların ortak yazma yardımcıları ---------------------------------------------------------


def save_settings_fields(session: Session, values: Mapping[str, Any]) -> None:
    """`SettingsForm` alan adlarıyla verilen değerleri ayarlara yazar (`commit` çağırana ait).

    Ayarlar sekmesinin grupları `values()` ile tam bu biçimde sözlük verir (bkz. `settings_groups`),
    sihirbaz sayfaları da aynı sözlüğü yazar; alan adı -> ayar anahtarı eşlemesi `FORM_KEYS`'te tek
    yerde durur. Bilinmeyen alan adı `KeyError` verir (sessizce yutulmaz).
    """
    for field, value in values.items():
        set_setting(session, FORM_KEYS[field], value)


def load_existing_profile(profile_path: Path) -> Optional[MasterProfile]:
    """Profil dosyası yoksa `None` (taze kurulum). Dosya VAR ama bozuksa `ValueError` fırlar:
    sihirbaz bozuk bir profilin üzerine sessizce yazmaz, kullanıcıya söyler."""
    if not profile_path.is_file():
        return None
    return load_profile(profile_path)


def save_personal_info(profile_path: Path, info: PersonalInfo) -> bool:
    """Kişisel bilgileri profile yazar; dosyaya yazıldıysa True.

    Profil yoksa yalnızca kişisel bilgilerden oluşan boş bir profil oluşturur (özet/deneyim
    uydurulmaz, kural 1/9). Varsa YALNIZCA `personal_info` değişir, geri kalan her şey aynen
    kalır; bilgi zaten aynıysa dosyaya hiç dokunulmaz (her "İleri"de gereksiz yedek oluşmaz).
    """
    existing = load_existing_profile(profile_path)
    if existing is None:
        write_profile(profile_path, new_minimal_profile(info))
        return True
    if existing.personal_info == info:
        return False
    updated = collect_profile(
        existing,
        personal_info=info,
        experience=list(existing.experience),
        education=list(existing.education),
        projects=list(existing.projects),
        skills=list(existing.skills),
    )
    write_profile(profile_path, updated)
    return True


def merge_profile_sections(
    existing: MasterProfile,
    *,
    experience: list[Experience],
    education: list[Education],
    projects: list[Project],
    skills: list[Skill],
) -> MasterProfile:
    """Düzenlenen dört listeyi `existing` ile birleştirir (kişisel bilgi/özet/başarı/dil AYNEN
    kalır) ve tam şema doğrulamasından geçirir; geçmezse `pydantic.ValidationError`."""
    return collect_profile(
        existing,
        personal_info=existing.personal_info,
        experience=experience,
        education=education,
        projects=projects,
        skills=skills,
    )


def save_profile_sections(
    profile_path: Path,
    *,
    experience: list[Experience],
    education: list[Education],
    projects: list[Project],
    skills: list[Skill],
) -> bool:
    """Deneyim/Eğitim/Proje/Yetenek listelerini profile yazar; dosyaya yazıldıysa True.

    Profil dosyası yoksa `FileNotFoundError` (kişisel bilgi sayfası önce profili oluşturur).
    Listeler zaten aynıysa dosyaya dokunulmaz (gereksiz yedek oluşmaz).
    """
    existing = load_existing_profile(profile_path)
    if existing is None:
        raise FileNotFoundError(f"Profil dosyası yok: {profile_path}")
    merged = merge_profile_sections(
        existing, experience=experience, education=education, projects=projects, skills=skills
    )
    if merged == existing:
        return False
    write_profile(profile_path, merged)
    return True


def initial_cv_mode(session: Session) -> str:
    """CV sayfasında başta seçili gelecek mod.

    Taze kurulumda (sihirbaz gerekiyor) ve CV modu hiç yazılmamışken ÖNERİLEN seçenek, yani kendi
    PDF'i gelir. Aksi halde kayıtlı mod aynen gösterilir: var olan bir kullanıcı sihirbazı yeniden
    açtığında (`onboarding_completed=True`) ya da bu sayfaya geri dönen kullanıcı kendi seçimini
    görür, "İleri" ona sormadan mod değiştirmez.
    """
    saved_mode = get_setting(session, KEY_CV_MODE)
    never_chosen = session.get(Setting, KEY_CV_MODE) is None
    if is_onboarding_needed(session) and never_chosen:
        return CV_MODE_OWN_PDF
    return saved_mode


# -- sayfa doğrulamaları -----------------------------------------------------------------------


def validate_welcome(consent_given: bool) -> list[str]:
    if not consent_given:
        return ["Devam etmek için bilgilendirmeyi okuyup onay kutusunu işaretleyin."]
    return []


def validate_personal(
    name: str, email: str, city_id: Optional[str], known_city_ids: Collection[str]
) -> list[str]:
    """Ad ve e-posta zorunlu, şehir 81 ilden biri olmalı. Telefon/LinkedIn isteğe bağlı."""
    errors: list[str] = []
    if not name.strip():
        errors.append("Ad soyad boş olamaz.")
    if not email.strip():
        errors.append("E-posta boş olamaz.")
    elif not is_valid_email(email.strip()):
        errors.append("E-posta adresi geçerli görünmüyor (örn. ad@ornek.com).")
    if not city_id:
        errors.append("Bir şehir seçin.")
    elif city_id not in known_city_ids:
        errors.append("Seçilen şehir listede yok.")
    return errors


def validate_target(
    target: Optional[str],
    profession_id: Optional[str],
    known_profession_ids: Collection[str],
    custom_name: Optional[str] = None,
    custom_keywords: Sequence[str] = (),
) -> list[str]:
    """Hedef ve meslek zorunlu. Meslek bir paket kimliği ya da "Diğer" (`CUSTOM_PROFESSION_ID`) olabilir;
    "Diğer"de meslek adı ve en az bir arama kelimesi de zorunludur."""
    errors: list[str] = []
    if target not in TARGETS:
        errors.append("Ne aradığınızı seçin (iş, staj, gönüllü staj, yarı zamanlı).")
    if not profession_id:
        errors.append("Bir meslek seçin.")
    elif profession_id == CUSTOM_PROFESSION_ID:
        errors.extend(validate_custom_profession(custom_name, custom_keywords))
    elif profession_id not in known_profession_ids:
        errors.append("Seçilen meslek listede yok.")
    return errors


def validate_cv(cv_mode: str, has_own_cv: bool, profile_available: bool = True) -> list[str]:
    """Kendi PDF'ini ekleme modunda dosya yüklenmiş olmalı; ATS modunda CV'nin üretileceği profil
    dosyası okunabilir olmalı (içinin dolu olması şart değil)."""
    if cv_mode == CV_MODE_OWN_PDF and not has_own_cv:
        return ["Kendi PDF'inizi eklemeyi seçtiniz ama henüz bir PDF seçmediniz."]
    if cv_mode != CV_MODE_OWN_PDF and not profile_available:
        return [
            "ATS CV'si profil bilgilerinizden üretilir ama profil dosyası okunamadı. "
            "Önce \"Kişisel bilgiler\" sayfasını doldurun ya da kendi PDF'inizi ekleyin."
        ]
    return []


def validate_mail(has_password: bool, connection_tested_ok: bool) -> list[str]:
    """Şifre kasaya kaydedilmiş ve "Bağlantıyı test et" başarılı olmadan ilerlenmez."""
    if not has_password:
        return ["Mail uygulama şifresini kaydedin."]
    if not connection_tested_ok:
        return ["Devam etmeden önce \"Bağlantıyı test et\" ile bağlantının çalıştığını doğrulayın."]
    return []


def validate_template(subject: str, body: str) -> list[str]:
    """Şablon sayfası: konu/gövde dolu ve yer tutucular bilinen olmalı (Ayarlar'daki kuralın aynısı)."""
    return validate_mail_template(subject, body)


def is_untouched_default_template(subject: str, body: str) -> bool:
    """Şablon hâlâ uygulamanın genel varsayılanı mı (kullanıcı hiç düzenlememiş)? Yalnızca öyleyse
    sihirbaz öneri sunabilir; kullanıcının kendi metni sorusuz ezilmez (kural 1/9)."""
    return subject == DEFAULT_MAIL_TEMPLATE_SUBJECT and body == DEFAULT_MAIL_TEMPLATE_BODY


def validate_sources(
    city_id: Optional[str], known_city_ids: Collection[str], places_monthly_limit: int
) -> list[str]:
    """Kaynaklar sayfası: şehir (firmalar orada aranır) seçili ve 81 ilden biri olmalı, Google Places
    aylık istek sınırı 0 ile kodda sabit tavan (`PLACES_HARD_MAX_MONTHLY`) arasında olmalı (aynı kural
    Ayarlar'da `validate_form`da). OpenStreetMap/Places açık-kapalı seçimi her haliyle geçerlidir."""
    errors: list[str] = []
    if not city_id:
        errors.append("Bir şehir seçin.")
    elif city_id not in known_city_ids:
        errors.append("Seçilen şehir listede yok.")
    if places_monthly_limit < 0:
        errors.append("Google Places aylık istek sınırı negatif olamaz (kapalı için 0 yazın).")
    elif places_monthly_limit > PLACES_HARD_MAX_MONTHLY:
        errors.append(
            f"Google Places aylık istek sınırı en çok {PLACES_HARD_MAX_MONTHLY} olabilir "
            "(kodda sabit üst sınır)."
        )
    return errors


#: Bağlantı testinin sonucunu etkileyen mail alanları (`MailAccountGroup.values()` anahtarları).
#: `inbox_check_minutes` bilerek yok: bağlantıyla ilgisi yok, değişince test bozulmasın.
MAIL_CONNECTION_FIELDS: tuple[str, ...] = (
    "sender_email",
    "sender_name",
    "mail_provider",
    "custom_smtp_host",
    "custom_smtp_port",
    "custom_smtp_security",
    "custom_imap_host",
    "custom_imap_port",
)


def mail_test_signature(values: Mapping[str, Any], has_password: bool) -> tuple:
    """"Bağlantıyı test et" sonucunun hangi ekran durumuna ait olduğunu belirleyen imza; alanlar ya
    da şifre kaydı değişirse imza değişir ve eski başarı geçersiz sayılır."""
    return (*(values.get(field) for field in MAIL_CONNECTION_FIELDS), has_password)


def validate_mail_account(
    values: Mapping[str, Any], has_password: bool, connection_tested_ok: bool
) -> list[str]:
    """Mail sayfasının tüm doğrulaması: önce alanlar (gönderen biçimi, sağlayıcı/özel sunucu), alanlar
    düzgünse şifre + test (`validate_mail`). `values` = `MailAccountGroup.values()`."""
    errors: list[str] = []
    sender = values.get("sender_email")
    if sender and not is_valid_email(sender):
        errors.append("Gönderen e-posta adresi geçerli görünmüyor (örn. ad@gmail.com).")
    errors.extend(validate_mail_provider(replace(default_form(), **values)))
    if errors:
        return errors
    return validate_mail(has_password, connection_tested_ok)


#: Test maili sayfasının imzasına giren ayar alanları (`SettingsForm` alan adları): gönderim hesabı,
#: CV modu ve mail şablonu değişirse önceki gönderim artık "şu anki ayarları gösteriyor" sayılmaz.
SELF_TEST_FORM_FIELDS: tuple[str, ...] = (
    *MAIL_CONNECTION_FIELDS,
    "cv_mode",
    "mail_template_subject",
    "mail_template_body",
    "application_target",
    "profession",
    "custom_profession_name",
    "mail_language",
)


def file_stamp(path: Path) -> Optional[tuple[int, int]]:
    """Dosyanın (değişim zamanı ns, boyut) çifti; dosya yoksa `None`. İçeriği okumaz."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def self_test_signature(
    values: Mapping[str, Any],
    has_password: bool,
    profile_stamp: Optional[tuple[int, int]],
    own_cv_stamp: Optional[tuple[int, int]],
) -> tuple:
    """Gönderilmiş bir test mailinin hangi kurulum durumuna ait olduğunu belirleyen imza.

    `values` = `SettingsForm` alanlarını veren bir eşleme (`dataclasses.asdict(form)`). Gönderen/
    sağlayıcı/CV modu/şablon, şifre kaydı, profil dosyası (ATS CV'si ondan üretilir) ya da kendi PDF'i
    değişirse imza değişir; sayfa o zaman "Mail geldi" onayını geçersiz sayar ve yeniden gönderim ister.
    """
    return (
        *(values.get(field) for field in SELF_TEST_FORM_FIELDS),
        has_password,
        profile_stamp,
        own_cv_stamp,
    )


def self_test_cooldown_remaining(
    last_sent_at: Optional[float], now: float, cooldown_seconds: float
) -> float:
    """Son gönderimden beri `cooldown_seconds` geçmediyse kalan saniye (>0), aksi halde 0.

    Düğmeye art arda basarak hesabı gereksiz yormamak ve sağlayıcının hız sınırına takılmamak için;
    süre kodda sabit alt sınır olan `HARD_MIN_DELAY_SECONDS` ile aynı alınır (sayfa o sabiti verir).
    """
    if last_sent_at is None:
        return 0.0
    return max(0.0, cooldown_seconds - (now - last_sent_at))


def validate_self_test(test_sent: bool, confirmed_received: bool) -> list[str]:
    """Test maili bu kurulum durumuyla GÖNDERİLMİŞ ve gelen kutusunda görülüp onaylanmış olmalı;
    ikisinden biri eksikse sihirbaz bitmez."""
    if not test_sent:
        return ["Önce \"Test mailini gönder\" düğmesiyle kendinize bir test maili gönderin."]
    if not confirmed_received:
        return ["Test mailinin gelen kutunuza ulaştığını görüp onay kutusunu işaretleyin."]
    return []
