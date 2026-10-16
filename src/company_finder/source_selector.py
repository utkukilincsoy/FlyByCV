"""Keşif kaynağı seçici — PLAN.md Adım 10.6c.

Meslek paketi + şehir + ayarlara göre HANGİ otomatik keşif kaynaklarının kullanılacağını söyleyen
SAF bir fonksiyon (`select_sources`): ağa, veritabanına, keyring'e dokunmaz. Ayarları/anahtarı
okumak ayrı (`load_source_settings`), böylece seçim mantığı testte sahte değerlerle denenir.

Seçici Otopilot'a 10.6d'de bağlandı (`autopilot/runner.py::resolve_discovery_sources`); OSM ve
Google Places seçilse bile kaynakları henüz uygulanmadığı için (10.6e/10.6f) atlanır. CSV ile
içe aktarma bu seçiciye girmez (her zaman açık, kullanıcının kendi elle yaptığı bir iş).
"""
import logging
from dataclasses import dataclass
from typing import Mapping, Optional

from sqlmodel import Session

from src.core import secrets
from src.core.cities import CityPreset, load_cities
from src.core.professions import ProfessionPreset
from src.core.settings import KEY_USE_GOOGLE_PLACES, KEY_USE_OSM, get_setting

logger = logging.getLogger(__name__)

SOURCE_TEKNOKENT = "teknokent"
SOURCE_OSM = "osm"
SOURCE_GOOGLE_PLACES = "google_places"

#: Teknokent kaynakları (`teknokent_source.TEKNOKENT_SOURCES`: ODTÜ, OSTİM, Hacettepe, Ankara)
#: yalnızca Ankara'dakileri tarar; il plaka kodu. Yeni bir ilin teknokenti eklenirse buraya.
TEKNOKENT_CITY_IDS: frozenset[str] = frozenset({"06"})

#: `secrets.get_api_key()` için Google Places anahtar adı (`autopilot/runner.py`deki
#: `GOOGLE_PLACES_API_KEY_NAME` ile aynı değer; o modül ağır bağımlılık çektiği için burada
#: tekrar tanımlandı).
GOOGLE_PLACES_API_KEY_NAME = "google_places"


@dataclass(frozen=True)
class SourceSettings:
    """`select_sources`'un ihtiyaç duyduğu ayarlar. `has_places_key` anahtarın KAYITLI olup
    olmadığıdır (değerin kendisi asla buraya gelmez, CLAUDE.md kural 6)."""

    use_osm: bool
    use_google_places: bool
    has_places_key: bool


def load_source_settings(session: Session) -> SourceSettings:
    """Ayarları ve keyring'deki Places anahtarının varlığını okur."""
    return SourceSettings(
        use_osm=bool(get_setting(session, KEY_USE_OSM)),
        use_google_places=bool(get_setting(session, KEY_USE_GOOGLE_PLACES)),
        has_places_key=bool(secrets.get_api_key(GOOGLE_PLACES_API_KEY_NAME)),
    )


def select_sources(
    profession: Optional[ProfessionPreset],
    city: Optional[str],
    settings: SourceSettings,
    cities: Optional[Mapping[str, CityPreset]] = None,
) -> list[str]:
    """Kullanılacak keşif kaynaklarını sabit sırayla (teknokent, osm, google_places) döner.

    - **teknokent**: meslek `technical` VE şehir teknokenti tanımlı bir il (`TEKNOKENT_CITY_IDS`).
    - **osm**: paket `osm_tags` taşıyor, `use_osm` açık VE şehir `cities`'te (bbox lazım).
    - **google_places**: `use_google_places` düğmesi açık VE anahtar kayıtlı VE şehir geçerli.
      Anahtar varlığı TEK BAŞINA yetmez — habersiz para harcanmasın.

    Meslek ya da şehir seçilmemişse (`None`) ya da şehir kodu bilinmiyorsa hiçbir kaynak seçilmez.
    `cities` verilmezse `load_cities()` kullanılır (testler kendi sözlüğünü verebilir).
    """
    if profession is None or city is None:
        return []

    known_cities = cities if cities is not None else load_cities()
    city_known = city in known_cities

    sources: list[str] = []
    if profession.technical and city in TEKNOKENT_CITY_IDS:
        sources.append(SOURCE_TEKNOKENT)
    if settings.use_osm and profession.osm_tags and city_known:
        sources.append(SOURCE_OSM)
    if settings.use_google_places and settings.has_places_key and city_known:
        sources.append(SOURCE_GOOGLE_PLACES)
    return sources
