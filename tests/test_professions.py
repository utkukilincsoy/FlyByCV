"""src/core/professions.py testleri (PLAN.md Faz 10 Adım 10.5).

Gerçek `presets/professions/*.json` paketleri (proje köküne bağlı, salt okunur) hem
şema doğrulaması hem de "en az 12 meslek + iç mimarlık/peyzaj mimarlığı var" şartı için
kullanılır. Bozuk paket senaryoları ise `tmp_path` altında SENTETİK dosyalarla test
edilir -- gerçek `presets/` klasörüne hiç dokunulmaz.
"""
import json
import logging

import pytest

from src.core.paths import professions_dir
from src.core.professions import (
    REQUIRED_TARGETS,
    ProfessionPreset,
    load_profession_presets,
)
from src.core.settings_migration import LEGACY_AUTOPILOT_QUERIES


def _valid_raw(profession_id: str, **overrides) -> dict:
    raw = {
        "id": profession_id,
        "name_tr": "Test Meslek",
        "technical": False,
        "search_queries_tr": ["a", "b", "c"],
        "company_types": ["a", "b", "c"],
        "default_mail_templates": {
            "job": {"subject": "s", "body": "b"},
            "internship": {"subject": "s", "body": "b"},
            "volunteer_internship": {"subject": "s", "body": "b"},
            "part_time": {"subject": "s", "body": "b"},
        },
    }
    raw.update(overrides)
    return raw


def test_real_packages_load_without_error():
    presets = load_profession_presets()
    assert len(presets) >= 12


def test_real_packages_have_ic_mimarlik_and_peyzaj_mimarligi():
    presets = load_profession_presets()
    assert "ic_mimarlik" in presets
    assert "peyzaj_mimarligi" in presets


def test_real_packages_each_have_min_queries_and_company_types():
    presets = load_profession_presets()
    for profession_id, preset in presets.items():
        assert len(preset.search_queries_tr) >= 3, profession_id
        assert len(preset.company_types) >= 3, profession_id
        assert set(preset.default_mail_templates) == set(REQUIRED_TARGETS), profession_id


def test_yazilim_package_matches_legacy_computer_engineering_behavior():
    """Bilgisayar mühendisliği davranışı bu adımdan ETKİLENMEDİ: 'yazilim' paketi eski
    sabit `autopilot_queries` varsayılanıyla (settings_migration.LEGACY_AUTOPILOT_QUERIES)
    birebir aynı arama kelimelerini taşır ve teknokent kaynağına uygun işaretlenir."""
    presets = load_profession_presets()
    yazilim = presets["yazilim"]
    assert yazilim.search_queries_tr == LEGACY_AUTOPILOT_QUERIES
    assert yazilim.technical is True


def test_professions_dir_points_at_real_folder_with_json_files():
    directory = professions_dir()
    assert directory.is_dir()
    assert list(directory.glob("*.json"))


