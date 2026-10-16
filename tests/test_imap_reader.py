"""`src/inbox/imap_reader.py` testleri — gerçek bir IMAP sunucusuna hiç bağlanmaz.

`fetch_headers`/`fetch_bodies` bir `conn` PARAMETRESİ alır (kendi bağlantısını kurmaz), bu
yüzden `imaplib.IMAP4_SSL`in `.uid()` metodunu taklit eden sahte bir nesneyle test edilebilir
— `src/sender/smtp_sender.py`deki `Transport` enjeksiyonuyla aynı kalıp.
"""
import imaplib
from datetime import date

import pytest

from src.inbox import imap_reader
from src.inbox.imap_reader import (
    EmailHeader,
    ImapConnectionError,
    _decode_header_value,
    _extract_text,
    _extract_uid,
    _find_embedded_message_id,
    _parse_date,
    _parse_references,
    fetch_bodies,
    fetch_headers,
)
from src.sender.mail_connection import SECURITY_SSL, MailConnection
from src.sender.smtp_sender import HEADER_INTENDED_TO, LEGACY_HEADER_INTENDED_TO, MailAccount, MailIdentity


HEADER_BYTES = (
    b"From: =?UTF-8?B?xLBLIC0gRmlybWE=?= <ik@firma.com>\r\n"
    b"Subject: =?UTF-8?B?QmHFn3Z1cnVudXogQWzEsW5kxLE=?=\r\n"
    b"Message-ID: <reply-1@firma.com>\r\n"
    b"In-Reply-To: <app-1@bizim.com>\r\n"
    b"References: <app-1@bizim.com> <older@bizim.com>\r\n"
    b"Date: Mon, 28 Sep 2026 10:00:00 +0300\r\n"
    b"\r\n"
)


class FakeImapConn:
    """`.uid(command, *args)` çağrılarını sabit yanıtlarla karşılayan sahte bağlantı."""

    def __init__(self, responses: dict[str, tuple]):
        self.responses = responses
        self.calls: list[tuple] = []

    def uid(self, command, *args):
        self.calls.append((command, *args))
        key = command.upper()
        if key not in self.responses:
            raise AssertionError(f"beklenmeyen IMAP komutu: {command} {args}")
        return self.responses[key]


# ---- saf yardımcı fonksiyonlar --------------------------------------------------------


def test_extract_uid_parses_uid_from_fetch_metadata():
    meta = b"12 (UID 45 BODY[HEADER] {342}"
    assert _extract_uid(meta) == "45"


def test_extract_uid_returns_none_when_missing():
    assert _extract_uid(b"12 (BODY[HEADER] {342}") is None


def test_decode_header_value_handles_turkish_rfc2047():
    decoded = _decode_header_value("=?UTF-8?B?QmHFn3Z1cnVudXogQWzEsW5kxLE=?=")
    assert decoded == "Başvurunuz Alındı"


def test_decode_header_value_empty_input():
    assert _decode_header_value(None) == ""
    assert _decode_header_value("") == ""


def test_parse_references_extracts_all_message_ids():
    assert _parse_references("<a@x.com> <b@y.com>") == ["<a@x.com>", "<b@y.com>"]


def test_parse_references_empty_when_missing():
    assert _parse_references(None) == []


def test_parse_date_converts_to_naive_utc():
    parsed = _parse_date("Mon, 28 Sep 2026 10:00:00 +0300")
    assert parsed is not None
    assert parsed.tzinfo is None
    assert parsed.hour == 7  # +03:00 -> UTC


def test_parse_date_returns_none_for_garbage():
    assert _parse_date("bu bir tarih değil") is None
    assert _parse_date(None) is None


def test_extract_text_prefers_plain_over_html():
    import email

    message = email.message_from_bytes(
        b"Content-Type: multipart/alternative; boundary=BOUND\r\n\r\n"
        b"--BOUND\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nDuz metin\r\n"
        b"--BOUND\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>Html metin</p>\r\n"
        b"--BOUND--\r\n"
    )
    assert _extract_text(message) == "Duz metin"


