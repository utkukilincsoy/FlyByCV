from typing import Optional

from sqlmodel import select

from src.company_finder import teknokent_source as ts
from src.company_finder.csv_import import add_company
from src.core.models import Company


def _cfemail_encode(email: str, key: int = 0x2A) -> str:
    """`decode_cfemail()`'in tersi -- testlerde gerçekçi Cloudflare gizlemesi üretmek için."""
    raw = bytes([key]) + bytes(byte ^ key for byte in email.encode("utf-8"))
    return raw.hex()


class _FakeFetcher:
    """Verilen URL -> HTML eşlemesini taklit eder; hata istenen URL'ler için exception fırlatır."""

    def __init__(self, pages: dict[str, str], errors: Optional[dict[str, Exception]] = None):
        self.pages = pages
        self.errors = errors or {}
        self.requested: list[str] = []

    def __call__(self, url: str) -> str:
        self.requested.append(url)
        if url in self.errors:
            raise self.errors[url]
        return self.pages[url]


class _SleepRecorder:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


# ---------------------------------------------------------------------------
# ODTÜ Teknokent
# ---------------------------------------------------------------------------


def test_iter_odtu_teknokent_parses_name_and_website():
    html = """
    <table class="table"><tbody>
        <tr><td>ÖRNEK ELEKTRONİK ANONİM ŞİRKETİ</td><td><a href="http://www.ornek-elektronik.example.com">www.ornek-elektronik.example.com</a></td></tr>
        <tr><td>Deneme Teknoloji Ltd. Şti.</td><td><a href="http://www.deneme-teknoloji.example.com">site</a></td></tr>
    </tbody></table>
    """
    fetcher = _FakeFetcher({ts.ODTU_LIST_URL: html})

    leads = list(ts.iter_odtu_teknokent(fetcher=fetcher, sleeper=_SleepRecorder()))

    assert [lead.name for lead in leads] == [
        "ÖRNEK ELEKTRONİK ANONİM ŞİRKETİ",
        "Deneme Teknoloji Ltd. Şti.",
    ]
    assert leads[0].website == "http://www.ornek-elektronik.example.com"
    assert leads[0].teknokent == "ODTÜ Teknokent"
    assert leads[0].city == "Ankara"
    assert leads[0].email is None


def test_iter_odtu_teknokent_row_without_website_link_has_none():
    html = '<table><tbody><tr><td>İsimsiz Site A.Ş.</td><td></td></tr></tbody></table>'
    fetcher = _FakeFetcher({ts.ODTU_LIST_URL: html})

    leads = list(ts.iter_odtu_teknokent(fetcher=fetcher, sleeper=_SleepRecorder()))

    assert len(leads) == 1
    assert leads[0].website is None


def test_iter_odtu_teknokent_fetch_failure_yields_nothing_without_raising():
    fetcher = _FakeFetcher({}, errors={ts.ODTU_LIST_URL: TimeoutError("zaman aşımı")})

    leads = list(ts.iter_odtu_teknokent(fetcher=fetcher, sleeper=_SleepRecorder()))

    assert leads == []


# ---------------------------------------------------------------------------
# OSTİM Teknopark
# ---------------------------------------------------------------------------


def _ostim_detail_html(*, website: Optional[str] = None, email: Optional[str] = None) -> str:
    email_li = ""
    if email:
        encoded = _cfemail_encode(email)
        email_li = (
            '<li><a href="/cdn-cgi/l/email-protection"><span><i class="bi bi-envelope"></i>'
            f'<span class="__cf_email__" data-cfemail="{encoded}">[e-posta korumalı]</span>'
            "</span></a></li>"
        )
    website_li = ""
    if website:
        website_li = (
            f'<li><a href="{website}"><span><i class="bi bi-link-45deg"></i> {website}</span></a></li>'
        )
    return f"""
    <div class="location"><ul class="con">
        <li><a href="tel:312 000 00 00"><span><i class="bi bi-phone"></i> 312 000 00 00</span></a></li>
        {email_li}
        <li><span><i class="bi bi-geo-alt"></i> Ankara</span></li>
        {website_li}
    </ul></div>
    """


def _ostim_listing_html(cards: list[tuple[str, str]]) -> str:
    items = "".join(
        f'<div class="new"><a href="{url}"><div class="desc"><span class="title">{name}</span></div></a></div>'
        for name, url in cards
    )
    return f'<div class="news py-5">{items}</div>'


