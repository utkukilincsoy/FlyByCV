"""src/core/cities.py testleri (PLAN.md Faz 10 Adım 10.6a).

Gerçek `presets/cities.json` (proje köküne bağlı, salt okunur, `scripts/generate_cities.py`
ile bir kerelik üretildi) 81 il + şema doğrulaması için kullanılır. Bozuk dosya senaryoları
ise `tmp_path` altında SENTETİK dosyalarla test edilir -- gerçek `presets/` klasörüne hiç
dokunulmaz (`test_professions.py` ile aynı ilke).
"""
import json
import logging

from src.core.cities import CityPreset, load_cities
from src.core.paths import cities_path


def _valid_entry(city_id: str = "99", **overrides) -> dict:
    entry = {
        "id": city_id,
        "name_tr": "Test İli",
        "iso_code": f"TR-{city_id}",
        "bbox": [32.0, 39.0, 33.0, 40.0],
        "center": [32.5, 39.5],
    }
    entry.update(overrides)
    return entry


def test_real_cities_file_loads_all_81():
    cities = load_cities()
    assert len(cities) == 81
    assert set(cities) == {f"{i:02d}" for i in range(1, 82)}


def test_real_ankara_entry_is_06():
    cities = load_cities()
    ankara = cities["06"]
    assert ankara.name_tr == "Ankara"
    assert ankara.iso_code == "TR-06"
    min_lon, min_lat, max_lon, max_lat = ankara.bbox
    assert min_lon < max_lon
    assert min_lat < max_lat


def test_real_ids_are_unique_and_two_digit():
    cities = load_cities()
    for city_id, city in cities.items():
        assert city_id == city.id
        assert len(city_id) == 2
        assert city_id.isdigit()


def test_missing_file_returns_empty_dict_without_crash(tmp_path, caplog):
    missing = tmp_path / "does_not_exist.json"
    with caplog.at_level(logging.WARNING):
        cities = load_cities(missing)
    assert cities == {}
    assert "bulunamadı" in caplog.text


def test_invalid_json_returns_empty_dict_without_crash(tmp_path, caplog):
    broken = tmp_path / "cities.json"
    broken.write_text("{not valid json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        cities = load_cities(broken)
    assert cities == {}
    assert "okunamadı" in caplog.text


def test_missing_cities_key_returns_empty_dict(tmp_path):
    wrong_schema = tmp_path / "cities.json"
    wrong_schema.write_text(json.dumps({"not_cities": []}), encoding="utf-8")
    assert load_cities(wrong_schema) == {}


def test_one_bad_entry_is_skipped_others_still_load(tmp_path, caplog):
    path = tmp_path / "cities.json"
    path.write_text(
        json.dumps({"cities": [_valid_entry("01"), {"id": "02", "name_tr": ""}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        cities = load_cities(path)
    assert set(cities) == {"01"}
    assert "atlandı" in caplog.text


def test_duplicate_id_second_entry_skipped(tmp_path, caplog):
    path = tmp_path / "cities.json"
    path.write_text(
        json.dumps(
            {"cities": [_valid_entry("01", name_tr="Birinci"), _valid_entry("01", name_tr="İkinci")]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        cities = load_cities(path)
    assert len(cities) == 1
    assert cities["01"].name_tr == "Birinci"
    assert "Yinelenen" in caplog.text


def test_bbox_must_be_min_before_max():
    bad = _valid_entry(bbox=[33.0, 39.0, 32.0, 40.0])  # min_lon > max_lon
    try:
        CityPreset.model_validate(bad)
        assert False, "ValueError bekleniyordu"
    except Exception as e:
        assert "bbox" in str(e)


def test_default_path_points_inside_presets_dir():
    assert cities_path().name == "cities.json"
    assert cities_path().parent.name == "presets"


# --- ilçeler (Google Places sorgusunu bölmek için; kaynak cities.json'da `districts_source`) ---

#: Vikipedi "X'in ilçeleri" sayfalarından (30.09.2026) doğrulanmış ilçe sayıları.
EXPECTED_DISTRICT_COUNTS = {
    "Adana": 15,
    "Ankara": 25,
    "Antalya": 19,
    "Bursa": 17,
    "İstanbul": 39,
    "İzmir": 30,
    "Konya": 31,
}


def test_real_big_cities_have_their_real_district_counts():
    by_name = {c.name_tr: c for c in load_cities().values()}
    for name, count in EXPECTED_DISTRICT_COUNTS.items():
        assert len(by_name[name].districts) == count, name
    # Listesi olmayan il ilçelere bölünmez.
    with_districts = {c.name_tr for c in by_name.values() if c.districts}
    assert with_districts == set(EXPECTED_DISTRICT_COUNTS)


def test_real_district_spot_checks():
    cities = load_cities()
    assert {"Çankaya", "Keçiören", "Yenimahalle"} <= set(cities["06"].districts)
    assert {"Kadıköy", "Beşiktaş", "Şişli"} <= set(cities["34"].districts)
    assert "Konyaaltı" in cities["07"].districts
    assert "Selçuklu" in cities["42"].districts


def test_real_cities_json_documents_districts_source():
    raw = json.loads(cities_path().read_text(encoding="utf-8"))
    assert "Vikipedi" in raw["districts_source"]


def test_districts_default_to_empty():
    assert CityPreset.model_validate(_valid_entry()).districts == []


def test_districts_reject_blank_and_duplicate_names():
    import pytest

    with pytest.raises(ValueError):
        CityPreset.model_validate(_valid_entry(districts=["Merkez", " "]))
    with pytest.raises(ValueError):
        CityPreset.model_validate(_valid_entry(districts=["Merkez", "Merkez"]))
