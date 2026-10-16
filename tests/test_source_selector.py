"""src/company_finder/source_selector.py testleri (PLAN.md Faz 10 Adım 10.6c). Saf fonksiyon:
ağ/keyring yok; `load_source_settings` için keyring sahte."""
import itertools

import pytest

from src.company_finder.source_selector import (
    SOURCE_GOOGLE_PLACES,
    SOURCE_OSM,
    SOURCE_TEKNOKENT,
    SourceSettings,
    load_source_settings,
    select_sources,
)
from src.core.cities import load_cities
from src.core.professions import load_profession_presets
from src.core.settings import KEY_USE_GOOGLE_PLACES, KEY_USE_OSM, set_setting

CITIES = load_cities()
PRESETS = load_profession_presets()
TECHNICAL = PRESETS["yazilim"]  # technical=True, osm_tags var
NON_TECHNICAL = PRESETS["hukuk"]  # technical=False, osm_tags var


def _settings(osm=False, places=False, key=False) -> SourceSettings:
    return SourceSettings(use_osm=osm, use_google_places=places, has_places_key=key)


def test_real_fixtures_are_what_the_matrix_assumes():
    assert TECHNICAL.technical and TECHNICAL.osm_tags
    assert not NON_TECHNICAL.technical and NON_TECHNICAL.osm_tags
    assert "06" in CITIES and "35" in CITIES


def test_existing_user_setup_selects_only_teknokent():
    """Göçten sonraki var olan kurulum: yazılım + Ankara(06) + OSM KAPALI + Places KAPALI."""
    assert select_sources(TECHNICAL, "06", _settings(osm=False), CITIES) == [SOURCE_TEKNOKENT]


@pytest.mark.parametrize("profession", [TECHNICAL, NON_TECHNICAL], ids=["teknik", "teknik_degil"])
@pytest.mark.parametrize("city", ["06", "35"], ids=["ankara", "izmir"])
@pytest.mark.parametrize("osm", [False, True], ids=["osm_kapali", "osm_acik"])
@pytest.mark.parametrize("places", [False, True], ids=["places_kapali", "places_acik"])
def test_full_matrix_with_key_registered(profession, city, osm, places):
    expected = []
    if profession.technical and city == "06":
        expected.append(SOURCE_TEKNOKENT)
    if osm:
        expected.append(SOURCE_OSM)
    if places:
        expected.append(SOURCE_GOOGLE_PLACES)

    result = select_sources(profession, city, _settings(osm=osm, places=places, key=True), CITIES)

    assert result == expected


def test_places_needs_both_toggle_and_key():
    """Anahtar varlığı TEK BAŞINA yetmez, düğme TEK BAŞINA da yetmez."""
    for places, key in itertools.product([False, True], repeat=2):
        result = select_sources(TECHNICAL, "35", _settings(places=places, key=key), CITIES)
        assert (SOURCE_GOOGLE_PLACES in result) is (places and key)


def test_key_alone_never_selects_places():
    assert SOURCE_GOOGLE_PLACES not in select_sources(
        TECHNICAL, "06", _settings(osm=True, places=False, key=True), CITIES
    )


def test_osm_skipped_when_package_has_no_osm_tags():
    bare = TECHNICAL.model_copy(update={"osm_tags": None})
    assert select_sources(bare, "35", _settings(osm=True), CITIES) == []
    assert select_sources(bare, "06", _settings(osm=True), CITIES) == [SOURCE_TEKNOKENT]


def test_no_profession_or_no_city_selects_nothing():
    everything_on = _settings(osm=True, places=True, key=True)
    assert select_sources(None, "06", everything_on, CITIES) == []
    assert select_sources(TECHNICAL, None, everything_on, CITIES) == []


def test_unknown_city_code_selects_no_bbox_sources_but_keeps_teknokent_for_ankara():
    everything_on = _settings(osm=True, places=True, key=True)
    assert select_sources(TECHNICAL, "99", everything_on, CITIES) == []
    # il dosyası okunamadıysa (boş sözlük) kayıtlı Ankara için teknokent yine çalışır
    assert select_sources(TECHNICAL, "06", everything_on, {}) == [SOURCE_TEKNOKENT]


def test_order_is_teknokent_osm_places():
    result = select_sources(TECHNICAL, "06", _settings(osm=True, places=True, key=True), CITIES)
    assert result == [SOURCE_TEKNOKENT, SOURCE_OSM, SOURCE_GOOGLE_PLACES]


def test_select_sources_loads_cities_file_when_not_given():
    assert select_sources(TECHNICAL, "35", _settings(osm=True)) == [SOURCE_OSM]


def test_load_source_settings_reads_settings_and_key_presence(session, monkeypatch):
    monkeypatch.setattr("src.company_finder.source_selector.secrets.get_api_key", lambda name: None)
    assert load_source_settings(session) == SourceSettings(
        use_osm=True, use_google_places=False, has_places_key=False
    )

    set_setting(session, KEY_USE_OSM, False)
    set_setting(session, KEY_USE_GOOGLE_PLACES, True)
    monkeypatch.setattr(
        "src.company_finder.source_selector.secrets.get_api_key",
        lambda name: "gizli" if name == "google_places" else None,
    )
    loaded = load_source_settings(session)
    assert loaded == SourceSettings(use_osm=False, use_google_places=True, has_places_key=True)
    assert "gizli" not in repr(loaded)  # anahtarın kendisi asla taşınmaz
