"""Ayarlar sekmesinin veri katmanı — PySide6 İÇERMEZ.

`src/ui/company_data.py` / `src/ui/queue_data.py` ile aynı kalıp: okuma/yazma, doğrulama ve
onay sırası burada saf Python fonksiyonlarıyla test edilir; Qt widget'ı (`src/ui/settings_tab.py`)
yalnızca bu fonksiyonları çağıran ince bir kabuktur.

`SettingsForm`'da API anahtarı/Gmail şifresi gibi hiçbir SIR alanı YOK (CLAUDE.md kural 6):
onlar yalnızca `src/core/secrets.py` (keyring) üzerinden, forma hiç uğramadan yazılır.
"""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlmodel import Session

from src.company_finder.google_places import PLACES_HARD_MAX_MONTHLY
from src.company_finder.source_selector import SOURCE_GOOGLE_PLACES, SOURCE_OSM, SOURCE_TEKNOKENT
from src.core.cities import load_cities
from src.core.custom_profession import CUSTOM_PROFESSION_ID, validate_custom_profession
from src.core.mail_providers import load_mail_provider_presets
from src.core.mail_template_presets import load_generic_mail_templates
from src.core.professions import MailTemplateSuggestion, ProfessionPreset, load_profession_presets
from src.core.settings import (
    CV_MODE_OWN_PDF,
    CV_MODES,
    DEFAULTS,
    KEY_APPLICATION_TARGET,
    KEY_AUTOPILOT_CITY,
    KEY_CUSTOM_IMAP_HOST,
    KEY_CUSTOM_IMAP_PORT,
    KEY_CUSTOM_PROFESSION_KEYWORDS,
    KEY_CUSTOM_PROFESSION_NAME,
    KEY_CUSTOM_SMTP_HOST,
    KEY_CUSTOM_SMTP_PORT,
    KEY_CUSTOM_SMTP_SECURITY,
    KEY_CV_MODE,
    KEY_DAILY_LIMIT,
    KEY_HOURLY_LIMIT,
    KEY_INBOX_CHECK_MINUTES,
    KEY_LLM_MODEL_CLAUDE,
    KEY_LLM_MODEL_GEMINI,
    KEY_LLM_MODEL_GROQ,
    KEY_LLM_PROVIDER,
    KEY_MAIL_LANGUAGE,
    KEY_MAIL_PROVIDER,
    KEY_MAIL_TEMPLATE_BODY,
    KEY_MAIL_TEMPLATE_SUBJECT,
    KEY_MAX_DELAY_SECONDS,
    KEY_MAX_PROJECTS,
    KEY_MIN_DELAY_SECONDS,
    KEY_PLACES_MONTHLY_LIMIT,
    KEY_PROFESSION,
    KEY_SEND_MODE,
    KEY_SENDER_EMAIL,
    KEY_SENDER_NAME,
    KEY_TAILORING_ENABLED,
    KEY_TEST_MODE,
    KEY_UPDATE_CHECK_ENABLED,
    KEY_USE_GOOGLE_PLACES,
    KEY_USE_OSM,
    KEY_WEEKDAYS_ONLY,
    KEY_WORK_END,
    KEY_WORK_START,
    KEY_WORKING_HOURS_ONLY,
    MODE_AUTO,
    MODE_MANUAL,
    SEND_MODE_LABELS_TR,
    SEND_MODES,
    get_setting,
    set_setting,
)
from src.core.timeutil import parse_hhmm
from src.cv_builder.mail_template import (
    LANGUAGE_TR,
    LANGUAGES,
    PLACEHOLDERS,
    TARGET_LABELS,
    TARGETS,
    find_unknown_placeholders,
)
from src.cv_builder.own_cv import has_own_cv
from src.cv_builder.profile_loader import load_profile
from src.llm.providers import API_KEY_PROVIDERS, LLM_PROVIDER_NAMES, MODEL_SETTING_KEYS, PROVIDER_NONE
from src.sender.mail_connection import PROVIDER_CUSTOM, SECURITIES
from src.sender.rate_limiter import HARD_MAX_DAILY, HARD_MAX_HOURLY, HARD_MIN_DELAY_SECONDS


