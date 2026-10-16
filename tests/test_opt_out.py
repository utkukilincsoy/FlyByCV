"""Opt-out cevabı -> kalıcı engel (PLAN.md Adım 10.8b) ve Cevaplar sekmesinde elle engelle/kaldır."""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox
from sqlmodel import select

from src.core.blocklist import is_domain_blocked
from src.core.db import get_session
from src.core.models import (
    BLOCK_REASON_MANUAL,
    BLOCK_REASON_OPT_OUT,
    BlockedDomain,
    CLASSIFIED_BY_RULE,
    CLASSIFIED_BY_USER,
    EmailReply,
    REPLY_AUTO,
    REPLY_BOUNCE,
    REPLY_OPT_OUT,
    REPLY_PENDING,
    REPLY_REJECTED,
    STATUS_REJECTED,
    STATUS_SENT,
)
from src.inbox import service
from src.inbox.classifier import classify
from src.inbox.imap_reader import EmailBody, EmailHeader
from src.inbox.reply_matcher import MATCH_BOUNCE, MATCH_DOMAIN
from src.sender.smtp_sender import MailAccount
from src.ui.replies_data import (
    block_reply_company,
    format_check_summary,
    load_reply_rows,
    set_reply_category,
    unblock_reply_company,
)
from src.ui.replies_tab import RepliesTab
from tests.conftest import build_application_row, build_email_reply


def _header(subject="Re: Başvurunuz", **overrides) -> EmailHeader:
    fields = dict(
        uid="1",
        message_id="<reply-1@firma.com>",
        from_addr="İK <ik@firma.com>",
        subject=subject,
        date=None,
        in_reply_to=None,
        references=[],
        intended_to=None,
        auto_submitted=None,
        precedence=None,
        x_autoreply=None,
        x_autorespond=None,
    )
    fields.update(overrides)
    return EmailHeader(**fields)


def _body(text: str) -> EmailBody:
    return EmailBody(uid="1", text=text)


# --- sınıflandırıcı -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Merhaba, bize bir daha yazmayın lütfen.",
        "Bir daha yazmayınız.",
        "Lütfen bizi listeden çıkarın.",
        "Adresimizi listenizden çıkarınız, teşekkürler.",
        "Bu tür başvurularla ilgilenmiyoruz.",
        "bir daha mail atmayin",  # Türkçe karaktersiz
        "BİR DAHA YAZMAYIN",  # büyük harf, noktalı İ
        "Lütfen bize\nbir daha\nyazmayın.",  # ifade satır sonuyla bölünmüş
        "Please unsubscribe me from your list.",
        "Remove me from your mailing list.",
        "Do not contact us again.",
        "Please don’t email us anymore.",  # kıvrık kesme işareti
        "We would like to opt out.",
    ],
)
def test_opt_out_phrases_are_classified_by_rule(session, text):
    result = classify(session, _header(), _body(text), matched_by=MATCH_DOMAIN)

    assert result.category == REPLY_OPT_OUT
    assert result.classified_by == CLASSIFIED_BY_RULE


def test_opt_out_phrase_in_subject_counts(session):
    result = classify(session, _header(subject="Listeden çıkarın"), _body("Teşekkürler."), matched_by=MATCH_DOMAIN)
    assert result.category == REPLY_OPT_OUT


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Maalesef başvurunuz olumsuz sonuçlanmıştır.", REPLY_REJECTED),
        ("Başvurunuzu aldık, değerlendirip döneceğiz.", REPLY_PENDING),
        ("Bağımsız bir yazılım firmasıyız; yazmayı seviyoruz.", REPLY_PENDING),  # "yazmayı" != "yazmayın"
    ],
)
def test_ordinary_replies_are_not_opt_out(session, text, expected):
    result = classify(session, _header(), _body(text), matched_by=MATCH_DOMAIN)
    assert result.category != REPLY_OPT_OUT


def test_bounce_wins_over_opt_out_phrase(session):
    result = classify(session, _header(), _body("Bir daha yazmayın"), matched_by=MATCH_BOUNCE)
    assert result.category == REPLY_BOUNCE


def test_auto_reply_wins_over_opt_out_phrase(session):
    """Otomatik bir tatil cevabının "unsubscribe" alt bilgisi engel oluşturmamalı."""
    header = _header(subject="Automatic reply: Başvuru", auto_submitted="auto-replied")
    result = classify(session, header, _body("I am away. To unsubscribe click here."), matched_by=MATCH_DOMAIN)
    assert result.category == REPLY_AUTO


# --- servis: opt-out cevabı engel oluşturur -----------------------------------------------------


@contextmanager
def _fake_imap_session(account):
    yield "FAKE_CONN"


def _patch_imap(monkeypatch, *, headers, bodies):
    monkeypatch.setattr(service, "imap_session", _fake_imap_session)
    monkeypatch.setattr(service, "fetch_headers", lambda conn, since: headers)
    monkeypatch.setattr(service, "fetch_bodies", lambda conn, uids: bodies)
    monkeypatch.setattr(
        service, "load_account", lambda identity, connection=None: MailAccount(identity=identity, password="x")
    )


