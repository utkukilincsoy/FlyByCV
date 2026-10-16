"""Gönderim kuyruğu: onay/red durum geçişleri ve moda göre sıradaki başvuruyu seçme.

Manuel modda SADECE kullanıcının onayladığı (`STATUS_APPROVED`) başvurular gönderilir. Otomatik
modda onaylananlara ek olarak, adresi rol adresi VE MX kaydı doğrulanmış (`is_auto_eligible`)
`STATUS_QUEUED` başvurular da gönderilir (CLAUDE.md kural 10) — diğerleri kuyrukta onay bekler.

`STATUS_SENDING` (yarıda kalmış gönderim, bkz. `smtp_sender.py`) hiçbir modda seçilmez; yalnızca
`mark_sent`/`mark_queued` kurtarma komutlarıyla çıkarılabilir.
"""
import logging
from typing import Collection, Optional, Sequence

from sqlmodel import Session, select

from src.core.blocklist import is_company_blocked
from src.core.models import (
    Application,
    Company,
    STATUS_APPROVED,
    STATUS_QUEUED,
    STATUS_REJECTED,
    STATUS_SENDING,
    STATUS_SENT,
    normalize_domain,
    utcnow,
)

logger = logging.getLogger(__name__)

#: `list_queue()`'nun varsayılan durumları: kullanıcının hâlâ ilgilenmesi gerekenler.
DEFAULT_QUEUE_STATUSES = (STATUS_QUEUED, STATUS_APPROVED, STATUS_SENDING)


def find_application_by_domain(session: Session, domain: str) -> Optional[Application]:
    """Bir şirketin alan adına (veya web sitesine) göre başvurusunu bulur; yoksa None."""
    normalized = normalize_domain(domain)
    company = session.exec(select(Company).where(Company.domain == normalized)).first()
    if company is None:
        return None
    return session.exec(select(Application).where(Application.company_id == company.id)).first()


def _immutable_reason(status: str) -> str:
    if status == STATUS_SENDING:
        return (
            "bu başvurunun gönderimi şu anda sürüyor (durumu belirsiz), onay durumu "
            "değiştirilemez — bkz. python scripts/queue_cli.py list"
        )
    return "bu başvuru zaten gönderilmiş, onay durumu değiştirilemez"


def approve_application(session: Session, application: Application) -> Optional[str]:
    """Başvuruyu onaylar (`STATUS_APPROVED`). Engel varsa Türkçe sebep döner, yoksa None.

    `sent`/`sending` dışındaki her durumdan onaylanabilir (fikir değiştirme serbest — örn.
    daha önce reddedilmiş bir başvuru sonradan onaylanabilir). Yalnızca `flush` edilir;
    commit çağırana aittir.
    """
    if application.status in (STATUS_SENT, STATUS_SENDING):
        return _immutable_reason(application.status)
    application.status = STATUS_APPROVED
    session.add(application)
    session.flush()
    return None


def reject_application(session: Session, application: Application) -> Optional[str]:
    """Başvuruyu reddeder (`STATUS_REJECTED`). Engel varsa Türkçe sebep döner, yoksa None."""
    if application.status in (STATUS_SENT, STATUS_SENDING):
        return _immutable_reason(application.status)
    application.status = STATUS_REJECTED
    session.add(application)
    session.flush()
    return None


def mark_sent(session: Session, application: Application) -> Optional[str]:
    """Kurtarma komutu: `sending` durumundaki bir başvuruyu `sent` yapar (`sent_at` doldurulur).

    Kullanıcı Gmail "Gönderilenler" klasörüne bakıp mailin gerçekten gittiğine karar verdiyse
    kullanılır (bkz. `scripts/queue_cli.py mark-sent`). Yalnızca `sending` durumunda çalışır —
    genel bir durum düzenleyici değil, tek amacı Ctrl+C/çökme sonrası yarıda kalan kaydı kapatmak.
    """
    if application.status != STATUS_SENDING:
        return (
            f"bu başvuru '{application.status}' durumunda; mark-sent yalnızca 'sending' "
            "durumundaki başvurularda kullanılabilir"
        )
    application.status = STATUS_SENT
    application.sent_at = utcnow()
    session.add(application)
    session.flush()
    return None


