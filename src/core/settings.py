"""Uygulama ayarlarını `Setting` tablosunda okur/yazar.

`Setting.value` bir JSON sözlüğü (dict) olduğu için tek değerler "value" anahtarı
altına sarılır. Kayıt yoksa `DEFAULTS` içindeki varsayılan döner.
"""
import copy
import logging
from typing import Any

from sqlmodel import Session

from src.core.models import Setting
from src.cv_builder.mail_template import LANGUAGE_TR

logger = logging.getLogger(__name__)

KEY_TAILORING_ENABLED = "tailoring_enabled"
KEY_MAX_PROJECTS = "max_projects"
KEY_LLM_PROVIDER = "llm_provider"

# CV modu (Faz 10 Adım 10.3a). "ats" = mevcut sistem (şirkete göre sıralama, Playwright ile
# PDF üretimi). "own_pdf" = kullanıcının kendi yüklediği PDF (`src/cv_builder/own_cv.py` ile
# `paths.own_cv_path()`e kopyalanır) her başvuruya OLDUĞU GİBİ eklenir; CV özelleştirme
# (`tag_matcher.tailor`) ve PDF üretimi (Playwright) hiç çalışmaz (CLAUDE.md kural 2 ile
# uyumlu). Var olan kullanıcı için varsayılan "ats": göç gerekmez, davranış aynı kalır.
KEY_CV_MODE = "cv_mode"
CV_MODE_ATS = "ats"
CV_MODE_OWN_PDF = "own_pdf"
CV_MODES = (CV_MODE_ATS, CV_MODE_OWN_PDF)

# Düzenlenebilir mail şablonu (Faz 10.4). Kullanıcı Ayarlar'da düzenler;
# `src/cv_builder/mail_template.fill_mail_template` doldurur. Yer tutucular:
# `src/cv_builder/mail_template.PLACEHOLDERS` (TEK kaynak). `{giris_cumlesi}` (LLM'in şirkete
# özel tek cümlesi) HENÜZ bağlanmadı — `company_focus_phrase()`/`src/llm/phrase_validator.py`
# SİLİNMEDİ, o yer tutucu geri geldiğinde yeniden bağlanacak.
KEY_MAIL_TEMPLATE_SUBJECT = "mail_template_subject"
KEY_MAIL_TEMPLATE_BODY = "mail_template_body"

# "Hedef" (başvuru türü) ve "Meslek" — Ayarlar'da seçilir, meslek paketindeki (`presets/
# professions/*.json`) o hedefe ait mail şablonu önerisini tetikler (bkz. `src/ui/settings_data.
# py::suggested_mail_template`). Varsayılan `None`: genel (kişiye özel olmayan) bir varsayılan;
# var olan kullanıcı için göç (`core/settings_migration.py`) LEGACY değerleri (gönüllü staj,
# yazılım) açıkça yazar, taze kurulumda kullanıcı Ayarlar'dan seçer. `KEY_PROFESSION`'ın değeri
# bir `ProfessionPreset.id`'dir (ör. "yazilim"); paket silinir/adı değişirse `settings_data.py`
# bunu seçim yapılmamış gibi ele alır, uygulama çökmez.
KEY_APPLICATION_TARGET = "application_target"
KEY_PROFESSION = "profession"

# "Diğer" meslek (bkz. `src/core/custom_profession.py`): `KEY_PROFESSION == "diger"` iken kullanıcının yazdığı
# meslek adı ve virgülle ayırdığı arama kelimeleri (liste olarak saklanır). Çalışma anında bellekte bir paket
# üretilir, dosyaya yazılmaz; `diger` dışında bir meslek seçiliyken bu ikisi okunmaz (ama silinmez).
KEY_CUSTOM_PROFESSION_NAME = "custom_profession_name"
KEY_CUSTOM_PROFESSION_KEYWORDS = "custom_profession_keywords"

