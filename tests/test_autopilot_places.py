"""Google Places'in Otopilot'a bağlanması (PLAN.md Faz 10 Adım 10.6h): kaynak kurulumu (kelime ×
ilçe), maliyet önizlemesi, hata/kota durumunda kalan aramaların atlanması, keşif sonuç mesajı ve —
EN ÖNEMLİSİ — Places KAPALIYKEN davranışın hiç değişmemesi. Gerçek Google/ağ/keyring yok."""
import httpx
import pytest
from sqlmodel import select

from src.autopilot import runner
from src.company_finder import google_places
from src.company_finder.google_places import GooglePlacesClient, GooglePlacesSource
from src.company_finder.teknokent_source import TEKNOKENT_SOURCES
from src.core.cities import CityPreset, load_cities
from src.core.db import get_session
from src.core.models import Company
from src.core.professions import load_profession_presets
from src.core.settings import (
    KEY_AUTOPILOT_CITY,
    KEY_DISCOVERY_STATE,
    KEY_PLACES_MONTHLY_LIMIT,
    KEY_PLACES_REQUESTS_THIS_MONTH,
    KEY_PROFESSION,
    KEY_USE_GOOGLE_PLACES,
    KEY_USE_OSM,
    get_setting,
    set_setting,
)
from src.ui.autopilot_data import format_source_preview
from tests.test_google_places import FakeGoogle, _page

PROFESSION_ID = "yazilim"
QUERIES = load_profession_presets()[PROFESSION_ID].search_queries_tr
ANKARA_DISTRICTS = len(load_cities()["06"].districts)

#: Test anahtarı kasada "kayıtlı" sayılır (gerçek keyring'e dokunulmaz).
KEYS: dict[str, str] = {}


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    KEYS.clear()
    monkeypatch.setattr(runner.secrets, "get_api_key", lambda provider: KEYS.get(provider))

    def _refuse(self, request):
        raise AssertionError("Test gerçek bir ağ isteği atmaya çalıştı")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _refuse)


def _configure(session, *, places: bool, key: bool = True, osm: bool = False, city: str = "06"):
    set_setting(session, KEY_PROFESSION, PROFESSION_ID)
    set_setting(session, KEY_AUTOPILOT_CITY, city)
    set_setting(session, KEY_USE_OSM, osm)
    set_setting(session, KEY_USE_GOOGLE_PLACES, places)
    session.commit()
    if key:
        KEYS["google_places"] = "sahte-anahtar"


def _places_only(sources):
    return [source for source in sources if isinstance(source, GooglePlacesSource)]


# --- Kaynak kurulumu -------------------------------------------------------------------------------


def test_places_on_builds_one_source_per_keyword_and_district_after_teknokent(session):
    _configure(session, places=True)

    sources = runner.resolve_discovery_sources(session)

    assert sources[: len(TEKNOKENT_SOURCES)] == list(TEKNOKENT_SOURCES)
    places = _places_only(sources)
    assert len(places) == len(QUERIES) * ANKARA_DISTRICTS
    assert len(sources) == len(TEKNOKENT_SOURCES) + len(places)
    assert {source.company_source for source in places} == {"google"}
    # Kelime-öncelikli sıra: önce ilk kelime tüm ilçelerde.
    assert [s.state_key.split("|")[0] for s in places[:ANKARA_DISTRICTS]] == [f"google:{QUERIES[0]}"] * ANKARA_DISTRICTS
    # Hepsi aynı istemciyi paylaşır → aylık sayaç/tavan ortak.
    assert len({id(source._client) for source in places}) == 1


def test_places_toggle_on_but_no_key_builds_no_places_source(session):
    _configure(session, places=True, key=False)

    assert runner.resolve_discovery_sources(session) == list(TEKNOKENT_SOURCES)


# --- "Benim kurulumum": Places KAPALI → davranış değişmez ---------------------------------------------