def test_broken_json_is_skipped_not_raised(tmp_path, caplog):
    (tmp_path / "broken.json").write_text("{not valid json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = load_profession_presets(tmp_path)
    assert result == {}
    assert "broken.json" in caplog.text


def test_schema_violation_is_skipped_not_raised(tmp_path, caplog):
    raw = _valid_raw("bad", search_queries_tr=["only-one"])
    (tmp_path / "bad.json").write_text(json.dumps(raw), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = load_profession_presets(tmp_path)
    assert result == {}
    assert "bad.json" in caplog.text


def test_id_filename_mismatch_is_skipped(tmp_path, caplog):
    raw = _valid_raw("does_not_match_filename")
    (tmp_path / "actual_filename.json").write_text(json.dumps(raw), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = load_profession_presets(tmp_path)
    assert result == {}
    assert "actual_filename.json" in caplog.text


def test_unknown_mail_placeholder_is_rejected(tmp_path, caplog):
    raw = _valid_raw("bad_placeholder")
    raw["default_mail_templates"]["job"]["subject"] = "{bilinmeyen} pozisyonu"
    (tmp_path / "bad_placeholder.json").write_text(json.dumps(raw), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = load_profession_presets(tmp_path)
    assert result == {}


def test_missing_target_is_rejected(tmp_path):
    raw = _valid_raw("missing_target")
    del raw["default_mail_templates"]["volunteer_internship"]
    (tmp_path / "missing_target.json").write_text(json.dumps(raw), encoding="utf-8")
    result = load_profession_presets(tmp_path)
    assert result == {}


def test_package_with_the_reserved_other_id_is_skipped(tmp_path, caplog):
    """`diger` "Diğer" seçeneğine ayrılmıştır (core/custom_profession.py): o id'li dosya paketi atlanır."""
    (tmp_path / "diger.json").write_text(json.dumps(_valid_raw("diger")), encoding="utf-8")
    (tmp_path / "gecerli.json").write_text(json.dumps(_valid_raw("gecerli")), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = load_profession_presets(tmp_path)
    assert set(result) == {"gecerli"}
    assert "ayrılmıştır" in caplog.text


def test_osm_tags_is_optional_and_defaults_to_none():
    preset = ProfessionPreset.model_validate(_valid_raw("no_osm"))
    assert preset.osm_tags is None


def test_osm_tags_accepts_known_wiki_tags(tmp_path):
    raw = _valid_raw("with_osm", osm_tags=["office=it"])
    (tmp_path / "with_osm.json").write_text(json.dumps(raw), encoding="utf-8")
    result = load_profession_presets(tmp_path)
    assert result["with_osm"].osm_tags == ["office=it"]


def test_osm_tags_rejects_made_up_tag(tmp_path, caplog):
    raw = _valid_raw("fake_osm", osm_tags=["office=made_up_value"])
    (tmp_path / "fake_osm.json").write_text(json.dumps(raw), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = load_profession_presets(tmp_path)
    assert result == {}
    assert "fake_osm.json" in caplog.text


def test_osm_tags_rejects_empty_list(tmp_path):
    raw = _valid_raw("empty_osm", osm_tags=[])
    (tmp_path / "empty_osm.json").write_text(json.dumps(raw), encoding="utf-8")
    result = load_profession_presets(tmp_path)
    assert result == {}


def test_osm_tags_rejects_duplicates(tmp_path):
    raw = _valid_raw("dup_osm", osm_tags=["office=it", "office=it"])
    (tmp_path / "dup_osm.json").write_text(json.dumps(raw), encoding="utf-8")
    result = load_profession_presets(tmp_path)
    assert result == {}


@pytest.mark.parametrize(
    "tag",
    ["office=graphic_design", "shop=interior_decoration", "craft=gardener", "tourism=hotel"],
)
def test_osm_tags_accepts_shop_craft_and_other_documented_keys(tmp_path, tag):
    raw = _valid_raw("multi_key", osm_tags=[tag])
    (tmp_path / "multi_key.json").write_text(json.dumps(raw), encoding="utf-8")
    assert load_profession_presets(tmp_path)["multi_key"].osm_tags == [tag]


def test_osm_tags_rejects_undocumented_value_of_documented_key(tmp_path):
    """`craft=` anahtarı belgeli, ama `craft=landscape_architect` wiki'de yok (uydurma)."""
    raw = _valid_raw("fake_craft", osm_tags=["craft=landscape_architect"])
    (tmp_path / "fake_craft.json").write_text(json.dumps(raw), encoding="utf-8")
    assert load_profession_presets(tmp_path) == {}


def test_real_packages_osm_tags_match_documented_wiki_tags():
    """PLAN.md Adım 10.6b: `osm_tags` alanı İSTEĞE BAĞLI kalır (şema testi yukarıda), ama
    bugün 16 gerçek paketin hepsinin OSM wiki'de belgeli en az bir karşılığı var."""
    presets = load_profession_presets()
    assert all(p.osm_tags for p in presets.values())
    assert presets["yazilim"].osm_tags == ["office=it"]
    assert presets["ic_mimarlik"].osm_tags == ["office=architect"]
    assert presets["peyzaj_mimarligi"].osm_tags == ["craft=gardener", "office=architect"]
    assert presets["grafik_tasarim"].osm_tags == ["office=advertising_agency", "office=graphic_design"]


def test_missing_directory_returns_empty_and_warns(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        result = load_profession_presets(tmp_path / "does_not_exist")
    assert result == {}
    assert "does_not_exist" in caplog.text


def test_profession_preset_id_must_be_slug():
    raw = _valid_raw("Not A Slug!")
    try:
        ProfessionPreset.model_validate(raw)
        assert False, "geçersiz id kabul edilmemeliydi"
    except Exception:
        pass
