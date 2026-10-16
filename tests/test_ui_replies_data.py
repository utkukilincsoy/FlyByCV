"""src/ui/replies_data.py testleri (Qt YOK -- saf Python, widget gerektirmez)."""
from datetime import datetime

from src.core.models import (
    CLASSIFIED_BY_KEYWORD,
    CLASSIFIED_BY_USER,
    REPLY_BOUNCE,
    REPLY_INTERVIEW,
    REPLY_PENDING,
    EmailReply,
)
from src.inbox.service import InboxCheckResult
from src.ui.replies_data import (
    FILTER_ALL,
    cell_text,
    format_check_summary,
    format_classification_line,
    format_matched_by_line,
    format_received_line,
    load_reply_rows,
    matches_category,
    row_tooltip,
    set_reply_category,
    should_auto_check,
)
from tests.conftest import build_application_row, build_email_reply

# ---------------------------------------------------------------------------
# load_reply_rows / to_reply_row
# ---------------------------------------------------------------------------


def test_load_reply_rows_joins_company_and_application_fields(session, pdf_path):
    application, company = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
    build_email_reply(
        session, application_id=application.id, message_id="<reply-1@acme.com>", from_addr="ik@acme.com"
    )
    session.commit()

    rows = load_reply_rows(session)

    assert len(rows) == 1
    row = rows[0]
    assert row.company_name == company.name
    assert row.domain == "acme.com"
    assert row.from_addr == "ik@acme.com"
    assert row.application_id == application.id


def test_load_reply_rows_orders_newest_first(session, pdf_path):
    application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
    build_email_reply(
        session,
        application_id=application.id,
        message_id="<older@acme.com>",
        received_at=datetime(2026, 1, 1),
    )
    build_email_reply(
        session,
        application_id=application.id,
        message_id="<newer@acme.com>",
        received_at=datetime(2026, 2, 1),
    )
    session.commit()

    rows = load_reply_rows(session)

    assert [row.subject for row in rows] == ["Re: Başvurunuz", "Re: Başvurunuz"]
    assert rows[0].received_at > rows[1].received_at


# ---------------------------------------------------------------------------
# cell_text / row_tooltip / matches_category
# ---------------------------------------------------------------------------


def test_cell_text_shows_turkish_category_label(session, pdf_path):
    application, company = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
    build_email_reply(
        session, application_id=application.id, message_id="<r@acme.com>", category=REPLY_INTERVIEW
    )
    session.commit()

    row = load_reply_rows(session)[0]

    assert cell_text(row, 0) == company.name
    assert cell_text(row, 3) == "Mülakat/Olumlu"


def test_row_tooltip_mentions_original_auto_category_after_manual_fix(session, pdf_path):
    application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
    build_email_reply(
        session,
        application_id=application.id,
        message_id="<r@acme.com>",
        category=REPLY_INTERVIEW,
        classified_by=CLASSIFIED_BY_USER,
        auto_category=REPLY_PENDING,
    )
    session.commit()

    row = load_reply_rows(session)[0]

    tooltip = row_tooltip(row)
    assert "Bekliyor" in tooltip


def test_row_tooltip_empty_when_not_manually_corrected(session, pdf_path):
    application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
    build_email_reply(session, application_id=application.id, message_id="<r@acme.com>")
    session.commit()

    row = load_reply_rows(session)[0]

    assert row_tooltip(row) == ""


def test_matches_category_all_accepts_everything(session, pdf_path):
    application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
    build_email_reply(session, application_id=application.id, message_id="<r@acme.com>", category=REPLY_BOUNCE)
    session.commit()

    row = load_reply_rows(session)[0]

    assert matches_category(row, FILTER_ALL) is True
    assert matches_category(row, REPLY_BOUNCE) is True
    assert matches_category(row, REPLY_INTERVIEW) is False


# ---------------------------------------------------------------------------
# format_* önizleme satırları
# ---------------------------------------------------------------------------


def test_format_classification_line_for_automatic_result(session, pdf_path):
    application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
    build_email_reply(
        session, application_id=application.id, message_id="<r@acme.com>", classified_by=CLASSIFIED_BY_KEYWORD
    )
    session.commit()

    row = load_reply_rows(session)[0]

    line = format_classification_line(row)
    assert "anahtar kelime" in line


def test_format_classification_line_for_manual_correction(session, pdf_path):
    application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
    build_email_reply(
        session,
        application_id=application.id,
        message_id="<r@acme.com>",
        classified_by=CLASSIFIED_BY_USER,
        auto_category=REPLY_PENDING,
    )
    session.commit()

    row = load_reply_rows(session)[0]

    line = format_classification_line(row)
    assert "elle düzeltti" in line
    assert "Bekliyor" in line


