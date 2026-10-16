"""IMAP üzerinden gelen kutusunu SALT OKUNUR okur (Gmail/Yandex/özel sunucu — Adım 10.7).

Salt okuma üç katmanda garanti edilir (PLAN.md Faz 7 notu):
* `select(MAILBOX, readonly=True)` sunucuya `EXAMINE` gönderir; `STORE`/`EXPUNGE` reddedilir.
* Her fetch `BODY.PEEK[...]` kullanır (düz `BODY[...]` `\\Seen` bayrağını sunucuda YAZARDI).
* Bu modülde `store`/`copy`/`move`/`expunge` çağrısı HİÇ YOK.

**İki aşamalı indirme:** `fetch_headers` yalnızca başlıkları çeker (gövde YOK); `fetch_bodies`
yalnızca çağıranın verdiği UID'lerin TAM gövdesini ayrıca çeker. Hangi mailin "aday" olduğuna
(eşleşme var mı, bounce mı) karar vermek bu modülün işi DEĞİL — burası saf IMAP mekaniği,
karar `src/inbox/reply_matcher.py`de (Adım 7.3) verilir. Ayrım bilinçli: gelen kutusu binlerce
mesaj içerebilir, her taramada hepsinin gövdesini indirmek hem yavaş hem gereksiz.

Kimlik bilgisi (Gmail adresi + uygulama şifresi) SMTP ile AYNI hesaptır; kasadan okuma tek
yerde kalsın diye burada tekrarlanmıyor, `src/sender/smtp_sender.resolve_identity`/
`load_account` çağıranın (Adım 7.5) işi.
"""
import email
import imaplib
import logging
import re
import ssl
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Iterator, Optional

from bs4 import BeautifulSoup

from src.sender.smtp_sender import HEADER_INTENDED_TO, LEGACY_HEADER_INTENDED_TO, MailAccount

logger = logging.getLogger(__name__)

IMAP_TIMEOUT = 30  # saniye
MAILBOX = "INBOX"
MAX_ERROR_LENGTH = 300
#: Gövdeden çıkarılan metin bu uzunlukta kesilir — bazı pazarlama/otomatik mailler dev HTML
#: gövdeleri olabilir, eşleştirme/sınıflandırma için bu kadarı zaten fazlasıyla yeterli.
MAX_BODY_CHARS = 20_000


class ImapConnectionError(RuntimeError):
    """Bağlantı, login veya klasör seçme başarısız oldu. Mesaj zaten kısaltılmış/maskelenmiş."""


@dataclass(frozen=True)
class EmailHeader:
    """`fetch_headers`in döndürdüğü tek bir mesaj — yalnızca başlık, gövde YOK."""

    uid: str
    message_id: Optional[str]
    from_addr: str
    subject: str
    date: Optional[datetime]  # naive UTC; başlık ayrıştırılamazsa None
    in_reply_to: Optional[str]
    references: list[str]
    #: Test modunda gönderilen mailin taşıdığı işaret başlığı (bkz. `smtp_sender.HEADER_INTENDED_TO`)
    #: — dolu olması bu mesajın kendi test gönderimimiz olduğunu, "cevap" olmadığını gösterir
    #: (`src/inbox/reply_matcher.py`, Adım 7.3).
    intended_to: Optional[str]
    #: RFC 3834 otomatik cevap başlığı (ham değer, örn. "auto-replied"/"auto-generated"; sunucu
    #: hiç eklemediyse `None`). Yorumlama `src/inbox/classifier.py`nin işi — "no" RFC'ye göre
    #: VARSAYILAN değerdir ve otomatik cevap OLMADIĞINI gösterir.
    auto_submitted: Optional[str]
    #: Eski (RFC öncesi) otomatik yanıtlayıcı kuralı: değeri "auto_reply" ise otomatik cevap.
    precedence: Optional[str]
    #: Yahoo/bazı istemcilerin kullandığı otomatik yanıtlayıcı başlığı (ham değer).
    x_autoreply: Optional[str]
    #: Bazı sunucuların (ör. Exchange) kullandığı otomatik yanıtlayıcı başlığı (ham değer).
    x_autorespond: Optional[str]


@dataclass(frozen=True)
class EmailBody:
    """`fetch_bodies`in döndürdüğü tek bir mesajın gövdesi (düz metin)."""

    uid: str
    text: str
    #: Bounce (DSN) gövdesine gömülü orijinal mesajın Message-ID'si, varsa (`src/inbox/
    #: reply_matcher.py`nin "bounce" eşleştirmesi için, bkz. `_find_embedded_message_id`).
    original_message_id: Optional[str] = None