def mark_queued(session: Session, application: Application) -> Optional[str]:
    """Kurtarma komutu: `sending` durumundaki bir başvuruyu `queued`'a döndürür.

    Kullanıcı mailin GİTMEDİĞİNE karar verdiyse kullanılır — başvuru tekrar denenebilir hale
    gelir. Yalnızca `sending` durumunda çalışır.
    """
    if application.status != STATUS_SENDING:
        return (
            f"bu başvuru '{application.status}' durumunda; mark-queued yalnızca 'sending' "
            "durumundaki başvurularda kullanılabilir"
        )
    application.status = STATUS_QUEUED
    session.add(application)
    session.flush()
    return None


def is_auto_eligible(company: Company) -> bool:
    """Şirket otomatik modda gönderime uygun mu?

    `email_is_role is True` VE `mx_valid is True` ister — BİLİNÇLİ olarak
    `smtp_sender.check_send_allowed`'dan daha sıkı: orası yalnızca `mx_valid is False`'u
    (kesin hayır) engeller ve `None`'a (hiç kontrol edilmemiş) izin verir, çünkü onaylı bir
    manuel gönderimi bloke etmemeli. Burada ise `None` yeterli DEĞİL: otomatik mod hiçbir
    veri karanlıkta ateş etmemeli (CLAUDE.md kural 10). Bu iki fonksiyon kasıtlı olarak
    farklıdır — biri "tutarsızlık" diye eşitlerse kural 10 sessizce delinir.
    """
    return company.email_is_role is True and company.mx_valid is True


def guard_auto_mode(application: Application) -> bool:
    """`send_application(..., auto_mode=...)`'a geçilecek değer.

    Kullanıcı onayladıysa (`STATUS_APPROVED`) `False` döner: smtp katmanındaki kural 10
    guard'ı (rol adresi kontrolü) manuel onay için gevşer. Kuyruktan otomatik seçilen
    `STATUS_QUEUED` başvurular için `True` — sıkı guard devrede kalır.
    """
    return application.status != STATUS_APPROVED


def list_queue(
    session: Session, *, statuses: Sequence[str] = DEFAULT_QUEUE_STATUSES
) -> list[Application]:
    """Verilen durumlardaki başvuruları FIFO sırayla (oluşturulma zamanı) döner."""
    statement = (
        select(Application)
        .where(Application.status.in_(statuses))
        .order_by(Application.created_at, Application.id)
    )
    return list(session.exec(statement).all())


def next_application(
    session: Session, *, auto_mode: bool, exclude_ids: Collection[int] = ()
) -> Optional[Application]:
    """Sıradaki gönderilecek başvuruyu seçer; kalmadıysa None.

    Sıra: önce `approved` (kullanıcı zaten onayladı, öncelikli), sonra — yalnızca
    `auto_mode=True` ise — `is_auto_eligible` geçen `queued`. İki ayrı sorgu: öncelik SQL
    `CASE`'e gömülmez, uygunluk süzgeci Python tarafında kalır (`is_auto_eligible` tek doğru
    kaynak). `sending`/`rejected`/`sent`/`failed` hiçbir zaman seçilmez.

    `exclude_ids`: bu çalıştırmada zaten denenmiş başvuruların (guard reddetse de) tekrar
    seçilmesini engeller — hem test modu tekrarına hem sonsuz döngüye karşı.
    """
    excluded = list(exclude_ids)

    approved_statement = select(Application).where(Application.status == STATUS_APPROVED)
    if excluded:
        approved_statement = approved_statement.where(Application.id.not_in(excluded))
    approved_statement = approved_statement.order_by(Application.created_at, Application.id)

    # Engelli alan adları (Adım 10.8b) hiç seçilmez: `send_application` zaten reddederdi ama
    # kuyruğun başında kalıp her turda yer tutmasın (engellenince başvuru `rejected` de olur).
    for approved in session.exec(approved_statement).all():
        if not is_company_blocked(session, approved.company):
            return approved

    if not auto_mode:
        return None

    queued_statement = select(Application).where(Application.status == STATUS_QUEUED)
    if excluded:
        queued_statement = queued_statement.where(Application.id.not_in(excluded))
    queued_statement = queued_statement.order_by(Application.created_at, Application.id)

    for candidate in session.exec(queued_statement).all():
        if is_auto_eligible(candidate.company) and not is_company_blocked(session, candidate.company):
            return candidate
    return None
