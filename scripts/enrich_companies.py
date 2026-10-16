"""Eksik e-posta/tanıtım metni olan şirketleri sitelerini tarayarak zenginleştirir.

Kullanım:
    python scripts/enrich_companies.py                          # eksikleri tara
    python scripts/enrich_companies.py --limit 5                # sadece ilk 5 şirket
    python scripts/enrich_companies.py --domain firma.com       # tek şirket (dolu olsa da)
    python scripts/enrich_companies.py --url https://firma.com  # DENEME: veritabanına dokunmaz

`--url` deneme modudur: verilen siteyi tarar, sonucu Türkçe yazdırır, veritabanına HİÇ
dokunmaz — şirket kaydı gerekmez, `data/app.db` yoksa oluşturulmaz bile.

Ağa çıkar (httpx, gerektiğinde Playwright) ve DNS sorgusu yapar (MX kontrolü). İlan sitesi
taranmaz, form doldurulmaz (CLAUDE.md). Tarama yavaştır (şirket başına ~10 sn); her şirketten
sonra commit edilir, Ctrl+C ile durdurulursa öncekiler kaybolmaz.
"""
import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import or_  # noqa: E402
from sqlmodel import select  # noqa: E402

from src.company_finder.enricher import EnrichResult, enrich_company  # noqa: E402
from src.company_finder.mx_check import has_mx_record  # noqa: E402
from src.company_finder.site_crawler import crawl_company, page_category_name  # noqa: E402
from src.core.db import get_engine, get_session, init_db  # noqa: E402
from src.core.logging_setup import quiet_noisy_loggers  # noqa: E402
from src.core.models import Company, normalize_domain  # noqa: E402

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--domain", help="Yalnızca bu alan adındaki şirketi işle (dolu olsa da)")
    target.add_argument("--url", help="DENEME: bu siteyi tara, veritabanına hiç dokunma")
    parser.add_argument("--limit", type=int, default=None, help="En fazla kaç şirket işlensin")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="(--url ile) gezilen her sayfanın türünü ve çıkarılan metin uzunluğunu göster",
    )
    return parser.parse_args()


def _format_mx(value: Optional[bool]) -> str:
    if value is True:
        return "var"
    if value is False:
        return "yok"
    return "bilinmiyor (zaman aşımı / DNS erişilemedi)"


def _print_crawl_preview(website: str, *, verbose: bool = False) -> None:
    """`--url` deneme modu: veritabanına dokunmadan bir siteyi tarar ve sonucu yazdırır."""
    logger.info(f"Deneme taraması: {website}")

    on_page = None
    if verbose:
        def on_page(url: str, visit_priority: int, text_length: int) -> None:
            logger.info(f"  [{page_category_name(visit_priority)}] {url}: {text_length} karakter")

    result = crawl_company(website, on_page=on_page)

    logger.info(f"Gezilen sayfa sayısı: {len(result.visited_urls)}")
    if not verbose:
        for url in result.visited_urls:
            logger.info(f"  - {url}")
    logger.info(f"Playwright kullanıldı mı: {'evet' if result.used_playwright else 'hayır'}")

    if result.error:
        logger.error(f"Hata: {result.error}")

    if result.emails:
        logger.info(f"Bulunan adresler ({len(result.emails)}):")
        for found in result.emails:
            role = "rol adresi" if found.is_role else "kişi adı gibi görünüyor"
            logger.info(f"  + {found.email} ({role}, {found.source_url})")
        best = result.emails[0]
        mx_domain = normalize_domain(best.email.rpartition("@")[2])
        logger.info(f"En iyi adresin MX durumu ({mx_domain}): {_format_mx(has_mx_record(mx_domain))}")
    else:
        logger.info("Hiç e-posta bulunamadı.")

    if result.about_text:
        preview = result.about_text.splitlines()[0][:200]
        logger.info(f"Tanıtım metni (ilk satır): {preview}")
    else:
        logger.info("Tanıtım metni bulunamadı.")


def _select_companies(session, *, domain: Optional[str], limit: Optional[int]) -> list[Company]:
    if domain:
        company = session.exec(
            select(Company).where(Company.domain == normalize_domain(domain))
        ).first()
        if company is None:
            logger.error(f"Bu alan adında kayıtlı şirket yok: {domain}")
            return []
        return [company]

    query = select(Company).where(or_(Company.email.is_(None), Company.about_text.is_(None)))
    if limit:
        query = query.limit(limit)
    return list(session.exec(query).all())


def _print_company_report(company: Company, result: EnrichResult) -> None:
    logger.info("=" * 70)
    logger.info(f"Şirket: {company.name} ({company.domain})")
    logger.info(f"Gezilen sayfa sayısı: {result.visited_count}")
    if result.email_added:
        role = (
            "rol adresi"
            if company.email_is_role
            else "kişi adı gibi görünüyor, otomatik gönderim YASAK (CLAUDE.md 10. kural)"
        )
        logger.info(f"E-posta eklendi: {result.email_added} ({role})")
    for note in result.notes:
        logger.info(f"Not: {note}")
    if result.about_added:
        logger.info("Tanıtım metni dolduruldu.")
    logger.info(f"MX durumu: {_format_mx(result.mx_valid)}")
    if result.error:
        logger.warning(f"Hata: {result.error}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    quiet_noisy_loggers()
    args = _parse_args()

    if args.limit and args.url:
        logger.warning("--limit, --url deneme modunda yok sayılır")

    if args.url:
        _print_crawl_preview(args.url, verbose=args.verbose)
        return

    engine = get_engine()
    init_db(engine=engine)

    with get_session(engine=engine) as session:
        companies = _select_companies(session, domain=args.domain, limit=args.limit)
        if not companies:
            logger.info("İşlenecek şirket yok (eksik e-posta/tanıtım metni olan şirket kalmadı).")
            return

        logger.info(f"{len(companies)} şirket işlenecek.")
        for company in companies:
            result = enrich_company(session, company)
            _print_company_report(company, result)
            session.commit()  # şirket başına: yavaş tarama, Ctrl+C'de öncekiler kaybolmasın

    logger.info("Bitti.")


if __name__ == "__main__":
    main()