def test_extract_text_falls_back_to_stripped_html():
    import email

    message = email.message_from_bytes(
        b"Content-Type: text/html; charset=utf-8\r\n\r\n<p>Merhaba <b>Dunya</b></p>\r\n"
    )
    assert _extract_text(message) == "Merhaba Dunya"


def test_find_embedded_message_id_from_message_rfc822_part():
    import email

    message = email.message_from_bytes(
        b"Content-Type: multipart/report; report-type=delivery-status; boundary=BOUND\r\n\r\n"
        b"--BOUND\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nTeslim edilemedi.\r\n"
        b"--BOUND\r\nContent-Type: message/rfc822\r\n\r\n"
        b"From: ben@gmail.com\r\nTo: ik@firma.com\r\nMessage-ID: <app-1@bizim.com>\r\n"
        b"Subject: Staj basvurusu\r\n\r\nMerhaba\r\n"
        b"--BOUND--\r\n"
    )
    assert _find_embedded_message_id(message) == "<app-1@bizim.com>"


def test_find_embedded_message_id_from_text_rfc822_headers_part():
    import email

    message = email.message_from_bytes(
        b"Content-Type: multipart/report; report-type=delivery-status; boundary=BOUND\r\n\r\n"
        b"--BOUND\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nTeslim edilemedi.\r\n"
        b"--BOUND\r\nContent-Type: text/rfc822-headers\r\n\r\n"
        b"From: ben@gmail.com\r\nMessage-ID: <app-2@bizim.com>\r\n"
        b"--BOUND--\r\n"
    )
    assert _find_embedded_message_id(message) == "<app-2@bizim.com>"


def test_find_embedded_message_id_returns_none_when_absent():
    import email

    message = email.message_from_bytes(
        b"Content-Type: text/plain; charset=utf-8\r\n\r\nSadece duz metin, DSN degil.\r\n"
    )
    assert _find_embedded_message_id(message) is None


def test_extract_text_skips_attachments():
    import email

    message = email.message_from_bytes(
        b"Content-Type: multipart/mixed; boundary=BOUND\r\n\r\n"
        b"--BOUND\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nGovde\r\n"
        b"--BOUND\r\nContent-Type: application/pdf\r\n"
        b'Content-Disposition: attachment; filename="cv.pdf"\r\n\r\n%PDF-fake\r\n'
        b"--BOUND--\r\n"
    )
    assert _extract_text(message) == "Govde"


# ---- fetch_headers / fetch_bodies (sahte conn ile) -------------------------------------


def test_fetch_headers_parses_message():
    conn = FakeImapConn(
        {
            "SEARCH": ("OK", [b"45"]),
            "FETCH": ("OK", [(b"1 (UID 45 BODY[HEADER] {1}", HEADER_BYTES), b")"]),
        }
    )
    headers = fetch_headers(conn, since=date(2026, 9, 1))

    assert len(headers) == 1
    header = headers[0]
    assert header.uid == "45"
    assert header.message_id == "<reply-1@firma.com>"
    assert header.in_reply_to == "<app-1@bizim.com>"
    assert header.references == ["<app-1@bizim.com>", "<older@bizim.com>"]
    assert "Başvurunuz Alındı" in header.subject
    assert header.date is not None and header.date.tzinfo is None
    assert header.intended_to is None
    assert header.auto_submitted is None
    assert header.precedence is None
    assert header.x_autoreply is None
    assert header.x_autorespond is None

    assert conn.calls[0] == ("SEARCH", None, "SINCE", "01-Sep-2026")
    assert conn.calls[1] == ("FETCH", "45", "(UID BODY.PEEK[HEADER])")