# Mail dili — yalnızca mail İÇERİĞİNİ (konu/gövde) etkiler, arayüz her zaman Türkçe kalır
# (CLAUDE.md). `{hedef}` yer tutucusu bu dile göre doldurulur (bkz. `mail_template.
# TARGET_LABELS`); meslek paketlerinin `default_mail_templates`'i yalnızca TR olduğu için dil
# İngilizce seçilince önerilen şablon `presets/mail_templates/` altındaki genel (mesleğe özel
# olmayan) İngilizce şablona düşer (meslek paketi TR kalmaya devam eder, bkz. `core/professions.
# py` "Not" bölümü — `name_en` bilinçli olarak eklenmedi, uydurma çeviri riski).
KEY_MAIL_LANGUAGE = "mail_language"

# Model adları kodda sabit DEĞİL, ayar: sağlayıcılar model adlarını sık değiştiriyor ve
# eskiyen bir ad yüzünden kod güncellemek gerekmemeli. Uç nokta adresleri ise sabittir
# (src/llm/providers.py) — onlar kullanıcının değiştirmesi gereken bir şey değil.
KEY_LLM_MODEL_CLAUDE = "llm_model_claude"
KEY_LLM_MODEL_GEMINI = "llm_model_gemini"
KEY_LLM_MODEL_GROQ = "llm_model_groq"

# Gönderim (Faz 6). Test modu VARSAYILAN AÇIK: kapatılmadan hiçbir mail gerçek şirkete gitmez.
# sender_email/sender_name boşsa (None) profildeki kişisel bilgilere düşülür
# (src/sender/smtp_sender.resolve_identity), bu yüzden ilk kurulumda ayrıca doldurulmaları
# gerekmez.
KEY_TEST_MODE = "test_mode"
KEY_SENDER_EMAIL = "sender_email"
KEY_SENDER_NAME = "sender_name"

# Mail sağlayıcısı (Faz 10 Adım 10.7). "gmail" varsayılan: bu ayar var olmadan önceki tek
# davranış Gmail'di, bu yüzden var olan bir kullanıcının hesabı göç GEREKMEDEN aynı kalır
# (kayıt hiç yoksa `get_setting` zaten bu varsayılana düşer — Adım 10.7b'nin "mevcut hesap
# gmail olarak işaretlenir" kararı ayrı bir göç yazmadan, sadece bu varsayılanla sağlanıyor).
# Sağlayıcı ön ayarları koda gömülü DEĞİL, `presets/mail_providers.json`de (bkz.
# `src/core/mail_providers.py`) — yeni bir sağlayıcı eklemek kod değişikliği gerektirmez.
# `custom_*` yalnızca `mail_provider == "custom"` iken okunur (bkz. `src/sender/mail_connection.
# PROVIDER_CUSTOM`); host/port boşsa (ilk kurulum) gönderim yolu Gmail'e düşer (`src/core/
# mail_providers.resolve_connection_for_provider`), göndermeye çalışan kod asla host'suz kalmaz.
KEY_MAIL_PROVIDER = "mail_provider"
KEY_CUSTOM_SMTP_HOST = "custom_smtp_host"
KEY_CUSTOM_SMTP_PORT = "custom_smtp_port"
KEY_CUSTOM_SMTP_SECURITY = "custom_smtp_security"
KEY_CUSTOM_IMAP_HOST = "custom_imap_host"
KEY_CUSTOM_IMAP_PORT = "custom_imap_port"

# Gönderim kuyruğu ve limitler (Faz 6.2). "manual" = yalnızca kullanıcının onayladığı
# başvurular gönderilir; "auto" = onaylananlar + rol adresi VE MX kaydı doğrulanmış
# başvurular otomatik gönderilir (CLAUDE.md kural 10 — bkz. src/sender/queue.py).
KEY_SEND_MODE = "send_mode"
MODE_MANUAL = "manual"
MODE_AUTO = "auto"
SEND_MODES = (MODE_MANUAL, MODE_AUTO)

#: Gönderim modu etiketleri (durum şeridi ve Ayarlar sekmesinde kullanılır). Burada, `status_strip.py`
#: içinde DEĞİL: o dosya PySide6 import ediyor, Qt'siz `src/ui/settings_data.py` oradan alamaz.
SEND_MODE_LABELS_TR = {MODE_MANUAL: "manuel", MODE_AUTO: "otomatik"}