def _short_error(text: str, password: Optional[str] = None) -> str:
    """Hata metnini kısaltır ve şifreyi maskeler (CLAUDE.md kural 6, `smtp_sender._short_error` ile aynı desen)."""
    if password:
        text = text.replace(password, "***")
    return text[:MAX_ERROR_LENGTH]


@contextmanager
def imap_session(account: MailAccount) -> Iterator[imaplib.IMAP4_SSL]:
    """Bağlanır, login olur, `INBOX`ı SALT OKUNUR açar; çıkışta her koşulda `LOGOUT` yapar.

    Başarısızlıkta `ImapConnectionError` fırlatır (şifre maskelenmiş, kısaltılmış mesajla).
    """
    try:
        # `ssl_context` AÇIKÇA verilir: verilmezse imaplib `ssl._create_stdlib_context()` kullanır,
        # o da sertifikayı DOĞRULAMAZ (araya giren biri uygulama şifresini çalabilir).
        conn = imaplib.IMAP4_SSL(
            account.connection.imap_host,
            account.connection.imap_port,
            ssl_context=ssl.create_default_context(),
            timeout=IMAP_TIMEOUT,
        )
    except OSError as error:
        raise ImapConnectionError(_short_error(str(error))) from error

    try:
        conn.login(account.identity.email, account.password)
        status, _ = conn.select(MAILBOX, readonly=True)
        if status != "OK":
            raise ImapConnectionError(f"{MAILBOX} klasörü salt okunur açılamadı")
        yield conn
    except (imaplib.IMAP4.error, OSError) as error:
        raise ImapConnectionError(_short_error(str(error), account.password)) from error
    finally:
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass  # oturum zaten koptuysa logout'un başarısız olması önemsiz


def check_connection(account: MailAccount) -> Optional[str]:
    """Yalnızca bağlanıp `INBOX`ı salt okunur açar, hiçbir mail ÇEKMEZ. Başarılıysa `None`.

    `smtp_sender.check_login` ile aynı amaç: Ayarlar sekmesindeki bir "bağlantıyı test et"
    butonu (ileri bir adım) bunu kullanabilir.
    """
    try:
        with imap_session(account):
            pass
    except ImapConnectionError as error:
        return str(error)
    return None


_UID_PATTERN = re.compile(rb"UID (\d+)")


def _extract_uid(fetch_metadata: bytes) -> Optional[str]:
    match = _UID_PATTERN.search(fetch_metadata)
    return match.group(1).decode() if match else None


