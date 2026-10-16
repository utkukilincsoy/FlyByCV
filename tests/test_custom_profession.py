"""src/core/custom_profession.py ve "Diğer" mesleğin tüketicilerdeki davranışı (PLAN.md 10.2g/10.5c).

Kullanıcının yazdığı ad + virgülle arama kelimeleri bellekte paket olur: teknokent yok, OpenStreetMap
atlanır, Google Places aramaları kullanıcının kelimeleriyle yapılır, mail şablonu genel şablondan gelir ve
{meslek} yazılan ad olur. Gerçek ağ/keyring yok.
"""
import httpx
import pytest

from src.autopilot import runner
from src.company_finder.google_places import GooglePlacesSource
from src.company_finder.source_selector import SourceSettings, select_sources
from src.core.cities import load_cities
from src.core.custom_profession import (
    CUSTOM_PROFESSION_ID,
    MAX_KEYWORDS,
    build_custom_preset,
    parse_keywords,
    presets_with_custom,
    resolve_profession,
    validate_custom_profession,
)
from src.core.professions import load_profession_presets
from src.core.settings import (
    KEY_AUTOPILOT_CITY,
    KEY_CUSTOM_PROFESSION_KEYWORDS,
    KEY_CUSTOM_PROFESSION_NAME,
    KEY_MAIL_TEMPLATE_BODY,
    KEY_MAIL_TEMPLATE_SUBJECT,
    KEY_PROFESSION,
    KEY_USE_GOOGLE_PLACES,
    KEY_USE_OSM,
    set_setting,
)
from src.cv_builder.application_builder import render_current_mail_text
from src.ui.settings_data import (
    SettingsForm,
    default_form,
    suggested_mail_template,
    validate_form,
)
from tests.conftest import build_company, build_sample_profile

NAME = "Diş hekimliği"
KEYWORDS = ["diş kliniği", "ortodonti"]


def _choose_custom(session, name=NAME, keywords=KEYWORDS, **extra):
    set_setting(session, KEY_PROFESSION, CUSTOM_PROFESSION_ID)
    set_setting(session, KEY_CUSTOM_PROFESSION_NAME, name)
    set_setting(session, KEY_CUSTOM_PROFESSION_KEYWORDS, list(keywords))
    for key, value in extra.items():
        set_setting(session, key, value)
    session.commit()


# --- ayrıştırma / doğrulama / paket kurma --------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("diş kliniği, ortodonti", ["diş kliniği", "ortodonti"]),
        ("  a ,b,, c  ,", ["a", "b", "c"]),
        ("Ortodonti, ortodonti, ORTODONTİ", ["Ortodonti", "ORTODONTİ"]),  # casefold: i/İ ayrı kalır
        ("a,\nb\nc", ["a", "b", "c"]),
        ("iki   boşluk,  tek", ["iki boşluk", "tek"]),
        ("", []),
        (" , ,", []),
    ],
)
def test_parse_keywords(text, expected):
    assert parse_keywords(text) == expected


def test_parse_keywords_keeps_order_and_drops_case_duplicates():
    assert parse_keywords("B, a, b, A, c") == ["B", "a", "c"]


def test_validate_accepts_a_normal_input():
    assert validate_custom_profession(NAME, KEYWORDS) == []
    assert validate_custom_profession("x", ["tek kelime"]) == []  # dosya paketlerindeki "≥3" şartı yok
    assert validate_custom_profession(NAME, [f"k{i}" for i in range(MAX_KEYWORDS)]) == []


@pytest.mark.parametrize(
    "name,keywords,fragment",
    [
        (None, KEYWORDS, "Meslek adını yazın"),
        ("   ", KEYWORDS, "Meslek adını yazın"),
        ("a" * 61, KEYWORDS, "en çok 60 karakter"),
        ("Diş {meslek}", KEYWORDS, "kullanılamaz"),
        (NAME, [], "En az bir arama kelimesi"),
        (NAME, [f"k{i}" for i in range(MAX_KEYWORDS + 1)], f"En çok {MAX_KEYWORDS}"),
        (NAME, ["k" * 61], "Her arama kelimesi"),
    ],
)
def test_validate_errors(name, keywords, fragment):
    errors = validate_custom_profession(name, keywords)
    assert len(errors) == 1 and fragment in errors[0]


def test_validate_reports_name_and_keyword_errors_together():
    assert len(validate_custom_profession("", [])) == 2


def test_build_custom_preset_has_the_documented_shape():
    preset = build_custom_preset("  Diş hekimliği ", KEYWORDS)

    assert preset.id == CUSTOM_PROFESSION_ID
    assert preset.name_tr == "Diş hekimliği"
    assert preset.search_queries_tr == KEYWORDS
    assert preset.technical is False  # teknokent yok
    assert preset.osm_tags is None  # OpenStreetMap atlanır
    assert preset.default_mail_templates == {}  # öneri genel şablondan gelir
    assert preset.company_types == []


def test_presets_with_custom_adds_only_a_valid_custom_entry():
    base = load_profession_presets()

    with_custom = presets_with_custom(base, NAME, KEYWORDS)
    assert with_custom[CUSTOM_PROFESSION_ID].name_tr == NAME
    assert set(with_custom) == set(base) | {CUSTOM_PROFESSION_ID}
    assert CUSTOM_PROFESSION_ID not in base  # girdi sözlüğü değişmez

    assert CUSTOM_PROFESSION_ID not in presets_with_custom(base, "", KEYWORDS)
    assert CUSTOM_PROFESSION_ID not in presets_with_custom(base, NAME, [])