@dataclass(frozen=True)
class SettingsForm:
    """Ayarlar sekmesinin tuttuğu alanlar — `sending_paused`/`inbox_last_check_at` hariç
    `DEFAULTS`'taki her ayar.

    `sending_paused` (acil durdur) burada BİLEREK yok: sahibi durum şeridindeki kırmızı buton
    (`src/ui/status_strip.py`); "Kaydet"e basmayı bekleyen bir güvenlik anahtarı yanlış olur.
    `inbox_last_check_at` da BİLEREK yok: kullanıcının düzenlediği bir ayar değil,
    `src/inbox/service.py::check_inbox()`nun kendiliğinden yazdığı bir zaman damgası.
    """

    test_mode: bool
    send_mode: str
    daily_limit: int
    hourly_limit: int
    min_delay_seconds: int
    max_delay_seconds: int
    working_hours_only: bool
    work_start: str
    work_end: str
    weekdays_only: bool
    cv_mode: str
    tailoring_enabled: bool
    max_projects: Optional[int]
    mail_template_subject: str
    mail_template_body: str
    application_target: Optional[str]
    profession: Optional[str]
    #: Yalnızca `profession == "diger"` iken okunur (bkz. `src/core/custom_profession.py`).
    custom_profession_name: Optional[str]
    custom_profession_keywords: list[str]
    mail_language: str
    llm_provider: str
    llm_model_claude: str
    llm_model_gemini: str
    llm_model_groq: str
    sender_email: Optional[str]
    sender_name: Optional[str]
    mail_provider: str
    custom_smtp_host: Optional[str]
    custom_smtp_port: Optional[int]
    custom_smtp_security: str
    custom_imap_host: Optional[str]
    custom_imap_port: Optional[int]
    inbox_check_minutes: int
    #: İl plaka kodu ("06") ya da `None` (seçilmedi) — bkz. `presets/cities.json`.
    autopilot_city: Optional[str]
    use_osm: bool
    #: Google Places düğmesi (varsayılan KAPALI; ücretli olabilir) ve uygulama içi aylık istek sınırı.
    use_google_places: bool
    places_monthly_limit: int
    #: "Güncellemeler" grubu: günde bir "yeni sürüm var mı?" kontrolü (bkz. `src/core/update_check.py`).
    update_check_enabled: bool


#: Alan adı -> ayar anahtarı. `test_defaults_cover_all_form_keys` bu sözlüğün, `sending_paused`
#: dışında `DEFAULTS`'taki her anahtarı kapsadığını denetler: yeni bir ayar eklenip burası
#: güncellenmezse test kırmızı olur, arayüzden unutulmuş bir ayar sessizce kaybolmaz.
FORM_KEYS: dict[str, str] = {
    "test_mode": KEY_TEST_MODE,
    "send_mode": KEY_SEND_MODE,
    "daily_limit": KEY_DAILY_LIMIT,
    "hourly_limit": KEY_HOURLY_LIMIT,
    "min_delay_seconds": KEY_MIN_DELAY_SECONDS,
    "max_delay_seconds": KEY_MAX_DELAY_SECONDS,
    "working_hours_only": KEY_WORKING_HOURS_ONLY,
    "work_start": KEY_WORK_START,
    "work_end": KEY_WORK_END,
    "weekdays_only": KEY_WEEKDAYS_ONLY,
    "cv_mode": KEY_CV_MODE,
    "tailoring_enabled": KEY_TAILORING_ENABLED,
    "max_projects": KEY_MAX_PROJECTS,
    "mail_template_subject": KEY_MAIL_TEMPLATE_SUBJECT,
    "mail_template_body": KEY_MAIL_TEMPLATE_BODY,
    "application_target": KEY_APPLICATION_TARGET,
    "profession": KEY_PROFESSION,
    "custom_profession_name": KEY_CUSTOM_PROFESSION_NAME,
    "custom_profession_keywords": KEY_CUSTOM_PROFESSION_KEYWORDS,
    "mail_language": KEY_MAIL_LANGUAGE,
    "llm_provider": KEY_LLM_PROVIDER,
    "llm_model_claude": KEY_LLM_MODEL_CLAUDE,
    "llm_model_gemini": KEY_LLM_MODEL_GEMINI,
    "llm_model_groq": KEY_LLM_MODEL_GROQ,
    "sender_email": KEY_SENDER_EMAIL,
    "sender_name": KEY_SENDER_NAME,
    "mail_provider": KEY_MAIL_PROVIDER,
    "custom_smtp_host": KEY_CUSTOM_SMTP_HOST,
    "custom_smtp_port": KEY_CUSTOM_SMTP_PORT,
    "custom_smtp_security": KEY_CUSTOM_SMTP_SECURITY,
    "custom_imap_host": KEY_CUSTOM_IMAP_HOST,
    "custom_imap_port": KEY_CUSTOM_IMAP_PORT,
    "inbox_check_minutes": KEY_INBOX_CHECK_MINUTES,
    "autopilot_city": KEY_AUTOPILOT_CITY,
    "use_osm": KEY_USE_OSM,
    "use_google_places": KEY_USE_GOOGLE_PLACES,
    "places_monthly_limit": KEY_PLACES_MONTHLY_LIMIT,
    "update_check_enabled": KEY_UPDATE_CHECK_ENABLED,
}

