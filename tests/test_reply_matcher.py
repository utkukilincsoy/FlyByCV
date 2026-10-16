"""`src/inbox/reply_matcher.py` testleri — gerçek IMAP/veritabanı sunucusu yok, `session`
fixture'ı (bellek içi SQLite, bkz. `conftest.py`) kullanılır."""
from datetime import datetime

import pytest

from src.core.models import STATUS_SENT
from src.inbox.imap_reader import EmailBody, EmailHeader
from src.inbox.reply_matcher import (
    MATCH_BOUNCE,
    MATCH_DOMAIN,
    MATCH_THREAD,
    is_own_test_mail,
    looks_like_bounce,
    match_reply,
    select_candidates,
)
from src.sender.smtp_sender import MailIdentity
from tests.conftest import build_application_row

IDENTITY = MailIdentity(email="ben@gmail.com", name="Ben")
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


def _sent_application(session, pdf_path, *, domain="firma.com", message_id="<app-1@bizim.com>"):
    application, company = build_application_row(
        session,
        domain=domain,
        cv_pdf_path=pdf_path,
        status=STATUS_SENT,
        sent_at=SENT_AT,
        message_id=message_id,
    )
    return application, company


# ---- is_own_test_mail -------------------------------------------------------------------


def test_is_own_test_mail_true_when_sender_is_self():
    header = _header(from_addr="Ben <ben@gmail.com>")
    assert is_own_test_mail(header, IDENTITY) is True


def test_is_own_test_mail_true_when_intended_to_header_present():
    header = _header(from_addr="birisi <birisi@gmail.com>", intended_to="ik@firma.com")
    assert is_own_test_mail(header, IDENTITY) is True


def test_is_own_test_mail_false_for_normal_reply():
    header = _header(from_addr="İK <ik@firma.com>")
    assert is_own_test_mail(header, IDENTITY) is False


# ---- looks_like_bounce -------------------------------------------------------------------


def test_looks_like_bounce_detects_mailer_daemon_sender():
    header = _header(from_addr="Mail Delivery Subsystem <mailer-daemon@googlemail.com>")
    assert looks_like_bounce(header) is True


def test_looks_like_bounce_detects_subject_keyword():
    header = _header(from_addr="postmaster@sunucu.com", subject="Delivery Status Notification (Failure)")
    assert looks_like_bounce(header) is True


def test_looks_like_bounce_false_for_normal_reply():
    header = _header(from_addr="İK <ik@firma.com>", subject="Başvurunuz Alındı")
    assert looks_like_bounce(header) is False


# ---- match_reply: thread ------------------------------------------------------------------


def test_match_reply_thread_match_via_in_reply_to(session, pdf_path):
    application, _ = _sent_application(session, pdf_path)
    header = _header(in_reply_to="<app-1@bizim.com>")

    result = match_reply(session, header, body=None, identity=IDENTITY)

    assert result is not None
    assert result.application_id == application.id
    assert result.matched_by == MATCH_THREAD


def test_match_reply_thread_match_via_references(session, pdf_path):
    application, _ = _sent_application(session, pdf_path)
    header = _header(in_reply_to=None, references=["<baska@x.com>", "<app-1@bizim.com>"])

    result = match_reply(session, header, body=None, identity=IDENTITY)

    assert result is not None
    assert result.application_id == application.id
    assert result.matched_by == MATCH_THREAD


# ---- match_reply: domain -------------------------------------------------------------------


def test_match_reply_domain_match_when_no_thread_header(session, pdf_path):
    application, _ = _sent_application(session, pdf_path, domain="firma.com")
    header = _header(from_addr="İK <ik@firma.com>", in_reply_to=None, references=[])

    result = match_reply(session, header, body=None, identity=IDENTITY)

    assert result is not None
    assert result.application_id == application.id
    assert result.matched_by == MATCH_DOMAIN


def test_match_reply_domain_match_matches_subdomain(session, pdf_path):
    application, _ = _sent_application(session, pdf_path, domain="firma.com")
    header = _header(from_addr="İK <ik@kariyer.firma.com>", in_reply_to=None, references=[])

    result = match_reply(session, header, body=None, identity=IDENTITY)

    assert result is not None
    assert result.application_id == application.id
    assert result.matched_by == MATCH_DOMAIN