# --- resolve_profession ------------------------------------------------------------------------------


def test_resolve_returns_none_without_a_choice(session):
    assert resolve_profession(session) is None


def test_resolve_listed_profession_matches_the_file_package(session):
    set_setting(session, KEY_PROFESSION, "hukuk")
    session.commit()

    assert resolve_profession(session) == load_profession_presets()["hukuk"]


def test_resolve_unknown_profession_is_none(session):
    set_setting(session, KEY_PROFESSION, "yok_boyle_meslek")
    session.commit()

    assert resolve_profession(session) is None


def test_resolve_other_builds_the_preset_from_settings(session):
    _choose_custom(session)

    preset = resolve_profession(session)

    assert preset is not None
    assert (preset.name_tr, preset.search_queries_tr) == (NAME, KEYWORDS)


@pytest.mark.parametrize("name,keywords", [(None, KEYWORDS), (NAME, []), ("", [])])
def test_resolve_other_with_broken_settings_is_none_not_a_crash(session, name, keywords):
    _choose_custom(session, name=name, keywords=keywords)

    assert resolve_profession(session) is None


def test_resolve_accepts_an_explicit_presets_mapping(session):
    set_setting(session, KEY_PROFESSION, "x")
    session.commit()
    fake = load_profession_presets()["hukuk"].model_copy(update={"id": "x"})

    assert resolve_profession(session, {"x": fake}) is fake


# --- tüketiciler -------------------------------------------------------------------------------------


def test_source_selector_skips_teknokent_and_osm_for_other():
    preset = build_custom_preset(NAME, KEYWORDS)
    cities = load_cities()
    everything_on = SourceSettings(use_osm=True, use_google_places=True, has_places_key=True)

    # Ankara: teknokent şehri; yine de technical=False olduğu için teknokent yok, osm_tags yok → OSM yok.
    assert select_sources(preset, "06", everything_on, cities) == ["google_places"]
    assert select_sources(preset, "06", SourceSettings(True, False, False), cities) == []


def test_other_profession_google_places_searches_use_the_users_keywords(session, monkeypatch):
    monkeypatch.setattr(runner.secrets, "get_api_key", lambda provider: "sahte-anahtar")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda self, request: pytest.fail("ağ"))
    _choose_custom(session, **{KEY_AUTOPILOT_CITY: "06", KEY_USE_OSM: True, KEY_USE_GOOGLE_PLACES: True})
    districts = len(load_cities()["06"].districts)

    sources = runner.resolve_discovery_sources(session)

    assert all(isinstance(source, GooglePlacesSource) for source in sources)  # teknokent/OSM yok
    assert len(sources) == len(KEYWORDS) * districts
    assert {source.state_key.split("|")[0] for source in sources} == {f"google:{k}" for k in KEYWORDS}


def test_other_profession_without_places_has_no_sources_and_preview_names_it(session, monkeypatch):
    monkeypatch.setattr(runner.secrets, "get_api_key", lambda provider: None)
    _choose_custom(session, **{KEY_AUTOPILOT_CITY: "06", KEY_USE_OSM: True, KEY_USE_GOOGLE_PLACES: False})

    assert runner.resolve_discovery_sources(session) == []
    preview = runner.build_source_preview(session)
    assert preview.profession_name == NAME
    assert preview.source_ids == []


def test_mail_template_meslek_placeholder_is_the_users_name(session):
    _choose_custom(
        session,
        **{
            KEY_MAIL_TEMPLATE_SUBJECT: "{meslek} başvurusu – {sirket_adi}",
            KEY_MAIL_TEMPLATE_BODY: "{sirket_adi} için {meslek} olarak başvuruyorum.",
        },
    )

    subject, body = render_current_mail_text(session, build_company(name="Acme"), build_sample_profile())

    assert subject == f"{NAME} başvurusu – Acme"
    assert body == f"Acme için {NAME} olarak başvuruyorum."


def test_suggested_template_for_other_falls_back_to_the_generic_one():
    from src.core.mail_template_presets import load_generic_mail_templates

    generic = load_generic_mail_templates()
    presets = presets_with_custom(load_profession_presets(), NAME, KEYWORDS)

    suggestion = suggested_mail_template("internship", CUSTOM_PROFESSION_ID, "tr", presets, generic)

    assert suggestion == generic[("internship", "tr")]


# --- Ayarlar doğrulaması -------------------------------------------------------------------------------


def _form(**overrides) -> SettingsForm:
    from dataclasses import replace

    return replace(default_form(), **overrides)


def test_validate_form_accepts_other_with_valid_fields():
    assert validate_form(_form(profession=CUSTOM_PROFESSION_ID, custom_profession_name=NAME,
                               custom_profession_keywords=KEYWORDS)) == ()


def test_validate_form_rejects_other_without_name_or_keywords():
    errors = validate_form(_form(profession=CUSTOM_PROFESSION_ID))
    assert any("Meslek adını yazın" in e for e in errors)
    assert any("arama kelimesi" in e for e in errors)


def test_validate_form_ignores_custom_fields_for_a_listed_profession():
    assert validate_form(_form(profession="hukuk", custom_profession_name="", custom_profession_keywords=[])) == ()


def test_validate_form_still_rejects_unknown_professions():
    assert any("Bilinmeyen meslek" in e for e in validate_form(_form(profession="yok_boyle_meslek")))