#: Alan adı -> "Kaydettiğiniz değişiklikler" listesinde ve hata/uyarı metinlerinde kullanılan
#: Türkçe etiket.
SETTING_LABELS_TR: dict[str, str] = {
    "test_mode": "Test modu",
    "send_mode": "Gönderim modu",
    "daily_limit": "Günlük limit",
    "hourly_limit": "Saatlik limit",
    "min_delay_seconds": "En az bekleme (saniye)",
    "max_delay_seconds": "En çok bekleme (saniye)",
    "working_hours_only": "Sadece mesai saatinde gönder",
    "work_start": "Mesai başlangıcı",
    "work_end": "Mesai bitişi",
    "weekdays_only": "Sadece hafta içi gönder",
    "cv_mode": "CV modu",
    "tailoring_enabled": "CV özelleştirme",
    "max_projects": "En çok proje sayısı",
    "mail_template_subject": "Mail konusu şablonu",
    "mail_template_body": "Mail gövdesi şablonu",
    "application_target": "Hedef",
    "profession": "Meslek",
    "custom_profession_name": "Kendi mesleğimin adı",
    "custom_profession_keywords": "Kendi mesleğim için arama kelimeleri",
    "mail_language": "Mail dili",
    "llm_provider": "Yapay zekâ sağlayıcısı",
    "llm_model_claude": "Claude model adı",
    "llm_model_gemini": "Gemini model adı",
    "llm_model_groq": "Groq model adı",
    "sender_email": "Gönderen e-posta",
    "sender_name": "Gönderen adı",
    "mail_provider": "Mail sağlayıcısı",
    "custom_smtp_host": "Özel SMTP sunucusu",
    "custom_smtp_port": "Özel SMTP portu",
    "custom_smtp_security": "Özel SMTP güvenliği",
    "custom_imap_host": "Özel IMAP sunucusu",
    "custom_imap_port": "Özel IMAP portu",
    "inbox_check_minutes": "Gelen kutusu otomatik kontrol aralığı",
    "autopilot_city": "Şehir",
    "use_osm": "OpenStreetMap kaynağı",
    "use_google_places": "Google Places kullan",
    "places_monthly_limit": "Google Places aylık istek sınırı",
    "update_check_enabled": "Yeni sürüm kontrolü",
}

#: "Mail dili" combo'sundaki Türkçe etiketler; ayar değeri her zaman "tr"/"en" kalır
#: (`src/cv_builder/mail_template.LANGUAGES`).
MAIL_LANGUAGE_LABELS_TR: dict[str, str] = {"tr": "Türkçe", "en": "İngilizce"}

#: "CV modu" combo'sundaki Türkçe etiketler; ayar değeri her zaman `CV_MODES`'tan biri kalır.
CV_MODE_LABELS_TR: dict[str, str] = {
    "ats": "ATS CV oluştur (mevcut sistem)",
    "own_pdf": "Kendi PDF'imi olduğu gibi ekle",
}

#: "CV modu" combo'sunun altında gösterilecek kısa Türkçe açıklama.
CV_MODE_DESCRIPTIONS_TR: dict[str, str] = {
    "ats": (
        "Her şirket için profildeki maddeler yeniden sıralanır ve ATS uyumlu bir PDF "
        "üretilir (mevcut davranış)."
    ),
    "own_pdf": (
        "CV özelleştirme ve PDF üretimi hiç çalışmaz; aşağıda yüklediğiniz PDF, her "
        "başvuruya OLDUĞU GİBİ eklenir."
    ),
}

#: "Hedef" combo'sundaki Türkçe etiketler — `mail_template.TARGET_LABELS[LANGUAGE_TR]` ile
#: AYNI (arayüz her zaman Türkçe kalır, CLAUDE.md); `{hedef}` yer tutucusunun TR karşılığı da
#: bu sözlükten gelir, iki ayrı kopya YOK.
APPLICATION_TARGET_LABELS_TR: dict[str, str] = TARGET_LABELS[LANGUAGE_TR]

#: Gönderim modu combo'sunun altında gösterilecek kısa Türkçe açıklama.
SEND_MODE_DESCRIPTIONS_TR: dict[str, str] = {
    MODE_MANUAL: "Manuel: yalnızca Onay Kuyruğu'nda sizin onayladığınız başvurular gönderilir.",
    MODE_AUTO: (
        "Otomatik: onaylananlar + rol adresi (kariyer@/info@ gibi) VE MX kaydı doğrulanmış "
        "başvurular kendiliğinden gönderilir. Kişi adresi görünen bir adrese (örn. "
        "ahmet@firma.com) otomatik modda gönderim YAPILMAZ, manuel onay gerekir."
    ),
}

#: `max_projects` alanı arayüzde tek bir sayı kutusunda tutulur; bu değer "sınırsız" (None)
#: anlamına gelir.
MAX_PROJECTS_UNLIMITED = 0

#: LLM bağlantı testinde kullanılan örnek metin (`scripts/try_llm.py`'deki örnekle aynı).
LLM_TEST_COMPANY = "Örnek Ağ Çözümleri A.Ş."
LLM_TEST_ABOUT = (
    "2010 yılından bu yana kurumsal müşterilere ağ altyapısı hizmeti veriyoruz. "
    "Cisco router ve switch kurulumu, LAN/WAN tasarımı, güvenlik duvarı yapılandırması ve "
    "7/24 ağ izleme hizmetlerimizle Türkiye'nin dört bir yanındaki fabrikalara destek "
    "sağlıyoruz. Ekibimizde sertifikalı ağ mühendisleri bulunmaktadır."
)


def read_form(session: Session) -> SettingsForm:
    """Veritabanından `FORM_KEYS`'teki ayarları okuyup bir `SettingsForm` oluşturur."""
    values = {field: get_setting(session, key) for field, key in FORM_KEYS.items()}
    return SettingsForm(**values)


