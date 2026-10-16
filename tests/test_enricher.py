from datetime import timedelta
from typing import Optional

from src.company_finder.email_extractor import FoundEmail
from src.company_finder.enricher import CRAWL_RETRY_COOLDOWN, enrich_company, is_crawl_due
from src.company_finder.site_crawler import CrawlResult
from src.core.models import utcnow
from tests.conftest import build_company


def _crawler(result: CrawlResult):
    def fn(website: str) -> CrawlResult:
        return result

    return fn


def _mx(value: Optional[bool]):
    def fn(domain: str) -> Optional[bool]:
        return value

    return fn


def test_email_filled_from_best_found_result(session):
    company = build_company(domain="acme.example.com", email=None)
    found = [
        FoundEmail(
            "kariyer@acme.example.com", "https://acme.example.com/iletisim",
            is_role=True, on_company_domain=True,
        )
    ]
    crawl_result = CrawlResult(website="https://acme.example.com", emails=found)

    result = enrich_company(session, company, crawler=_crawler(crawl_result), mx=_mx(None))

    assert company.email == "kariyer@acme.example.com"
    assert company.email_source == "https://acme.example.com/iletisim"
    assert company.email_is_role is True
    assert result.email_added == "kariyer@acme.example.com"


def test_existing_user_email_never_overwritten(session):
    """CSV'den gelen e-posta ASLA ezilmez (CLAUDE.md kural 9'un e-posta karşılığı)."""
    company = build_company(domain="acme.example.com", email="ozel@acme.example.com", source="csv")
    found = [FoundEmail("kariyer@acme.example.com", "url", is_role=True, on_company_domain=True)]
    crawl_result = CrawlResult(website="https://acme.example.com", emails=found)

    result = enrich_company(session, company, crawler=_crawler(crawl_result), mx=_mx(True))

    assert company.email == "ozel@acme.example.com"
    assert "mevcut e-posta korundu" in result.notes
    assert company.found_emails and company.found_emails[0]["email"] == "kariyer@acme.example.com"


def test_about_text_filled_when_empty(session):
    company = build_company(domain="acme.example.com", about_text=None)
    crawl_result = CrawlResult(website="https://acme.example.com", about_text="Şirket tanıtımı")

    result = enrich_company(session, company, crawler=_crawler(crawl_result), mx=_mx(None))

    assert company.about_text == "Şirket tanıtımı"
    assert result.about_added is True


def test_about_text_not_overwritten_when_already_present(session):
    company = build_company(domain="acme.example.com", about_text="Zaten var (CSV'den)")
    crawl_result = CrawlResult(website="https://acme.example.com", about_text="Yeni metin")

    result = enrich_company(session, company, crawler=_crawler(crawl_result), mx=_mx(None))

    assert company.about_text == "Zaten var (CSV'den)"
    assert result.about_added is False


def test_mx_false_sets_mx_valid_false(session):
    company = build_company(domain="acme.example.com")
    crawl_result = CrawlResult(website="https://acme.example.com")

    result = enrich_company(session, company, crawler=_crawler(crawl_result), mx=_mx(False))

    assert company.mx_valid is False
    assert result.mx_valid is False


def test_mx_unknown_leaves_mx_valid_none(session):
    """None = bilinmiyor; 'kontrol edilmedi' anlamı korunmalı (src/core/models.py yorumu)."""
    company = build_company(domain="acme.example.com")
    crawl_result = CrawlResult(website="https://acme.example.com")

    result = enrich_company(session, company, crawler=_crawler(crawl_result), mx=_mx(None))

    assert company.mx_valid is None
    assert result.mx_valid is None


def test_mx_checked_against_email_domain_not_website_domain(session):
    """E-posta bulunmuşsa MX ONUN alan adına bakmalı (örn. gmail.com), site alanına değil."""
    company = build_company(domain="acme.example.com", email=None)
    found = [FoundEmail("destek@baskadomain.example.com", "url", is_role=True, on_company_domain=False)]
    crawl_result = CrawlResult(website="https://acme.example.com", emails=found)

    checked_domains: list[str] = []

    def mx(domain: str) -> Optional[bool]:
        checked_domains.append(domain)
        return True

    enrich_company(session, company, crawler=_crawler(crawl_result), mx=mx)

    assert checked_domains == ["baskadomain.example.com"]