@pytest.mark.parametrize("key_registered", [False, True], ids=["anahtar_yok", "anahtar_var_ama_dugme_kapali"])
def test_places_off_behaves_exactly_as_before(session, monkeypatch, key_registered):
    _configure(session, places=False, key=key_registered)

    def _never(*args, **kwargs):
        raise AssertionError("Places kapalıyken GooglePlacesClient kurulmamalı")

    monkeypatch.setattr(runner, "GooglePlacesClient", _never)
    monkeypatch.setattr(GooglePlacesClient, "search_text", _never)

    sources = runner.resolve_discovery_sources(session)
    preview = runner.build_source_preview(session)
    text, warning = format_source_preview(preview)

    assert sources == list(TEKNOKENT_SOURCES)  # yalnızca teknokent, eskisi gibi
    assert preview.source_ids == ["teknokent"]
    assert preview.places is None
    assert "Google" not in text and "$" not in text and not warning
    profession_name = load_profession_presets()[PROFESSION_ID].name_tr
    assert text == f"Meslek: {profession_name} · Şehir: Ankara — Kaynaklar: Teknokent firma rehberleri."
    assert get_setting(session, KEY_PLACES_REQUESTS_THIS_MONTH) == 0  # sayaç dokunulmadı
    assert get_setting(session, KEY_DISCOVERY_STATE) == {}


def test_places_off_does_not_change_the_discovery_loop_or_its_events(session, monkeypatch):
    """Places kapalı kurulumda keşif döngüsü eskisi gibi: teknokent firmaları eklenir, `done`
    yazılır, hiçbir Google kaynağı/istek yok; olay akışı yalnızca teknokent kaynaklarını anar."""
    from src.company_finder.teknokent_source import TeknokentLead, TeknokentSource

    def fake_discover(*, fetcher=None, sleeper=None):
        yield TeknokentLead(name="Acme", website="https://acme.example.com", teknokent="Sahte")

    fake = TeknokentSource(key="sahte", name="Sahte Teknokent", discover=fake_discover)
    monkeypatch.setattr(runner, "TEKNOKENT_SOURCES", (fake,))
    _configure(session, places=False, key=True)
    events = []

    sources = runner.resolve_discovery_sources(session)
    cursors = runner._build_cursors(session, sources, fetcher=None, sleeper=lambda s: None)
    while runner._advance_discovery(session, cursors, 10, events.append, runner.DiscoveryTally()):
        pass

    assert [c.name for c in session.exec(select(Company)).all()] == ["Acme"]
    assert get_setting(session, KEY_DISCOVERY_STATE) == {"teknokent:sahte": {"done": True}}
    progress = [e.message for e in events if not e.message.startswith("Keşif bitti")]
    assert progress and all("Google" not in message for message in progress)
    assert "Google Places: " not in events[-1].message  # özet Google'dan firma saymaz (yalnızca öneri anar)
    assert get_setting(session, KEY_PLACES_REQUESTS_THIS_MONTH) == 0


# --- Maliyet önizlemesi ------------------------------------------------------------------------------------


def test_preview_with_places_counts_keyword_x_district_x_three_pages(session):
    _configure(session, places=True)
    set_setting(session, KEY_PLACES_REQUESTS_THIS_MONTH, 0)

    preview = runner.build_source_preview(session)

    assert preview.source_ids == ["teknokent", "google_places"]
    places = preview.places
    assert places is not None
    assert (places.keyword_count, places.district_count) == (len(QUERIES), ANKARA_DISTRICTS)
    assert places.pending_searches == len(QUERIES) * ANKARA_DISTRICTS
    assert places.max_requests == len(QUERIES) * ANKARA_DISTRICTS * 3
    assert places.monthly_limit == 900 and places.used_this_month == 0


def test_preview_excludes_already_finished_searches(session):
    _configure(session, places=True)
    first = _places_only(runner.resolve_discovery_sources(session))[0]
    set_setting(session, KEY_DISCOVERY_STATE, {first.state_key: {"done": True}})
    session.commit()

    places = runner.build_source_preview(session).places

    assert places.pending_searches == len(QUERIES) * ANKARA_DISTRICTS - 1