def default_form() -> SettingsForm:
    """Hiç veritabanına dokunmadan, `DEFAULTS`'tan bir form üretir ("Varsayılanlara dön")."""
    values = {field: DEFAULTS[key] for field, key in FORM_KEYS.items()}
    return SettingsForm(**values)


def apply_form(session: Session, form: SettingsForm) -> tuple[str, ...]:
    """Veritabanındaki değerden FARKLI olan alanları yazar; değişen ayar anahtarlarını döner.

    `commit` çağırana aittir (`set_setting`'in kendi sözleşmesi, `src/core/settings.py:120`).
    """
    changed: list[str] = []
    for field, key in FORM_KEYS.items():
        new_value = getattr(form, field)
        if get_setting(session, key) != new_value:
            set_setting(session, key, new_value)
            changed.append(key)
    return tuple(changed)


def _valid_hhmm(value: str) -> bool:
    try:
        parse_hhmm(value)
    except ValueError:
        return False
    return True


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def is_valid_email(text: str) -> bool:
    """Basit biçim kontrolü (`validate_form` ile aynı kural); sihirbaz da bunu kullanır."""
    return bool(_EMAIL_RE.match(text))


def validate_mail_provider(form: SettingsForm) -> list[str]:
    """Sağlayıcı/özel sunucu alanlarını doğrular (Adım 10.7; kurulum sihirbazı da kullanır).

    Her `Setting` okuması gibi burada da ön ayar dosyası okunur (`load_mail_provider_presets`
    kendi kendine güvenli düşer, bkz. `src/core/mail_providers.py`); Ayarlar sekmesindeki
    combo'nun ta kendisi zaten desteklenmeyen bir sağlayıcıyı SEÇİLEMEZ yapar (bkz.
    `src/ui/settings_tab.py`), bu kontrol yalnızca ikinci bir savunma hattı (ör. eski bir ayar
    dosyasının elle düzenlenmesi).
    """
    errors: list[str] = []
    presets = load_mail_provider_presets()

    if form.mail_provider == PROVIDER_CUSTOM:
        if not (form.custom_smtp_host or "").strip():
            errors.append("Özel SMTP sunucusu boş olamaz.")
        if form.custom_smtp_port is None or not (1 <= form.custom_smtp_port <= 65535):
            errors.append("Özel SMTP portu 1-65535 aralığında olmalı.")
        if form.custom_smtp_security not in SECURITIES:
            errors.append(f"Özel SMTP güvenliği şunlardan biri olmalı: {', '.join(SECURITIES)}.")
        if not (form.custom_imap_host or "").strip():
            errors.append("Özel IMAP sunucusu boş olamaz.")
        if form.custom_imap_port is None or not (1 <= form.custom_imap_port <= 65535):
            errors.append("Özel IMAP portu 1-65535 aralığında olmalı.")
    elif form.mail_provider not in presets:
        errors.append(f"Bilinmeyen mail sağlayıcısı: {form.mail_provider}.")
    elif not presets[form.mail_provider].supported:
        reason = presets[form.mail_provider].unsupported_reason_tr or "bu sürümde desteklenmiyor"
        errors.append(f"{presets[form.mail_provider].name_tr} desteklenmiyor: {reason}")

    return errors


def validate_mail_template(subject: str, body: str) -> list[str]:
    """Konu/gövde boş olamaz ve yalnızca `PLACEHOLDERS`'taki yer tutucuları içerebilir (Ayarlar ve
    kurulum sihirbazı aynı kuralı kullanır)."""
    errors: list[str] = []
    if not subject.strip():
        errors.append("Mail konusu şablonu boş olamaz.")
    if not body.strip():
        errors.append("Mail gövdesi şablonu boş olamaz.")
    unknown = sorted(set(find_unknown_placeholders(subject)) | set(find_unknown_placeholders(body)))
    if unknown:
        errors.append(
            f"Mail şablonunda bilinmeyen yer tutucu(lar): {', '.join(unknown)}. "
            f"Kullanılabilir yer tutucular: {', '.join(PLACEHOLDERS)}."
        )
    return errors


