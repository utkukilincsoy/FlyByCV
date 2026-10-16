"""Gelen bir mailin HANGİ başvuruya ait olduğunu bulur (PLAN.md Faz 7.3).

Eşleştirme sırası (Faz 7.1 notu):
  (a) **thread**  — `In-Reply-To`/`References` başlığı → `Application.message_id`
  (b) **domain**  — gönderenin alan adı → `Company.domain` (alt alan adı dahil). Gönderenin
      alan adı ücretsiz bir mail sağlayıcısıysa (`gmail.com` vb.,
      `company_finder/free_mail.is_free_mail_domain`) bu adım hiç denenmez: `Company.domain`
      normalde asla ücretsiz olamaz (`csv_import._build_company` bunu zaten reddeder), ama bu
      kontrol o kuralı burada da tekrarlayan ikinci bir savunma hattı — aksi halde ileride bir
      kayıt bu kuralı atlayarak eklenirse (ör. elle veritabanı düzenlemesi), o "şirkete" gelen
      HERHANGİ bir gmail.com maili yanlışlıkla eşleşirdi.
  (c) **bounce**  — DSN (teslim edilemedi bildirimi) gövdesine gömülü orijinal Message-ID →
      `Application.message_id` (bkz. `imap_reader._find_embedded_message_id`)

Yalnızca `sent_at` DOLU başvurular aday; mail `sent_at`'ten ÖNCEYSE eşleşmez (bir başvurudan
önce gelen bir mail o başvuruya cevap olamaz). Eşleşmeyen mail bu modülden `None` döner —
kaydetmemek çağıranın (Adım 7.5, `service.py`) işi.

**Test modu koruması:** kendi adresimizden gelen ya da `X-FlybyCV-Intended-To` başlığını
taşıyan mail (test gönderiminin kendi INBOX'ımıza düşen kopyası) hiçbir zaman eşleştirilmez —
"cevap" sayılmaz.

**İki aşamalı indirmeyle uyum (Adım 7.2):** `select_candidates` yalnızca BAŞLIKLARLA çalışır
ve "bu mesajın gövdesini indirmeye değer mi?" sorusuna cevap verir — thread/alan adı eşleşmesi
zaten başlıktan anlaşılabilir; bounce içinse (gönderen genelde şirketin DEĞİL, posta
sağlayıcısının bir "mailer-daemon" adresi olduğu için alan adından anlaşılamaz) yalnızca ucuz
bir ön eleme (`looks_like_bounce`) yapılır, kesin karar gövde indirildikten sonra `match_reply`
ile verilir. Bu modül veritabanına hiç YAZMAZ (yalnızca okur) ve IMAP'a hiç bağlanmaz.
"""
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Optional

from sqlmodel import Session, select

from src.company_finder.free_mail import is_free_mail_domain
from src.core.models import Application, normalize_domain
from src.core.text import build_word_pattern, matches_any
from src.inbox.imap_reader import EmailBody, EmailHeader
from src.sender.smtp_sender import MailIdentity

MATCH_THREAD = "thread"
MATCH_DOMAIN = "domain"
MATCH_BOUNCE = "bounce"

_BOUNCE_FROM_KEYWORDS = (
    "mailer-daemon",
    "postmaster",
    "mail delivery subsystem",
    "mail delivery system",
)
_BOUNCE_SUBJECT_KEYWORDS = (
    "undelivered mail returned to sender",
    "delivery status notification",
    "mail delivery failed",
    "returned mail",
    "delivery failure",
    "undeliverable",
)
_BOUNCE_FROM_PATTERN = build_word_pattern(_BOUNCE_FROM_KEYWORDS)
_BOUNCE_SUBJECT_PATTERN = build_word_pattern(_BOUNCE_SUBJECT_KEYWORDS)


@dataclass(frozen=True)
class MatchedReply:
    """`match_reply`nin başarılı sonucu."""

    application_id: int
    matched_by: str  # MATCH_* sabitlerinden biri, yalnızca teşhis amaçlı


def is_own_test_mail(header: EmailHeader, identity: MailIdentity) -> bool:
    """Bu mesaj test modunda kendi INBOX'ımıza düşen gönderim kopyamız mı?

    İki bağımsız işaretten biri yeterli: gönderen bizzat kendi adresimiz mi, YA DA mesaj
    `X-FlybyCV-Intended-To` başlığını taşıyor mu (bkz. modül docstring'i).
    """
    _, sender_addr = parseaddr(header.from_addr)
    if sender_addr.strip().lower() == identity.email.strip().lower():
        return True
    return bool(header.intended_to)


