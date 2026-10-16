"""Google Places istemcisi (PLAN.md Adım 10.6f). Gerçek Google'a HİÇ bağlanılmaz:
`httpx.MockTransport` sahte yanıt verir, gerçek taşıma katmanı fixture'la engellenir."""
import json
from datetime import datetime

import httpx
import pytest
from sqlmodel import select

from src.company_finder.google_places import (
    FIELD_MASK,
    KEY_TEST_FIELD_MASK,
    PLACES_HARD_MAX_MONTHLY,
    GooglePlacesClient,
    GooglePlacesSource,
    PlacesError,
    PlacesQuotaExceeded,
    build_request_body,
    check_api_key,
    known_place_ids,
    monthly_limit,
    parse_page,
    places_sources,
    record_place_ref,
    remaining_requests,
    requests_this_month,
    reserve_request,
)
from src.core.cities import CityPreset
from src.core.db import get_session
from src.core.models import Company, PlaceRef
from src.core.settings import (
    KEY_DISCOVERY_STATE,
    KEY_PLACES_MONTHLY_LIMIT,
    KEY_PLACES_REQUESTS_MONTH_KEY,
    KEY_PLACES_REQUESTS_THIS_MONTH,
    get_setting,
    set_setting,
)
from src.core.timeutil import pacific_month_key

NOW = datetime(2026, 9, 30, 12, 0)  # naive UTC
BBOX = (32.0, 39.0, 33.0, 40.0)


@pytest.fixture(autouse=True)
def no_real_google(monkeypatch):
    def _refuse(self, request):
        raise AssertionError("Test gerçek bir ağ isteği atmaya çalıştı")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _refuse)


@pytest.fixture
def city():
    return CityPreset(
        id="06", name_tr="Ankara", iso_code="TR-06", bbox=BBOX, center=(32.5, 39.5)
    )


@pytest.fixture
def factory(engine):
    return lambda: get_session(engine=engine)


