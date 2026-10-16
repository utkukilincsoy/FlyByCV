"""`src/inbox/service.py` testleri — gerçek IMAP'a hiç bağlanmaz.

`check_inbox()`nun sorumluluğu `fetch_headers`/`fetch_bodies`/`select_candidates`/
`match_reply`/`classify`'ı BİRLEŞTİRMEK; IMAP baytlarının ayrıştırılması zaten
`test_imap_reader.py`de test edildi. Bu yüzden burada `imap_session`/`fetch_headers`/
`fetch_bodies` sahte (`monkeypatch`) verilir, geri kalan gerçek fonksiyonlar (`select_candidates`,
`match_reply`, `classify`) gerçek bir veritabanı oturumuyla çalışır — asıl test edilen
"birleştirme" mantığıdır.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlmodel import select

from src.core.models import (
    CLASSIFIED_BY_KEYWORD,
    CLASSIFIED_BY_RULE,
    REPLY_BOUNCE,
    REPLY_REJECTED,
    STATUS_SENT,
    EmailReply,
)
from src.core.settings import (
    KEY_BOUNCE_MIN_SAMPLE,
    KEY_BOUNCE_PAUSE_RATE,
    KEY_INBOX_LAST_CHECK_AT,
    KEY_PAUSE_REASON,
    KEY_SENDING_PAUSED,
    get_setting,
    set_setting,
)
from src.inbox import service
from src.inbox.imap_reader import EmailBody, EmailHeader
from src.sender.smtp_sender import MailAccount
from tests.conftest import build_application_row

SENT_AT = datetime(2026, 9, 20, 10, 0, 0)


def _header(
    *,
    uid="1",
    message_id="<reply-1@firma.com>",
    from_addr="İK <ik@firma.com>",
    subject="Re: Başvurunuz",
    date=None,
    in_reply_to=None,
    references=None,
    intended_to=None,
) -> EmailHeader:
    return EmailHeader(
        uid=uid,
        message_id=message_id,
        from_addr=from_addr,
        subject=subject,
        date=date,
        in_reply_to=in_reply_to,
        references=references or [],
        intended_to=intended_to,
        auto_submitted=None,
        precedence=None,
        x_autoreply=None,
        x_autorespond=None,
    )


def _body(uid, text, *, original_message_id=None) -> EmailBody:
    return EmailBody(uid=uid, text=text, original_message_id=original_message_id)


@contextmanager
def _fake_imap_session(account):
    yield "FAKE_CONN"


def _patch_imap(monkeypatch, *, headers, bodies):
    """`imap_session`/`fetch_headers`/`fetch_bodies`'i sahtesiyle değiştirir; `load_account`
    gerçek kasaya (keyring) hiç dokunmasın diye sabit bir hesap döner."""
    monkeypatch.setattr(service, "imap_session", _fake_imap_session)
    monkeypatch.setattr(service, "fetch_headers", lambda conn, since: headers)
    monkeypatch.setattr(service, "fetch_bodies", lambda conn, uids: bodies)
    monkeypatch.setattr(
        service, "load_account", lambda identity, connection=None: MailAccount(identity=identity, password="x")
    )


def _sent_application(session, pdf_path, *, domain="firma.com", message_id="<app-1@bizim.com>"):
    return build_application_row(
        session,
        domain=domain,
        cv_pdf_path=pdf_path,
        status=STATUS_SENT,
        sent_at=SENT_AT,
        message_id=message_id,
    )


# ---- since hesaplama (`_compute_since`) yoluyla erken çıkış -------------------------------


def test_check_inbox_returns_error_when_nothing_ever_sent(session, sample_profile, monkeypatch):
    """Hiç gönderim yapılmamışsa IMAP'a hiç bağlanılmaz — `load_account` çağrılmamalı."""
    def _fail_if_called(identity):
        raise AssertionError("hiç gönderim yokken load_account çağrılmamalı")

    monkeypatch.setattr(service, "load_account", _fail_if_called)

    result = service.check_inbox(session, sample_profile)

    assert result.error is not None
    assert result.headers_fetched == 0


