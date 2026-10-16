"""Kalıcı engelli alan adı listesi (PLAN.md Faz 10 Adım 10.8b).

Bir şirket "bir daha yazmayın / listeden çıkarın" dediğinde (ya da kullanıcı elle engellediğinde)
alan adı `BlockedDomain` tablosuna yazılır; keşif/CSV (`csv_import`), kuyruk seçimi
(`sender/queue`), Otopilot hazırlığı (`autopilot/runner`) ve gönderim (`smtp_sender`, SON
savunma) bu tabloyu kontrol eder. Tüm alan adları `normalize_domain()` ile karşılaştırılır
(CLAUDE.md kural 4).

Bir şirket İKİ alan adıyla eşleşebilir: kayıtlı `Company.domain` ve e-posta adresinin alan adı
(site `firma.com`, adres `info@firma.com.tr` olabilir) -- ikisinden biri engelliyse şirket
engellidir; şirketi engellemek ikisini de yazar.

Fonksiyonlar `flush` eder, COMMIT ETMEZ ("commit çağırana aittir", `src/core/db.py`).
"""
import logging
from typing import Optional

from sqlmodel import Session, select

from src.core.models import (
    Application,
    BLOCK_REASON_MANUAL,
    BlockedDomain,
    Company,
    STATUS_APPROVED,
    STATUS_QUEUED,
    STATUS_REJECTED,
    normalize_domain,
)

logger = logging.getLogger(__name__)


def blocked_domains(session: Session) -> set[str]:
    """Engelli alan adlarının (normalize edilmiş) tamamı; toplu kontrollerde tek sorgu için."""
    return {normalize_domain(domain) for domain in session.exec(select(BlockedDomain.domain))}


def is_domain_blocked(session: Session, domain: str) -> bool:
    normalized = normalize_domain(domain)
    return bool(normalized) and session.get(BlockedDomain, normalized) is not None


def company_domains(company: Company) -> set[str]:
    """Şirketin engel kontrolünde bakılan alan adları: kayıtlı alan adı + e-postanın alan adı."""
    domains = {normalize_domain(company.domain)}
    if company.email and "@" in company.email:
        domains.add(normalize_domain(company.email.rpartition("@")[2]))
    domains.discard("")
    return domains


def is_company_blocked(session: Session, company: Company) -> bool:
    return any(is_domain_blocked(session, domain) for domain in company_domains(company))


def block_domain(
    session: Session, domain: str, reason: str = BLOCK_REASON_MANUAL
) -> Optional[BlockedDomain]:
    """Alan adını engeller. Zaten engelliyse mevcut kaydı döner (sebep DEĞİŞMEZ); boş/geçersiz
    alan adında `None` döner."""
    normalized = normalize_domain(domain)
    if not normalized:
        return None
    row = session.get(BlockedDomain, normalized)
    if row is None:
        row = BlockedDomain(domain=normalized, reason=reason)
        session.add(row)
        session.flush()
        logger.info(f"{normalized}: alan adı engellendi ({reason})")
    return row


def unblock_domain(session: Session, domain: str) -> bool:
    """Engeli kaldırır; engel yoksa `False`."""
    row = session.get(BlockedDomain, normalize_domain(domain))
    if row is None:
        return False
    session.delete(row)
    session.flush()
    logger.info(f"{row.domain}: alan adı engeli kaldırıldı")
    return True


def block_company(session: Session, company: Company, reason: str = BLOCK_REASON_MANUAL) -> None:
    """Şirketin tüm alan adlarını engeller ve henüz gönderilmemiş başvurusunu reddeder
    (`queued`/`approved` -> `rejected`; gönderilmiş/yarıda kalmış kayda DOKUNMAZ)."""
    for domain in company_domains(company):
        block_domain(session, domain, reason)
    application = session.exec(
        select(Application).where(Application.company_id == company.id)
    ).first()
    if application is not None and application.status in (STATUS_QUEUED, STATUS_APPROVED):
        application.status = STATUS_REJECTED
        session.add(application)
        session.flush()


def unblock_company(session: Session, company: Company) -> bool:
    """Şirketin alan adlarının engelini kaldırır; en az biri kaldırıldıysa `True`.

    Reddedilmiş başvuru ESKİ durumuna DÖNMEZ (kullanıcı kuyrukta tekrar onaylar)."""
    removed = False
    for domain in company_domains(company):
        removed = unblock_domain(session, domain) or removed
    return removed
