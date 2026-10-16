"""Şirketler sekmesinin veri katmanı — PySide6 İÇERMEZ.

ORM nesneleri (`Company`, `Application`) arayüz katmanına asla girmez: SQLModel nesneleri
oturumuna bağlıdır ve iş parçacıkları arasında taşınamaz (bkz. `src/core/db.py`). Bu modüldeki
`CompanyRow`, tablo satırının DEĞİŞMEZ bir kopyasıdır; GUI iş parçacığında kısa ömürlü bir
oturumla doldurulur, oturum kapansa da güvenle kullanılabilir.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import or_
from sqlmodel import Session, select

from src.company_finder.email_extractor import is_role_address
from src.core.models import (
    STATUS_APPROVED,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_REJECTED,
    STATUS_SENDING,
    STATUS_SENT,
    Application,
    Company,
)
from src.core.text import fold_turkish

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FoundEmailRow:
    """`Company.found_emails` içindeki tek bir kaydın değişmez kopyası."""

    email: str
    source_url: str
    is_role: bool


@dataclass(frozen=True)
class CompanyRow:
    """Şirket tablosundaki bir satırın değişmez kopyası (bkz. modül docstring'i)."""

    id: int
    name: str
    domain: str
    website: Optional[str]
    city: Optional[str]
    email: Optional[str]
    email_source: Optional[str]
    email_is_role: Optional[bool]
    mx_valid: Optional[bool]
    sector_tags: tuple[str, ...]
    source: str
    about_text: Optional[str]
    found_emails: tuple[FoundEmailRow, ...]
    application_status: Optional[str]  # None = başvuru hazırlanmadı
    created_at: datetime


def to_company_row(company: Company, application_status: Optional[str]) -> CompanyRow:
    """Bir `Company` ORM nesnesini değişmez `CompanyRow` kopyasına çevirir."""
    return CompanyRow(
        id=company.id,
        name=company.name,
        domain=company.domain,
        website=company.website,
        city=company.city,
        email=company.email,
        email_source=company.email_source,
        email_is_role=company.email_is_role,
        mx_valid=company.mx_valid,
        sector_tags=tuple(company.sector_tags),
        source=company.source,
        about_text=company.about_text,
        found_emails=tuple(
            FoundEmailRow(email=entry["email"], source_url=entry["source_url"], is_role=entry["is_role"])
            for entry in company.found_emails
        ),
        application_status=application_status,
        created_at=company.created_at,
    )


def load_company_rows(session: Session) -> list[CompanyRow]:
    """Tüm şirketleri tablo satırı kopyası olarak okur (ada göre sıralı).

    GUI iş parçacığında, kısa ömürlü bir oturumla çağrılır. Tek sorgu (LEFT JOIN) ile
    okunur ki oturum kapandıktan sonra tembel yükleme (lazy-load) hatası çıkmasın.
    """
    statement = (
        select(Company, Application.status)
        .join(Application, Application.company_id == Company.id, isouter=True)
        .order_by(Company.name)
    )
    return [to_company_row(company, status) for company, status in session.exec(statement)]


def ids_missing_email(session: Session, *, limit: Optional[int] = None) -> list[int]:
    """E-postası veya tanıtım metni eksik şirketlerin id'leri.

    `scripts/enrich_companies.py`'deki ölçütle AYNI olmalı ki arayüz ve komut satırı hiçbir
    zaman farklı bir şirket listesi görmesin.
    """
    query = select(Company.id).where(or_(Company.email.is_(None), Company.about_text.is_(None)))
    if limit:
        query = query.limit(limit)
    return list(session.exec(query).all())


def ids_ready_for_application(session: Session) -> list[int]:
    """E-postası olan ama henüz başvurusu hazırlanmamış şirketlerin id'leri.

    `scripts/prepare_applications.py`'deki ölçütle AYNI.
    """
    query = (
        select(Company.id)
        .where(Company.email.is_not(None))
        .where(Company.id.not_in(select(Application.company_id)))
    )
    return list(session.exec(query).all())


#: Tablo sütun başlıkları, `cell_text`'teki sütun indeksleriyle aynı sırada.
COLUMN_TITLES = (
    "Şirket adı",
    "Alan adı",
    "E-posta",
    "Rol adresi",
    "MX",
    "Etiketler",
    "Kaynak",
    "Başvuru",
)

#: `queue_cli._format_tristate`'in tablo karşılığı. `None` kalıcı bir "hayır" değil,
#: "bilinmiyor" anlamına gelir.
ROLE_SYMBOLS = {True: "✓", False: "⚠", None: "—"}
MX_SYMBOLS = {True: "✓", False: "✗", None: "?"}

STATUS_LABELS_TR = {
    STATUS_QUEUED: "kuyrukta",
    STATUS_APPROVED: "onaylandı",
    STATUS_SENDING: "gönderiliyor",
    STATUS_REJECTED: "reddedildi",
    STATUS_SENT: "gönderildi",
    STATUS_FAILED: "hata",
}
NO_APPLICATION_LABEL = "hazırlanmadı"

ROLE_WARNING_TOOLTIP = (
    "Bu adres kişi adı gibi görünüyor; otomatik gönderim yapılmaz (CLAUDE.md kural 10)."
)


def cell_text(row: CompanyRow, column: int) -> str:
    """`COLUMN_TITLES`'teki sütun indeksine göre görüntülenecek metni döndürür."""
    if column == 0:
        return row.name
    if column == 1:
        return row.domain
    if column == 2:
        return row.email or "—"
    if column == 3:
        return ROLE_SYMBOLS[row.email_is_role]
    if column == 4:
        return MX_SYMBOLS[row.mx_valid]
    if column == 5:
        return ", ".join(row.sector_tags)
    if column == 6:
        return row.source
    if column == 7:
        if row.application_status is None:
            return NO_APPLICATION_LABEL
        return STATUS_LABELS_TR.get(row.application_status, row.application_status)
    raise IndexError(f"Bilinmeyen sütun: {column}")


def role_tooltip(row: CompanyRow) -> str:
    """`email_is_role` False ise kural 10 uyarısı, aksi halde boş metin."""
    if row.email_is_role is False:
        return ROLE_WARNING_TOOLTIP
    return ""


def search_text(row: CompanyRow) -> str:
    """Aramada taranan alanlar, Türkçe katlanmış: ad + alan adı + e-posta."""
    return fold_turkish(" ".join(filter(None, (row.name, row.domain, row.email or ""))))


def matches_search(row: CompanyRow, query: str) -> bool:
    """`query` boşsa her satır eşleşir; değilse Türkçe'ye duyarsız bir alt dize araması yapılır."""
    return fold_turkish(query).strip() in search_text(row)


WARN_NOT_ROLE_ADDRESS = (
    "Bu adres kişi adı gibi görünüyor (kariyer@/info@ gibi bir rol adresi değil). "
    "Bu şirkete OTOMATİK gönderim yapılmaz; yalnızca onay kuyruğunda onaylarsanız gönderilir. "
    "Yine de bu adresi kullanmak istiyor musunuz?"
)


def use_email(session: Session, company: Company, email: str) -> None:
    """`found_emails` listesinden seçilen adresi şirketin ana adresi yapar.

    `email`, `email_is_role` ve `email_source` BİRLİKTE güncellenir — üçü tutarlı olmalı.
    `email_is_role`, saklanan kayıttan kopyalanmaz; `is_role_address()` ile YENİDEN
    hesaplanır (CLAUDE.md kural 10'un tek yetkili dedektörü). `flush` eder, COMMIT ETMEZ
    (`src/core/db.py` kuralı) — commit çağırana aittir.

    Adres `found_emails` içinde yoksa Türkçe mesajlı `ValueError` fırlatır.
    """
    session.add(company)
    entry = next((item for item in company.found_emails if item["email"] == email), None)
    if entry is None:
        raise ValueError(f"Bu adres şirketin bulunan e-postaları arasında yok: {email}")

    company.email = email
    company.email_source = entry["source_url"]
    company.email_is_role = is_role_address(email)
    session.flush()