def test_check_inbox_returns_error_when_no_account_configured(session, sample_profile, pdf_path, monkeypatch):
    _sent_application(session, pdf_path)
    session.commit()

    monkeypatch.setattr(service, "load_account", lambda identity, connection=None: None)

    result = service.check_inbox(session, sample_profile)

    assert result.error is not None
    assert "şifre" in result.error


# ---- uçtan uca: eşleşme + sınıflandırma + kayıt -------------------------------------------


def test_check_inbox_records_new_reply_via_keyword_provider(session, sample_profile, pdf_path, monkeypatch):
    application, company = _sent_application(session, pdf_path)
    session.commit()

    header = _header(subject="Başvurunuz Hakkında", date=datetime(2026, 9, 21, 9, 0, 0))
    body = _body(header.uid, "Maalesef başvurunuz olumsuz sonuçlanmıştır.")
    _patch_imap(monkeypatch, headers=[header], bodies={header.uid: body})

    result = service.check_inbox(session, sample_profile)

    assert result.error is None
    assert result.headers_fetched == 1
    assert result.candidates == 1
    assert result.new_replies == 1
    assert result.new_reply_categories == {REPLY_REJECTED: 1}

    stored = session.exec(select(EmailReply)).one()
    assert stored.category == REPLY_REJECTED
    assert stored.classified_by == CLASSIFIED_BY_KEYWORD
    assert stored.application_id == application.id
    assert stored.message_id == header.message_id
    assert stored.received_at == header.date
    assert stored.updated_at == header.date
    assert stored.body_preview.startswith("Maalesef")


def test_check_inbox_updates_last_check_setting(session, sample_profile, pdf_path, monkeypatch):
    _sent_application(session, pdf_path)
    session.commit()
    assert get_setting(session, KEY_INBOX_LAST_CHECK_AT) is None

    header = _header(subject="Merhaba")
    body = _body(header.uid, "Başvurunuzu aldık.")
    _patch_imap(monkeypatch, headers=[header], bodies={header.uid: body})

    service.check_inbox(session, sample_profile)

    assert get_setting(session, KEY_INBOX_LAST_CHECK_AT) is not None


def test_check_inbox_skips_duplicate_message_id(session, sample_profile, pdf_path, monkeypatch):
    """Tarama pencereleri örtüşür (`LOOKBACK_DAYS`); zaten kaydedilmiş bir mail tekrar
    eklenmemeli."""
    application, _ = _sent_application(session, pdf_path)
    session.add(
        EmailReply(
            application_id=application.id,
            from_addr="ik@firma.com",
            category=REPLY_REJECTED,
            classified_by=CLASSIFIED_BY_KEYWORD,
            message_id="<reply-1@firma.com>",
        )
    )
    session.commit()

    header = _header(message_id="<reply-1@firma.com>")
    body = _body(header.uid, "Maalesef uygun bulunmadı.")
    _patch_imap(monkeypatch, headers=[header], bodies={header.uid: body})

    result = service.check_inbox(session, sample_profile)

    assert result.new_replies == 0
    assert result.duplicate_skipped == 1
    assert len(session.exec(select(EmailReply)).all()) == 1


def test_check_inbox_skips_match_without_message_id(session, sample_profile, pdf_path, monkeypatch):
    application, _ = _sent_application(session, pdf_path)
    session.commit()

    header = _header(message_id=None)
    body = _body(header.uid, "Görüşmeye davetlisiniz.")
    _patch_imap(monkeypatch, headers=[header], bodies={header.uid: body})

    result = service.check_inbox(session, sample_profile)

    assert result.new_replies == 0
    assert result.missing_message_id_skipped == 1
    assert session.exec(select(EmailReply)).all() == []


def test_check_inbox_marks_bounce_and_sets_bounced_at(session, sample_profile, pdf_path, monkeypatch):
    application, company = _sent_application(session, pdf_path, message_id="<app-1@bizim.com>")
    session.commit()

    header = _header(
        uid="9",
        message_id="<dsn-1@mail.example>",
        from_addr="Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
        subject="Delivery Status Notification (Failure)",
    )
    body = _body(header.uid, "Teslim edilemedi.", original_message_id="<app-1@bizim.com>")
    _patch_imap(monkeypatch, headers=[header], bodies={header.uid: body})

    result = service.check_inbox(session, sample_profile)

    assert result.new_reply_categories == {REPLY_BOUNCE: 1}
    assert result.bounced_domains == [company.domain]

    session.refresh(application)
    assert application.bounced_at is not None
    assert application.status == STATUS_SENT  # kural 4: status/sent_at asla değişmez

    stored = session.exec(select(EmailReply)).one()
    assert stored.classified_by == CLASSIFIED_BY_RULE