def _decode_header_value(value: Optional[str]) -> str:
    """RFC 2047 kodlu başlıkları (`=?UTF-8?B?...?=`) okunabilir metne çevirir."""
    if not value:
        return ""
    decoded_parts = []
    for text_part, charset in decode_header(value):
        if isinstance(text_part, bytes):
            decoded_parts.append(text_part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded_parts.append(text_part)
    return "".join(decoded_parts)


def _clean_id(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = value.strip()
    return cleaned or None


def _parse_references(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return re.findall(r"<[^>]+>", value)


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _parse_header(uid: str, message: Message) -> EmailHeader:
    return EmailHeader(
        uid=uid,
        message_id=_clean_id(message.get("Message-ID")),
        from_addr=_decode_header_value(message.get("From", "")),
        subject=_decode_header_value(message.get("Subject", "")),
        date=_parse_date(message.get("Date")),
        in_reply_to=_clean_id(message.get("In-Reply-To")),
        references=_parse_references(message.get("References")),
        intended_to=_clean_id(message.get(HEADER_INTENDED_TO) or message.get(LEGACY_HEADER_INTENDED_TO)),
        auto_submitted=_clean_id(message.get("Auto-Submitted")),
        precedence=_clean_id(message.get("Precedence")),
        x_autoreply=_clean_id(message.get("X-Autoreply")),
        x_autorespond=_clean_id(message.get("X-Autorespond")),
    )


def fetch_headers(conn: imaplib.IMAP4_SSL, since: date) -> list[EmailHeader]:
    """`since` tarihinden (dahil) bu yana gelen TÜM mesajların başlıklarını çeker (gövde YOK).

    IMAP `SINCE` gün duyarlıdır (saat bilgisi yok); güvenlik payı bırakmak çağıranın işi (bkz.
    PLAN.md Faz 7.1 notu — son kontrolden 2 gün öncesi kullanılır). UID kullanılır, sıra
    numarası DEĞİL: sıra numarası oturumdan oturuma değişebilir, UID mesaj silinene kadar sabit
    kalır ve `fetch_bodies`e aynı mesajı işaret etmek için güvenle taşınabilir.
    """
    status, data = conn.uid("SEARCH", None, "SINCE", since.strftime("%d-%b-%Y"))
    if status != "OK":
        raise ImapConnectionError("IMAP SEARCH başarısız oldu")

    uids = data[0].split() if data and data[0] else []
    if not uids:
        return []

    uid_set = b",".join(uids).decode()
    status, fetch_data = conn.uid("FETCH", uid_set, "(UID BODY.PEEK[HEADER])")
    if status != "OK":
        raise ImapConnectionError("IMAP FETCH (başlık) başarısız oldu")

    headers: list[EmailHeader] = []
    for part in fetch_data:
        if not isinstance(part, tuple):
            continue
        uid = _extract_uid(part[0])
        if uid is None:
            continue
        message = email.message_from_bytes(part[1])
        headers.append(_parse_header(uid, message))
    return headers


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:  # bilinmeyen/yazım hatalı charset adı
        return payload.decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)


def _extract_text(message: Message) -> str:
    """Düz metin gövdeyi tercih eder; yalnızca HTML varsa etiketlerden arındırıp döner."""
    plain: Optional[str] = None
    html: Optional[str] = None

    if message.is_multipart():
        for part in message.walk():
            if part.is_multipart():
                continue
            if "attachment" in str(part.get("Content-Disposition", "")):
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain" and plain is None:
                plain = _decode_part(part)
            elif content_type == "text/html" and html is None:
                html = _decode_part(part)
    elif message.get_content_type() == "text/html":
        html = _decode_part(message)
    else:
        plain = _decode_part(message)

    text = plain if plain is not None else _strip_html(html) if html is not None else ""
    return text[:MAX_BODY_CHARS]


def _find_embedded_message_id(message: Message) -> Optional[str]:
    """Bounce (DSN) gövdesine gömülü orijinal mesajın Message-ID'sini bulur, yoksa `None`.

    RFC 3464 gereği bir teslim edilemedi bildirimi genelde orijinal mesajı ya tam olarak
    (`message/rfc822` — gömülü `Message` nesnesinin başlıkları doğrudan okunur) ya da yalnızca
    başlıklarını düz metin olarak (`text/rfc822-headers`) ekler; ikisi de denenir. Bulunamazsa
    eşleştirme (`src/inbox/reply_matcher.py`) alan adı/thread'e düşer.
    """
    for part in message.walk():
        content_type = part.get_content_type()
        if content_type == "message/rfc822":
            payload = part.get_payload()
            if isinstance(payload, list) and payload:
                message_id = _clean_id(payload[0].get("Message-ID"))
                if message_id:
                    return message_id
        elif content_type == "text/rfc822-headers":
            match = re.search(
                r"^Message-ID:\s*(<[^>]+>)", _decode_part(part), re.MULTILINE | re.IGNORECASE
            )
            if match:
                return match.group(1)
    return None


def fetch_bodies(conn: imaplib.IMAP4_SSL, uids: list[str]) -> dict[str, EmailBody]:
    """Yalnızca verilen UID'lerin TAM gövdesini çeker — adaylık kararı çağıranın işi.

    `fetch_headers`ten bilinçli olarak AYRI bir adım (bkz. modül docstring'i): aday olmayan
    mesajların gövdesini indirmemek için.
    """
    if not uids:
        return {}

    uid_set = ",".join(uids)
    status, fetch_data = conn.uid("FETCH", uid_set, "(UID BODY.PEEK[])")
    if status != "OK":
        raise ImapConnectionError("IMAP FETCH (gövde) başarısız oldu")

    bodies: dict[str, EmailBody] = {}
    for part in fetch_data:
        if not isinstance(part, tuple):
            continue
        uid = _extract_uid(part[0])
        if uid is None:
            continue
        message = email.message_from_bytes(part[1])
        bodies[uid] = EmailBody(
            uid=uid,
            text=_extract_text(message),
            original_message_id=_find_embedded_message_id(message),
        )
    return bodies