def test_fetch_headers_parses_auto_reply_headers():
    header_bytes = HEADER_BYTES.replace(
        b"\r\n\r\n",
        b"\r\nAuto-Submitted: auto-replied\r\nPrecedence: auto_reply\r\n"
        b"X-Autoreply: yes\r\nX-Autorespond: 1\r\n\r\n",
    )
    conn = FakeImapConn(
        {
            "SEARCH": ("OK", [b"45"]),
            "FETCH": ("OK", [(b"1 (UID 45 BODY[HEADER] {1}", header_bytes), b")"]),
        }
    )
    header = fetch_headers(conn, since=date(2026, 9, 1))[0]

    assert header.auto_submitted == "auto-replied"
    assert header.precedence == "auto_reply"
    assert header.x_autoreply == "yes"
    assert header.x_autorespond == "1"


def test_fetch_headers_parses_intended_to_header():
    header_bytes = HEADER_BYTES.replace(
        b"\r\n\r\n", f"\r\n{HEADER_INTENDED_TO}: ik@firma.com\r\n\r\n".encode()
    )
    conn = FakeImapConn(
        {
            "SEARCH": ("OK", [b"45"]),
            "FETCH": ("OK", [(b"1 (UID 45 BODY[HEADER] {1}", header_bytes), b")"]),
        }
    )
    headers = fetch_headers(conn, since=date(2026, 9, 1))
    assert headers[0].intended_to == "ik@firma.com"


def test_fetch_headers_recognizes_the_old_app_name_intended_to_header():
    """Ad değişikliğinden önce gönderilen test kopyaları (`X-CV-Autopilot-Intended-To`) da tanınır."""
    header_bytes = HEADER_BYTES.replace(
        b"\r\n\r\n", f"\r\n{LEGACY_HEADER_INTENDED_TO}: ik@firma.com\r\n\r\n".encode()
    )
    conn = FakeImapConn(
        {
            "SEARCH": ("OK", [b"45"]),
            "FETCH": ("OK", [(b"1 (UID 45 BODY[HEADER] {1}", header_bytes), b")"]),
        }
    )
    assert LEGACY_HEADER_INTENDED_TO == "X-CV-Autopilot-Intended-To"
    assert fetch_headers(conn, since=date(2026, 9, 1))[0].intended_to == "ik@firma.com"


def test_fetch_headers_returns_empty_when_no_uids():
    conn = FakeImapConn({"SEARCH": ("OK", [b""])})
    assert fetch_headers(conn, since=date(2026, 9, 1)) == []
    assert len(conn.calls) == 1  # FETCH hiç çağrılmadı


def test_fetch_headers_raises_on_search_failure():
    conn = FakeImapConn({"SEARCH": ("NO", [])})
    with pytest.raises(ImapConnectionError):
        fetch_headers(conn, since=date(2026, 9, 1))


def test_fetch_bodies_returns_empty_without_calling_conn():
    conn = FakeImapConn({})
    assert fetch_bodies(conn, []) == {}
    assert conn.calls == []


def test_fetch_bodies_parses_plain_text():
    raw = (
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"Merhaba, basvurunuz alinmistir.\r\n"
    )
    conn = FakeImapConn(
        {"FETCH": ("OK", [(b"1 (UID 45 BODY[] {1}", raw), b")"])}
    )
    bodies = fetch_bodies(conn, ["45"])

    assert set(bodies) == {"45"}
    assert "basvurunuz alinmistir" in bodies["45"].text.lower()
    assert bodies["45"].original_message_id is None
    assert conn.calls[0] == ("FETCH", "45", "(UID BODY.PEEK[])")


def test_fetch_bodies_extracts_embedded_original_message_id():
    raw = (
        b"Content-Type: multipart/report; report-type=delivery-status; boundary=BOUND\r\n\r\n"
        b"--BOUND\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nTeslim edilemedi.\r\n"
        b"--BOUND\r\nContent-Type: message/rfc822\r\n\r\n"
        b"Message-ID: <app-1@bizim.com>\r\n\r\nMerhaba\r\n"
        b"--BOUND--\r\n"
    )
    conn = FakeImapConn({"FETCH": ("OK", [(b"1 (UID 45 BODY[] {1}", raw), b")"])})
    bodies = fetch_bodies(conn, ["45"])
    assert bodies["45"].original_message_id == "<app-1@bizim.com>"


