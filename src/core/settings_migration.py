"""Ayar şeması göçleri (PLAN.md Faz 10 Adım 10.1b/10.1c/10.4/10.6c/10.2a; v3: şehir metni → plaka
kodu + var olan kullanıcıda OpenStreetMap kapalı; v4: var olan kullanıcıda ilk açılış sihirbazı
tamamlandı sayılır).

Faz 10'dan önce `autopilot_city`/`autopilot_queries` (bkz. `core/settings.py`) TEK bir
kullanıcı için sabit varsayılana sahipti (Ankara, yazılım/bilişim aramaları) — v1 göçü bunu
korur (Adım 10.1d). Adım 10.4 iki yeni ayar ekledi: `application_target`/`profession`
("Hedef"/"Meslek") — v2 göçü var olan kullanıcının o güne kadarki TEK davranışını (gönüllü/
karşılıksız staj arayan bir yazılım/bilgisayar mühendisliği öğrencisi) aynı şekilde korur. Bu
modül uygulama açılışında BİR KEZ (`core.db.init_db()`'den) çalışarak var olan bir kullanıcının
o güne kadar VARSAYILANDAN gelen değerlerini veritabanına AÇIKÇA yazar, sonra
`schema_version`'ı günceller — bir daha asla tekrar çalışmaz (idempotent, her açılışta
çağrılması güvenlidir).

**"Var olan kullanıcı" tespiti:** `Setting` tablosunda `schema_version` DIŞINDA en az bir
satır VARSA (kullanıcı Ayarlar'ı en az bir kez kullanmış ya da Otopilot en az bir kez bir iç
durum yazmış demektir) — `schema_version`'ın KENDİSİ bu tespitte SAYILMAZ, aksi halde "önceki
bir göç yalnızca `schema_version`'ı yazdı ama başka hiçbir gerçek ayar hiç yazılmadı" durumundaki
TAZE bir kurulum yanlışlıkla "var olan kullanıcı" sayılıp LEGACY değerlerle kirlenirdi. Tablo
TAMAMEN boşsa (taze kurulum) göç hiçbir şey YAZMAZ — genel varsayılanlar (None/boş liste) zaten
doğru davranıştır, kurulum sihirbazı (Adım 10.2) kullanıcıdan soracak.
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from src.core.data_migration import _copy_sqlite_database
from src.core.models import Setting
from src.core.settings import (
    KEY_APPLICATION_TARGET,
    KEY_AUTOPILOT_CITY,
    KEY_AUTOPILOT_QUERIES,
    KEY_ONBOARDING_COMPLETED,
    KEY_PROFESSION,
    KEY_SCHEMA_VERSION,
    KEY_USE_OSM,
    VALUE_FIELD,
    set_setting,
)
from src.core.cities import load_cities
from src.cv_builder.mail_template import TARGET_VOLUNTEER_INTERNSHIP

logger = logging.getLogger(__name__)

#: `KEY_SCHEMA_VERSION`'ın anahtar METNİ `core/settings.py`de tanımlı (DEFAULTS'a kayıtlı
#: olması gerekiyor, bkz. `settings._check_key`); göçün kendi mantığı olan HEDEF sürüm ise
#: burada -- bu modülün sahip olduğu tek bilgi.
CURRENT_SCHEMA_VERSION = 4

#: Faz 10 öncesi TEK varsayılandı (bkz. modül docstring'i); kod artık genel varsayılana
#: geçtiği için var olan kullanıcının değeri göç sırasında AÇIKÇA veritabanına yazılarak korunur.
LEGACY_AUTOPILOT_CITY = "Ankara"
LEGACY_AUTOPILOT_QUERIES = [
    "yazılım şirketi",
    "bilişim şirketi",
    "siber güvenlik şirketi",
    "ağ ve network çözümleri",
    "gömülü sistemler",
    "elektronik donanım tasarım",
]

#: v1 -> v2 (Faz 10 Adım 10.4): "Hedef"/"Meslek" Ayarlar'a bu göçte eklendi. Var olan
#: kullanıcının o güne kadarki TEK davranışı (gönüllü/karşılıksız staj arayan bir yazılım/
#: bilgisayar mühendisliği öğrencisi) açıkça yazılır -- `LEGACY_AUTOPILOT_QUERIES` ile aynı
#: mantık, bu adımdan ETKİLENMEDİ.
LEGACY_APPLICATION_TARGET = TARGET_VOLUNTEER_INTERNSHIP
LEGACY_PROFESSION = "yazilim"

#: v2 -> v3 (Adım 10.6c): `autopilot_city` serbest metinden il PLAKA KODUNA geçti. Var olan
#: kullanıcının tek değeri "Ankara" idi; `presets/cities.json` okunamasa bile çalışsın diye kodda
#: da sabit tutulur (diğer metinler `load_cities()` adıyla eşlenir).
LEGACY_AUTOPILOT_CITY_CODE = "06"
#: v3: OpenStreetMap kaynağı var olan kullanıcıda KAPALI yazılır (kullanıcı kararı 30.09.2026).
LEGACY_USE_OSM = False


#: v3 -> v4 (Adım 10.2a): ilk açılış sihirbazı eklendi. Var olan kullanıcı kurulumu zaten elle
#: yapmış demektir; sihirbaz ona otomatik açılmaz.
LEGACY_ONBOARDING_COMPLETED = True


def _city_name_to_code(name: str) -> Optional[str]:
    """Eski serbest metin şehir adını plaka koduna çevirir; bulunamazsa `None`."""
    if name == LEGACY_AUTOPILOT_CITY:
        return LEGACY_AUTOPILOT_CITY_CODE
    wanted = name.strip().casefold()
    for city in load_cities().values():
        if city.name_tr.casefold() == wanted:
            return city.id
    return None


def _backup_database(db_path: Path) -> None:
    """Göçten ÖNCE `app.db`'nin zaman damgalı bir kopyasını alır (Adım 10.1c).

    WAL modunda (bkz. `core.db.configure_sqlite_engine`) ham bir dosya kopyası commit edilmiş
    ama henüz checkpoint edilmemiş satırları kaybedebilir; `data_migration._copy_sqlite_database`
    zaten bunu çözen çevrimiçi yedekleme API'sini kullanıyor, burada TEKRAR YAZILMADI.
    Kopyalama başarısız olursa göç YİNE DE dener — yedeksiz kalmak, kullanıcının o günkü
    çalışmasını hiç göçmeden bırakmaktan daha az kötü bir risktir; hata loglanır, fırlatılmaz.
    """
    backup_path = db_path.with_name(f"{db_path.name}.bak-{datetime.now():%Y%m%d%H%M%S}")
    try:
        _copy_sqlite_database(db_path, backup_path)
    except Exception:
        logger.exception(f"Göç öncesi veritabanı yedeklenemedi: {db_path}")
        return
    logger.info(f"Göç öncesi veritabanı yedeklendi: {backup_path}")


def run_settings_migrations(session: Session, *, db_path: Optional[Path] = None) -> None:
    """Uygulama açılışında BİR KEZ çağrılır (`core.db.init_db()`'den hemen sonra).

    Zaten güncel bir veritabanında (`schema_version >= CURRENT_SCHEMA_VERSION`) hiçbir şey
    yapmaz. `db_path` verilmezse (ör. bellek içi test veritabanı) yedekleme atlanır — yedeğe
    zaten gerek yoktur.

    "Var olan kullanıcı" tespiti `schema_version` satırının VARLIĞINA değil, `schema_version`
    DIŞINDA en az bir `Setting` satırının var olup olmadığına bakar: taze bir kurulum, önceki
    bir göçün yalnızca `schema_version`'ı yazdığı bir durumdan (o kurulum hâlâ tazedir, hiçbir
    LEGACY değer zorla yazılmamalı) böyle ayrılır. Bu, v1'den (yalnızca `autopilot_city`/
    `autopilot_queries`) v2'ye (Adım 10.4: `application_target`/`profession`) geçerken de aynı
    kalır -- her sürüm adımı kendi LEGACY yazımını, o adıma özel `current_version < N` koşuluyla
    YAPAR YA DA YAPMAZ, "var olan kullanıcı" olma durumu tek bir hesaplama.
    """
    schema_row = session.get(Setting, KEY_SCHEMA_VERSION)
    current_version = schema_row.value.get(VALUE_FIELD, 0) if schema_row is not None else 0
    if current_version >= CURRENT_SCHEMA_VERSION:
        return

    other_rows_exist = (
        session.exec(select(Setting.key).where(Setting.key != KEY_SCHEMA_VERSION).limit(1)).first()
        is not None
    )
    is_existing_user = other_rows_exist

    if is_existing_user and db_path is not None and db_path.exists():
        _backup_database(db_path)

    if is_existing_user and current_version < 1:
        # Kullanıcı bu ayarları zaten kendi (ör. ileride bir Ayarlar formu ile) yazmışsa o
        # değere ASLA dokunulmaz — yalnızca satır hiç yoksa eski varsayılan açıkça yazılır.
        if session.get(Setting, KEY_AUTOPILOT_CITY) is None:
            set_setting(session, KEY_AUTOPILOT_CITY, LEGACY_AUTOPILOT_CITY)
        if session.get(Setting, KEY_AUTOPILOT_QUERIES) is None:
            set_setting(session, KEY_AUTOPILOT_QUERIES, list(LEGACY_AUTOPILOT_QUERIES))
        logger.info(
            "Ayar şeması göçü (v1): var olan kullanıcının önceki varsayılanları (şehir: "
            "Ankara, yazılım/bilişim aramaları) veritabanına açıkça yazıldı"
        )

    if is_existing_user and current_version < 2:
        if session.get(Setting, KEY_APPLICATION_TARGET) is None:
            set_setting(session, KEY_APPLICATION_TARGET, LEGACY_APPLICATION_TARGET)
        if session.get(Setting, KEY_PROFESSION) is None:
            set_setting(session, KEY_PROFESSION, LEGACY_PROFESSION)
        logger.info(
            "Ayar şeması göçü (v2): var olan kullanıcının önceki hedefi/mesleği (gönüllü "
            "staj, yazılım) veritabanına açıkça yazıldı"
        )

    if is_existing_user and current_version < 3:
        _migrate_city_to_code(session)
        if session.get(Setting, KEY_USE_OSM) is None:
            set_setting(session, KEY_USE_OSM, LEGACY_USE_OSM)
        logger.info(
            "Ayar şeması göçü (v3): şehir plaka koduna çevrildi, OpenStreetMap kaynağı var olan "
            "kullanıcı için KAPALI yazıldı"
        )

    if is_existing_user and current_version < 4:
        if session.get(Setting, KEY_ONBOARDING_COMPLETED) is None:
            set_setting(session, KEY_ONBOARDING_COMPLETED, LEGACY_ONBOARDING_COMPLETED)
        logger.info(
            "Ayar şeması göçü (v4): var olan kullanıcı için ilk açılış sihirbazı tamamlandı "
            "olarak işaretlendi (otomatik açılmaz)"
        )

    set_setting(session, KEY_SCHEMA_VERSION, CURRENT_SCHEMA_VERSION)
    session.commit()


def _migrate_city_to_code(session: Session) -> None:
    """`autopilot_city` eski metinse ("Ankara") plaka koduna ("06") çevirir.

    Zaten iki haneli koddaysa dokunmaz. Tanınmayan bir metin (bugün hiçbir arayüz serbest metin
    yazdırmıyor, yalnızca elle düzenlenmiş bir veritabanında olur) `None`'a çekilir ve loglanır
    -- geçersiz bir değer Ayarlar'ı kaydetmeyi sonsuza dek engellerdi."""
    row = session.get(Setting, KEY_AUTOPILOT_CITY)
    if row is None:
        return
    value = row.value.get(VALUE_FIELD)
    if value is None or (isinstance(value, str) and len(value) == 2 and value.isdigit()):
        return
    code = _city_name_to_code(value) if isinstance(value, str) else None
    if code is None:
        logger.warning(f"autopilot_city={value!r} bilinen bir ile karşılık gelmiyor, boşaltıldı")
    set_setting(session, KEY_AUTOPILOT_CITY, code)