def test_check_inbox_blocks_domain_on_opt_out_reply(session, sample_profile, pdf_path, monkeypatch):
    application, company = build_application_row(
        session,
        domain="firma.com",
        cv_pdf_path=pdf_path,
        status=STATUS_SENT,
        sent_at=datetime(2026, 9, 20, 10, 0, 0),
        message_id="<app-1@bizim.com>",
    )
    session.commit()

    header = _header(date=datetime(2026, 9, 21, 9, 0, 0))
    body = _body("Merhaba, bir daha yazmayın lütfen.")
    _patch_imap(monkeypatch, headers=[header], bodies={header.uid: body})

    result = service.check_inbox(session, sample_profile)

    assert result.new_reply_categories == {REPLY_OPT_OUT: 1}
    assert result.blocked_domains == ["firma.com"]
    assert is_domain_blocked(session, "firma.com")
    assert session.get(BlockedDomain, "firma.com").reason == BLOCK_REASON_OPT_OUT
    assert "engellendi: firma.com" in format_check_summary(result)


def test_check_inbox_does_not_block_on_ordinary_rejection(session, sample_profile, pdf_path, monkeypatch):
    build_application_row(
        session,
        domain="firma.com",
        cv_pdf_path=pdf_path,
        status=STATUS_SENT,
        sent_at=datetime(2026, 9, 20, 10, 0, 0),
        message_id="<app-1@bizim.com>",
    )
    session.commit()
    header = _header(date=datetime(2026, 9, 21, 9, 0, 0))
    _patch_imap(
        monkeypatch,
        headers=[header],
        bodies={header.uid: _body("Maalesef başvurunuz olumsuz sonuçlanmıştır.")},
    )

    result = service.check_inbox(session, sample_profile)

    assert result.blocked_domains == []
    assert session.exec(select(BlockedDomain)).all() == []


# --- Cevaplar sekmesi veri katmanı: elle engelle / kaldır ---------------------------------------


def _reply_with_application(session, pdf_path, *, domain="firma.com", **application_overrides):
    application, company = build_application_row(
        session, domain=domain, cv_pdf_path=pdf_path, **application_overrides
    )
    reply = build_email_reply(session, application_id=application.id, message_id=f"<r@{domain}>")
    session.commit()
    return reply, application, company


def test_block_and_unblock_reply_company(session, pdf_path):
    reply, application, company = _reply_with_application(session, pdf_path)
    assert load_reply_rows(session)[0].blocked is False

    block_reply_company(session, reply)
    session.commit()

    assert session.get(BlockedDomain, "firma.com").reason == BLOCK_REASON_MANUAL
    assert load_reply_rows(session)[0].blocked is True
    assert application.status == STATUS_REJECTED  # bekleyen başvuru reddedildi

    assert unblock_reply_company(session, reply) is True
    session.commit()
    assert load_reply_rows(session)[0].blocked is False
    assert unblock_reply_company(session, reply) is False


def test_setting_category_to_opt_out_blocks_company(session, pdf_path):
    reply, _, _ = _reply_with_application(session, pdf_path)

    assert set_reply_category(session, reply, REPLY_OPT_OUT) is None

    assert reply.classified_by == CLASSIFIED_BY_USER
    assert is_domain_blocked(session, "firma.com")


def test_changing_category_away_from_opt_out_does_not_unblock(session, pdf_path):
    reply, _, _ = _reply_with_application(session, pdf_path)
    set_reply_category(session, reply, REPLY_OPT_OUT)

    set_reply_category(session, reply, REPLY_PENDING)

    assert is_domain_blocked(session, "firma.com")  # yalnızca "Engeli kaldır" kaldırır


# --- Cevaplar sekmesi arayüzü -------------------------------------------------------------------


def _make_tab(qtbot, engine) -> RepliesTab:
    tab = RepliesTab(engine, Path("olmayan_profil.json"))
    qtbot.addWidget(tab)
    return tab


def test_preview_panel_buttons_follow_blocked_state(qtbot, engine, pdf_path):
    with get_session(engine) as session:
        _reply_with_application(session, pdf_path)

    tab = _make_tab(qtbot, engine)
    panel = tab._preview
    assert panel._block_button.isEnabled() is False  # seçim yok
    assert panel._unblock_button.isEnabled() is False

    tab._table.selectRow(0)
    assert panel._block_button.isEnabled() is True
    assert panel._unblock_button.isEnabled() is False
    assert "engelli değil" in panel._blocked_label.text()

    panel._block_button.click()

    with get_session(engine) as session:
        assert is_domain_blocked(session, "firma.com")
    tab._table.selectRow(0)
    assert panel._block_button.isEnabled() is False
    assert panel._unblock_button.isEnabled() is True
    assert "ENGELLİ" in panel._blocked_label.text()


def test_unblock_button_asks_confirmation(qtbot, engine, pdf_path, monkeypatch):
    with get_session(engine) as session:
        reply, _, _ = _reply_with_application(session, pdf_path)
        block_reply_company(session, reply)
        session.commit()

    tab = _make_tab(qtbot, engine)
    tab._table.selectRow(0)

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.No)
    tab._preview._unblock_button.click()
    with get_session(engine) as session:
        assert is_domain_blocked(session, "firma.com")  # vazgeçildi

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    tab._preview._unblock_button.click()
    with get_session(engine) as session:
        assert not is_domain_blocked(session, "firma.com")


def test_opt_out_category_is_selectable_in_filter_and_preview(qtbot, engine, pdf_path):
    with get_session(engine) as session:
        application, _ = build_application_row(session, domain="firma.com", cv_pdf_path=pdf_path)
        build_email_reply(
            session, application_id=application.id, message_id="<o@firma.com>", category=REPLY_OPT_OUT
        )
        session.commit()

    tab = _make_tab(qtbot, engine)
    tab._filter_box.setCurrentIndex(tab._filter_box.findData(REPLY_OPT_OUT))
    assert tab._proxy.rowCount() == 1
    assert tab._preview._category_combo.findData(REPLY_OPT_OUT) >= 0
