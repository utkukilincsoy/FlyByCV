"""Dashboard sekmesinin veri katmanı — PySide6 İÇERMEZ (bkz. `src/ui/replies_data.py`).

**Test modundaki gönderimler istatistiklere hiç KARIŞMAZ, bilinçli bir filtreye gerek yok:**
test modu `Application` kaydına HİÇ dokunmuyor (`status` "queued" kalır, `sent_at` boş kalır —
bkz. `src/sender/smtp_sender.py` modül docstring'i, "Ctrl+C / çökme koruması" bölümünden önceki
paragraf). Bu yüzden `Application.status`/`sent_at` üzerinden sayılan her rakam zaten yalnızca
GERÇEK gönderimleri kapsıyor.
"""
from dataclasses import dataclass

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
    EmailReply,
)
from src.inbox.categories import REPLY_LABELS_TR
from src.ui.company_data import STATUS_LABELS_TR

#: Başvuru durumu sayımının gösterileceği sabit sıra (kuyruktan gönderilmişe/reddedilmişe doğru).
STATUS_ORDER: tuple[str, ...] = (
    STATUS_QUEUED,
    STATUS_APPROVED,
    STATUS_SENDING,
    STATUS_SENT,
    STATUS_REJECTED,
    STATUS_FAILED,
)
#: Cevap kategorisi sayımının gösterileceği sabit sıra (`REPLY_LABELS_TR`'nin kendi sırası).
CATEGORY_ORDER: tuple[str, ...] = tuple(REPLY_LABELS_TR)

EMPTY_STATE_TEXT = (
    "Henüz hiç şirket eklenmedi. Şirketler sekmesinden şirket ekleyip başvuru hazırladıkça "
    "istatistikler burada görünecek."
)


@dataclass(frozen=True)
class DashboardStats:
    """Dashboard'da gösterilen tüm sayılar; tek bir kısa ömürlü oturumla bir kerede toplanır."""

    total_companies: int
    companies_with_email: int
    status_counts: dict[str, int]
    reply_counts: dict[str, int]

    @property
    def has_data(self) -> bool:
        """Hiç şirket eklenmemişse Dashboard boş kutular yerine `EMPTY_STATE_TEXT` göstermeli."""
        return self.total_companies > 0


def load_dashboard_stats(session: Session) -> DashboardStats:
    """Tüm istatistikleri tek seferde toplar.

    GUI iş parçacığında, kısa ömürlü bir oturumla çağrılır (diğer sekmelerin `reload()`
    kalıbıyla aynı). Bu projenin ölçeğinde (tek kullanıcı) tüm satırları çekip Python'da
    saymak yeterli — `rate_limiter.py`deki gibi, `func.count()`e gerek yok.
    """
    companies = session.exec(select(Company)).all()
    applications = session.exec(select(Application)).all()
    replies = session.exec(select(EmailReply)).all()

    status_counts = {status: 0 for status in STATUS_ORDER}
    for application in applications:
        status_counts[application.status] = status_counts.get(application.status, 0) + 1

    reply_counts = {category: 0 for category in CATEGORY_ORDER}
    for reply in replies:
        reply_counts[reply.category] = reply_counts.get(reply.category, 0) + 1

    return DashboardStats(
        total_companies=len(companies),
        companies_with_email=sum(1 for company in companies if company.email),
        status_counts=status_counts,
        reply_counts=reply_counts,
    )


def format_company_lines(stats: DashboardStats) -> list[str]:
    return [
        f"Toplam şirket: {stats.total_companies}",
        f"E-posta adresi bulunan: {stats.companies_with_email}",
    ]


def format_status_lines(stats: DashboardStats) -> list[str]:
    return [f"{STATUS_LABELS_TR[status]}: {stats.status_counts[status]}" for status in STATUS_ORDER]


def format_reply_lines(stats: DashboardStats) -> list[str]:
    return [f"{REPLY_LABELS_TR[category]}: {stats.reply_counts[category]}" for category in CATEGORY_ORDER]