class FakeGoogle:
    """`responses` sırayla tüketilir: (durum kodu, gövde)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self._responses.pop(0)
        return httpx.Response(status, json=body)

    def body(self, index: int = -1) -> dict:
        return json.loads(self.requests[index].content)


def _page(*places, token=None):
    body = {"places": [{"id": pid, **({"websiteUri": site} if site else {})} for pid, site in places]}
    if token:
        body["nextPageToken"] = token
    return (200, body)


def _client(fake, factory, now=NOW, key="test-key"):
    return GooglePlacesClient(key, factory, client=fake.client(), now=lambda: now)


# --- İstek biçimi ---------------------------------------------------------------------------


def test_field_mask_is_exactly_the_three_allowed_fields():
    assert FIELD_MASK == "places.id,places.websiteUri,nextPageToken"


def test_request_body_uses_region_bias_rectangle_and_repeats_params_for_paging():
    first = build_request_body("mimarlık ofisi", BBOX, None)
    assert first["textQuery"] == "mimarlık ofisi" and first["regionCode"] == "TR"
    assert first["locationBias"]["rectangle"] == {
        "low": {"latitude": 39.0, "longitude": 32.0},
        "high": {"latitude": 40.0, "longitude": 33.0},
    }
    assert "pageToken" not in first
    second = build_request_body("mimarlık ofisi", BBOX, "TOKEN")
    assert second.pop("pageToken") == "TOKEN" and second == first


def test_search_sends_key_and_mask_in_headers_not_url(factory):
    fake = FakeGoogle([_page(("p1", "https://a.com"))])
    page = _client(fake, factory).search_text("mimarlık", BBOX)
    request = fake.requests[0]
    assert request.headers["X-Goog-Api-Key"] == "test-key"
    assert request.headers["X-Goog-FieldMask"] == FIELD_MASK
    assert "test-key" not in str(request.url)
    assert [(h.place_id, h.website_uri) for h in page.places] == [("p1", "https://a.com")]
    assert page.next_page_token is None


def test_parse_page_handles_empty_and_odd_payloads():
    assert parse_page({}).places == []  # sonuç yoksa `places` alanı gelmez
    page = parse_page({"places": [{"id": "a"}, {"websiteUri": "x"}, "x", {"id": ""}], "nextPageToken": ""})
    assert [h.place_id for h in page.places] == ["a"] and page.next_page_token is None
    with pytest.raises(PlacesError):
        parse_page([])


# --- Hatalar --------------------------------------------------------------------------------


def test_http_error_raises_places_error_without_leaking_key(factory):
    fake = FakeGoogle([(403, {"error": {"message": "API key not valid"}})])
    with pytest.raises(PlacesError) as info:
        _client(fake, factory).search_text("x", BBOX)
    assert info.value.status_code == 403 and "test-key" not in str(info.value)


def test_network_error_is_wrapped_and_key_free(factory):
    def boom(request):
        raise httpx.ConnectError("bağlanılamadı", request=request)

    client = GooglePlacesClient(
        "test-key", factory, client=httpx.Client(transport=httpx.MockTransport(boom)), now=lambda: NOW
    )
    with pytest.raises(PlacesError) as info:
        client.search_text("x", BBOX)
    assert "test-key" not in str(info.value)


def test_missing_key_or_blank_query_sends_nothing_and_counts_nothing(factory, session):
    fake = FakeGoogle([])
    with pytest.raises(PlacesError):
        _client(fake, factory, key="").search_text("x", BBOX)
    with pytest.raises(ValueError):
        _client(fake, factory).search_text("  ", BBOX)
    assert fake.requests == [] and requests_this_month(session, NOW) == 0


# --- Aylık sayaç ----------------------------------------------------------------------------


def test_every_request_is_counted_including_failed_ones(factory, session):
    fake = FakeGoogle([(500, {}), _page(("p1", None))])
    client = _client(fake, factory)
    with pytest.raises(PlacesError):
        client.search_text("x", BBOX)
    client.search_text("x", BBOX)
    session.expire_all()
    assert requests_this_month(session, NOW) == 2


def test_counter_is_committed_before_the_request_is_sent(factory, session):
    seen = []

    def handler(request):
        with factory() as other:
            seen.append(requests_this_month(other, NOW))
        return httpx.Response(200, json={})

    client = GooglePlacesClient("k", factory, client=httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: NOW)
    client.search_text("x", BBOX)
    assert seen == [1]


def test_request_is_never_sent_once_limit_is_reached(factory, session):
    set_setting(session, KEY_PLACES_MONTHLY_LIMIT, 2)
    session.commit()
    fake = FakeGoogle([_page(), _page()])
    client = _client(fake, factory)
    client.search_text("x", BBOX)
    client.search_text("x", BBOX)
    with pytest.raises(PlacesQuotaExceeded):
        client.search_text("x", BBOX)
    assert len(fake.requests) == 2
    session.expire_all()
    assert requests_this_month(session, NOW) == 2 and remaining_requests(session, NOW) == 0


def test_limit_above_hard_max_is_clipped_and_logged(session, caplog):
    set_setting(session, KEY_PLACES_MONTHLY_LIMIT, 50_000)
    with caplog.at_level("WARNING"):
        assert monthly_limit(session) == PLACES_HARD_MAX_MONTHLY == 2000
    assert "sabit" in caplog.text


def test_hard_max_actually_stops_requests_at_2000(session):
    set_setting(session, KEY_PLACES_MONTHLY_LIMIT, 99_999)
    set_setting(session, KEY_PLACES_REQUESTS_MONTH_KEY, pacific_month_key(NOW))
    set_setting(session, KEY_PLACES_REQUESTS_THIS_MONTH, PLACES_HARD_MAX_MONTHLY - 1)
    session.commit()
    assert reserve_request(session, NOW) is True
    assert reserve_request(session, NOW) is False
    assert requests_this_month(session, NOW) == PLACES_HARD_MAX_MONTHLY


@pytest.mark.parametrize("bad", [None, "900", 1.5, True])
def test_invalid_limit_falls_back_to_default(session, bad):
    set_setting(session, KEY_PLACES_MONTHLY_LIMIT, bad)
    assert monthly_limit(session) == 900


def test_negative_limit_blocks_everything(session):
    set_setting(session, KEY_PLACES_MONTHLY_LIMIT, -5)
    assert monthly_limit(session) == 0 and reserve_request(session, NOW) is False


def test_default_limit_is_900(session):
    assert monthly_limit(session) == 900


def test_counter_resets_when_month_changes(session):
    assert reserve_request(session, NOW) and reserve_request(session, NOW)
    assert requests_this_month(session, NOW) == 2
    next_month = datetime(2026, 10, 15, 12, 0)
    assert requests_this_month(session, next_month) == 0
    assert reserve_request(session, next_month)
    assert requests_this_month(session, next_month) == 1
    assert get_setting(session, KEY_PLACES_REQUESTS_MONTH_KEY) == "2026-10"


def test_month_boundary_follows_pacific_time():
    # 1 Ekim 02:00 UTC hâlâ 30 Eylül 19:00 Pasifik: Google'ın fatura ayı Eylül.
    assert pacific_month_key(datetime(2026, 10, 1, 2, 0)) == "2026-09"
    assert pacific_month_key(datetime(2026, 10, 1, 8, 0)) == "2026-10"


# --- PlaceRef -------------------------------------------------------------------------------


def test_place_ref_is_keyed_by_place_id_and_upgrades_company_link(session):
    company = Company(name="A", domain="a.com", source="google")
    session.add(company)
    session.commit()
    record_place_ref(session, "p1")
    record_place_ref(session, "p1", company.id)
    record_place_ref(session, "p1")  # bağ geri silinmez
    assert known_place_ids(session, ["p1", "p2"]) == {"p1"}
    assert session.get(PlaceRef, "p1").company_id == company.id
    assert known_place_ids(session, []) == set()


# --- Kaynak ---------------------------------------------------------------------------------


def _source(fake, factory, city, query="mimarlık ofisi"):
    return GooglePlacesSource(query, city, _client(fake, factory), session_factory=factory)


def test_source_identity(factory, city):
    source = _source(FakeGoogle([]), factory, city)
    assert source.state_key == "google:mimarlık ofisi|06"
    assert source.company_source == "google"


def test_source_yields_leads_named_from_domain_and_skips_sitless_and_social(factory, city, session):
    fake = FakeGoogle(
        [_page(("p1", "https://www.xyzmimarlik.com/"), ("p2", None), ("p3", "https://facebook.com/x"))]
    )
    leads = list(_source(fake, factory, city).discover())
    assert [(l.name, l.website, l.place_id, l.city) for l in leads] == [
        ("Xyzmimarlik", "https://www.xyzmimarlik.com/", "p1", "Ankara")
    ]
    assert known_place_ids(session, ["p1", "p2", "p3"]) == {"p1", "p2", "p3"}
    assert len(fake.requests) == 1


def test_known_places_are_not_yielded_again(factory, city, session):
    record_place_ref(session, "p1")
    fake = FakeGoogle([_page(("p1", "https://a.com"), ("p2", "https://b.com"))])
    assert [l.place_id for l in _source(fake, factory, city).discover()] == ["p2"]


def test_place_ref_is_recorded_only_after_consumer_takes_the_lead(factory, city, session):
    fake = FakeGoogle([_page(("p1", "https://a.com"), ("p2", "https://b.com"))])
    generator = _source(fake, factory, city).discover()
    assert next(generator).place_id == "p1"
    session.expire_all()
    assert known_place_ids(session, ["p1", "p2"]) == set()  # tüketilmeden yazılmaz
    assert next(generator).place_id == "p2"
    session.expire_all()
    assert known_place_ids(session, ["p1", "p2"]) == {"p1"}
    generator.close()  # p2 hiç devam ettirilmedi: kayıtsız kalır, sonra yeniden sunulur
    session.expire_all()
    assert known_place_ids(session, ["p2"]) == set()


def test_company_link_is_filled_when_company_already_exists(factory, city, session):
    company = Company(name="A", domain="a.com", source="csv")
    session.add(company)
    session.commit()
    fake = FakeGoogle([_page(("p1", "https://www.a.com"))])
    list(_source(fake, factory, city).discover())
    session.expire_all()
    assert session.get(PlaceRef, "p1").company_id == company.id


def test_paging_repeats_query_and_sends_token(factory, city):
    fake = FakeGoogle([_page(("p1", "https://a.com"), token="T1"), _page(("p2", "https://b.com"))])
    leads = list(_source(fake, factory, city).discover())
    assert [l.place_id for l in leads] == ["p1", "p2"]
    assert "pageToken" not in fake.body(0) and fake.body(1)["pageToken"] == "T1"
    assert {k: v for k, v in fake.body(1).items() if k != "pageToken"} == fake.body(0)


def test_token_is_saved_after_each_page_and_cleared_at_the_end(factory, city, session):
    fake = FakeGoogle([_page(("p1", "https://a.com"), token="T1"), _page(("p2", "https://b.com"))])
    generator = _source(fake, factory, city).discover()
    next(generator)
    next(generator)  # 1. sayfa tamamen işlendi, 2. sayfa istendi
    session.expire_all()
    assert get_setting(session, KEY_DISCOVERY_STATE)["google:mimarlık ofisi|06"] == {"next_page_token": "T1"}
    list(generator)
    session.expire_all()
    assert "google:mimarlık ofisi|06" not in get_setting(session, KEY_DISCOVERY_STATE)


def test_resumes_from_saved_token_in_a_new_process(factory, city, session):
    state = {"google:mimarlık ofisi|06": {"next_page_token": "T9"}, "teknokent:odtu": {"done": True}}
    set_setting(session, KEY_DISCOVERY_STATE, state)
    session.commit()
    fake = FakeGoogle([_page(("p5", "https://e.com"))])
    assert [l.place_id for l in _source(fake, factory, city).discover()] == ["p5"]
    assert fake.body(0)["pageToken"] == "T9"
    session.expire_all()
    assert get_setting(session, KEY_DISCOVERY_STATE) == {"teknokent:odtu": {"done": True}}


def test_rejected_saved_token_restarts_from_first_page_once(factory, city, session):
    set_setting(session, KEY_DISCOVERY_STATE, {"google:mimarlık ofisi|06": {"next_page_token": "OLD"}})
    session.commit()
    fake = FakeGoogle([(400, {"error": {"message": "bad token"}}), _page(("p1", "https://a.com"))])
    source = _source(fake, factory, city)
    assert [l.place_id for l in source.discover()] == ["p1"]
    assert "pageToken" in fake.body(0) and "pageToken" not in fake.body(1)
    assert source.failed is False


def test_quota_exhaustion_stops_source_keeps_token_and_marks_failed(factory, city, session):
    set_setting(session, KEY_PLACES_MONTHLY_LIMIT, 1)
    session.commit()
    fake = FakeGoogle([_page(("p1", "https://a.com"), token="T1")])
    source = _source(fake, factory, city)
    assert [l.place_id for l in source.discover()] == ["p1"]
    assert source.failed is True and "sınırı" in source.failure_reason
    assert len(fake.requests) == 1
    session.expire_all()
    assert get_setting(session, KEY_DISCOVERY_STATE)["google:mimarlık ofisi|06"] == {"next_page_token": "T1"}


def test_http_failure_marks_source_failed_without_crashing(factory, city):
    fake = FakeGoogle([(500, {})])
    source = _source(fake, factory, city)
    assert list(source.discover()) == [] and source.failed is True


def test_leads_import_through_add_company_with_google_source(factory, city, session):
    from src.company_finder.discovery import import_leads

    fake = FakeGoogle([_page(("p1", "https://www.xyzmimarlik.com/iletisim"))])
    source = _source(fake, factory, city)
    report = import_leads(session, source.discover(), company_source=source.company_source)
    assert len(report.added) == 1
    row = session.exec(select(Company)).one()
    assert (row.domain, row.source, row.name) == ("xyzmimarlik.com", "google", "Xyzmimarlik")



# --- İlçe bölme ------------------------------------------------------------------------------


@pytest.fixture
def big_city():
    return CityPreset(
        id="06", name_tr="Ankara", iso_code="TR-06", bbox=BBOX, center=(32.5, 39.5),
        districts=["Çankaya", "Keçiören", "Yenimahalle"],
    )


def test_query_text_is_keyword_district_city_and_city_only_without_districts(factory, city, big_city):
    fake = FakeGoogle([])
    plain = _source(fake, factory, city)
    assert plain.text_query == "mimarlık ofisi Ankara"
    assert plain.state_key == "google:mimarlık ofisi|06"  # eski anahtar biçimi değişmedi
    split = GooglePlacesSource(
        "mimarlık ofisi", big_city, _client(fake, factory), district="Çankaya", session_factory=factory
    )
    assert split.text_query == "mimarlık ofisi Çankaya Ankara"


def test_places_sources_gives_one_source_per_district_with_own_state_key(factory, big_city, city):
    client = _client(FakeGoogle([]), factory)
    sources = places_sources("mimarlık ofisi", big_city, client, session_factory=factory)
    assert [s.state_key for s in sources] == [
        "google:mimarlık ofisi|06|Çankaya",
        "google:mimarlık ofisi|06|Keçiören",
        "google:mimarlık ofisi|06|Yenimahalle",
    ]
    assert all(s.company_source == "google" for s in sources)
    assert "Çankaya" in sources[0].name
    # İlçe listesi olmayan il: tek kaynak, bölme yok.
    assert len(places_sources("mimarlık ofisi", city, client, session_factory=factory)) == 1


def test_district_request_sends_district_query_to_google(factory, big_city):
    fake = FakeGoogle([_page(("p1", "https://a.com"))])
    source = places_sources("mimarlık ofisi", big_city, _client(fake, factory), session_factory=factory)[1]
    list(source.discover())
    assert fake.body(0)["textQuery"] == "mimarlık ofisi Keçiören Ankara"


def test_page_token_is_stored_per_district_key(factory, big_city, session):
    fake = FakeGoogle([_page(("p1", "https://a.com"), token="TC"), _page(("p2", "https://b.com"))])
    source = places_sources("mimarlık ofisi", big_city, _client(fake, factory), session_factory=factory)[0]
    generator = source.discover()
    next(generator)
    next(generator)  # 1. sayfa tamamen işlendi, 2. sayfa istendi
    session.expire_all()
    state = get_setting(session, KEY_DISCOVERY_STATE)
    assert state == {"google:mimarlık ofisi|06|Çankaya": {"next_page_token": "TC"}}


def test_monthly_counter_and_limit_are_shared_across_districts(factory, big_city, session):
    set_setting(session, KEY_PLACES_MONTHLY_LIMIT, 2)
    session.commit()
    fake = FakeGoogle([_page(("p1", "https://a.com")), _page(("p2", "https://b.com"))])
    sources = places_sources("mimarlık ofisi", big_city, _client(fake, factory), session_factory=factory)
    results = [list(source.discover()) for source in sources]
    assert [len(r) for r in results] == [1, 1, 0]
    assert [s.failed for s in sources] == [False, False, True]  # 3. ilçe: sınır doldu
    assert len(fake.requests) == 2  # 3. ilçe için HİÇ istek atılmadı
    session.expire_all()
    assert requests_this_month(session, NOW) == 2


def test_hard_max_still_applies_across_districts(factory, big_city, session):
    set_setting(session, KEY_PLACES_MONTHLY_LIMIT, 999_999)
    set_setting(session, KEY_PLACES_REQUESTS_MONTH_KEY, pacific_month_key(NOW))
    set_setting(session, KEY_PLACES_REQUESTS_THIS_MONTH, PLACES_HARD_MAX_MONTHLY)
    session.commit()
    fake = FakeGoogle([])
    sources = places_sources("mimarlık ofisi", big_city, _client(fake, factory), session_factory=factory)
    for source in sources:
        assert list(source.discover()) == [] and source.failed
    assert fake.requests == []


# --- Anahtar testi (10.6g): yalnız places.id, ücretsiz, sayaca girmez ----------------------------


def test_key_test_sends_only_places_id_mask_and_key_in_header():
    fake = FakeGoogle([(200, {})])
    assert check_api_key("KEY-123", client=fake.client()) is None
    request = fake.requests[0]
    assert request.headers["X-Goog-FieldMask"] == "places.id" == KEY_TEST_FIELD_MASK
    assert "websiteUri" not in request.headers["X-Goog-FieldMask"]  # ücretli Enterprise sınıfı
    assert request.headers["X-Goog-Api-Key"] == "KEY-123"
    assert "KEY-123" not in str(request.url)
    assert fake.body(0)["pageSize"] == 1  # 1 sonuçluk istek


def test_key_test_does_not_touch_monthly_counter(session):
    fake = FakeGoogle([(200, {})])
    assert check_api_key("KEY", client=fake.client()) is None
    assert requests_this_month(session, NOW) == 0


def test_key_test_maps_errors_to_turkish_without_leaking_key():
    for status, expected in ((400, "geçersiz"), (403, "Places API (New)"), (429, "kota")):
        fake = FakeGoogle([(status, {"error": {"message": "boom"}})])
        message = check_api_key("SECRET-KEY", client=fake.client())
        assert message is not None and expected in message
        assert "SECRET-KEY" not in message
    fake = FakeGoogle([(500, {})])
    assert "500" in check_api_key("SECRET-KEY", client=fake.client())


def test_key_test_network_error_and_missing_key():
    def boom(request):
        raise httpx.ConnectError("secret detail", request=request)

    client = httpx.Client(transport=httpx.MockTransport(boom))
    message = check_api_key("SECRET-KEY", client=client)
    assert "ConnectError" in message and "SECRET-KEY" not in message and "secret detail" not in message
    assert check_api_key("") is not None