def test_match_reply_domain_no_match_when_sender_is_free_mail(session, pdf_path):
    # Company.domain normalde asla ücretsiz olamaz (csv_import._build_company reddeder), ama
    # bu test o kuralın atlandığı bir kaydı taklit ediyor (build_application_row doğrudan
    # Company oluşturur) — alan adı eşleşmesi yine de bu durumda tetiklenmemeli.
    _sent_application(session, pdf_path, domain="gmail.com")
    header = _header(from_addr="rastgele biri <rastgele@gmail.com>", in_reply_to=None, references=[])

    assert match_reply(session, header, body=None, identity=IDENTITY) is None


def test_match_reply_domain_no_match_for_unsent_application(session, pdf_path):
    from src.core.models import STATUS_QUEUED

    build_application_row(session, domain="firma.com", cv_pdf_path=pdf_path, status=STATUS_QUEUED)
    header = _header(from_addr="İK <ik@firma.com>", in_reply_to=None, references=[])

    assert match_reply(session, header, body=None, identity=IDENTITY) is None


# ---- match_reply: bounce -------------------------------------------------------------------


def test_match_reply_bounce_via_embedded_message_id(session, pdf_path):
    application, _ = _sent_application(session, pdf_path, domain="firma.com", message_id="<app-1@bizim.com>")
    header = _header(
        from_addr="Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
        subject="Delivery Status Notification (Failure)",
        in_reply_to=None,
        references=[],
    )
    body = EmailBody(uid="1", text="teslim edilemedi", original_message_id="<app-1@bizim.com>")

    result = match_reply(session, header, body=body, identity=IDENTITY)

    assert result is not None
    assert result.application_id == application.id
    assert result.matched_by == MATCH_BOUNCE


def test_match_reply_bounce_without_body_does_not_match(session, pdf_path):
    _sent_application(session, pdf_path, domain="firma.com")
    header = _header(from_addr="mailer-daemon@googlemail.com", in_reply_to=None, references=[])

    assert match_reply(session, header, body=None, identity=IDENTITY) is None


# ---- match_reply: sıra / hiç eşleşmeme -----------------------------------------------------


def test_match_reply_rejects_mail_sent_before_application(session, pdf_path):
    _sent_application(session, pdf_path, domain="firma.com")
    header = _header(
        from_addr="İK <ik@firma.com>",
        date=datetime(2026, 9, 1, 0, 0, 0),  # SENT_AT'ten (9/20) önce
        in_reply_to=None,
        references=[],
    )

    assert match_reply(session, header, body=None, identity=IDENTITY) is None


def test_match_reply_returns_none_for_own_test_mail(session, pdf_path):
    _sent_application(session, pdf_path, domain="firma.com")
    header = _header(from_addr="Ben <ben@gmail.com>", in_reply_to="<app-1@bizim.com>")

    assert match_reply(session, header, body=None, identity=IDENTITY) is None


def test_match_reply_returns_none_when_nothing_matches(session, pdf_path):
    _sent_application(session, pdf_path, domain="firma.com")
    header = _header(from_addr="ilgisiz@baskasirket.com", in_reply_to=None, references=[])

    assert match_reply(session, header, body=None, identity=IDENTITY) is None


# ---- select_candidates ----------------------------------------------------------------------


def test_select_candidates_filters_own_test_mail(session, pdf_path):
    _sent_application(session, pdf_path, domain="firma.com")
    own = _header(uid="1", from_addr="Ben <ben@gmail.com>")
    reply = _header(uid="2", from_addr="İK <ik@firma.com>", in_reply_to=None, references=[])

    candidates = select_candidates([own, reply], session=session, identity=IDENTITY)

    assert [h.uid for h in candidates] == ["2"]


def test_select_candidates_includes_bounce_look_alike_without_domain_match(session, pdf_path):
    _sent_application(session, pdf_path, domain="firma.com")
    bounce = _header(
        uid="3",
        from_addr="mailer-daemon@googlemail.com",
        subject="Undelivered Mail Returned to Sender",
        in_reply_to=None,
        references=[],
    )

    candidates = select_candidates([bounce], session=session, identity=IDENTITY)

    assert [h.uid for h in candidates] == ["3"]


def test_select_candidates_excludes_unrelated_mail(session, pdf_path):
    _sent_application(session, pdf_path, domain="firma.com")
    unrelated = _header(uid="4", from_addr="haber@baskabulten.com", in_reply_to=None, references=[])

    assert select_candidates([unrelated], session=session, identity=IDENTITY) == []