# Limitler ve mesai saati (CLAUDE.md kural 5 — her iki modda da zorunlu). Saat dilimi
# Europe/Istanbul (src/core/timeutil.py); work_start/work_end "SS:DD" biçiminde metin.
KEY_DAILY_LIMIT = "daily_limit"
KEY_HOURLY_LIMIT = "hourly_limit"
KEY_MIN_DELAY_SECONDS = "min_delay_seconds"
KEY_MAX_DELAY_SECONDS = "max_delay_seconds"
KEY_WORKING_HOURS_ONLY = "working_hours_only"
KEY_WORK_START = "work_start"
KEY_WORK_END = "work_end"
KEY_WEEKDAYS_ONLY = "weekdays_only"

# Acil durdur: açıkken hiçbir gönderim (manuel ya da otomatik) yapılmaz. `src/sender/worker.py`
# her gönderimden ÖNCE bu ayarı veritabanından tekrar okur — başka bir pencereden/terminalden
# çalışırken de durdurulabilsin.
KEY_SENDING_PAUSED = "sending_paused"

# Gelen kutusu kontrolü (Faz 7). Dakika cinsinden aralık; 0 = otomatik kontrol kapalı,
# yalnızca "Şimdi kontrol et" ile elle çalıştırılır. `KEY_INBOX_LAST_CHECK_AT` bir sonraki
# taramanın hangi tarihten başlayacağını belirlemek için okunur (ISO 8601 metin, UTC, naive);
# hiç kontrol yapılmadıysa None — bu durumda `src/inbox/service.py` en eski
# `Application.sent_at`'i kullanır.
KEY_INBOX_CHECK_MINUTES = "inbox_check_minutes"
KEY_INBOX_LAST_CHECK_AT = "inbox_last_check_at"

# Otopilot (Faz 9). `autopilot_city`/`autopilot_queries` Google Places sorgularında kullanılacak
# (Adım 9.3/10.6d, henüz kodda yok); teknokent kaynağı (Adım 9.2) şehre/sorguya bakmaz, kendi
# sabit listesini tarar. Varsayılanları PERSONA'YA ÖZEL DEĞİL (Faz 10 Adım 10.1d — bkz.
# `core/settings_migration.py`): şehir `None`, aramalar boş liste; var olan bir kullanıcının
# önceki değerleri (Ankara, yazılım/bilişim aramaları) uygulama ilk kez bu sürümle açıldığında
# göç ile açıkça veritabanına yazılır, sessizce kaybolmaz. `discovery_state`, her keşif
# kaynağının (sorgu/teknokent) nerede kaldığını tutan bir sözlüktür
# (`{"done": bool, "next_page_token": str|None, ...}`) — Otopilot kapanıp açılsa da bitmiş bir
# kaynak tekrar taranmaz. `pause_reason`, `sending_paused` AÇIKKEN neden duraklatıldığını
# (Otopilot'un kendi güvenlik freni mi, kullanıcı mı) Türkçe tutan ayrı bir alandır — kalıcı
# `sending_paused` ayarının kendisi (CLAUDE.md kural 5) bundan ETKİLENMEZ. `bounce_pause_rate`/
# `bounce_min_sample`, Adım 9.4'teki bounce oranı frenini yapılandırır: en az `bounce_min_sample`
# mail gönderilmişse ve bunların `bounce_pause_rate`'ten fazlası geri dönmüşse Otopilot durur.
#
# `autopilot_city` (Adım 10.6c) artık serbest metin DEĞİL, `presets/cities.json`'daki bir ilin
# PLAKA KODU ("06"); göç `core/settings_migration.py` (v3) eski "Ankara" metnini "06"ya çevirir.
#
# Keşif kaynağı seçimi (Adım 10.6c, bkz. `company_finder/source_selector.py`): `use_osm`
# OpenStreetMap kaynağını açar -- taze kurulumda VARSAYILAN AÇIK, ama var olan kullanıcının
# göçü (v3) bunu açıkça KAPALI yazar (teknokent + mevcut davranış aynen kalsın, kullanıcı
# kararı 30.09.2026). `use_google_places` Google Places için AYRI bir düğme (varsayılan KAPALI):
# API anahtarının kayıtlı olması TEK BAŞINA yetmez, habersiz para harcanmasın. Ayarlar'da
# (10.6g) kendi onay kutusu vardır; AÇILIRKEN ayrıca Evet/Hayır onayı istenir.
KEY_AUTOPILOT_CITY = "autopilot_city"
KEY_USE_OSM = "use_osm"
KEY_USE_GOOGLE_PLACES = "use_google_places"
KEY_AUTOPILOT_QUERIES = "autopilot_queries"
# Google Places aylık istek sayacı (Adım 10.6f, bkz. `company_finder/google_places.py`): kullanıcının
# koyduğu aylık sınır (`PLACES_HARD_MAX_MONTHLY` üstü Ayarlar'da reddedilir, kodda sessizce kırpılır), bu ay atılan istek sayısı ve
# sayacın hangi aya ait olduğu ("2026-09", Google'ın fatura ayı = Pasifik saati). Ay değişince sayaç
# sıfırlanır. Sayaç Ayarlar'da gösterilir (10.6g); kullanıcı düzenlemez.
KEY_PLACES_MONTHLY_LIMIT = "places_monthly_limit"
KEY_PLACES_REQUESTS_THIS_MONTH = "places_requests_this_month"
KEY_PLACES_REQUESTS_MONTH_KEY = "places_requests_month_key"
KEY_DISCOVERY_STATE = "discovery_state"
KEY_PAUSE_REASON = "pause_reason"
KEY_BOUNCE_PAUSE_RATE = "bounce_pause_rate"
KEY_BOUNCE_MIN_SAMPLE = "bounce_min_sample"