def test_format_matched_by_line_unknown_value_falls_back_gracefully(session, pdf_path):
    application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
    build_email_reply(session, application_id=application.id, message_id="<r@acme.com>", matched_by=None)
    session.commit()

    row = load_reply_rows(session)[0]

    assert "bilinmiyor" in format_matched_by_line(row)


def test_format_received_line_uses_istanbul_time(session, pdf_path):
    application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
    build_email_reply(
        session, application_id=application.id, message_id="<r@acme.com>", received_at=datetime(2026, 3, 5, 10, 0)
    )
    session.commit()

    row = load_reply_rows(session)[0]

    assert "Alındı:" in format_received_line(row)


# ---------------------------------------------------------------------------
# set_reply_category (elle düzeltme)
# ---------------------------------------------------------------------------


def test_set_reply_category_updates_category_and_classified_by(session, pdf_path):
    application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
    reply = build_email_reply(
        session, application_id=application.id, message_id="<r@acme.com>", category=REPLY_PENDING
    )
    session.commit()

    error = set_reply_category(session, reply, REPLY_INTERVIEW)
    session.commit()

    assert error is None
    refreshed = session.get(EmailReply, reply.id)
    assert refreshed.category == REPLY_INTERVIEW
    assert refreshed.classified_by == CLASSIFIED_BY_USER


def test_set_reply_category_keeps_auto_category_unchanged(session, pdf_path):
    """"İlk otomatik sonuç ne denmişti" bilgisi elle düzeltmeden SONRA da korunmalı (Faz 7.1)."""
    application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
    reply = build_email_reply(
        session,
        application_id=application.id,
        message_id="<r@acme.com>",
        category=REPLY_PENDING,
        auto_category=REPLY_PENDING,
    )
    session.commit()

    set_reply_category(session, reply, REPLY_INTERVIEW)
    session.commit()

    refreshed = session.get(EmailReply, reply.id)
    assert refreshed.auto_category == REPLY_PENDING


def test_set_reply_category_rejects_unknown_category(session, pdf_path):
    application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
    reply = build_email_reply(session, application_id=application.id, message_id="<r@acme.com>")
    session.commit()

    error = set_reply_category(session, reply, "not-a-real-category")

    assert error is not None
    assert reply.category != "not-a-real-category"


# ---------------------------------------------------------------------------
# format_check_summary ("Şimdi kontrol et" bitiş özeti)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# should_auto_check (periyodik kontrol zamanlama kararı -- saf, gerçek saate bakmaz)
# ---------------------------------------------------------------------------


def test_should_auto_check_false_when_interval_is_zero():
    """0 = otomatik kontrol kapalı, hiç deneme yapılmamış olsa bile."""
    now = datetime(2026, 9, 28, 12, 0)
    assert should_auto_check(now, None, 0) is False


def test_should_auto_check_false_when_interval_is_negative():
    now = datetime(2026, 9, 28, 12, 0)
    assert should_auto_check(now, None, -5) is False


def test_should_auto_check_true_on_first_attempt():
    """Hiç deneme yapılmamışsa (`last_attempt_at=None`) ve aralık açıksa hemen dener."""
    now = datetime(2026, 9, 28, 12, 0)
    assert should_auto_check(now, None, 30) is True


def test_should_auto_check_false_before_interval_elapsed():
    last_attempt_at = datetime(2026, 9, 28, 12, 0)
    now = datetime(2026, 9, 28, 12, 29)
    assert should_auto_check(now, last_attempt_at, 30) is False


def test_should_auto_check_true_exactly_at_interval():
    last_attempt_at = datetime(2026, 9, 28, 12, 0)
    now = datetime(2026, 9, 28, 12, 30)
    assert should_auto_check(now, last_attempt_at, 30) is True


def test_should_auto_check_true_well_past_interval():
    last_attempt_at = datetime(2026, 9, 28, 12, 0)
    now = datetime(2026, 9, 28, 15, 0)
    assert should_auto_check(now, last_attempt_at, 30) is True


def test_format_check_summary_lists_new_reply_categories_and_bounces():
    result = InboxCheckResult(
        headers_fetched=10,
        candidates=3,
        new_replies=2,
        duplicate_skipped=1,
        new_reply_categories={REPLY_INTERVIEW: 1, REPLY_BOUNCE: 1},
        bounced_domains=["firma.com"],
        missing_message_id_skipped=1,
    )

    summary = format_check_summary(result)

    assert "10 başlık tarandı" in summary
    assert "Mülakat/Olumlu: 1" in summary
    assert "firma.com" in summary
    assert "Message-ID eksik" in summary