def test_found_emails_stores_all_best_first(session):
    company = build_company(domain="acme.example.com")
    found = [
        FoundEmail("kariyer@acme.example.com", "url1", is_role=True, on_company_domain=True),
        FoundEmail("ahmet.yilmaz@acme.example.com", "url2", is_role=False, on_company_domain=True),
    ]
    crawl_result = CrawlResult(website="https://acme.example.com", emails=found)

    enrich_company(session, company, crawler=_crawler(crawl_result), mx=_mx(None))

    assert len(company.found_emails) == 2
    assert company.found_emails[0]["email"] == "kariyer@acme.example.com"


def test_person_only_email_kept_but_flagged_not_role(session):
    company = build_company(domain="acme.example.com")
    found = [FoundEmail("ahmet.yilmaz@acme.example.com", "url", is_role=False, on_company_domain=True)]
    crawl_result = CrawlResult(website="https://acme.example.com", emails=found)

    result = enrich_company(session, company, crawler=_crawler(crawl_result), mx=_mx(None))

    assert company.email == "ahmet.yilmaz@acme.example.com"
    assert company.email_is_role is False
    assert result.email_added == "ahmet.yilmaz@acme.example.com"


def test_crawler_exception_recorded_as_error_without_raising(session):
    company = build_company(domain="acme.example.com")

    def boom(website: str) -> CrawlResult:
        raise RuntimeError("ağ hatası")

    result = enrich_company(session, company, crawler=boom, mx=_mx(None))

    assert result.error is not None and "tarama başarısız oldu" in result.error
    assert company.email is None  # şirket bozulmadı


def test_crawl_result_error_field_is_propagated(session):
    company = build_company(domain="acme.example.com")
    crawl_result = CrawlResult(website="https://acme.example.com", error="ana sayfa açılamadı")

    result = enrich_company(session, company, crawler=_crawler(crawl_result), mx=_mx(None))

    assert result.error == "ana sayfa açılamadı"


def test_crawled_at_set_after_successful_crawl(session):
    """Faz 9 — Otopilot yalnızca `crawled_at is None` olanları tarar; başarılı taramadan sonra
    dolmalı ki aynı şirket kendiliğinden tekrar taranmasın."""
    company = build_company(domain="acme.example.com")
    assert company.crawled_at is None
    crawl_result = CrawlResult(website="https://acme.example.com")

    enrich_company(session, company, crawler=_crawler(crawl_result), mx=_mx(None))

    assert company.crawled_at is not None


def test_crawled_at_set_even_when_crawler_raises(session):
    """Beklenmeyen bir tarama hatasında da işaretlenmeli -- aksi halde sürekli aynı bozuk
    şirket tekrar tekrar denenir (sonsuz döngü riski)."""
    company = build_company(domain="acme.example.com")

    def boom(website: str) -> CrawlResult:
        raise RuntimeError("ağ hatası")

    enrich_company(session, company, crawler=boom, mx=_mx(None))

    assert company.crawled_at is not None


def test_temporary_crawl_failure_flags_crawl_failed(session):
    """Zaman aşımı/bağlantı hatası/5xx gibi GEÇİCİ bir hatadan sonra `crawl_failed` True olmalı
    ki `is_crawl_due()` birkaç gün sonra şirketi tekrar tarama kuyruğuna alabilsin."""
    company = build_company(domain="acme.example.com")
    crawl_result = CrawlResult(
        website="https://acme.example.com", error="ana sayfa açılamadı", temporary_failure=True
    )

    enrich_company(session, company, crawler=_crawler(crawl_result), mx=_mx(None))

    assert company.crawl_failed is True
    assert company.crawled_at is not None


def test_permanent_crawl_result_does_not_flag_crawl_failed(session):
    """Kalıcı bir sonuçtan sonra (site açıldı, robots engelledi vb.) `crawl_failed` False
    kalmalı -- bir daha KENDİLİĞİNDEN denenmemeli."""
    company = build_company(domain="acme.example.com")
    crawl_result = CrawlResult(
        website="https://acme.example.com", error="robots.txt taramaya izin vermiyor"
    )

    enrich_company(session, company, crawler=_crawler(crawl_result), mx=_mx(None))

    assert company.crawl_failed is False


def test_unexpected_crawler_exception_flags_crawl_failed(session):
    """Beklenmeyen bir tarama hatasının sebebi bilinmiyor -- güvenli taraf GEÇİCİ saymak."""
    company = build_company(domain="acme.example.com")

    def boom(website: str) -> CrawlResult:
        raise RuntimeError("ağ hatası")

    enrich_company(session, company, crawler=boom, mx=_mx(None))

    assert company.crawl_failed is True


def test_is_crawl_due_true_when_never_crawled():
    company = build_company(domain="acme.example.com")
    assert company.crawled_at is None

    assert is_crawl_due(company) is True