# KVKK / 6563 bilgilendirme onayı (Faz 10 Adım 10.8b, bkz. `src/core/legal.py`). `legal_consent_version`
# onaylanan metnin sürümü (None = hiç onaylanmadı); `legal_consent_at` ISO 8601 UTC onay zamanı.
# Varsayılan None: var olan kullanıcıda da onay SORULUR (göç yazmaz), onaysız gerçek gönderim yok.
KEY_LEGAL_CONSENT_VERSION = "legal_consent_version"
KEY_LEGAL_CONSENT_AT = "legal_consent_at"

# İlk açılış sihirbazı (Faz 10 Adım 10.2, bkz. `src/ui/onboarding_data.py`). `onboarding_completed`
# sihirbazın bitip bitmediği: taze kurulumda False (sihirbaz açılır); var olan kullanıcı için
# göç (`core/settings_migration.py`, v4) açıkça True yazar, yani sihirbaz ona otomatik açılmaz.
# `onboarding_step` yarıda kapatılan sihirbazın kaldığı sayfanın kimliği (None = henüz başlamadı
# ya da bitti); sonraki açılışta o sayfadan devam edilir. İkisi de kullanıcının Ayarlar'da
# DÜZENLEDİĞİ ayar değil, sihirbazın kendi yazdığı durum.
KEY_ONBOARDING_COMPLETED = "onboarding_completed"
KEY_ONBOARDING_STEP = "onboarding_step"

# Güncelleme kontrolü (Faz 10 Adım 10.9c, bkz. `src/core/update_check.py`). `update_check_enabled`
# kullanıcının açıp kapattığı ayar (varsayılan AÇIK; Ayarlar'a bağlanması Adım 10.9d'de, KVKK metni
# bu dış bağlantıyı anlatır). `update_last_check_at` (ISO 8601 UTC, naive) günde en fazla bir kez
# kuralı için son kontrol zamanı, `update_skipped_version` "bu sürümü atla" ile seçilen sürüm
# ("1.2.0", `v`siz); ikisi de kullanıcının DÜZENLEDİĞİ ayar değil, kontrolün kendi yazdığı durum.
# `GITHUB_REPO` (src/core/version.py) `None` iken üçü de hiçbir şey yapmaz, ağa çıkılmaz.
KEY_UPDATE_CHECK_ENABLED = "update_check_enabled"
KEY_UPDATE_LAST_CHECK_AT = "update_last_check_at"
KEY_UPDATE_SKIPPED_VERSION = "update_skipped_version"

