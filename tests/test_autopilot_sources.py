"""Otopilot'un çok kaynaklı keşfi (PLAN.md Faz 10 Adım 10.6d): `resolve_discovery_sources`,
`DiscoverySource` arayüzü ve `import_leads`. Ağ/keyring yok."""
import httpx
import pytest
from sqlmodel import select

from src.autopilot import runner
from src.company_finder import discovery, osm_source
from src.company_finder.osm_source import OsmSource
from src.company_finder.csv_import import SOURCE_TEKNOKENT
from src.company_finder.teknokent_source import (
    TEKNOKENT_SOURCES,
    TeknokentLead,
    TeknokentSource,
)
from src.core.models import Company
from src.core.settings import (
    KEY_AUTOPILOT_CITY,
    KEY_DISCOVERY_STATE,
    KEY_PROFESSION,
    KEY_USE_GOOGLE_PLACES,
    KEY_USE_OSM,
    get_setting,
    set_setting,
)


@pytest.fixture(autouse=True)
def no_real_keyring(monkeypatch):
    monkeypatch.setattr(runner.secrets, "get_api_key", lambda provider: None)


def _configure(session, *, profession, city, osm):
    set_setting(session, KEY_PROFESSION, profession)
    set_setting(session, KEY_AUTOPILOT_CITY, city)
    set_setting(session, KEY_USE_OSM, osm)
    set_setting(session, KEY_USE_GOOGLE_PLACES, False)
    session.commit()


# --- "Benim kurulumum": yazılım + Ankara(06) + OSM kapalı -> yalnızca teknokent -----------------


def test_existing_user_setup_resolves_only_teknokent(session):
    _configure(session, profession="yazilim", city="06", osm=False)

    sources = runner.resolve_discovery_sources(session)

    assert sources == list(TEKNOKENT_SOURCES)
    assert [source.state_key for source in sources] == [
        "teknokent:odtu",
        "teknokent:ostim",
        "teknokent:hacettepe",
    ]
    assert {source.company_source for source in sources} == {SOURCE_TEKNOKENT}


def test_osm_enabled_adds_real_osm_source_after_teknokent(session):
    _configure(session, profession="yazilim", city="06", osm=True)

    sources = runner.resolve_discovery_sources(session)

    assert sources[:-1] == list(TEKNOKENT_SOURCES)
    osm = sources[-1]
    assert isinstance(osm, OsmSource)
    assert osm.state_key == "osm:yazilim|06"
    assert osm.company_source == "osm"


def test_osm_source_is_marked_done_with_osm_key_and_imports_as_osm(session, tmp_path, monkeypatch):
    monkeypatch.setattr(osm_source, "fetch_overpass", lambda *a, **k: [
        {"type": "node", "id": 1, "tags": {"name": "Acme", "website": "https://acme.example.com"}}
    ])
    monkeypatch.setattr(osm_source, "cache_dir", lambda: tmp_path)
    _configure(session, profession="yazilim", city="06", osm=True)
    source = runner.resolve_discovery_sources(session)[-1]

    cursors = runner._build_cursors(session, [source], fetcher=None, sleeper=lambda s: None)
    assert runner._advance_discovery(session, cursors, batch_size=10) is True

    company = session.exec(select(Company)).one()
    assert company.source == "osm"
    assert get_setting(session, KEY_DISCOVERY_STATE) == {"osm:yazilim|06": {"done": True}}


def test_failed_osm_source_is_not_marked_done(session, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("ağ yok")

    monkeypatch.setattr(osm_source, "fetch_overpass", boom)
    monkeypatch.setattr(osm_source, "cache_dir", lambda: tmp_path)
    _configure(session, profession="yazilim", city="06", osm=True)
    source = runner.resolve_discovery_sources(session)[-1]

    cursors = runner._build_cursors(session, [source], fetcher=None, sleeper=lambda s: None)
    runner._advance_discovery(session, cursors, batch_size=10)

    assert get_setting(session, KEY_DISCOVERY_STATE) == {}


@pytest.mark.parametrize(
    ("profession", "city"),
    [("hukuk", "06"), ("yazilim", "35")],
    ids=["teknik_degil", "baska_sehir"],
)
def test_no_teknokent_when_selector_rejects(session, profession, city):
    _configure(session, profession=profession, city=city, osm=False)

    assert runner.resolve_discovery_sources(session) == []


@pytest.mark.parametrize(("profession", "city"), [(None, "06"), ("yazilim", None), (None, None)])
def test_unconfigured_setup_keeps_legacy_teknokent(session, profession, city):
    """Meslek/şehir hiç seçilmemişse (taze kurulum) eski davranış: tüm teknokent kaynakları."""
    _configure(session, profession=profession, city=city, osm=False)

    assert runner.resolve_discovery_sources(session) == list(TEKNOKENT_SOURCES)


# --- Seçilen kaynak Otopilot'un keşif döngüsünde eskisi gibi çalışır ----------------------------


def test_resolved_source_is_discovered_and_marked_done_with_same_state_key(session, monkeypatch):
    def fake_discover(*, fetcher=None, sleeper=None):
        yield TeknokentLead(name="Acme", website="https://acme.example.com", teknokent="Sahte")

    fake = TeknokentSource(key="sahte", name="Sahte Teknokent", discover=fake_discover)
    monkeypatch.setattr(runner, "TEKNOKENT_SOURCES", (fake,))
    _configure(session, profession="yazilim", city="06", osm=False)

    sources = runner.resolve_discovery_sources(session)
    cursors = runner._build_cursors(session, sources, fetcher=None, sleeper=lambda seconds: None)
    assert runner._advance_discovery(session, cursors, batch_size=10) is True

    company = session.exec(select(Company)).first()
    assert company is not None
    assert company.source == SOURCE_TEKNOKENT
    assert get_setting(session, KEY_DISCOVERY_STATE) == {"teknokent:sahte": {"done": True}}


# --- import_leads: kaynaktan bağımsız ortak içe aktarma -----------------------------------------


class _Lead:
    def __init__(self, name, website=None, email=None, city="İzmir"):
        self.name, self.website, self.email, self.city = name, website, email, city


def test_import_leads_uses_given_company_source_and_collects_skips(session):
    leads = [
        _Lead("Acme", website="https://acme.example.com"),
        _Lead("Hayalet"),  # site de e-posta da yok -> atlanır
    ]

    report = discovery.import_leads(session, iter(leads), company_source="osm", label="Deneme")

    assert [row.domain for row in report.added] == ["acme.example.com"]
    assert [note.name for note in report.skipped] == ["Hayalet"]
    company = session.exec(select(Company)).first()
    assert company.source == "osm"
    assert company.city == "İzmir"