def test_fetch_bodies_raises_on_fetch_failure():
    conn = FakeImapConn({"FETCH": ("NO", [])})
    with pytest.raises(ImapConnectionError):
        fetch_bodies(conn, ["45"])


# ---- imap_session / check_connection (imaplib.IMAP4_SSL taklit edilir) -----------------


class FakeImap4Ssl:
    """`imaplib.IMAP4_SSL`in `login`/`select`/`logout` yüzeyini taklit eder."""

    fail_login = False
    instances: list = []

    def __init__(self, host, port, ssl_context=None, timeout=None):
        self.host = host
        self.port = port
        self.ssl_context = ssl_context
        FakeImap4Ssl.instances.append(self)

    def login(self, user, password):
        if self.fail_login:
            raise imaplib.IMAP4.error("kimlik doğrulama başarısız")

    def select(self, mailbox, readonly=False):
        assert readonly is True  # salt okuma garantisi
        return "OK", [b"1"]

    def logout(self):
        return "BYE", [b"logging out"]


def _account() -> MailAccount:
    return MailAccount(identity=MailIdentity(email="ben@gmail.com", name="Ben"), password="sifre123")


def test_check_connection_succeeds(monkeypatch):
    monkeypatch.setattr(imap_reader.imaplib, "IMAP4_SSL", FakeImap4Ssl)
    assert imap_reader.check_connection(_account()) is None


def test_imap_session_verifies_server_certificate(monkeypatch):
    """`ssl_context` verilmezse imaplib sertifikayı doğrulamaz; bağlam açıkça ve doğrulamalı olmalı."""
    import ssl

    FakeImap4Ssl.instances = []
    monkeypatch.setattr(imap_reader.imaplib, "IMAP4_SSL", FakeImap4Ssl)

    assert imap_reader.check_connection(_account()) is None

    context = FakeImap4Ssl.instances[0].ssl_context
    assert context is not None
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_imap_session_uses_accounts_connection_host_and_port(monkeypatch):
    """Adım 10.7f: sabit bir Gmail host'u DEĞİL, `account.connection`daki host/port kullanılmalı
    (ör. Yandex seçiliyken `imap.yandex.com:993`'e bağlanmalı)."""
    FakeImap4Ssl.instances = []
    monkeypatch.setattr(imap_reader.imaplib, "IMAP4_SSL", FakeImap4Ssl)

    yandex_connection = MailConnection(
        provider="yandex",
        smtp_host="smtp.yandex.com",
        smtp_port=465,
        smtp_security=SECURITY_SSL,
        imap_host="imap.yandex.com",
        imap_port=993,
    )
    account = MailAccount(
        identity=MailIdentity(email="ben@yandex.com", name="Ben"),
        password="sifre123",
        connection=yandex_connection,
    )

    assert imap_reader.check_connection(account) is None
    instance = FakeImap4Ssl.instances[0]
    assert instance.host == "imap.yandex.com"
    assert instance.port == 993


def test_check_connection_reports_login_failure(monkeypatch):
    class FailingImap(FakeImap4Ssl):
        fail_login = True

    monkeypatch.setattr(imap_reader.imaplib, "IMAP4_SSL", FailingImap)
    error = imap_reader.check_connection(_account())
    assert error is not None
    assert "kimlik doğrulama" in error


def test_check_connection_masks_password_in_error(monkeypatch):
    class FailingImap(FakeImap4Ssl):
        def login(self, user, password):
            raise imaplib.IMAP4.error(f"login failed for {password}")

    monkeypatch.setattr(imap_reader.imaplib, "IMAP4_SSL", FailingImap)
    error = imap_reader.check_connection(_account())
    assert error is not None
    assert "sifre123" not in error
    assert "***" in error