# Arayüz teması ("light" | "dark"); varsayılan koyu. Bkz. `src/ui/theme.py`.
KEY_UI_THEME = "ui_theme"

# Ayar şeması sürümü (Faz 10 Adım 10.1b — `core/settings_migration.py`). Kullanıcının
# DÜZENLEDİĞİ bir ayar DEĞİL, göçün kendisinin yazdığı bir işaretçi; varsayılanı 0 ("göç henüz
# çalışmadı/bilinmiyor") — göç her açılışta çalışıp gerekirse günceller, kalıcı bir kayıt
# olmadan bu varsayılana asla güvenilmez.
KEY_SCHEMA_VERSION = "schema_version"

#: Genel (kişisel olmayan) varsayılan mail şablonu — herkes için aynı, kullanıcı Ayarlar'dan
#: kendi metnine değiştirir. `{sirket_adi}`/`{ad_soyad}`/`{okul}`/`{bolum}` yer tutucuları
#: `src/cv_builder/mail_template.py::fill_mail_template` tarafından doldurulur.
DEFAULT_MAIL_TEMPLATE_SUBJECT = "{sirket_adi} – Başvuru"
DEFAULT_MAIL_TEMPLATE_BODY = (
    "Sayın Yetkili,\n\n"
    "{okul} {bolum} bölümü öğrencisiyim. {sirket_adi} bünyesinde değerlendirilmek üzere "
    "özgeçmişimi ekte paylaşıyorum.\n\n"
    "Olumlu dönüşünüzü umar, iyi çalışmalar dilerim.\n\n"
    "Saygılarımla,\n{ad_soyad}"
)

VALUE_FIELD = "value"

# Bilinen ayarların listesi ve varsayılanları. Yeni ayar buraya eklenmeden kullanılamaz;
# böylece yazım hatası sessizce varsayılana düşmek yerine hata verir.
DEFAULTS: dict[str, Any] = {
    KEY_TAILORING_ENABLED: True,
    KEY_MAX_PROJECTS: None,
    # "none" = anahtar kelime tabanlı sağlayıcı; API anahtarı gerektirmez (src/llm/factory.py).
    KEY_LLM_PROVIDER: "none",
    KEY_CV_MODE: CV_MODE_ATS,
    KEY_MAIL_TEMPLATE_SUBJECT: DEFAULT_MAIL_TEMPLATE_SUBJECT,
    KEY_MAIL_TEMPLATE_BODY: DEFAULT_MAIL_TEMPLATE_BODY,
    KEY_APPLICATION_TARGET: None,
    KEY_PROFESSION: None,
    KEY_CUSTOM_PROFESSION_NAME: None,
    KEY_CUSTOM_PROFESSION_KEYWORDS: [],
    KEY_MAIL_LANGUAGE: LANGUAGE_TR,
    # Yaptığımız iş basit sınıflandırma (etiket seç, kategori seç), bu yüzden varsayılanlar
    # ucuz ve hızlı modeller. Sağlayıcının konsolunda ad değişirse buradan değil, ayardan
    # güncellenir.
    KEY_LLM_MODEL_CLAUDE: "claude-haiku-4-5",
    KEY_LLM_MODEL_GEMINI: "gemini-2.5-flash",
    KEY_LLM_MODEL_GROQ: "llama-3.3-70b-versatile",
    KEY_TEST_MODE: True,
    KEY_SENDER_EMAIL: None,
    KEY_SENDER_NAME: None,
    KEY_MAIL_PROVIDER: "gmail",
    KEY_CUSTOM_SMTP_HOST: None,
    KEY_CUSTOM_SMTP_PORT: None,
    KEY_CUSTOM_SMTP_SECURITY: "starttls",
    KEY_CUSTOM_IMAP_HOST: None,
    KEY_CUSTOM_IMAP_PORT: None,
    KEY_SEND_MODE: MODE_MANUAL,
    KEY_DAILY_LIMIT: 20,
    KEY_HOURLY_LIMIT: 20,
    KEY_MIN_DELAY_SECONDS: 60,
    KEY_MAX_DELAY_SECONDS: 180,
    KEY_WORKING_HOURS_ONLY: True,
    KEY_WORK_START: "09:00",
    KEY_WORK_END: "18:00",
    KEY_WEEKDAYS_ONLY: True,
    KEY_SENDING_PAUSED: False,
    KEY_INBOX_CHECK_MINUTES: 30,
    KEY_INBOX_LAST_CHECK_AT: None,
    # Genel (kişiye özel olmayan) varsayılan: None/boş liste. Var olan kullanıcının önceki
    # değeri (Ankara, yazılım/bilişim aramaları) `core/settings_migration.py` ile korunur --
    # burada DEĞİŞTİRİLMESİ o göçü etkilemez, göç kendi sabit LEGACY_* değerlerini kullanır.
    KEY_AUTOPILOT_CITY: None,
    KEY_USE_OSM: True,
    KEY_USE_GOOGLE_PLACES: False,
    KEY_AUTOPILOT_QUERIES: [],
    KEY_PLACES_MONTHLY_LIMIT: 900,
    KEY_PLACES_REQUESTS_THIS_MONTH: 0,
    KEY_PLACES_REQUESTS_MONTH_KEY: None,
    KEY_DISCOVERY_STATE: {},
    KEY_PAUSE_REASON: None,
    KEY_BOUNCE_PAUSE_RATE: 0.10,
    KEY_BOUNCE_MIN_SAMPLE: 5,
    KEY_LEGAL_CONSENT_VERSION: None,
    KEY_LEGAL_CONSENT_AT: None,
    KEY_ONBOARDING_COMPLETED: False,
    KEY_ONBOARDING_STEP: None,
    KEY_UPDATE_CHECK_ENABLED: True,
    KEY_UPDATE_LAST_CHECK_AT: None,
    KEY_UPDATE_SKIPPED_VERSION: None,
    KEY_UI_THEME: "dark",
    KEY_SCHEMA_VERSION: 0,
}