def test_iter_ostim_teknopark_full_flow_parses_website_and_decoded_email():
    detail_url = "https://ostimteknopark.com.tr/tr/company/firmalar-119/acme-yazilim"
    listing = _ostim_listing_html([("ACME YAZILIM A.Ş.", detail_url)])
    detail = _ostim_detail_html(website="https://acme.example.com", email="info@acme.example.com")
    fetcher = _FakeFetcher({ts.OSTIM_LIST_URL: listing, detail_url: detail})
    sleeper = _SleepRecorder()

    leads = list(ts.iter_ostim_teknopark(fetcher=fetcher, sleeper=sleeper))

    assert len(leads) == 1
    assert leads[0].name == "ACME YAZILIM A.Ş."
    assert leads[0].website == "https://acme.example.com"
    assert leads[0].email == "info@acme.example.com"
    assert leads[0].teknokent == "OSTİM Teknopark"
    # Firma sayfası ile detay sayfası arasında en az bir bekleme yapılmalı (kibar tarama).
    assert len(sleeper.sleeps) >= 1


def test_iter_ostim_teknopark_rejects_email_shaped_website_field():
    """Gerçek dünyada gözlemlendi: bir firma site alanına yanlışlıkla e-posta girmiş
    (https://team@example.com). Bu website olarak kabul edilirse normalize_domain()
    "team@example.com" üretip firmayı -- geçerli e-postası olsa bile -- reddettirir."""
    detail_url = "https://ostimteknopark.com.tr/tr/company/firmalar-119/ornek-bulut"
    listing = _ostim_listing_html([("ÖRNEK BULUT A.Ş.", detail_url)])
    detail = _ostim_detail_html(website="https://team@example.com", email="ik@example.com")
    fetcher = _FakeFetcher({ts.OSTIM_LIST_URL: listing, detail_url: detail})

    leads = list(ts.iter_ostim_teknopark(fetcher=fetcher, sleeper=_SleepRecorder()))

    assert leads[0].website is None
    assert leads[0].email == "ik@example.com"


def test_iter_ostim_teknopark_skips_failed_detail_page_but_keeps_others():
    ok_url = "https://ostimteknopark.com.tr/tr/company/firmalar-119/ok-firma"
    broken_url = "https://ostimteknopark.com.tr/tr/company/firmalar-119/bozuk-firma"
    listing = _ostim_listing_html([("BOZUK FİRMA", broken_url), ("OK FİRMA", ok_url)])
    detail_ok = _ostim_detail_html(website="https://ok.example.com")
    fetcher = _FakeFetcher(
        {ts.OSTIM_LIST_URL: listing, ok_url: detail_ok},
        errors={broken_url: TimeoutError("zaman aşımı")},
    )

    leads = list(ts.iter_ostim_teknopark(fetcher=fetcher, sleeper=_SleepRecorder()))

    assert [lead.name for lead in leads] == ["OK FİRMA"]


def test_iter_ostim_teknopark_listing_failure_yields_nothing():
    fetcher = _FakeFetcher({}, errors={ts.OSTIM_LIST_URL: TimeoutError("zaman aşımı")})

    leads = list(ts.iter_ostim_teknopark(fetcher=fetcher, sleeper=_SleepRecorder()))

    assert leads == []


# ---------------------------------------------------------------------------
# Hacettepe Teknokent
# ---------------------------------------------------------------------------


def _hacettepe_category_html(cards: list[tuple[str, str]]) -> str:
    items = "".join(
        f'<div class="firma"><div class="firma_adi"><a href="{href}">{name}</a></div></div>'
        for name, href in cards
    )
    return items


def _hacettepe_detail_html(*, website: Optional[str] = None, email: Optional[str] = None) -> str:
    website_div = (
        f'<div class="firma_detay_bilgi "><i class="fa-solid fa-arrow-pointer"></i> {website}</div>'
        if website
        else '<div class="firma_detay_bilgi d-none"><i class="fa-solid fa-arrow-pointer"></i> </div>'
    )
    email_div = (
        f'<div class="firma_detay_bilgi "><i class="fa-solid fa-square-envelope"></i> {email}</div>'
        if email
        else ""
    )
    return f"""
    <div class="firma_detay_baslik">Test Firma</div>
    <div class="firma_detay_bilgi "><i class="fa-solid fa-square-phone"></i> (312) 000-00-00</div>
    {website_div}
    {email_div}
    """


