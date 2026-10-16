"""Onay Kuyruğu sekmesinin veri katmanı — PySide6 İÇERMEZ.

`src/ui/company_data.py` ile aynı mimari: ORM nesneleri (`Application`, `Company`) arayüz
katmanına asla girmez (bkz. o modülün docstring'i). `ApplicationRow`, kuyruk tablosundaki bir
satırın DEĞİŞMEZ kopyasıdır; GUI iş parçacığında kısa ömürlü bir oturumla doldurulur.

`plan_send()`, `scripts/send_one.py`'deki onay sırasını (acil durdur -> limit/mesai ->
rol adresi -> gerçek alıcı) Qt'siz, saf bir fonksiyona taşır ki testlenebilsin; arayüz bu
fonksiyonun döndürdüğü uyarıları sırayla `QMessageBox` ile sorar.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

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
from src.core.text import format_duration_tr
from src.core.timeutil import to_istanbul
from src.sender.queue import DEFAULT_QUEUE_STATUSES
from src.sender.rate_limiter import SendWindow
from src.sender.worker import (
    EVENT_FAILED,
    EVENT_PAUSED_BY_WORKER,
    EVENT_REFUSED,
    EVENT_RUN_STARTED,
    EVENT_SENT,
    EVENT_STOPPED,
    EVENT_WAITING,
    QueueEvent,
    QueueRunResult,
)
from src.ui.company_data import ROLE_SYMBOLS, ROLE_WARNING_TOOLTIP, STATUS_LABELS_TR, MX_SYMBOLS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApplicationRow:
    """Kuyruk tablosundaki bir satırın değişmez kopyası (bkz. modül docstring'i)."""

    id: int
    company_id: int
    company_name: str
    domain: str
    email: Optional[str]
    email_is_role: Optional[bool]
    mx_valid: Optional[bool]
    status: str
    tailored: bool
    matched_tags: tuple[str, ...]
    cv_pdf_path: Optional[str]
    mail_subject: Optional[str]
    mail_body: Optional[str]
    created_at: datetime
    sent_at: Optional[datetime]
    error: Optional[str]


def to_application_row(application: Application, company: Company) -> ApplicationRow:
    """Bir `Application` + `Company` ORM çiftini değişmez `ApplicationRow` kopyasına çevirir."""
    return ApplicationRow(
        id=application.id,
        company_id=company.id,
        company_name=company.name,
        domain=company.domain,
        email=company.email,
        email_is_role=company.email_is_role,
        mx_valid=company.mx_valid,
        status=application.status,
        tailored=application.tailored,
        matched_tags=tuple(application.matched_tags),
        cv_pdf_path=application.cv_pdf_path,
        mail_subject=application.mail_subject,
        mail_body=application.mail_body,
        created_at=application.created_at,
        sent_at=application.sent_at,
        error=application.error,
    )


def load_application_rows(session: Session) -> list[ApplicationRow]:
    """Tüm başvuruları tablo satırı kopyası olarak okur (oluşturulma sırasına göre).

    GUI iş parçacığında, kısa ömürlü bir oturumla çağrılır. Tek sorgu (JOIN) ile okunur ki
    oturum kapandıktan sonra tembel yükleme (lazy-load) hatası çıkmasın (bkz.
    `company_data.load_company_rows`). Durum süzgeci burada değil, `QueueStatusProxy`'de.
    """
    statement = (
        select(Application, Company)
        .join(Company, Application.company_id == Company.id)
        .order_by(Application.created_at, Application.id)
    )
    return [to_application_row(application, company) for application, company in session.exec(statement)]


def load_application_row(session: Session, application_id: int) -> Optional[ApplicationRow]:
    """Tek bir başvuruyu tablo satırı kopyası olarak okur (bir işlem sonrası tazelemek için)."""
    application = session.get(Application, application_id)
    if application is None:
        return None
    return to_application_row(application, application.company)


#: Tablo sütun başlıkları, `cell_text`'teki sütun indeksleriyle aynı sırada.
COLUMN_TITLES = ("Şirket", "E-posta", "Rol adresi", "MX", "Durum", "Oluşturulma")


def cell_text(row: ApplicationRow, column: int) -> str:
    """`COLUMN_TITLES`'teki sütun indeksine göre görüntülenecek metni döndürür."""
    if column == 0:
        return row.company_name
    if column == 1:
        return row.email or "—"
    if column == 2:
        return ROLE_SYMBOLS[row.email_is_role]
    if column == 3:
        return MX_SYMBOLS[row.mx_valid]
    if column == 4:
        return STATUS_LABELS_TR.get(row.status, row.status)
    if column == 5:
        return to_istanbul(row.created_at).strftime("%d.%m.%Y %H:%M") if row.created_at else "—"
    raise IndexError(f"Bilinmeyen sütun: {column}")


#: Kural 10: kişi adı gibi görünen adres için belirgin uyarı (önizleme paneli + satır rengi).
PERSON_ADDRESS_WARNING_TEXT = (
    "⚠ DİKKAT: Bu adres rol adresi (kariyer@/ik@/info@) değil, kişi adı gibi görünüyor. "
    "Otomatik modda gönderilmez; yalnızca sizin onayınızla, manuel gönderilebilir."
)


def is_person_address(row: ApplicationRow) -> bool:
    """Adres kişi adı gibi görünüyorsa (`email_is_role is False`) True; bilinmiyorsa False."""
    return row.email_is_role is False


def row_tooltip(row: ApplicationRow) -> str:
    """Kural 10 rol adresi uyarısı, `sending` belirsizliği ve hata metnini birleştirir."""
    lines = []
    if row.email_is_role is False:
        lines.append(ROLE_WARNING_TOOLTIP)
    if row.status == STATUS_SENDING:
        lines.append(SENDING_WARNING_TEXT)
    if row.error:
        lines.append(f"Hata: {row.error}")
    return "\n".join(lines)


#: `queue_cli.py`'deki "kuyrukta gösterilecek" fikriyle aynı: kullanıcının hâlâ ilgilenmesi
#: gereken kayıtlar. CLI komutu yerine buton adları geçer (arayüzde anlamı yok).
SENDING_WARNING_TEXT = (
    "Bu başvurunun gönderimi yarıda kalmış, durumu BELİRSİZ. Gmail \"Gönderilenler\" "
    "klasörünü kontrol edip aşağıdan \"Gönderildi olarak işaretle\" ya da \"Kuyruğa geri al\" "
    "ile karar verin."
)

#: Durum filtresi seçenekleri: (görünen etiket, filtre değeri). Açılıştaki ilk eleman
#: (kullanıcı onayı) "Aktif kuyruk" -- `DEFAULT_QUEUE_STATUSES` ile aynı üç durum.
FILTER_ACTIVE = "active"
FILTER_ALL = "all"

STATUS_FILTER_CHOICES: tuple[tuple[str, str], ...] = (
    ("Aktif kuyruk", FILTER_ACTIVE),
    ("Hepsi", FILTER_ALL),
    (STATUS_LABELS_TR[STATUS_QUEUED], STATUS_QUEUED),
    (STATUS_LABELS_TR[STATUS_APPROVED], STATUS_APPROVED),
    (STATUS_LABELS_TR[STATUS_SENDING], STATUS_SENDING),
    (STATUS_LABELS_TR[STATUS_REJECTED], STATUS_REJECTED),
    (STATUS_LABELS_TR[STATUS_SENT], STATUS_SENT),
    (STATUS_LABELS_TR[STATUS_FAILED], STATUS_FAILED),
)


def matches_status(row: ApplicationRow, choice: str) -> bool:
    """`choice`, `STATUS_FILTER_CHOICES`'teki bir filtre değeri olmalı."""
    if choice == FILTER_ALL:
        return True
    if choice == FILTER_ACTIVE:
        return row.status in DEFAULT_QUEUE_STATUSES
    return row.status == choice


def format_recipient_line(row: ApplicationRow, *, identity_email: str, test_mode: bool) -> str:
    """Önizleme panelinde alıcının kim olacağını AÇIKÇA söyler (`smtp_sender._envelope`).

    Test modunda zarf alıcısı her zaman kendi adresimizdir, şirketin adresi hiçbir yerde
    kullanılmaz -- kullanıcı bunu tereddütsüz görmeli.
    """
    if test_mode:
        return (
            f"Bu mail SİZE gidecek: {identity_email} — şirketin adresi "
            f"({row.email or '—'}) KULLANILMAYACAK"
        )
    return f"GERÇEK ALICI: {row.email or '—'}"


def format_tags_line(row: ApplicationRow) -> str:
    """Eşleşen etiketleri gösterir; boşsa ana CV kullanıldığını açıkça söyler."""
    if not row.matched_tags:
        return "ana CV kullanıldı (eşleşen etiket yok)"
    return ", ".join(row.matched_tags)


@dataclass(frozen=True)
class SendPlan:
    """`plan_send()`'in sonucu."""

    #: Doluysa gönderim KESİN yapılmaz (acil durdur) -- onayla aşılamaz (CLAUDE.md kural 5).
    blocked_reason: Optional[str]
    #: Sırayla onay istenecek Türkçe uyarılar; biri reddedilirse gönderim iptal edilir.
    prompts: tuple[str, ...]
    #: `send_application(..., auto_mode=...)`'a geçecek değer.
    auto_mode: bool


def plan_send(
    row: ApplicationRow, *, window: SendWindow, sending_paused: bool, test_mode: bool
) -> SendPlan:
    """`scripts/send_one.py`'deki onay sırasının (108-139. satırlar) saf kopyası.

    Sıra: (1) acil durdur -- MUTLAK, hiçbir onayla aşılamaz; (2) limit/mesai kısıtı -- uyarı +
    onay; (3) rol adresi değilse -- büyük uyarı + onay, onaylanırsa `auto_mode=False` (kural 10
    manuel onay alındı); (4) test modu kapalıysa -- gerçek alıcıyı adıyla gösteren son onay.
    `status`/PDF/mail metni gibi engeller burada tekrarlanmaz; `check_send_allowed()` son
    savunma hattı olarak kalır.
    """
    if sending_paused:
        return SendPlan(
            blocked_reason=(
                "Gönderim ACİL DURDUR ile duraklatılmış. Devam etmek için önce acil durdur'u "
                "kapatın."
            ),
            prompts=(),
            auto_mode=True,
        )

    prompts: list[str] = []

    if not window.allowed:
        prompts.append(f"DİKKAT: {window.reason}\n\nYine de göndermek istiyor musunuz?")

    auto_mode = True
    if row.email_is_role is False:
        prompts.append(
            "DİKKAT: bu adres kariyer@/info@ gibi bir rol adresi değil, kişi adresi gibi "
            "görünüyor.\n\nGöndermek istediğinize emin misiniz?"
        )
        auto_mode = False  # manuel onay alındı (CLAUDE.md kural 10)

    if not test_mode:
        prompts.append(
            f"GERÇEK GÖNDERİM yapılacak, mail {row.email or '—'} adresine gidecek.\n\n"
            "Onaylıyor musunuz?"
        )

    return SendPlan(blocked_reason=None, prompts=tuple(prompts), auto_mode=auto_mode)


# ---------------------------------------------------------------------------
# Toplu gönderim: canlı log satırı / geri sayım / bitiş özeti (saf biçimlendiriciler)
# ---------------------------------------------------------------------------

#: `QueueEvent.kind` -> log panelinde önüne konacak simge. Renk yerine simge kullanılıyor
#: (`ROLE_SYMBOLS`/`MX_SYMBOLS` üslubu) -- düz metin kalır, her temada okunur.
EVENT_SYMBOLS = {
    EVENT_RUN_STARTED: "▶",
    EVENT_SENT: "✓",
    EVENT_REFUSED: "—",
    EVENT_FAILED: "✗",
    EVENT_WAITING: "⏳",
    EVENT_PAUSED_BY_WORKER: "⛔",
    EVENT_STOPPED: "■",
}


def format_log_line(event: QueueEvent, *, at: datetime) -> str:
    """Canlı log panelindeki tek satır: `"[14:05:12] ✓ firma.com: gönderildi"`.

    `at` dışarıdan verilir (testler deterministik olsun diye); sekme `now_istanbul()` geçer --
    zaten İstanbul saatinde olduğu için burada ayrıca dönüştürme yapılmaz.
    """
    symbol = EVENT_SYMBOLS.get(event.kind, "•")
    return f"[{at.strftime('%H:%M:%S')}] {symbol} {event.message}"


def format_countdown(remaining_seconds: float) -> str:
    """Geri sayım etiketi; `0` (veya altı) BOŞ metin döner -- etiket bu şekilde temizlenir."""
    if remaining_seconds <= 0:
        return ""
    return f"Sonraki gönderime {format_duration_tr(remaining_seconds)}"


def format_run_summary(run: QueueRunResult) -> str:
    """Bir gönderim turunun bitiş özeti.

    İçerik [scripts/run_sender.py](scripts/run_sender.py) ile aynı; CLI komutları yerine
    Onay Kuyruğu sekmesindeki buton adları geçer.
    """
    parts = [
        f"Bitti — gönderildi: {run.sent_count}, atlandı: {run.refused_count}, "
        f"hata: {run.failed_count}."
    ]
    if run.stop_reason:
        parts.append(f"Durma sebebi: {run.stop_reason}")
    if run.interrupted_domain:
        parts.append(
            f"DİKKAT: kesinti tam '{run.interrupted_domain}' şirketinin gönderimi sırasında "
            'oldu; durumu belirsiz. Tabloda bu satırı seçip Gmail "Gönderilenler" klasörünü '
            'kontrol ettikten sonra "Gönderildi olarak işaretle" ya da "Kuyruğa geri al" ile '
            "karar verin."
        )
    return " ".join(parts)