def validate_form(form: SettingsForm) -> tuple[str, ...]:
    """Kaydetmeyi engelleyen Türkçe hataları döner; hiçbiri yoksa boş demet.

    Her kural, o ayarı gerçekten okuyan koddan geliyor (bkz. plan dosyası): `rate_limiter.py`
    sessizce varsayılana düşerken, burası kullanıcının bunu GÖRMESİNİ sağlıyor.
    """
    errors: list[str] = []

    if form.min_delay_seconds > form.max_delay_seconds:
        errors.append("En az bekleme, en çok beklemeden büyük olamaz.")

    if form.daily_limit < 1:
        errors.append("Günlük limit en az 1 olmalı.")
    elif form.daily_limit > HARD_MAX_DAILY:
        errors.append(f"Günlük limit en çok {HARD_MAX_DAILY} olabilir (kodda sabit üst sınır).")
    if form.hourly_limit < 1:
        errors.append("Saatlik limit en az 1 olmalı.")
    elif form.hourly_limit > HARD_MAX_HOURLY:
        errors.append(f"Saatlik limit en çok {HARD_MAX_HOURLY} olabilir (kodda sabit üst sınır).")
    if form.min_delay_seconds < HARD_MIN_DELAY_SECONDS:
        errors.append(
            f"En az bekleme en az {HARD_MIN_DELAY_SECONDS} saniye olmalı (kodda sabit alt sınır)."
        )

    if not _valid_hhmm(form.work_start) or not _valid_hhmm(form.work_end):
        errors.append("Mesai saatleri 'SS:DD' biçiminde olmalı (örn. 09:00).")
    elif parse_hhmm(form.work_start) >= parse_hhmm(form.work_end):
        errors.append("Mesai başlangıcı, mesai bitişinden önce olmalı (gece vardiyası desteklenmiyor).")

    errors.extend(validate_mail_template(form.mail_template_subject, form.mail_template_body))

    if form.application_target is not None and form.application_target not in TARGETS:
        errors.append(f"Hedef şunlardan biri olmalı: {', '.join(TARGETS)}.")

    if form.mail_language not in LANGUAGES:
        errors.append(f"Mail dili şunlardan biri olmalı: {', '.join(LANGUAGES)}.")

    if form.profession == CUSTOM_PROFESSION_ID:
        errors.extend(
            validate_custom_profession(form.custom_profession_name, form.custom_profession_keywords)
        )
    elif form.profession is not None and form.profession not in load_profession_presets():
        errors.append(f"Bilinmeyen meslek: {form.profession}.")

    if form.autopilot_city is not None:
        cities = load_cities()
        # İl listesi dosyası okunamadıysa (boş sözlük) kaydı engellemeyiz: sorun kullanıcının
        # seçiminde değil dosyada, ve zaten kayıtlı bir kod yüzünden diğer ayarlar kilitlenmemeli.
        if cities and form.autopilot_city not in cities:
            errors.append(f"Bilinmeyen şehir kodu: {form.autopilot_city}.")

    if form.places_monthly_limit < 0:
        errors.append("Google Places aylık istek sınırı negatif olamaz (kapalı için 0 yazın).")
    elif form.places_monthly_limit > PLACES_HARD_MAX_MONTHLY:
        errors.append(
            f"Google Places aylık istek sınırı en çok {PLACES_HARD_MAX_MONTHLY} olabilir "
            "(kodda sabit üst sınır)."
        )

    if form.max_projects is not None and form.max_projects < 1:
        errors.append("En çok proje sayısı en az 1 olmalı (sınırsız için 'sınırsız' seçin).")

    if form.cv_mode not in CV_MODES:
        errors.append(f"CV modu şunlardan biri olmalı: {', '.join(CV_MODES)}.")
    elif form.cv_mode == CV_MODE_OWN_PDF and not has_own_cv():
        errors.append(
            "CV modu 'Kendi PDF'imi olduğu gibi ekle' seçili ama henüz bir PDF "
            "yüklenmedi. Önce CV bölümünden bir PDF seçin."
        )

    if form.sender_email and not _EMAIL_RE.match(form.sender_email):
        errors.append("Gönderen e-posta adresi geçerli görünmüyor.")

    errors.extend(validate_mail_provider(form))

    if form.inbox_check_minutes < 0:
        errors.append("Gelen kutusu kontrol aralığı negatif olamaz (kapalı için 0 seçin).")

    if form.send_mode not in SEND_MODES:
        errors.append(f"Gönderim modu şunlardan biri olmalı: {', '.join(SEND_MODES)}.")

    if form.llm_provider not in LLM_PROVIDER_NAMES:
        errors.append(f"Yapay zekâ sağlayıcısı şunlardan biri olmalı: {', '.join(LLM_PROVIDER_NAMES)}.")
    elif form.llm_provider != PROVIDER_NONE:
        model_field = {
            KEY_LLM_MODEL_CLAUDE: "llm_model_claude",
            KEY_LLM_MODEL_GEMINI: "llm_model_gemini",
            KEY_LLM_MODEL_GROQ: "llm_model_groq",
        }[MODEL_SETTING_KEYS[form.llm_provider]]
        if not getattr(form, model_field).strip():
            errors.append(f"{form.llm_provider} için model adı boş olamaz.")

    return tuple(errors)


def form_warnings(form: SettingsForm) -> tuple[str, ...]:
    """Kaydetmeyi ENGELLEMEYEN, kaydettikten sonra gösterilecek Türkçe uyarılar."""
    warnings: list[str] = []
    if form.daily_limit > 30:
        warnings.append(
            "Günlük limit 30'un üzerinde. Gmail hesabınız, çok sayıda mail göndermeniz "
            "durumunda geçici olarak kısıtlanabilir."
        )
    if form.hourly_limit > form.daily_limit:
        warnings.append("Saatlik limit, günlük limitten büyük — saatlik limit fiilen etkisiz kalır.")
    return tuple(warnings)