def test_preview_is_read_only_and_makes_no_request(session, monkeypatch):
    _configure(session, places=True)
    monkeypatch.setattr(GooglePlacesClient, "search_text", lambda *a, **k: pytest.fail("istek atıldı"))
    before = get_setting(session, KEY_DISCOVERY_STATE), get_setting(session, KEY_PLACES_REQUESTS_THIS_MONTH)

    runner.build_source_preview(session)

    assert not session.new and not session.dirty and not session.deleted
    assert (get_setting(session, KEY_DISCOVERY_STATE), get_setting(session, KEY_PLACES_REQUESTS_THIS_MONTH)) == before


def test_preview_text_shows_request_counter_free_quota_and_no_warning_within_free(session):
    _configure(session, places=True)
    set_setting(session, KEY_PLACES_REQUESTS_THIS_MONTH, 0)

    text, warning = format_source_preview(runner.build_source_preview(session))

    assert f"en fazla {len(QUERIES) * ANKARA_DISTRICTS * 3} istek" in text
    assert f"{len(QUERIES)} arama kelimesi × {ANKARA_DISTRICTS} ilçe × 3 sayfa" in text
    assert "Bu ay kullanılan / sınır: 0 / 900" in text
    assert "ücretsiz kota içinde" in text and "$0" in text
    assert "son kontrol 30.09.2026" in text and "fiyat değişebilir" in text
    assert not warning


def test_preview_warns_in_red_when_free_quota_would_be_exceeded(session):
    _configure(session, places=True)
    set_setting(session, KEY_PLACES_MONTHLY_LIMIT, 2000)
    set_setting(session, KEY_PLACES_REQUESTS_THIS_MONTH, 900)
    from src.core.models import utcnow
    from src.core.settings import KEY_PLACES_REQUESTS_MONTH_KEY
    from src.core.timeutil import pacific_month_key

    set_setting(session, KEY_PLACES_REQUESTS_MONTH_KEY, pacific_month_key(utcnow()))
    session.commit()

    text, warning = format_source_preview(runner.build_source_preview(session))

    assert warning
    assert "ÜCRETSİZ KOTA AŞILIR" in text and "$" in text and "Google hesabınıza fatura" in text


# --- Uçtan uca keşif: sahte Google ile -------------------------------------------------------------


@pytest.fixture
def small_city(monkeypatch):
    """Ankara, ama yalnızca iki ilçeli; teknokent kapalı → kaynaklar yalnızca Places."""
    city = CityPreset(
        id="06",
        name_tr="Ankara",
        iso_code="TR-06",
        bbox=(32.0, 39.0, 33.0, 40.0),
        center=(32.5, 39.5),
        districts=["Çankaya", "Keçiören"],
    )
    monkeypatch.setattr(runner, "load_cities", lambda: {"06": city})
    monkeypatch.setattr(runner, "TEKNOKENT_SOURCES", ())
    return city


def _use_fake_google(monkeypatch, fake):
    monkeypatch.setattr(
        runner,
        "GooglePlacesClient",
        lambda api_key, session_factory=get_session: GooglePlacesClient(api_key, session_factory, client=fake.client()),
    )


def _run_discovery(session, events, tally):
    sources = runner.resolve_discovery_sources(session)
    cursors = runner._build_cursors(session, sources, fetcher=None, sleeper=lambda s: None)
    while runner._advance_discovery(session, cursors, 10, events.append, tally):
        pass
    return sources