def test_iter_hacettepe_teknokent_dedupes_company_seen_in_two_categories():
    detail_href = "/tr/firma/acme-yazilim-1"
    detail_url = ts.HACETTEPE_BASE_URL + detail_href
    category_html = _hacettepe_category_html([("ACME YAZILIM A.Ş.", detail_href)])
    detail_html = _hacettepe_detail_html(email="info@acme.example.com")

    pages = {detail_url: detail_html}
    for slug in ts._HACETTEPE_CATEGORY_SLUGS:
        pages[f"{ts.HACETTEPE_BASE_URL}/tr/firma_rehberi/{slug}"] = category_html
    fetcher = _FakeFetcher(pages)

    leads = list(ts.iter_hacettepe_teknokent(fetcher=fetcher, sleeper=_SleepRecorder()))

    # 15 kategorinin HEPSİNDE aynı firma listelense de, detay sayfası sadece BİR kez istenir.
    assert len(leads) == 1
    assert fetcher.requested.count(detail_url) == 1
    assert leads[0].email == "info@acme.example.com"
    assert leads[0].teknokent == "Hacettepe Teknokent"


def test_parse_hacettepe_detail_hidden_website_field_is_none():
    html = _hacettepe_detail_html(email="info@acme.example.com")

    website, email = ts._parse_hacettepe_detail(html)

    assert website is None
    assert email == "info@acme.example.com"


def test_parse_hacettepe_detail_visible_website_field_is_read():
    html = _hacettepe_detail_html(website="acme.example.com", email="info@acme.example.com")

    website, email = ts._parse_hacettepe_detail(html)

    assert website == "acme.example.com"


def test_iter_hacettepe_teknokent_one_category_failure_does_not_stop_others():
    ok_slug = ts._HACETTEPE_CATEGORY_SLUGS[0]
    broken_slug = ts._HACETTEPE_CATEGORY_SLUGS[1]
    ok_url = f"{ts.HACETTEPE_BASE_URL}/tr/firma_rehberi/{ok_slug}"
    broken_url = f"{ts.HACETTEPE_BASE_URL}/tr/firma_rehberi/{broken_slug}"
    detail_href = "/tr/firma/acme-yazilim-1"
    detail_url = ts.HACETTEPE_BASE_URL + detail_href

    pages = {
        ok_url: _hacettepe_category_html([("ACME YAZILIM A.Ş.", detail_href)]),
        detail_url: _hacettepe_detail_html(email="info@acme.example.com"),
    }
    for slug in ts._HACETTEPE_CATEGORY_SLUGS:
        url = f"{ts.HACETTEPE_BASE_URL}/tr/firma_rehberi/{slug}"
        pages.setdefault(url, "<html></html>")
    fetcher = _FakeFetcher(pages, errors={broken_url: TimeoutError("zaman aşımı")})

    leads = list(ts.iter_hacettepe_teknokent(fetcher=fetcher, sleeper=_SleepRecorder()))

    assert [lead.name for lead in leads] == ["ACME YAZILIM A.Ş."]


# ---------------------------------------------------------------------------
# import_teknokent_leads
# ---------------------------------------------------------------------------


def test_import_teknokent_leads_adds_valid_leads(session):
    leads = [
        ts.TeknokentLead(name="Acme A.Ş.", website="https://acme.example.com", teknokent="ODTÜ Teknokent"),
        ts.TeknokentLead(name="Beta Ltd.", email="info@beta.example.com", teknokent="Hacettepe Teknokent"),
    ]

    report = ts.import_teknokent_leads(session, iter(leads))

    assert report.added_count == 2
    assert report.total_rows == 2
    domains = {row.domain for row in report.added}
    assert domains == {"acme.example.com", "beta.example.com"}


def test_import_teknokent_leads_skips_lead_without_contact_info(session):
    leads = [ts.TeknokentLead(name="Hayalet Firma", teknokent="OSTİM Teknopark")]

    report = ts.import_teknokent_leads(session, iter(leads))

    assert report.added_count == 0
    assert report.skipped_count == 1
    assert "gerekli" in report.skipped[0].reason


def test_import_teknokent_leads_skips_duplicate_domain_already_in_db(session):
    add_company(session, name="Var Olan Şirket", website="https://acme.example.com")
    leads = [ts.TeknokentLead(name="Acme A.Ş. (Teknokent)", website="https://acme.example.com")]

    report = ts.import_teknokent_leads(session, iter(leads))

    assert report.added_count == 0
    assert report.skipped_count == 1


def test_import_teknokent_leads_sets_company_source_to_teknokent(session):
    leads = [ts.TeknokentLead(name="Acme A.Ş.", website="https://acme.example.com")]

    ts.import_teknokent_leads(session, iter(leads))

    company = session.exec(select(Company).where(Company.domain == "acme.example.com")).one()
    assert company.source == "teknokent"
    assert company.city == "Ankara"