@dataclass(frozen=True)
class Confirmation:
    """Kaydetmeden önce sırayla sorulacak tek bir onay."""

    title: str
    text: str


@dataclass(frozen=True)
class SavePlan:
    """`plan_save()`'in sonucu."""

    #: Doluysa hiçbir şey yazılmaz, onay da sorulmaz — önce bunlar düzeltilmeli.
    errors: tuple[str, ...]
    #: Sırayla onay istenecek uyarılar; biri reddedilirse/kelime yanlış yazılırsa kayıt iptal edilir.
    confirmations: tuple[Confirmation, ...]
    #: Kaydettikten SONRA gösterilecek, engellemeyen uyarılar.
    warnings: tuple[str, ...]
    #: Değişen ayar anahtarları (bilgi amaçlı; `apply_form` zaten kendi hesaplar).
    changed_keys: tuple[str, ...]


#: Test modu Kaydet ile KAPATILAMAZ: yalnızca "Gerçek gönderime geç" düğmesiyle (KVKK onayı + "Anladım"
#: kutulu pencere, `src/ui/test_mode_dialog.py`). Kaydet yolu bu kapıyı atlayamasın diye engellenir.
TEST_MODE_OFF_VIA_SAVE_ERROR = (
    "Test modunu kapatmak için \"Gerçek gönderime geç\" düğmesini kullanın; Kaydet ile kapatılamaz."
)

PLACES_ON_CONFIRM_TITLE = "Google Places açılıyor"
PLACES_ON_CONFIRM_TEXT = (
    "Google Places, firma bulmak için Google'ın ÜCRETLİ olabilen servisini kullanır. Anahtarınızın "
    "bağlı olduğu Google hesabına, ücretsiz kotayı aşarsanız FATURA çıkabilir.\n\n"
    "Uygulama aylık istek sayısını sizin koyduğunuz sınırla (ve kodda sabit 2000 tavanıyla) durdurur, "
    "ama bu yalnızca uygulamanın kendi freni. Google Cloud Console'da günlük kota koymadıysanız "
    "'Anahtar nasıl alınır?' rehberindeki adımı mutlaka yapın. Bütçe uyarısı faturayı DURDURMAZ, "
    "yalnızca e-posta gönderir.\n\n"
    "Google Places'i açmak istediğinize emin misiniz?"
)

AUTO_MODE_CONFIRM_TITLE = "Otomatik moda geçiliyor"
AUTO_MODE_CONFIRM_TEXT = (
    "Otomatik modda, rol adresi (kariyer@/info@ gibi) VE MX kaydı doğrulanmış başvurular "
    "sizin onayınız olmadan gönderilir. Kişi adresi görünen bir adrese (örn. "
    "ahmet@firma.com) otomatik modda yine de gönderim yapılmaz; o başvurular manuel onay "
    "kuyruğunda bekler.\n\n"
    "Otomatik moda geçmek istediğinize emin misiniz?"
)


def plan_save(old: SettingsForm, new: SettingsForm) -> SavePlan:
    """Kaydetmeden önceki doğrulama + onay sırası.

    Sıra: (1) doğrulama hataları -- varsa hiç yazılmaz, onay da sorulmaz; test modunu açıktan
    kapalıya çeviren bir form da hatadır (o geçiş yalnızca "Gerçek gönderime geç" düğmesinden
    olur); (2) gönderim modu manuel'den otomatiğe -- Evet/Hayır (CLAUDE.md kural 10); (3) Google
    Places'i açmak -- Evet/Hayır. Test modunu AÇMAK (güvenli yöne dönmek) ve otomatik'ten manuel'e
    dönmek onaysızdır.
    """
    errors = validate_form(new)
    if not errors and old.test_mode and not new.test_mode:
        errors = (TEST_MODE_OFF_VIA_SAVE_ERROR,)
    if errors:
        return SavePlan(errors=errors, confirmations=(), warnings=(), changed_keys=())

    changed_keys = tuple(
        key for field, key in FORM_KEYS.items() if getattr(old, field) != getattr(new, field)
    )

    confirmations: list[Confirmation] = []
    if old.send_mode == MODE_MANUAL and new.send_mode == MODE_AUTO:
        confirmations.append(Confirmation(title=AUTO_MODE_CONFIRM_TITLE, text=AUTO_MODE_CONFIRM_TEXT))

    if not old.use_google_places and new.use_google_places:
        confirmations.append(Confirmation(title=PLACES_ON_CONFIRM_TITLE, text=PLACES_ON_CONFIRM_TEXT))

    return SavePlan(
        errors=(),
        confirmations=tuple(confirmations),
        warnings=form_warnings(new),
        changed_keys=changed_keys,
    )


