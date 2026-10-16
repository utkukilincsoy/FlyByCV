"""Cevaplar sekmesinin veri katmanı — PySide6 İÇERMEZ (bkz. `src/ui/queue_data.py`).

ORM nesneleri (`EmailReply`, `Application`, `Company`) arayüz katmanına asla girmez;
`ReplyRow` tablodaki bir satırın DEĞİŞMEZ kopyasıdır, GUI iş parçacığında kısa ömürlü bir
oturumla doldurulur.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from src.core.blocklist import (
    block_company,
    blocked_domains,
    company_domains,
    unblock_company,
)
from src.core.models import (
    BLOCK_REASON_MANUAL,
    BLOCK_REASON_OPT_OUT,
    CLASSIFIED_BY_USER,
    REPLY_CATEGORIES_ALL,
    REPLY_OPT_OUT,
    Application,
    Company,
    EmailReply,
    utcnow,
)
from src.core.timeutil import to_istanbul
from src.inbox.categories import CLASSIFIED_BY_LABELS_TR, REPLY_LABELS_TR
from src.inbox.service import InboxCheckResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReplyRow:
    """Cevaplar tablosundaki bir satırın değişmez kopyası (bkz. modül docstring'i)."""

    id: int
    application_id: int
    company_name: str
    domain: str
    from_addr: str
    subject: Optional[str]
    received_at: datetime
    category: str
    classified_by: str
    auto_category: Optional[str]
    body_preview: Optional[str]
    matched_by: Optional[str]
    #: Şirketin alan adı (ya da e-postasının alan adı) engelli listesinde mi (Adım 10.8b)?
    blocked: bool = False


def to_reply_row(
    reply: EmailReply,
    application: Application,
    company: Company,
    blocked: frozenset[str] | set[str] = frozenset(),
) -> ReplyRow:
    """Bir `EmailReply` + `Application` + `Company` ORM üçlüsünü değişmez `ReplyRow`'a çevirir."""
    return ReplyRow(
        id=reply.id,
        application_id=application.id,
        company_name=company.name,
        domain=company.domain,
        from_addr=reply.from_addr,
        subject=reply.subject,
        received_at=reply.received_at,
        category=reply.category,
        classified_by=reply.classified_by,
        auto_category=reply.auto_category,
        body_preview=reply.body_preview,
        matched_by=reply.matched_by,
        blocked=bool(company_domains(company) & blocked),
    )


def load_reply_rows(session: Session) -> list[ReplyRow]:
    """Tüm cevapları tablo satırı kopyası olarak okur (en yeni önce).

    GUI iş parçacığında, kısa ömürlü bir oturumla çağrılır. Tek sorgu (JOIN) ile okunur ki
    oturum kapandıktan sonra tembel yükleme hatası çıkmasın (bkz. `queue_data.load_application_rows`).
    """
    statement = (
        select(EmailReply, Application, Company)
        .join(Application, EmailReply.application_id == Application.id)
        .join(Company, Application.company_id == Company.id)
        .order_by(EmailReply.received_at.desc(), EmailReply.id.desc())
    )
    blocked = blocked_domains(session)
    return [
        to_reply_row(reply, application, company, blocked)
        for reply, application, company in session.exec(statement)
    ]


#: Tablo sütun başlıkları, `cell_text`'teki sütun indeksleriyle aynı sırada.
COLUMN_TITLES = ("Şirket", "Gönderen", "Konu", "Kategori", "Alındı")

#: `EmailReply.matched_by` için Türkçe etiketler (bkz. `src/inbox/reply_matcher.py`).
MATCHED_BY_LABELS_TR = {
    "thread": "Mail zinciri",
    "domain": "Alan adı",
    "bounce": "Teslim edilemedi kaydı",
}


def cell_text(row: ReplyRow, column: int) -> str:
    """`COLUMN_TITLES`'teki sütun indeksine göre görüntülenecek metni döndürür."""
    if column == 0:
        return row.company_name
    if column == 1:
        return row.from_addr
    if column == 2:
        return row.subject or "—"
    if column == 3:
        return REPLY_LABELS_TR.get(row.category, row.category)
    if column == 4:
        return to_istanbul(row.received_at).strftime("%d.%m.%Y %H:%M") if row.received_at else "—"
    raise IndexError(f"Bilinmeyen sütun: {column}")


def row_tooltip(row: ReplyRow) -> str:
    """Elle düzeltilmiş bir kayıtta ilk otomatik sonucu hatırlatır, aksi halde boş metin."""
    if row.classified_by == CLASSIFIED_BY_USER and row.auto_category:
        auto_label = REPLY_LABELS_TR.get(row.auto_category, row.auto_category)
        return f"Elle düzeltildi (ilk otomatik sonuç: {auto_label})"
    return ""


#: Kategori filtresi seçenekleri: (görünen etiket, filtre değeri). `REPLY_LABELS_TR`'nin sırası
#: korunur (`Faz 7.1`'deki gibi sabit bir kategori kümesi).
FILTER_ALL = "all"

CATEGORY_FILTER_CHOICES: tuple[tuple[str, str], ...] = (("Hepsi", FILTER_ALL),) + tuple(
    (label, category) for category, label in REPLY_LABELS_TR.items()
)


def matches_category(row: ReplyRow, choice: str) -> bool:
    """`choice`, `CATEGORY_FILTER_CHOICES`'teki bir filtre değeri olmalı."""
    if choice == FILTER_ALL:
        return True
    return row.category == choice


# ---------------------------------------------------------------------------
# Önizleme paneli metinleri (saf biçimlendiriciler, `src/ui/replies_preview.py` kullanır)
# ---------------------------------------------------------------------------


def format_received_line(row: ReplyRow) -> str:
    return f"Alındı: {to_istanbul(row.received_at).strftime('%d.%m.%Y %H:%M')}"


def format_matched_by_line(row: ReplyRow) -> str:
    label = MATCHED_BY_LABELS_TR.get(row.matched_by, row.matched_by or "bilinmiyor")
    return f"Eşleştirme yöntemi: {label}"


def format_classification_line(row: ReplyRow) -> str:
    """Otomatik sınıflandırma yöntemini (ve elle düzeltildiyse ilk sonucu) gösterir."""
    if row.classified_by == CLASSIFIED_BY_USER:
        if row.auto_category:
            auto_label = REPLY_LABELS_TR.get(row.auto_category, row.auto_category)
            return f"Kullanıcı elle düzeltti (ilk otomatik sonuç: {auto_label})"
        return "Kullanıcı elle düzeltti"
    method_label = CLASSIFIED_BY_LABELS_TR.get(row.classified_by, row.classified_by)
    return f"Otomatik sınıflandırma: {method_label}"


# ---------------------------------------------------------------------------
# Elle düzeltme (kısa ömürlü oturum, GUI iş parçacığı)
# ---------------------------------------------------------------------------


def set_reply_category(session: Session, reply: EmailReply, category: str) -> Optional[str]:
    """Kullanıcının elle seçtiği kategoriyi kaydeder; `classified_by` "user" olur.

    İlk otomatik sonuç `auto_category`de DEĞİŞMEDEN kalır (Faz 7.1 kararı — "başlangıçta ne
    denilmişti" bilgisi kaybolmasın). Geçersiz bir kategori verilirse Türkçe hata döner, hiçbir
    şey değiştirmez. `flush` eder, COMMIT ETMEZ (`src/core/db.py` kuralı) — commit çağırana aittir.
    """
    if category not in REPLY_CATEGORIES_ALL:
        return f"Bilinmeyen kategori: {category}"
    reply.category = category
    reply.classified_by = CLASSIFIED_BY_USER
    reply.updated_at = utcnow()
    session.add(reply)
    if category == REPLY_OPT_OUT:
        # "Bir daha yazmayın" otomatik ya da elle seçilsin, sonuç aynı: kalıcı engel. Tersi
        # (başka kategoriye çevirmek) engeli KALDIRMAZ -- engeli yalnızca "Engeli kaldır" kaldırır.
        block_company(session, reply.application.company, BLOCK_REASON_OPT_OUT)
    session.flush()
    return None


def block_reply_company(session: Session, reply: EmailReply) -> None:
    """Cevabın şirketini elle engeller ("Bu alan adını engelle" düğmesi). `flush` eder."""
    block_company(session, reply.application.company, BLOCK_REASON_MANUAL)


def unblock_reply_company(session: Session, reply: EmailReply) -> bool:
    """Cevabın şirketinin engelini kaldırır ("Engeli kaldır" düğmesi); engel yoksa `False`.

    Reddedilmiş başvuru eski durumuna DÖNMEZ. Kullanıcı engeli bile bile kaldırıyor: şirketin
    bir daha yazmama isteği varsa sorumluluk kullanıcıdadır (bkz. `presets/legal/kvkk_tr.md`)."""
    return unblock_company(session, reply.application.company)


# ---------------------------------------------------------------------------
# "Şimdi kontrol et" bitiş özeti (bkz. `scripts/check_inbox.py`'nin CLI çıktısıyla aynı içerik)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Periyodik otomatik kontrol (`src/ui/replies_tab.py::RepliesTab`'in `QTimer`'ı çağırır)
# ---------------------------------------------------------------------------


def should_auto_check(
    now: datetime, last_attempt_at: Optional[datetime], interval_minutes: int
) -> bool:
    """Otomatik gelen kutusu kontrolünün zamanı geldi mi?

    Saf bir fonksiyon: `now` DIŞARIDAN verilir, gerçek saate hiç bakmaz — testte gerçek bir
    zamanlayıcının beklenmesine ya da çalışma anının saatine gerek kalmaz.

    `interval_minutes <= 0` ise otomatik kontrol KAPALI (kullanıcı yalnızca "Şimdi kontrol
    et" ile elle çalıştırabilir). `last_attempt_at` BİLEREK `Setting.inbox_last_check_at`
    (son BAŞARILI/denenmiş taramanın IMAP penceresi) DEĞİL, son DENEME zamanıdır — çağıran
    bunu kendi belleğinde tutar (`RepliesTab._last_auto_attempt_at`). Böylece kalıcı bir IMAP
    bağlantı hatasında her `QTimer` turunda yeniden bağlanmayı DENEMEZ; `check_inbox()`nun
    kendisi zaten başarısız denemede `inbox_last_check_at`'i güncellemiyor (bkz.
    `src/inbox/service.py`), o alana güvenilseydi sıkı bir yeniden deneme döngüsü oluşurdu.
    """
    if interval_minutes <= 0:
        return False
    if last_attempt_at is None:
        return True
    return now - last_attempt_at >= timedelta(minutes=interval_minutes)


def format_check_summary(result: InboxCheckResult) -> str:
    lines = [
        f"{result.headers_fetched} başlık tarandı, {result.candidates} aday bulundu, "
        f"{result.new_replies} yeni cevap kaydedildi ({result.duplicate_skipped} zaten kayıtlıydı)."
    ]
    for category, count in result.new_reply_categories.items():
        label = REPLY_LABELS_TR.get(category, category)
        lines.append(f"  {label}: {count}")
    for domain in result.bounced_domains:
        lines.append(f"  ⚠ teslim edilemedi: {domain}")
    for domain in result.blocked_domains:
        lines.append(f"  ⛔ bir daha yazılmaması istendi, alan adı engellendi: {domain}")
    if result.missing_message_id_skipped:
        lines.append(
            f"{result.missing_message_id_skipped} eşleşme Message-ID eksik olduğu için "
            "kaydedilemedi"
        )
    return "\n".join(lines)