def looks_like_bounce(header: EmailHeader) -> bool:
    """Yalnızca BAŞLIKTAN ucuz bir ön eleme: bu mesajın gövdesi indirilmeye değer mi?

    Kesin "bounce" kategorisi kararı `src/inbox/classifier.py`nin (Adım 7.4) işi; burası
    yalnızca `select_candidates` için gövdeyi indirmeye değer görür — gerçek eşleştirme
    (`Application.id`) gövdeye gömülü orijinal Message-ID bulunduktan sonra `match_reply` ile
    yapılır.
    """
    return matches_any(_BOUNCE_FROM_PATTERN, header.from_addr) or matches_any(
        _BOUNCE_SUBJECT_PATTERN, header.subject
    )


def _sender_domain(from_addr: str) -> Optional[str]:
    _, addr = parseaddr(from_addr)
    if "@" not in addr:
        return None
    return normalize_domain(addr.rpartition("@")[2])


def _is_after_sent(header: EmailHeader, application: Application) -> bool:
    """Mail, başvurunun gönderiminden SONRA mı geldi? Başlık tarihi ayrıştırılamadıysa
    (`header.date is None`) reddetmek yanlış pozitif üretmez diye lehte yorumlanır."""
    if header.date is None:
        return True
    return header.date >= application.sent_at


def _find_by_message_id(session: Session, message_id: str) -> Optional[Application]:
    statement = select(Application).where(
        Application.message_id == message_id,
        Application.sent_at.is_not(None),
    )
    return session.exec(statement).first()


def _find_by_domain(session: Session, domain: str) -> Optional[Application]:
    """`domain`, şirketin kendi alan adıyla AYNI ya da onun bir alt alan adı olan ilk (gönderimi
    yapılmış) başvuruyu döner. Tek tek karşılaştırma yapılır (SQL `LIKE` alt alan adı için
    yeterli değil); bu projenin ölçeğinde (tek kullanıcı, tek gelen kutusu) sorun değil.
    """
    statement = select(Application).where(Application.sent_at.is_not(None))
    for application in session.exec(statement):
        company_domain = application.company.domain
        if domain == company_domain or domain.endswith(f".{company_domain}"):
            return application
    return None


def _match_thread_or_domain(session: Session, header: EmailHeader) -> Optional[MatchedReply]:
    message_ids = ([header.in_reply_to] if header.in_reply_to else []) + header.references
    for message_id in message_ids:
        application = _find_by_message_id(session, message_id)
        if application is not None and _is_after_sent(header, application):
            return MatchedReply(application.id, MATCH_THREAD)

    domain = _sender_domain(header.from_addr)
    if domain and not is_free_mail_domain(domain):
        application = _find_by_domain(session, domain)
        if application is not None and _is_after_sent(header, application):
            return MatchedReply(application.id, MATCH_DOMAIN)
    return None


def select_candidates(
    headers: list[EmailHeader], *, session: Session, identity: MailIdentity
) -> list[EmailHeader]:
    """Gövdesi indirilmeye değer başlıkları seçer (Adım 7.2'nin iki aşamalı indirimi için).

    Kendi test mailimiz hiç aday sayılmaz. Geri kalanlar: thread/alan adı eşleşmesi ZATEN
    başlıktan kurulabiliyorsa, ya da mesaj bir DSN'e benziyorsa (`looks_like_bounce`) aday.
    """
    candidates = []
    for header in headers:
        if is_own_test_mail(header, identity):
            continue
        if _match_thread_or_domain(session, header) is not None or looks_like_bounce(header):
            candidates.append(header)
    return candidates


def match_reply(
    session: Session,
    header: EmailHeader,
    body: Optional[EmailBody],
    *,
    identity: MailIdentity,
) -> Optional[MatchedReply]:
    """Bu mesajın hangi başvuruya ait olduğunu bulur; eşleşmiyorsa `None`.

    `body`, `select_candidates` sonrası `imap_reader.fetch_bodies` ile indirilmiş olmalı —
    yalnızca bounce eşleşmesi (gömülü orijinal Message-ID) için kullanılır, thread/alan adı
    eşleşmesi başlıktan yeterlidir.
    """
    if is_own_test_mail(header, identity):
        return None

    matched = _match_thread_or_domain(session, header)
    if matched is not None:
        return matched

    if body is not None and body.original_message_id:
        application = _find_by_message_id(session, body.original_message_id)
        if application is not None and _is_after_sent(header, application):
            return MatchedReply(application.id, MATCH_BOUNCE)

    return None