def _format_value(field: str, value: object) -> str:
    if field == "send_mode":
        return SEND_MODE_LABELS_TR.get(value, str(value))
    if field == "cv_mode":
        return CV_MODE_LABELS_TR.get(value, str(value))
    if field == "application_target":
        return APPLICATION_TARGET_LABELS_TR.get(value, "(boş)" if value is None else str(value))
    if field == "mail_language":
        return MAIL_LANGUAGE_LABELS_TR.get(value, str(value))
    if field == "autopilot_city":
        city = load_cities().get(value) if value is not None else None
        return city.name_tr if city else ("(boş)" if value is None else str(value))
    if field == "inbox_check_minutes":
        return "kapalı" if value == 0 else f"{value} dk"
    if isinstance(value, bool):
        return "Açık" if value else "Kapalı"
    if value is None:
        return "(boş)"
    return str(value)


def describe_changes(old: SettingsForm, new: SettingsForm) -> tuple[str, ...]:
    """Değişen her alan için `"Etiket: eski → yeni"` satırı üretir (sır İÇERMEZ)."""
    lines: list[str] = []
    for field in FORM_KEYS:
        old_value = getattr(old, field)
        new_value = getattr(new, field)
        if old_value == new_value:
            continue
        label = SETTING_LABELS_TR[field]
        lines.append(f"{label}: {_format_value(field, old_value)} → {_format_value(field, new_value)}")
    return tuple(lines)


def format_secret_status(saved: bool) -> str:
    """Keyring'deki bir kayıt için ekranda gösterilecek durum metni (değerin KENDİSİ DEĞİL)."""
    return "kayıtlı ✓" if saved else "kayıtlı değil"


#: Kaynak kimliği -> Ayarlar "Firma kaynakları" grubunda ve Otopilot önizlemesinde görünen ad.
SOURCE_LABELS_TR = {
    SOURCE_TEKNOKENT: "Teknokent firma rehberleri",
    SOURCE_OSM: "OpenStreetMap",
    SOURCE_GOOGLE_PLACES: "Google Places",
}


def format_places_usage(used: int, configured_limit: int) -> str:
    """"Bu ay N / M istek" satırı. M, ayardaki sınır ile kodda sabit tavanın küçüğüdür (tavan
    aşılmış bir ayar, eski bir kayıttan gelebilir; `google_places.monthly_limit` ile aynı kural)."""
    effective = max(min(configured_limit, PLACES_HARD_MAX_MONTHLY), 0)
    return f"Bu ay (Google fatura ayı, Pasifik saati): {used} / {effective} istek kullanıldı."


#: "Anahtar nasıl alınır?" rehberi (ekran görüntüsüsüz, adım adım). Menü adları Google tarafından
#: değiştirilebilir; bu yüzden adımlar "şuna benzer bir yer" değil, aranacak KELİMELERLE yazıldı.
PLACES_KEY_GUIDE_TITLE_TR = "Google Places anahtarı nasıl alınır?"
PLACES_KEY_GUIDE_TR = """Google Places İSTEĞE BAĞLIDIR. Uygulama OpenStreetMap ve CSV ile de çalışır. Google Places daha çok firma bulur ama bir Google Cloud hesabı, faturalandırma bilgisi (kredi kartı) ve biraz dikkat ister.

ÖNEMLİ: Google ücretsiz kota verir, ama kotayı aşarsanız kartınızdan PARA çekilir. Aşağıdaki 6. adımı (günlük kota) ATLAMAYIN.

1) Hesap
   • https://console.cloud.google.com adresine kendi Google hesabınızla girin, şartları kabul edin.

2) Proje
   • Sayfanın üstündeki proje adına tıklayın → "Yeni proje".
   • Ad olarak "flybycv" yazın → "Oluştur". Sonra üstten bu projeyi seçtiğinizden emin olun.

3) Faturalandırma
   • Sol menüden "Faturalandırma" (Billing) bölümüne girin.
   • "Faturalandırma hesabı oluştur" deyip kart bilgisi girin ve hesabı bu projeye bağlayın.
   • Kart olmadan Places API çalışmaz. Yeni hesaplara Google deneme kredisi verebilir; koşulları orada okuyun.

4) Yalnızca "Places API (New)" servisini açın
   • Sol menü → "API'ler ve Hizmetler" → "Kitaplık" (Library).
   • Arama kutusuna "Places API (New)" yazın, parantez içinde (New) yazan sonucu seçin → "Etkinleştir".
   • Başka hiçbir Maps servisini etkinleştirmeyin (eski "Places API" değil, "(New)" olan).

5) API anahtarı oluşturun ve kısıtlayın
   • "API'ler ve Hizmetler" → "Kimlik bilgileri" (Credentials) → "Kimlik bilgisi oluştur" → "API anahtarı".
   • Anahtar görününce "Anahtarı kısıtla" (Restrict key) deyin.
   • "API kısıtlamaları" bölümünde "Anahtarı kısıtla"yı seçin, listeden yalnızca "Places API (New)" işaretleyin → "Kaydet".
   • Anahtarı kopyalayın ve bu sayfadaki "Anahtar kaydet" düğmesiyle yapıştırın. Anahtarı başkasına vermeyin, e-postayla göndermeyin, dosyaya yazmayın; uygulama onu yalnızca Windows şifre kasasında tutar.

6) GÜNLÜK İSTEK KOTASI KOYUN (faturayı gerçekten sınırlayan adım)
   • Cloud Console'da arama kutusuna "Quotas" (Kotalar) yazıp "Google Maps Platform > Quotas" sayfasını açın (ya da "API'ler ve Hizmetler" → "Etkin API'ler" → "Places API (New)" → "Kotalar").
   • Text Search ile ilgili, "per day" (günlük) yazan bir kota satırını işaretleyip "Düzenle" (Edit) deyin, küçük bir sayı girin (örn. 30) ve "İstek gönder" (Submit) ile kaydedin. Günlük satır yoksa dakikalık (per minute) satırı düşük (örn. 5) tutun.
   • Limit dolunca Google istekleri reddeder: kullanılan para bu sayıyla sınırlanır. Kota adları zamanla değişebilir; "Text Search" ve "day" kelimelerini arayın.

7) Bütçe uyarısı (isteğe bağlı, ek güvenlik)
   • "Faturalandırma" → "Bütçeler ve uyarılar" → "Bütçe oluştur" → küçük bir tutar (örn. 5 $).
   • DİKKAT: Bütçe uyarısı faturayı DURDURMAZ. Yalnızca eşik aşılınca size e-posta gönderir; harcama devam eder. Gerçek fren 6. adımdaki günlük kota ve bu uygulamanın aylık istek sınırıdır.

8) Uygulamada
   • "Anahtar kaydet" → anahtarı yapıştırın → "Anahtarı test et".
   • Test yalnızca "places.id" alanını ister; Google'ın fiyat listesine göre bu ücretsiz sınıftadır (fiyatlar değişebilir, güncel listeyi Google'ın sitesinden kontrol edin).
   • Hazırsanız "Google Places kullan" kutusunu işaretleyip Kaydet'e basın. Aylık istek sınırını düşük tutun (varsayılan 900; en çok 2000).

Bu uygulama her istekten önce kendi aylık sayacına bakar ve sınır dolunca Google'a hiç istek atmaz. Sayaç yalnızca bu bilgisayardaki uygulamayı sayar; aynı anahtarı başka yerde kullanırsanız orada yapılan istekleri bilemez.
"""


