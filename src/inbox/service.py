"""Gelen kutusunu uçtan uca kontrol eder (PLAN.md Faz 7 Adım 7.5).

Önceki adımların TEK birleştiği yer:
  * `imap_reader` — ham IMAP (başlık/gövde indirme); veritabanına hiç dokunmaz.
  * `reply_matcher` — hangi başvuruya ait (`select_candidates`/`match_reply`); veritabanını
    yalnızca OKUR.
  * `classifier` — hangi kategori; veritabanını yalnızca `get_provider` için okur.
  * **BURASI** — `EmailReply` oluşturur, `Application.bounced_at`'i doldurur, `Setting.
    inbox_last_check_at`'i günceller. Üçünden hiçbiri YAZMAZ; tek yazan yer burasıdır.

**Pencere / tekrarlanan tarama:** `SEARCH SINCE` gün duyarlıdır (saat bilgisi yok); bu yüzden
her tarama bir önceki taramadan `LOOKBACK_DAYS` gün ÖNCESİNE kadar geri gider (bkz. PLAN.md
Faz 7.1 notu). Bu, aynı maili birden fazla taramada "aday" yapabilir; asıl tekilleştirme
`EmailReply.message_id`nin BENZERSİZ olmasıyla burada yapılır — zaten kaydedilmiş bir
Message-ID bir daha eklenmez.

**Commit burada yapılır**, çağırana bırakılmaz (`sender/worker.process_queue` ile aynı
gerekçe): bu tek seferlik, kısa bir taramadır. IMAP bağlantısı/indirme sırasında bir hata
(`imap_reader.ImapConnectionError`) olursa hiçbir şey commit edilmemiş olur (yalnızca
`flush`) — bir sonraki taramada aynı pencere baştan denenir, veri kaybı olmaz.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from src.core.blocklist import block_company
from src.core.models import (
    Application,
    BLOCK_REASON_OPT_OUT,
    EmailReply,
    REPLY_BOUNCE,
    REPLY_OPT_OUT,
    utcnow,
)
from src.core.mail_providers import resolve_mail_connection
from src.core.settings import KEY_INBOX_LAST_CHECK_AT, get_setting, set_setting
from src.cv_builder.profile_loader import MasterProfile
from src.inbox.classifier import ClassifiedReply, classify
from src.inbox.imap_reader import (
    EmailBody,
    EmailHeader,
    fetch_bodies,
    fetch_headers,
    imap_session,
)
from src.inbox.reply_matcher import MatchedReply, match_reply, select_candidates
from src.sender.safety_brake import check_bounce_rate
from src.sender.smtp_sender import load_account, resolve_identity

logger = logging.getLogger(__name__)

#: `SEARCH SINCE` gün duyarlı; bir önceki taramanın kaç gün öncesine kadar TEKRAR bakılacağı.
LOOKBACK_DAYS = 2


@dataclass
class InboxCheckResult:
    """`check_inbox()`nun döndürdüğü özet.

    `error` doluysa (ör. Gmail şifresi kayıtlı değil, hiç gönderim yapılmamış) hiçbir şey
    denenmemiştir — bu BEKLENEN, kurulum eksikliği türü bir durumdur, istisna FIRLATILMAZ.
    IMAP bağlantı/protokol hatası (`ImapConnectionError`) ise ayrıca fırlatılır (bkz. modül
    docstring'i).
    """

    error: Optional[str] = None
    #: True yalnızca "hiç gönderim yapılmamış" durumunda -- `error` yine de doludur ama bu
    #: gerçek bir sorun değil, sadece henüz kontrol edilecek bir şey yok (bkz. `check_inbox.py`
    #: ve `src/ui/jobs.py::check_inbox_job`, ikisi de bunu INFO olarak gösterir, ERROR değil).
    no_applications_sent: bool = False
    headers_fetched: int = 0
    candidates: int = 0
    new_replies: int = 0
    duplicate_skipped: int = 0
    #: Message-ID'si olmayan (bu yüzden `EmailReply.message_id` benzersizlik kısıtını
    #: sağlayamayan) bir eşleşme — nadir ama IMAP'ta mümkün, kaydedilmeden atlanır.
    missing_message_id_skipped: int = 0
    #: kategori -> yeni kaydedilen sayısı (Cevaplar sekmesi/CLI özeti için).
    new_reply_categories: dict[str, int] = field(default_factory=dict)
    #: bounce tespit edilen başvuruların alan adları.
    bounced_domains: list[str] = field(default_factory=list)
    #: "bir daha yazmayın" cevabı yüzünden bu taramada engellenen alan adları (Adım 10.8b).
    blocked_domains: list[str] = field(default_factory=list)
    #: bounce oranı güvenlik freni bu taramada tetiklendiyse Türkçe sebebi (bkz.
    #: `src/sender/safety_brake.check_bounce_rate`); tetiklenmediyse veya `sending_paused` zaten
    #: açıksa `None`.
    bounce_rate_paused: Optional[str] = None


def _compute_since(session: Session) -> Optional[date]:
    """Bir sonraki `SEARCH SINCE` tarihini hesaplar.

    Hiç kontrol yapılmadıysa en eski GÖNDERİLMİŞ başvurudan başlanır (ondan öncesine bakmanın
    anlamı yok, henüz hiçbir cevap gelmiş olamaz); hiç gönderim yapılmamışsa `None` döner
    (taranacak bir şey yok, IMAP'a hiç bağlanılmaz).
    """
    last_check_iso = get_setting(session, KEY_INBOX_LAST_CHECK_AT)
    if last_check_iso:
        last_check = datetime.fromisoformat(last_check_iso)
        return (last_check - timedelta(days=LOOKBACK_DAYS)).date()

    statement = (
        select(Application.sent_at)
        .where(Application.sent_at.is_not(None))
        .order_by(Application.sent_at)
    )
    earliest = session.exec(statement).first()
    return earliest.date() if earliest else None


def _already_recorded(session: Session, message_id: str) -> bool:
    statement = select(EmailReply).where(EmailReply.message_id == message_id)
    return session.exec(statement).first() is not None


def _record_reply(
    session: Session,
    application: Application,
    header: EmailHeader,
    body: Optional[EmailBody],
    matched: MatchedReply,
    classified: ClassifiedReply,
) -> EmailReply:
    """`EmailReply`yi ekler ve bounce ise `Application.bounced_at`i doldurur.

    `received_at`/`updated_at` mailin KENDİ `Date` başlığından alınır (ayrıştırılamadıysa
    işlendiği ana düşer); ikisi oluşturmada bilerek AYNI değeri taşır (bkz. `EmailReply.
    updated_at` docstring'i, `src/core/models.py`).
    """
    received_at = header.date or utcnow()
    reply = EmailReply(
        application_id=matched.application_id,
        from_addr=header.from_addr,
        subject=header.subject,
        received_at=received_at,
        category=classified.category,
        classified_by=classified.classified_by,
        message_id=header.message_id,
        in_reply_to=header.in_reply_to,
        body_preview=body.text[:1000] if body else None,
        matched_by=matched.matched_by,
        auto_category=classified.category,
        updated_at=received_at,
    )
    session.add(reply)

    # Yalnızca İLK bounce kaydedilir: "bu başvuru en az bir kez geri döndü" bilgisi yeterli,
    # sonraki bounce'lar (aynı adrese tekrar tekrar teslim denemesi) bu alanı değiştirmez.
    if classified.category == REPLY_BOUNCE and application.bounced_at is None:
        application.bounced_at = utcnow()
        session.add(application)

    session.flush()
    return reply


def check_inbox(session: Session, profile: MasterProfile) -> InboxCheckResult:
    """Gelen kutusunu bir kez tarar, eşleşen cevapları kaydeder. Başarıyla biterse `commit()` eder.

    Raises:
        ImapConnectionError: bağlantı/login/SEARCH/FETCH başarısız olursa (mesaj zaten
            kısaltılmış/şifresi maskelenmiş) — bu durumda HİÇBİR ŞEY commit edilmemiştir.
    """
    since = _compute_since(session)
    if since is None:
        logger.info("Henüz gönderilmiş başvuru yok, gelen kutusu taranmadı")
        return InboxCheckResult(
            error="Henüz gönderilmiş başvuru yok, taranacak bir şey yok",
            no_applications_sent=True,
        )

    identity = resolve_identity(session, profile)
    account = load_account(identity, connection=resolve_mail_connection(session))
    if account is None:
        return InboxCheckResult(
            error="Mail uygulama şifresi kayıtlı değil (python scripts/set_mail_password.py)"
        )

    result = InboxCheckResult()

    with imap_session(account) as conn:
        headers = fetch_headers(conn, since)
        result.headers_fetched = len(headers)

        candidate_headers = select_candidates(headers, session=session, identity=identity)
        result.candidates = len(candidate_headers)

        bodies = fetch_bodies(conn, [header.uid for header in candidate_headers])

    # IMAP bağlantısı burada zaten kapandı (LOGOUT edildi) — eşleştirme/sınıflandırma/kayıt
    # saf veritabanı işi, bağlantıyı gereksiz yere açık tutmaz (LLM çağrısı yavaş olabilir).
    for header in candidate_headers:
        body = bodies.get(header.uid)
        matched = match_reply(session, header, body, identity=identity)
        if matched is None:
            continue

        if not header.message_id:
            result.missing_message_id_skipped += 1
            logger.warning(
                f"Message-ID'si olmayan bir eşleşme atlandı (uid={header.uid}), kaydedilemedi"
            )
            continue

        if _already_recorded(session, header.message_id):
            result.duplicate_skipped += 1
            continue

        classified = classify(session, header, body, matched_by=matched.matched_by)
        application = session.get(Application, matched.application_id)
        _record_reply(session, application, header, body, matched, classified)

        result.new_replies += 1
        result.new_reply_categories[classified.category] = (
            result.new_reply_categories.get(classified.category, 0) + 1
        )
        if classified.category == REPLY_BOUNCE:
            result.bounced_domains.append(application.company.domain)
        if classified.category == REPLY_OPT_OUT:
            # Kalıcı engel + bekleyen başvuruyu reddet (Adım 10.8b). Aynı commit'te: cevap
            # kaydı ile engel ya birlikte yazılır ya hiç.
            block_company(session, application.company, BLOCK_REASON_OPT_OUT)
            result.blocked_domains.append(application.company.domain)
            logger.warning(f"{application.company.domain}: bir daha yazılmaması istendi, alan adı engellendi")

    result.bounce_rate_paused = check_bounce_rate(session)

    set_setting(session, KEY_INBOX_LAST_CHECK_AT, utcnow().isoformat())
    session.commit()
    logger.info(
        f"Gelen kutusu tarandı: {result.headers_fetched} başlık, {result.candidates} aday, "
        f"{result.new_replies} yeni cevap kaydedildi ({result.duplicate_skipped} zaten kayıtlıydı)"
    )
    return result