def _check_key(key: str) -> None:
    if key not in DEFAULTS:
        known = ", ".join(sorted(DEFAULTS))
        raise KeyError(f"Bilinmeyen ayar: {key!r}. Bilinen ayarlar: {known}")


def get_setting(session: Session, key: str) -> Any:
    """Ayarın değerini döndürür; kayıt yoksa varsayılanı verir.

    Değer bir liste/sözlük ise (örn. `KEY_AUTOPILOT_QUERIES`/`KEY_DISCOVERY_STATE`) her zaman
    DERİN BİR KOPYA döner, `DEFAULTS`'un ya da `row.value`'nun kendisi DEĞİL. `MutableDict.
    as_mutable(JSON)` (models.py) yalnızca `Setting.value`'nun EN ÜST seviyesindeki değişimi
    izler; `value["value"]` içindeki iç içe bir liste/sözlük çağıran tarafından yerinde
    değiştirilirse (`state["x"] = 1`) ne SQLAlchemy bunu fark edip commit'e yazar, ne de (kayıt
    hiç yoksa) `DEFAULTS[key]` kalıcı olarak bozulmaktan korunur. Doğru kullanım her zaman:
    oku → yeni bir değer kur → `set_setting()` ile yaz.
    """
    _check_key(key)
    row = session.get(Setting, key)
    if row is None:
        return copy.deepcopy(DEFAULTS[key])
    return copy.deepcopy(row.value.get(VALUE_FIELD, DEFAULTS[key]))


def set_setting(session: Session, key: str, value: Any) -> Setting:
    """Ayarı yazar (varsa günceller) ve kaydı döndürür.

    `flush` edilir, böylece aynı oturumda hemen okunabilir; `commit` çağırana aittir.
    """
    _check_key(key)
    row = session.get(Setting, key)
    if row is None:
        row = Setting(key=key, value={VALUE_FIELD: value})
        session.add(row)
    else:
        row.value = {VALUE_FIELD: value}
    session.flush()
    logger.debug(f"Ayar güncellendi: {key}")  # değer yazılmaz: adres/şablon gibi kişisel veri içerebilir
    return row