def sender_name_hint(profile_path: Path) -> str:
    """`sender_name` boşken gri yazıyla gösterilecek, profildeki adı içeren ipucu metni.

    `src/ui/queue_tab.py::_resolve_identity_email` ile aynı kalıp: profil okunamazsa arayüzü
    ENGELLEMEZ, bunu söyleyen bir metin döner.
    """
    try:
        profile = load_profile(profile_path)
    except (FileNotFoundError, ValueError):
        return "(profil okunamadı)"
    return profile.personal_info.name


def suggested_mail_template(
    target: Optional[str],
    profession_id: Optional[str],
    language: str,
    professions: dict[str, ProfessionPreset],
    generic_templates: dict[tuple[str, str], MailTemplateSuggestion],
) -> Optional[MailTemplateSuggestion]:
    """Hedef/meslek/dil seçimine göre ÖNERİLECEK şablonu döner; hedef seçilmemişse `None`.

    Öncelik: dil Türkçe İSE ve seçili meslek paketinde bu hedef için bir şablon VARSA o
    kullanılır (mesleğe özel, daha isabetli); aksi halde `presets/mail_templates/`deki genel
    (mesleğe özel olmayan) şablona düşülür — dil İngilizce olduğunda meslek paketleri hiç TR
    dışı şablon taşımadığı için (bkz. `core/professions.py`) her zaman bu ikinci yol kullanılır.

    Ekranda BUNU göstermek/üzerine yazıp yazmamak `src/ui/settings_tab.py`nin işi (Qt onay
    kutusu) -- bu fonksiyon yalnızca "önerilen ne" sorusuna saf bir cevap verir, hiçbir widget'a
    dokunmaz.
    """
    if target is None:
        return None
    if language == LANGUAGE_TR and profession_id is not None:
        preset = professions.get(profession_id)
        if preset is not None and target in preset.default_mail_templates:
            return preset.default_mail_templates[target]
    return generic_templates.get((target, language))


__all__ = [
    "SettingsForm",
    "FORM_KEYS",
    "SETTING_LABELS_TR",
    "SEND_MODE_DESCRIPTIONS_TR",
    "MAIL_LANGUAGE_LABELS_TR",
    "CV_MODE_LABELS_TR",
    "CV_MODE_DESCRIPTIONS_TR",
    "APPLICATION_TARGET_LABELS_TR",
    "MAX_PROJECTS_UNLIMITED",
    "LLM_TEST_COMPANY",
    "LLM_TEST_ABOUT",
    "read_form",
    "default_form",
    "apply_form",
    "validate_form",
    "validate_mail_provider",
    "validate_mail_template",
    "form_warnings",
    "suggested_mail_template",
    "Confirmation",
    "SavePlan",
    "TEST_MODE_OFF_VIA_SAVE_ERROR",
    "AUTO_MODE_CONFIRM_TITLE",
    "AUTO_MODE_CONFIRM_TEXT",
    "plan_save",
    "describe_changes",
    "format_secret_status",
    "sender_name_hint",
    "API_KEY_PROVIDERS",
]