def test_end_to_end_places_discovery_adds_companies_counts_requests_and_reports(session, monkeypatch, small_city):
    _configure(session, places=True)
    total = len(QUERIES) * 2
    fake = FakeGoogle([_page((f"p{i}", f"https://firma{i}.example.com")) for i in range(total)])
    _use_fake_google(monkeypatch, fake)
    events, tally = [], runner.DiscoveryTally(city_name="Ankara")

    _run_discovery(session, events, tally)

    companies = session.exec(select(Company)).all()
    assert len(companies) == total and {c.source for c in companies} == {"google"}
    assert get_setting(session, KEY_PLACES_REQUESTS_THIS_MONTH) == total == len(fake.requests)
    assert all(state == {"done": True} for state in get_setting(session, KEY_DISCOVERY_STATE).values())
    assert len(get_setting(session, KEY_DISCOVERY_STATE)) == total
    summaries = [e.message for e in events if e.message.startswith("Keşif bitti")]
    assert len(summaries) == 1  # tek, en sonda
    assert f"Ankara ilinde {total} yeni firma bulundu (Google Places: {total})" in summaries[0]
    assert events[-1].message == summaries[0]


def test_http_error_stops_remaining_places_searches_and_is_reported(session, monkeypatch, small_city):
    _configure(session, places=True)
    fake = FakeGoogle([(403, {"error": {"message": "API not enabled"}})])
    _use_fake_google(monkeypatch, fake)
    events, tally = [], runner.DiscoveryTally(city_name="Ankara")

    _run_discovery(session, events, tally)

    assert len(fake.requests) == 1  # kalan aramalar hiç denenmedi (kalıcı hata tekrarlanmaz)
    assert get_setting(session, KEY_PLACES_REQUESTS_THIS_MONTH) == 1  # hata dönen istek de sayıldı
    assert get_setting(session, KEY_DISCOVERY_STATE) == {}  # "bitti" yazılmadı → sonra yeniden denenir
    messages = [e.message for e in events]
    assert any("atlandı" in m for m in messages)
    assert "yeniden denenecek" in messages[-1] and "0 yeni firma" in messages[-1]
    assert session.exec(select(Company)).all() == []


def test_monthly_quota_stops_places_without_sending_extra_requests(session, monkeypatch, small_city):
    _configure(session, places=True)
    set_setting(session, KEY_PLACES_MONTHLY_LIMIT, 2)
    session.commit()
    fake = FakeGoogle([_page((f"p{i}", f"https://firma{i}.example.com")) for i in range(10)])
    _use_fake_google(monkeypatch, fake)
    events, tally = [], runner.DiscoveryTally(city_name="Ankara")

    _run_discovery(session, events, tally)

    assert len(fake.requests) == 2  # sınırdan fazla istek atılmadı
    assert len(session.exec(select(Company)).all()) == 2
    assert "aylık istek sınırı doldu" in events[-1].message
    assert len(get_setting(session, KEY_DISCOVERY_STATE)) == 2  # yalnızca tamamlananlar "done"


def test_second_run_does_not_repeat_finished_places_searches(session, monkeypatch, small_city):
    _configure(session, places=True)
    fake = FakeGoogle([_page((f"p{i}", f"https://firma{i}.example.com")) for i in range(len(QUERIES) * 2)])
    _use_fake_google(monkeypatch, fake)
    _run_discovery(session, [], runner.DiscoveryTally())
    requests_after_first = len(fake.requests)

    events = []
    _run_discovery(session, events, runner.DiscoveryTally())

    assert len(fake.requests) == requests_after_first  # hiç yeni istek yok
    assert events == []
    assert runner.build_source_preview(session).places.pending_searches == 0


def test_warning_logged_only_when_key_registered_but_places_unused(session, caplog):
    import logging

    _configure(session, places=False, key=True)
    with caplog.at_level(logging.INFO):
        runner._warn_if_places_key_unused(runner.resolve_discovery_sources(session))
    assert "kullanılmıyor" in caplog.text

    caplog.clear()
    _configure(session, places=True, key=True)
    with caplog.at_level(logging.INFO):
        runner._warn_if_places_key_unused(runner.resolve_discovery_sources(session))
    assert "Google Places" not in caplog.text


def test_places_sources_share_failure_stops_siblings_flag():
    assert GooglePlacesSource.failure_stops_siblings is True
    assert google_places.MAX_PAGES_PER_QUERY == 3
