"""OSM/Overpass kaynağı (PLAN.md Adım 10.6e) ve `naming.derive_company_name`.
Gerçek Overpass'a HİÇ bağlanılmaz: `httpx.MockTransport` sahte yanıt verir."""
from datetime import datetime, timedelta

import httpx
import pytest
from sqlmodel import select

from src.company_finder import osm_source
from src.company_finder.naming import derive_company_name, name_from_domain
from src.company_finder.osm_source import (
    MAX_RETRIES,
    OVERPASS_BACKUP_URL,
    OVERPASS_URL,
    OsmSource,
    build_query,
    clean_osm_website,
    import_osm_leads,
    leads_from_elements,
)
from src.core.cities import CityPreset
from src.core.models import Company
from src.core.professions import load_profession_presets
from src.cv_builder.mail_template import fill_mail_template
from tests.conftest import FakeSleeper

NOW = datetime(2026, 9, 30, 12, 0)


@pytest.fixture(autouse=True)
def no_real_overpass(monkeypatch):
    def _refuse(self, request):
        raise AssertionError("Test gerçek bir ağ isteği atmaya çalıştı")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _refuse)


@pytest.fixture
def profession():
    return load_profession_presets()["ic_mimarlik"]


@pytest.fixture
def city():
    return CityPreset(
        id="06", name_tr="Ankara", iso_code="TR-06",
        bbox=(32.0, 39.0, 33.0, 40.0), center=(32.5, 39.5),
    )


def _el(name=None, website=None, **extra):
    tags = dict(extra)
    if name:
        tags["name"] = name
    if website:
        tags["website"] = website
    return {"type": "node", "id": 1, "tags": tags}


