"""src/ui/queue_data.py testleri (Qt YOK -- saf Python, widget gerektirmez)."""
import dataclasses
from datetime import datetime

from src.core.db import get_session
from src.core.models import (
    STATUS_APPROVED,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_REJECTED,
    STATUS_SENDING,
    STATUS_SENT,
)
from src.core.timeutil import ISTANBUL, to_istanbul
from src.sender.rate_limiter import SendWindow
from src.sender.worker import EVENT_SENT, QueueEvent, QueueRunResult
from src.ui.company_data import ROLE_WARNING_TOOLTIP
from src.ui.queue_data import (
    FILTER_ACTIVE,
    FILTER_ALL,
    SENDING_WARNING_TEXT,
    ApplicationRow,
    cell_text,
    format_countdown,
    format_log_line,
    format_recipient_line,
    format_run_summary,
    format_tags_line,
    load_application_row,
    load_application_rows,
    matches_status,
    plan_send,
    row_tooltip,
)
from tests.conftest import build_application_row


# ---------------------------------------------------------------------------
# load_application_rows / to_application_row
# ---------------------------------------------------------------------------


def test_load_application_rows_joins_company_fields(session, pdf_path):
    build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
    session.commit()

    rows = load_application_rows(session)

    assert len(rows) == 1
    row = rows[0]
    assert row.domain == "acme.com"
    assert row.email == "kariyer@acme.com"
    assert row.email_is_role is True
    assert row.mx_valid is True
    assert row.status == STATUS_QUEUED


def test_load_application_row_returns_none_for_unknown_id(session):
    assert load_application_row(session, 999) is None


def test_application_row_is_a_plain_copy_after_session_closes(engine, pdf_path):
    """ORM sızıntısı regresyon testi: oturum kapansa da kopya düz Python değerleri olmalı."""
    with get_session(engine) as session:
        build_application_row(session, domain="gamma.com", cv_pdf_path=pdf_path)
        session.commit()
        rows = load_application_rows(session)

    row = rows[0]
    as_dict = dataclasses.asdict(row)  # ORM nesnesiyle patlardı (oturuma bağlı, expired olur)
    assert as_dict["domain"] == "gamma.com"


# ---------------------------------------------------------------------------
# cell_text / row_tooltip
# ---------------------------------------------------------------------------


def _row(**overrides) -> ApplicationRow:
    defaults = dict(
        id=1,
        company_id=1,
        company_name="Acme A.Ş.",
        domain="acme.com",
        email="kariyer@acme.com",
        email_is_role=True,
        mx_valid=True,
        status=STATUS_QUEUED,
        tailored=False,
        matched_tags=(),
        cv_pdf_path=None,
        mail_subject="Staj Başvurusu",
        mail_body="Sayın İnsan Kaynakları Ekibi,",
        created_at=None,
        sent_at=None,
        error=None,
    )
    defaults.update(overrides)
    return ApplicationRow(**defaults)


def test_cell_text_email_missing_shows_dash():
    assert cell_text(_row(email=None), 1) == "—"
    assert cell_text(_row(email="kariyer@acme.com"), 1) == "kariyer@acme.com"


def test_cell_text_role_and_mx_symbols():
    assert cell_text(_row(email_is_role=True), 2) == "✓"
    assert cell_text(_row(email_is_role=False), 2) == "⚠"
    assert cell_text(_row(mx_valid=None), 3) == "?"


def test_cell_text_status_label():
    assert cell_text(_row(status=STATUS_SENDING), 4) == "gönderiliyor"


def test_cell_text_created_at_formatted_in_istanbul_time():
    naive_utc = datetime(2026, 6, 15, 9, 30)  # yaz saati -> İstanbul'da +3
    row = _row(created_at=naive_utc)

    assert cell_text(row, 5) == to_istanbul(naive_utc).strftime("%d.%m.%Y %H:%M")
    assert cell_text(_row(created_at=None), 5) == "—"


def test_row_tooltip_combines_role_warning_and_error():
    tooltip = row_tooltip(_row(email_is_role=False, error="mail sunucusu zaman aşımına uğradı"))
    assert ROLE_WARNING_TOOLTIP in tooltip
    assert "mail sunucusu zaman aşımına uğradı" in tooltip


def test_row_tooltip_sending_shows_warning_text():
    assert row_tooltip(_row(status=STATUS_SENDING)) == SENDING_WARNING_TEXT


def test_row_tooltip_empty_when_nothing_to_warn_about():
    assert row_tooltip(_row(email_is_role=True, status=STATUS_QUEUED, error=None)) == ""


# ---------------------------------------------------------------------------
# matches_status
# ---------------------------------------------------------------------------


def test_matches_status_active_includes_queued_approved_sending():
    for status in (STATUS_QUEUED, STATUS_APPROVED, STATUS_SENDING):
        assert matches_status(_row(status=status), FILTER_ACTIVE)


def test_matches_status_active_excludes_terminal_statuses():
    for status in (STATUS_SENT, STATUS_REJECTED, STATUS_FAILED):
        assert not matches_status(_row(status=status), FILTER_ACTIVE)


def test_matches_status_all_matches_everything():
    for status in (STATUS_QUEUED, STATUS_APPROVED, STATUS_SENDING, STATUS_SENT, STATUS_REJECTED, STATUS_FAILED):
        assert matches_status(_row(status=status), FILTER_ALL)


