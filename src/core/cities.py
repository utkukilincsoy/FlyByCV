"""81 il listesi: `presets/cities.json` — PLAN.md Adım 10.6a.

Amaç: kaynak seçici (10.6c) ve keşif kaynakları (OSM/Overpass 10.6e, Google Places 10.6f)
hangi şehrin sınırları içinde arama yapacağını bu dosyadan öğrensin; il adı/plaka/koordinat
kodda veya başka bir modülde TEKRAR yazılmasın. `presets/cities.json`, `scripts/
generate_cities.py` ile BİR KERELİK (OpenStreetMap Nominatim'den) üretilip commit edilir —
uygulama çalışırken bu dosyayı yalnızca OKUR, ağa hiç çıkmaz.

`professions.py` ile AYNI ilke: bozuk/eksik dosya uygulamayı ÇÖKERTMEZ, yalnızca uyarı
loglanır ve boş liste dönülür (Otopilot/Ayarlar şehir seçimini boş görür, çökmez).
"""
import json
import logging
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, field_validator

from src.core.paths import cities_path

logger = logging.getLogger(__name__)


class CityPreset(BaseModel):
    """Bir il. `bbox`/`center` OSM/Google Places gibi kaynaklarda "bu şehrin sınırları
    içinde ara" demek için kullanılır — tam idari sınır DEĞİL, dikdörtgen bir yaklaşıklık
    (bounding box); şehir merkezine uzak ama il sınırları içindeki birkaç ilçe kenardan
    taşabilir/dışarıda kalabilir, bu Otopilot için kabul edilebilir bir yaklaşıklıktır."""

    id: str  # plaka kodu, iki haneli string ("06")
    name_tr: str
    iso_code: str  # "TR-06"
    #: [min_lon, min_lat, max_lon, max_lat] — Overpass/Google Places `locationBias` bbox sırası.
    bbox: tuple[float, float, float, float]
    center: tuple[float, float]  # [lon, lat]
    #: Gerçek ilçe adları (yalnız büyük illerde; kaynak: `cities.json` başındaki `districts_source`).
    #: Boşsa il ilçelere bölünmeden aranır. Google Places (10.6f) sorgusunu ilçe ilçe ayırmak için.
    districts: list[str] = []

    @field_validator("id")
    @classmethod
    def id_is_two_digit(cls, value: str) -> str:
        if not (len(value) == 2 and value.isdigit()):
            raise ValueError("id iki haneli rakam olmalı (ör. '06')")
        return value

    @field_validator("name_tr")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name_tr boş olamaz")
        return value

    @field_validator("districts")
    @classmethod
    def districts_valid(cls, value: list[str]) -> list[str]:
        cleaned = [d.strip() for d in value]
        if any(not d for d in cleaned):
            raise ValueError("districts içinde boş ilçe adı olamaz")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("districts içinde yinelenen ilçe adı var")
        return cleaned

    @field_validator("iso_code")
    @classmethod
    def iso_code_format(cls, value: str) -> str:
        if not value.startswith("TR-") or len(value) != 5:
            raise ValueError("iso_code 'TR-NN' biçiminde olmalı")
        return value

    @field_validator("bbox")
    @classmethod
    def bbox_ordered(cls, value: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        min_lon, min_lat, max_lon, max_lat = value
        if not (min_lon < max_lon and min_lat < max_lat):
            raise ValueError("bbox [min_lon, min_lat, max_lon, max_lat] sırasında ve min < max olmalı")
        return value


def load_cities(path: Optional[Path] = None) -> dict[str, CityPreset]:
    """Tüm geçerli illeri `plaka kodu -> CityPreset` olarak döner.

    `path` verilmezse `paths.cities_path()` kullanılır (testler kendi geçici dosyasını
    verebilir). Dosya yoksa/bozuksa/şemaya uymuyorsa çökmek yerine uyarı loglayıp boş
    sözlük döner — 10.6b/10.6c bu boş sözlüğü "hiçbir şehir seçilemiyor" olarak ele alır,
    uygulama açılmaya devam eder."""
    file_path = path if path is not None else cities_path()
    if not file_path.is_file():
        logger.warning(f"İl listesi dosyası bulunamadı: {file_path}")
        return {}

    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"İl listesi okunamadı: {file_path}: {e}")
        return {}

    entries = raw.get("cities") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        logger.warning(f"İl listesi beklenen şemada değil ('cities' listesi yok): {file_path}")
        return {}

    cities: dict[str, CityPreset] = {}
    for entry in entries:
        try:
            city = CityPreset.model_validate(entry)
        except Exception as e:  # pydantic ValidationError + tip hataları (liste değil vb.)
            logger.warning(f"İl kaydı şemaya uymuyor, atlandı: {entry!r}: {e}")
            continue
        if city.id in cities:
            logger.warning(f"Yinelenen il kodu '{city.id}', ikinci kayıt atlandı")
            continue
        cities[city.id] = city

    logger.info(f"{len(cities)} il yüklendi")
    return cities