def test_is_crawl_due_false_after_permanent_result():
    company = build_company(domain="acme.example.com")
    company.crawled_at = utcnow()
    company.crawl_failed = False

    assert is_crawl_due(company) is False


def test_is_crawl_due_false_before_cooldown_elapses():
    company = build_company(domain="acme.example.com")
    company.crawled_at = utcnow() - (CRAWL_RETRY_COOLDOWN - timedelta(hours=1))
    company.crawl_failed = True

    assert is_crawl_due(company) is False


def test_is_crawl_due_true_after_cooldown_elapses():
    company = build_company(domain="acme.example.com")
    company.crawled_at = utcnow() - (CRAWL_RETRY_COOLDOWN + timedelta(hours=1))
    company.crawl_failed = True

    assert is_crawl_due(company) is True


def test_enrich_company_does_not_commit(session, monkeypatch):
    commit_calls: list[int] = []
    monkeypatch.setattr(session, "commit", lambda: commit_calls.append(1))

    company = build_company(domain="acme.example.com")
    session.add(company)
    crawl_result = CrawlResult(website="https://acme.example.com", about_text="Yeni metin")

    enrich_company(session, company, crawler=_crawler(crawl_result), mx=_mx(None))

    assert commit_calls == []


# --- Adı alan adından üretilmiş OSM/Google firmalarında ad, site başlığından düzeltilir ---

_HOME = (
    "<html><head><title>Anasayfa | Xyz Mimarlık</title></head>"
    "<body>merhaba</body></html>"
)


def _home_result(html: str = _HOME) -> CrawlResult:
    return CrawlResult(website="https://xyzmimarlik.com", home_html=html)


def test_google_company_name_replaced_from_site_title(session):
    company = build_company(name="Xyzmimarlik", domain="xyzmimarlik.com", source="google")

    result = enrich_company(session, company, crawler=_crawler(_home_result()), mx=_mx(None))

    assert company.name == "Xyz Mimarlık"
    assert result.name_updated == "Xyz Mimarlık"


def test_osm_company_without_name_tag_gets_name_from_site(session):
    company = build_company(name="Xyzmimarlik", domain="xyzmimarlik.com", source="osm")

    enrich_company(session, company, crawler=_crawler(_home_result()), mx=_mx(None))

    assert company.name == "Xyz Mimarlık"


def test_osm_name_from_tag_is_never_overwritten(session):
    company = build_company(name="Xyz Mimarlık", domain="xyzmimarlik.com", source="osm")

    result = enrich_company(
        session, company, crawler=_crawler(_home_result("<title>Başka Bir Ad</title>")), mx=_mx(None)
    )

    assert company.name == "Xyz Mimarlık"
    assert result.name_updated is None


def test_name_of_other_sources_is_never_touched(session):
    company = build_company(name="Xyzmimarlik", domain="xyzmimarlik.com", source="csv")

    enrich_company(session, company, crawler=_crawler(_home_result()), mx=_mx(None))

    assert company.name == "Xyzmimarlik"


def test_name_stays_when_site_title_is_meaningless(session):
    company = build_company(name="Xyzmimarlik", domain="xyzmimarlik.com", source="google")

    result = enrich_company(
        session, company, crawler=_crawler(_home_result("<title>Anasayfa</title>")), mx=_mx(None)
    )

    assert company.name == "Xyzmimarlik"
    assert result.name_updated is None


def test_name_stays_when_homepage_was_not_fetched(session):
    company = build_company(name="Xyzmimarlik", domain="xyzmimarlik.com", source="google")
    crawl_result = CrawlResult(website="https://xyzmimarlik.com", error="ana sayfa açılamadı")

    enrich_company(session, company, crawler=_crawler(crawl_result), mx=_mx(None))

    assert company.name == "Xyzmimarlik"


def test_cleaned_name_fills_mail_placeholder(session, sample_profile):
    """Uçtan uca: alan adından üretilmiş ad, taramadan sonra mail konusunda temiz görünür."""
    from src.cv_builder.mail_template import fill_mail_template

    company = build_company(name="Xyzmimarlik", domain="xyzmimarlik.com", source="google")
    enrich_company(session, company, crawler=_crawler(_home_result()), mx=_mx(None))

    subject, body = fill_mail_template(
        "{sirket_adi} – Başvuru", "{sirket_adi} bünyesinde...", sample_profile, company.name
    )

    assert subject == "Xyz Mimarlık – Başvuru"
    assert body == "Xyz Mimarlık bünyesinde..."
    assert "Anasayfa" not in subject