def test_check_inbox_pauses_sending_when_bounce_rate_exceeds_threshold(
    session, sample_profile, pdf_path, monkeypatch
):
    """Yeni bounce, örneklemi eşiğin üzerine çıkarırsa `check_inbox` gönderimi durdurmalı."""
    set_setting(session, KEY_BOUNCE_MIN_SAMPLE, 4)
    set_setting(session, KEY_BOUNCE_PAUSE_RATE, 0.10)
    for index in range(3):
        build_application_row(
            session, domain=f"ok{index}.com", cv_pdf_path=pdf_path, status=STATUS_SENT,
            sent_at=SENT_AT, message_id=f"<app-ok{index}@bizim.com>",
        )
    application, company = _sent_application(session, pdf_path, message_id="<app-1@bizim.com>")
    session.commit()

    header = _header(
        uid="9",
        message_id="<dsn-1@mail.example>",
        from_addr="Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
        subject="Delivery Status Notification (Failure)",
    )
    body = _body(header.uid, "Teslim edilemedi.", original_message_id="<app-1@bizim.com>")
    _patch_imap(monkeypatch, headers=[header], bodies={header.uid: body})

    result = service.check_inbox(session, sample_profile)

    assert result.bounce_rate_paused is not None
    assert "bounce oranı" in result.bounce_rate_paused
    assert get_setting(session, KEY_SENDING_PAUSED) is True
    assert get_setting(session, KEY_PAUSE_REASON) == result.bounce_rate_paused


def test_check_inbox_does_not_overwrite_first_bounced_at(session, sample_profile, pdf_path, monkeypatch):
    """İkinci bir bounce, ilk `bounced_at`i EZMEMELİ."""
    application, company = _sent_application(session, pdf_path, message_id="<app-1@bizim.com>")
    first_bounce = datetime(2026, 9, 21, 8, 0, 0)
    application.bounced_at = first_bounce
    session.add(application)
    session.commit()

    header = _header(
        uid="9",
        message_id="<dsn-2@mail.example>",
        from_addr="mailer-daemon@googlemail.com",
        subject="Undelivered Mail Returned to Sender",
    )
    body = _body(header.uid, "Teslim edilemedi.", original_message_id="<app-1@bizim.com>")
    _patch_imap(monkeypatch, headers=[header], bodies={header.uid: body})

    service.check_inbox(session, sample_profile)

    session.refresh(application)
    assert application.bounced_at == first_bounce


def test_check_inbox_ignores_unmatched_headers(session, sample_profile, pdf_path, monkeypatch):
    _sent_application(session, pdf_path)
    session.commit()

    header = _header(from_addr="birisi <birisi@alakasiz.com>", message_id="<x@alakasiz.com>")
    _patch_imap(monkeypatch, headers=[header], bodies={})

    result = service.check_inbox(session, sample_profile)

    assert result.headers_fetched == 1
    assert result.candidates == 0
    assert result.new_replies == 0


# ---- _compute_since --------------------------------------------------------------------


def test_compute_since_uses_earliest_sent_application_when_never_checked(session, pdf_path):
    _sent_application(session, pdf_path)
    session.commit()

    since = service._compute_since(session)

    assert since == SENT_AT.date()


def test_compute_since_returns_none_when_nothing_sent(session):
    assert service._compute_since(session) is None


def test_compute_since_applies_lookback_to_last_check(session, pdf_path):
    _sent_application(session, pdf_path)
    last_check = datetime(2026, 9, 25, 12, 0, 0)
    set_setting(session, KEY_INBOX_LAST_CHECK_AT, last_check.isoformat())
    session.commit()

    since = service._compute_since(session)

    assert since == (last_check - timedelta(days=service.LOOKBACK_DAYS)).date()