def test_matches_status_specific_status_matches_only_itself():
    assert matches_status(_row(status=STATUS_SENT), STATUS_SENT)
    assert not matches_status(_row(status=STATUS_QUEUED), STATUS_SENT)


# ---------------------------------------------------------------------------
# format_recipient_line / format_tags_line
# ---------------------------------------------------------------------------


def test_format_recipient_line_test_mode_shows_own_address_and_hides_company_use():
    row = _row(email="kariyer@acme.com")
    line = format_recipient_line(row, identity_email="ben@gmail.com", test_mode=True)

    assert "ben@gmail.com" in line
    assert "kariyer@acme.com" in line
    assert "SİZE" in line


def test_format_recipient_line_real_mode_shows_company_address():
    row = _row(email="kariyer@acme.com")
    line = format_recipient_line(row, identity_email="ben@gmail.com", test_mode=False)

    assert "kariyer@acme.com" in line
    assert "GERÇEK ALICI" in line


def test_format_tags_line_empty_means_general_cv():
    assert "ana CV" in format_tags_line(_row(matched_tags=()))


def test_format_tags_line_joins_matched_tags():
    assert format_tags_line(_row(matched_tags=("backend", "yapay-zeka"))) == "backend, yapay-zeka"


# ---------------------------------------------------------------------------
# plan_send (scripts/send_one.py'nin saf kopyası)
# ---------------------------------------------------------------------------

_ALLOWED_WINDOW = SendWindow(True, None, 0)
_BLOCKED_WINDOW = SendWindow(False, "günlük gönderim limitine ulaşıldı (20/20)", 3600)


def test_plan_send_sending_paused_blocks_unconditionally():
    plan = plan_send(_row(), window=_ALLOWED_WINDOW, sending_paused=True, test_mode=True)

    assert plan.blocked_reason is not None
    assert plan.prompts == ()


def test_plan_send_limit_exceeded_prompts_once():
    plan = plan_send(_row(), window=_BLOCKED_WINDOW, sending_paused=False, test_mode=True)

    assert plan.blocked_reason is None
    assert len(plan.prompts) == 1
    assert "günlük gönderim limitine ulaşıldı" in plan.prompts[0]
    assert plan.auto_mode is True


def test_plan_send_personal_address_prompts_and_relaxes_auto_mode():
    plan = plan_send(
        _row(email_is_role=False), window=_ALLOWED_WINDOW, sending_paused=False, test_mode=True
    )

    assert plan.blocked_reason is None
    assert len(plan.prompts) == 1
    assert plan.auto_mode is False


def test_plan_send_real_mode_prompts_with_real_recipient():
    row = _row(email="kariyer@acme.com")
    plan = plan_send(row, window=_ALLOWED_WINDOW, sending_paused=False, test_mode=False)

    assert plan.blocked_reason is None
    assert len(plan.prompts) == 1
    assert "kariyer@acme.com" in plan.prompts[0]


def test_plan_send_everything_fine_test_mode_has_no_prompts():
    plan = plan_send(_row(), window=_ALLOWED_WINDOW, sending_paused=False, test_mode=True)

    assert plan.blocked_reason is None
    assert plan.prompts == ()
    assert plan.auto_mode is True


# ---------------------------------------------------------------------------
# format_log_line / format_countdown / format_run_summary (5.2b: canlı log paneli)
# ---------------------------------------------------------------------------


def test_format_log_line_includes_timestamp_symbol_and_message():
    at = datetime(2026, 9, 28, 14, 5, 12, tzinfo=ISTANBUL)
    event = QueueEvent(EVENT_SENT, "acme.com: gönderildi", domain="acme.com")

    line = format_log_line(event, at=at)

    assert line == "[14:05:12] ✓ acme.com: gönderildi"


def test_format_log_line_unknown_kind_uses_fallback_symbol():
    at = datetime(2026, 9, 28, 9, 0, 0, tzinfo=ISTANBUL)
    event = QueueEvent("bilinmeyen_tur", "test mesajı")

    line = format_log_line(event, at=at)

    assert line == "[09:00:00] • test mesajı"


def test_format_countdown_shows_duration_text():
    assert "Sonraki gönderime" in format_countdown(200)
    assert "3 dakika" in format_countdown(200)


def test_format_countdown_zero_or_negative_is_empty():
    assert format_countdown(0) == ""
    assert format_countdown(-5) == ""


def test_format_run_summary_shows_counts():
    run = QueueRunResult(mode="manual", sent_count=2, refused_count=1, failed_count=0)
    summary = format_run_summary(run)

    assert "gönderildi: 2" in summary
    assert "atlandı: 1" in summary
    assert "hata: 0" in summary


def test_format_run_summary_includes_stop_reason():
    run = QueueRunResult(mode="manual", stop_reason="onaylanmış başvuru kalmadı")
    summary = format_run_summary(run)

    assert "Durma sebebi: onaylanmış başvuru kalmadı" in summary


def test_format_run_summary_warns_about_interrupted_domain():
    run = QueueRunResult(mode="manual", interrupted_domain="acme.com")
    summary = format_run_summary(run)

    assert "acme.com" in summary
    assert "Gönderildi olarak işaretle" in summary
    assert "Kuyruğa geri al" in summary


def test_format_run_summary_no_interrupted_domain_has_no_recovery_text():
    run = QueueRunResult(mode="manual")
    summary = format_run_summary(run)

    assert "işaretle" not in summary
