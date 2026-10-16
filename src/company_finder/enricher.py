"""Bir `Company` kaydını sitesini tarayarak zenginleştirir: e-posta ve tanıtım metni doldurur,
MX kaydını kontrol eder. `src/company_finder/site_crawler.py` ve `mx_check.py`'yi birleştirir.

COMMIT ETMEZ (`src/core/db.py`: "commit çağırana aittir"). Beklenmeyen hata `EnrichResult.error`
alanına yazılır, fırlatılmaz: 40 şirketin 12.'si patlayınca kalan 28'i işlenmeye devam etmeli.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlmodel import Session

from src.company_finder.mx_check import has_mx_record
from src.company_finder.naming import derive_company_name, name_from_domain
from src.company_finder.site_crawler import CrawlResult, crawl_company
from src.core.models import Company, normalize_domain, utcnow

logger = logging.getLogger(__name__)

#: Adı alan adından üretilmiş olabilen kaynaklar (OSM `name` etiketsiz, Google Places hep).
#: Bu kaynaklarda site taranınca ad, sayfa başlığından temizlenerek düzeltilir.
NAME_FROM_SITE_SOURCES = frozenset({"osm", "google"})

#: `crawl_failed` True olan bir şirket `crawled_at`'ten bu kadar süre sonra tekrar tarama
#: kuyruğuna girebilir (bkz. `is_crawl_due`). Kalıcı sonuçlarda (site açıldı ama e-posta yok,
#: robots.txt engelledi) bu süre hiç işletilmez -- bir daha KENDİLİĞİNDEN denenmez.
CRAWL_RETRY_COOLDOWN = timedelta(days=3)


@dataclass
class EnrichResult:
    domain: str
    email_added: Optional[str] = None
    about_added: bool = False
    name_updated: Optional[str] = None
    mx_valid: Optional[bool] = None
    visited_count: int = 0
    error: Optional[str] = None
    notes: list[str] = field(default_factory=list)  # Türkçe


def enrich_company(
    session: Session,
    company: Company,
    *,
    crawler: Callable[[str], CrawlResult] = crawl_company,
    mx: Callable[..., Optional[bool]] = has_mx_record,
) -> EnrichResult:
    """`company`'yi tarayıp e-posta/tanıtım metni/MX bilgisiyle günceller.

    Kullanıcının verdiği `company.email` ASLA ezilmez: doluysa dokunulmaz, sadece
    `found_emails` doldurulur. `about_text` de aynı şekilde — doluysa (CSV'den geldiyse)
    korunur.
    """
    session.add(company)
    result = EnrichResult(domain=company.domain)

    website = company.website or f"https://{company.domain}"
    try:
        crawl_result = crawler(website)
    except Exception as exc:  # crawler kendi içinde hataları yutuyor; bu son savunma hattı
        logger.exception(f"{company.domain}: tarama beklenmeyen şekilde başarısız oldu")
        result.error = f"tarama başarısız oldu: {exc}"
        # Sebebi bilinmiyor -- güvenli taraf GEÇİCİ saymak (aksi halde kalıcı bozuk sanılıp
        # bir daha hiç denenmeyebilir).
        crawl_result = CrawlResult(website=website, temporary_failure=True)

    result.visited_count = len(crawl_result.visited_urls)
    if crawl_result.error:
        result.error = crawl_result.error

    company.found_emails = [
        {"email": found.email, "source_url": found.source_url, "is_role": found.is_role}
        for found in crawl_result.emails
    ]

    if company.email:
        result.notes.append("mevcut e-posta korundu")
    elif crawl_result.emails:
        best = crawl_result.emails[0]
        company.email = best.email
        company.email_source = best.source_url
        company.email_is_role = best.is_role
        result.email_added = best.email

    if not company.about_text and crawl_result.about_text:
        company.about_text = crawl_result.about_text
        result.about_added = True

    _refine_company_name(company, crawl_result, result)

    mx_domain = (
        normalize_domain(company.email.rpartition("@")[2]) if company.email else company.domain
    )
    try:
        mx_result = mx(mx_domain)
    except Exception as exc:  # mx_check kendi içinde hataları yutuyor; bu son savunma hattı
        logger.warning(f"{mx_domain}: MX kontrolü başarısız oldu ({exc!r})")
        mx_result = None
    if mx_result is not None:
        company.mx_valid = mx_result
    result.mx_valid = company.mx_valid

    # Başarılı/başarısız fark etmeksizin: "bir daha KENDİLİĞİNDEN denenmesin" (Faz 9 — Otopilot
    # yalnızca is_crawl_due(company) True olan şirketleri kendi tarama kuyruğuna alır). Kullanıcı
    # elle tekrar taramak isterse zaten mevcut butonlar/scriptler bu alana hiç bakmıyor.
    company.crawled_at = utcnow()
    # GEÇİCİ bir hatadan sonra (zaman aşımı, bağlantı hatası, 5xx) is_crawl_due() birkaç gün
    # sonra True döner; kalıcı bir sonuçtan (site açıldı ama e-posta yok, robots.txt engelledi)
    # sonra False kalır.
    company.crawl_failed = crawl_result.temporary_failure

    return result


def _refine_company_name(company: Company, crawl_result: CrawlResult, result: EnrichResult) -> None:
    """Adı alan adından üretilmiş OSM/Google firmasına sitenin gerçek adını yazar.

    Yalnız `name == name_from_domain(domain)` iken (ad hâlâ üretilmiş haldeyse) dokunulur: OSM
    `name` etiketinden gelen ya da kullanıcının değiştirdiği ad ASLA ezilmez. Sitedeki başlık da
    anlamsızsa `derive_company_name` zaten aynı alan adı adını döner, yani değişiklik olmaz.
    """
    if company.source not in NAME_FROM_SITE_SOURCES or not crawl_result.home_html:
        return
    if company.name != name_from_domain(company.domain):
        return
    new_name = derive_company_name(crawl_result.home_html, company.domain)
    if new_name and new_name != company.name:
        result.notes.append(f"ad sitenin başlığından güncellendi: {company.name} -> {new_name}")
        company.name = new_name
        result.name_updated = new_name


def is_crawl_due(company: Company, *, now: Optional[datetime] = None) -> bool:
    """`company` Otopilot'un (Faz 9.5) kendi tarama kuyruğuna alabileceği bir şirket mi?

    Hiç taranmadıysa (`crawled_at is None`) her zaman True. Son tarama GEÇİCİ bir hatayla
    bittiyse (`crawl_failed`, bkz. `CrawlResult.temporary_failure`) `CRAWL_RETRY_COOLDOWN`
    (3 gün) dolduktan sonra tekrar True olur. Kalıcı bir sonuçtan sonra (site açıldı ama
    e-posta yok, robots.txt engelledi, alan adı geçersiz) her zaman False -- bir daha
    KENDİLİĞİNDEN denenmez, yalnızca kullanıcı elle tekrar tarayabilir.
    """
    if company.crawled_at is None:
        return True
    if not company.crawl_failed:
        return False
    return (now or utcnow()) - company.crawled_at >= CRAWL_RETRY_COOLDOWN