class FakeOverpass:
    """`responses` sırayla tüketilir: (durum kodu, gövde, başlıklar)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body, headers = (self._responses.pop(0) + ({},))[:3]
        return httpx.Response(status, json=body, headers=headers)


def _ok(elements):
    return (200, {"elements": elements})


# --- Sorgu -----------------------------------------------------------------------------------


def test_query_uses_overpass_bbox_order_and_all_tags():
    query = build_query(["office=architect", "shop=interior_decoration"], (32.0, 39.0, 33.0, 40.0))
    regex = '[~"^(website|contact:website|url|email|contact:email)$"~"."]'
    assert f'["office"="architect"]{regex}(39.0,32.0,40.0,33.0)' in query
    assert f'["shop"="interior_decoration"]{regex}' in query
    assert query.count("nwr[") == 2  # tek sorgu, etiket başına tek satır
    assert query.startswith("[out:json]")


# --- Site temizliği --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        None, "", "  ",
        "https://www.facebook.com/firma", "facebook.com/firma", "https://tr-tr.facebook.com/x",
        "https://instagram.com/firma", "http://www.linkedin.com/company/x", "https://linktr.ee/x",
        "yok",
    ],
)
def test_clean_website_rejects_missing_and_social(value):
    assert clean_osm_website(value) is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://firma.com", "https://firma.com"),
        ("firma.com.tr", "https://firma.com.tr"),
        ("http://www.firma.com/iletisim;https://b.com", "http://www.firma.com/iletisim"),
    ],
)
def test_clean_website_accepts_real_sites(value, expected):
    assert clean_osm_website(value) == expected


def test_facebook_lookalike_domain_is_not_rejected():
    assert clean_osm_website("https://notfacebook.com") == "https://notfacebook.com"


# --- Kayıt -> aday ---------------------------------------------------------------------------


def test_leads_drop_no_website_social_and_duplicates(city):
    elements = [
        _el("A Mimarlık", "https://a.com"),
        _el("Sitesiz Ofis"),
        _el("Sosyal Ofis", "https://facebook.com/sosyal"),
        _el("A Mimarlık 2", "https://www.a.com/iletisim"),  # aynı alan adı
        {"type": "node", "id": 9},  # tags yok
    ]
    leads = leads_from_elements(elements, "Ankara")
    assert [lead.name for lead in leads] == ["A Mimarlık"]
    assert leads[0].city == "Ankara" and leads[0].email is None


@pytest.mark.parametrize("key", ["website", "contact:website", "url"])
def test_website_is_read_from_all_website_keys(key):
    leads = leads_from_elements([_el("A", **{key: "https://a.com"})], "Ankara")
    assert [l.website for l in leads] == ["https://a.com"]


def test_website_key_priority_and_social_fallthrough():
    element = _el("A", **{"website": "https://facebook.com/a", "contact:website": "https://a.com"})
    assert leads_from_elements([element], "Ankara")[0].website == "https://a.com"


def test_email_only_record_takes_domain_from_email():
    leads = leads_from_elements([_el("A", **{"contact:email": "mailto:Info@Firma.com;x@y.com"})], "Ankara")
    assert len(leads) == 1
    assert leads[0].website is None and leads[0].email == "info@firma.com"


def test_email_only_free_mail_is_dropped_but_kept_with_website():
    assert leads_from_elements([_el("A", email="firma@gmail.com")], "Ankara") == []
    leads = leads_from_elements([_el("A", "https://a.com", email="firma@gmail.com")], "Ankara")
    assert leads[0].website == "https://a.com"


def test_invalid_email_without_website_is_dropped():
    assert leads_from_elements([_el("A", email="yok")], "Ankara") == []


def test_email_only_lead_dedups_against_site_lead_by_domain():
    elements = [_el("A", "https://firma.com"), _el("B", email="info@firma.com")]
    assert [l.name for l in leads_from_elements(elements, "Ankara")] == ["A"]


def test_email_only_import_respects_role_rule(session):
    elements = [_el("Rol", email="info@rol.com"), _el("Kisi", email="ahmet.yilmaz@kisi.com")]
    report = import_osm_leads(session, iter(leads_from_elements(elements, "Ankara")))
    assert len(report.added) == 2
    rows = {c.domain: c for c in session.exec(select(Company)).all()}
    assert rows["rol.com"].email_is_role is True and rows["rol.com"].website is None
    assert rows["kisi.com"].email_is_role is False  # kural 10: otomatik modda gönderilmez


def test_name_comes_from_osm_tag_else_domain(city):
    leads = leads_from_elements(
        [_el("Gerçek Ad Ltd.", "https://gercekad.com"), _el(None, "https://xyzmimarlik.com")], "Ankara"
    )
    assert leads[0].name == "Gerçek Ad Ltd."
    assert leads[1].name == "Xyzmimarlik"


# --- Kaynak: istek, önbellek, yeniden deneme -------------------------------------------------


def test_source_identity(profession, city):
    source = OsmSource(profession, city)
    assert source.state_key == "osm:ic_mimarlik|06"
    assert source.company_source == "osm"


def test_source_requires_osm_tags(profession, city):
    with pytest.raises(ValueError):
        OsmSource(profession.model_copy(update={"osm_tags": None}), city)


def test_request_has_identifying_user_agent_and_single_query(profession, city, tmp_path):
    fake = FakeOverpass([_ok([_el("A", "https://a.com")])])
    source = OsmSource(profession, city, client=fake.client(), cache_directory=tmp_path)
    leads = list(source.discover(sleeper=FakeSleeper()))
    assert len(leads) == 1
    assert len(fake.requests) == 1  # paket+şehir başına TEK sorgu
    assert fake.requests[0].headers["User-Agent"].startswith("FlybyCV/")


def test_second_run_uses_cache_without_network(profession, city, tmp_path):
    fake = FakeOverpass([_ok([_el("A", "https://a.com")])])
    kwargs = dict(cache_directory=tmp_path, now=lambda: NOW)
    first = list(OsmSource(profession, city, client=fake.client(), **kwargs).discover(sleeper=FakeSleeper()))
    # İkinci kaynak: yanıt kuyruğu boş -> istek atsaydı IndexError olurdu
    second = list(OsmSource(profession, city, client=fake.client(), **kwargs).discover(sleeper=FakeSleeper()))
    assert [l.website for l in second] == [l.website for l in first]
    assert len(fake.requests) == 1


def test_cache_expires_after_seven_days(profession, city, tmp_path):
    fake = FakeOverpass([_ok([_el("A", "https://a.com")]), _ok([_el("B", "https://b.com")])])
    clock = [NOW]
    source = lambda: OsmSource(  # noqa: E731
        profession, city, client=fake.client(), cache_directory=tmp_path, now=lambda: clock[0]
    )
    list(source().discover(sleeper=FakeSleeper()))
    clock[0] = NOW + timedelta(days=6, hours=23)
    assert [l.name for l in source().discover(sleeper=FakeSleeper())] == ["A"]
    assert len(fake.requests) == 1
    clock[0] = NOW + timedelta(days=7, minutes=1)
    assert [l.name for l in source().discover(sleeper=FakeSleeper())] == ["B"]
    assert len(fake.requests) == 2


def test_cache_key_changes_with_tags(profession, city, tmp_path):
    fake = FakeOverpass([_ok([_el("A", "https://a.com")]), _ok([_el("B", "https://b.com")])])
    kwargs = dict(cache_directory=tmp_path, now=lambda: NOW)
    list(OsmSource(profession, city, client=fake.client(), **kwargs).discover(sleeper=FakeSleeper()))
    other = profession.model_copy(update={"osm_tags": ["office=architect", "office=engineer"]})
    leads = list(OsmSource(other, city, client=fake.client(), **kwargs).discover(sleeper=FakeSleeper()))
    assert [l.name for l in leads] == ["B"]


def test_cache_key_changes_with_bbox(profession, city, tmp_path):
    fake = FakeOverpass([_ok([_el("A", "https://a.com")]), _ok([_el("B", "https://b.com")])])
    kwargs = dict(cache_directory=tmp_path, now=lambda: NOW)
    list(OsmSource(profession, city, client=fake.client(), **kwargs).discover(sleeper=FakeSleeper()))
    moved = city.model_copy(update={"bbox": (32.0, 39.0, 33.5, 40.0)})
    leads = list(OsmSource(profession, moved, client=fake.client(), **kwargs).discover(sleeper=FakeSleeper()))
    assert [l.name for l in leads] == ["B"]
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_cache_path_is_stable_for_same_query(profession, city, tmp_path):
    query = build_query(profession.osm_tags, city.bbox)
    a = osm_source._cache_path(profession.id, city.id, query, tmp_path)
    assert a == osm_source._cache_path(profession.id, city.id, query, tmp_path)
    other = build_query(profession.osm_tags, (1.0, 2.0, 3.0, 4.0))
    assert a != osm_source._cache_path(profession.id, city.id, other, tmp_path)


def test_corrupt_cache_is_ignored(profession, city, tmp_path):
    fake = FakeOverpass([_ok([_el("A", "https://a.com")])])
    source = OsmSource(profession, city, client=fake.client(), cache_directory=tmp_path, now=lambda: NOW)
    for path in (tmp_path,):
        path.mkdir(exist_ok=True)
    query = build_query(profession.osm_tags, city.bbox)
    cache_file = osm_source._cache_path(profession.id, city.id, query, tmp_path)
    cache_file.write_text("{bozuk json", encoding="utf-8")
    assert [l.name for l in source.discover(sleeper=FakeSleeper())] == ["A"]


@pytest.mark.parametrize("status", [429, 504])
def test_retries_after_waiting_on_rate_limit_and_timeout(profession, city, tmp_path, status):
    fake = FakeOverpass([(status, {}), (status, {}), _ok([_el("A", "https://a.com")])])
    sleeper = FakeSleeper()
    source = OsmSource(profession, city, client=fake.client(), cache_directory=tmp_path)
    leads = list(source.discover(sleeper=sleeper))
    assert [l.name for l in leads] == ["A"]
    assert len(fake.requests) == 3
    assert len(sleeper.sleeps) == 2 and all(s > 0 for s in sleeper.sleeps)


def test_retry_after_header_is_respected(profession, city, tmp_path):
    fake = FakeOverpass([(429, {}, {"Retry-After": "7"}), _ok([])])
    sleeper = FakeSleeper()
    list(OsmSource(profession, city, client=fake.client(), cache_directory=tmp_path).discover(sleeper=sleeper))
    assert sleeper.sleeps == [7.0]


def test_gives_up_after_max_retries_on_both_servers_without_crashing_or_caching(profession, city, tmp_path):
    fake = FakeOverpass([(504, {})] * (2 * (MAX_RETRIES + 1)))
    sleeper = FakeSleeper()
    source = OsmSource(profession, city, client=fake.client(), cache_directory=tmp_path)
    assert list(source.discover(sleeper=sleeper)) == []
    assert source.failed is True
    assert len(fake.requests) == 2 * (MAX_RETRIES + 1)
    assert not list(tmp_path.glob("*.json"))  # başarısız sonuç önbelleğe yazılmaz


def test_falls_back_to_backup_server_after_main_exhausts_retries(profession, city, tmp_path):
    fake = FakeOverpass([(504, {})] * (MAX_RETRIES + 1) + [_ok([_el("A", "https://a.com")])])
    sleeper = FakeSleeper()
    source = OsmSource(profession, city, client=fake.client(), cache_directory=tmp_path)
    assert [l.name for l in source.discover(sleeper=sleeper)] == ["A"]
    hosts = [r.url.host for r in fake.requests]
    assert hosts == ["overpass-api.de"] * (MAX_RETRIES + 1) + ["overpass.private.coffee"]
    assert len(sleeper.sleeps) == MAX_RETRIES  # yedeğe geçerken ek bekleme yok
    assert len(list(tmp_path.glob("*.json"))) == 1  # yedekten gelen sonuç da önbelleğe yazılır


def test_main_server_success_never_touches_backup(profession, city, tmp_path):
    fake = FakeOverpass([_ok([_el("A", "https://a.com")])])
    list(OsmSource(profession, city, client=fake.client(), cache_directory=tmp_path).discover(sleeper=FakeSleeper()))
    assert [str(r.url) for r in fake.requests] == [OVERPASS_URL]


def test_backup_server_is_documented_public_instance():
    assert OVERPASS_BACKUP_URL == "https://overpass.private.coffee/api/interpreter"


def test_falls_back_to_backup_on_connection_error(profession, city, tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "overpass-api.de":
            raise httpx.ConnectError("bağlanılamadı", request=request)
        return httpx.Response(200, json={"elements": [_el("A", "https://a.com")]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = OsmSource(profession, city, client=client, cache_directory=tmp_path)
    assert [l.name for l in source.discover(sleeper=FakeSleeper())] == ["A"]
    assert calls == ["overpass-api.de", "overpass.private.coffee"]


def test_other_http_errors_do_not_retry(profession, city, tmp_path):
    fake = FakeOverpass([(400, {})])
    sleeper = FakeSleeper()
    source = OsmSource(profession, city, client=fake.client(), cache_directory=tmp_path)
    assert list(source.discover(sleeper=sleeper)) == []
    assert len(fake.requests) == 1 and sleeper.sleeps == []  # 4xx: bizim sorgu bozuk, yedek denenmez


def test_malformed_response_yields_nothing(profession, city, tmp_path):
    fake = FakeOverpass([(200, {"remark": "runtime error"})])
    source = OsmSource(profession, city, client=fake.client(), cache_directory=tmp_path)
    assert list(source.discover(sleeper=FakeSleeper())) == []


# --- Veritabanına aktarma (kural 4: alan adı normalizasyonu/tekilleştirme add_company'de) -----


def test_import_osm_leads_writes_source_osm_and_dedups(session, profession, city, tmp_path):
    elements = [_el("A Mimarlık", "https://www.a.com/"), _el("B", "https://b.com")]
    fake = FakeOverpass([_ok(elements)])
    source = OsmSource(profession, city, client=fake.client(), cache_directory=tmp_path)
    report = import_osm_leads(session, source.discover(sleeper=FakeSleeper()))
    assert len(report.added) == 2

    rows = session.exec(select(Company)).all()
    assert {c.domain for c in rows} == {"a.com", "b.com"}
    assert all(c.source == "osm" and c.city == "Ankara" for c in rows)

    again = import_osm_leads(session, leads_from_elements(elements, "Ankara"))
    assert len(again.added) == 0 and len(again.skipped) == 2


# --- derive_company_name ---------------------------------------------------------------------


def _title(title):
    return f"<html><head><title>{title}</title></head></html>"


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Anasayfa | Xyz Mimarlık", "Xyz Mimarlık"),
        ("Xyz Mimarlık | Anasayfa", "Xyz Mimarlık"),
        ("Ana Sayfa - Xyz Mimarlık", "Xyz Mimarlık"),
        ("Xyz Mimarlık – Home", "Xyz Mimarlık"),
        ("Hoş Geldiniz - Xyz Mimarlık", "Xyz Mimarlık"),
        ("Welcome | Xyz Mimarlık", "Xyz Mimarlık"),
        ("Xyz Mimarlık'a Hoş Geldiniz", "Xyz Mimarlık"),
        ("Xyz Mimarlık Hoş Geldiniz!", "Xyz Mimarlık"),
        ("Welcome to Xyz Mimarlık", "Xyz Mimarlık"),
        ("Xyz Mimarlık", "Xyz Mimarlık"),
        ("Zone Hoş Geldiniz", "Zone"),
    ],
)
def test_title_noise_is_removed(title, expected):
    assert derive_company_name(_title(title), "xyzmimarlik.com") == expected


def test_multiple_pieces_prefers_one_matching_domain():
    title = "İç Mimarlık ve Dekorasyon | Xyz Mimarlık"
    assert derive_company_name(_title(title), "xyzmimarlik.com") == "Xyz Mimarlık"


def test_multiple_pieces_without_domain_match_takes_first():
    assert derive_company_name(_title("Alfa Ltd | Bir Slogan"), "baska.com") == "Alfa Ltd"


def test_og_site_name_wins_over_title():
    html = (
        '<head><meta property="og:site_name" content="Gerçek Ad">'
        "<title>Anasayfa | Başka</title></head>"
    )
    assert derive_company_name(html, "x.com") == "Gerçek Ad"


@pytest.mark.parametrize(
    "html",
    ["", "<html></html>", _title("Anasayfa"), _title("Hoş Geldiniz"), _title("Home | Welcome"), _title("x" * 80)],
)
def test_falls_back_to_domain(html):
    assert derive_company_name(html, "xyzmimarlik.com") == "Xyzmimarlik"


def test_name_from_domain_variants():
    assert name_from_domain("www.xyz-mimarlik.com.tr") == "Xyz Mimarlik"
    assert name_from_domain("") == ""


def test_mail_template_gets_cleaned_company_name(sample_profile):
    """`{sirket_adi}` temizlenmiş adla dolar, "Anasayfa" sızmaz."""
    name = derive_company_name(_title("Anasayfa | Xyz Mimarlık"), "xyzmimarlik.com")
    subject, body = fill_mail_template(
        "Başvuru – {sirket_adi}", "Sayın {sirket_adi} ekibi,", sample_profile, name
    )
    assert subject == "Başvuru – Xyz Mimarlık"
    assert body == "Sayın Xyz Mimarlık ekibi,"
