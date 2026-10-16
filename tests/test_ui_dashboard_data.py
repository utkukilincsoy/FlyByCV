"""src/ui/dashboard_data.py testleri (Qt YOK -- saf Python, widget gerektirmez)."""
from src.core.models import (
    REPLY_BOUNCE,
    REPLY_INTERVIEW,
    STATUS_QUEUED,
    STATUS_SENT,
)
from src.ui.dashboard_data import (
    format_company_lines,
    format_reply_lines,
    format_status_lines,
    load_dashboard_stats,
)
from tests.conftest import build_application_row, build_company, build_email_reply


def test_load_dashboard_stats_empty_database_has_no_data(session):
    stats = load_dashboard_stats(session)

    assert stats.total_companies == 0
    assert stats.has_data is False


def test_load_dashboard_stats_counts_companies_with_and_without_email(session):
    session.add(build_company(domain="a.com", email="ik@a.com", email_is_role=True))
    session.add(build_company(domain="b.com", email=None))
    session.commit()

    stats = load_dashboard_stats(session)

    assert stats.total_companies == 2
    assert stats.companies_with_email == 1
    assert stats.has_data is True


def test_load_dashboard_stats_counts_application_status(session, pdf_path):
    build_application_row(session, domain="a.com", cv_pdf_path=pdf_path, status=STATUS_QUEUED)
    build_application_row(session, domain="b.com", cv_pdf_path=pdf_path, status=STATUS_SENT)
    session.commit()

    stats = load_dashboard_stats(session)

    assert stats.status_counts[STATUS_QUEUED] == 1
    assert stats.status_counts[STATUS_SENT] == 1


def test_load_dashboard_stats_excludes_test_mode_sends(session, pdf_path):
    """Test modu Application kaydına hiç dokunmuyor (bkz. smtp_sender.py) -- bu yüzden hiçbir
    başvuru "queued" dışında bir duruma geçmemişse test gönderimleri istatistiğe hiç girmez."""
    build_application_row(session, domain="a.com", cv_pdf_path=pdf_path, status=STATUS_QUEUED)
    session.commit()

    stats = load_dashboard_stats(session)

    assert stats.status_counts[STATUS_SENT] == 0
    assert stats.status_counts[STATUS_QUEUED] == 1


def test_load_dashboard_stats_counts_reply_categories(session, pdf_path):
    application, _ = build_application_row(session, domain="a.com", cv_pdf_path=pdf_path)
    build_email_reply(
        session, application_id=application.id, message_id="<r1@a.com>", category=REPLY_INTERVIEW
    )
    build_email_reply(
        session, application_id=application.id, message_id="<r2@a.com>", category=REPLY_BOUNCE
    )
    session.commit()

    stats = load_dashboard_stats(session)

    assert stats.reply_counts[REPLY_INTERVIEW] == 1
    assert stats.reply_counts[REPLY_BOUNCE] == 1


def test_format_lines_include_turkish_labels(session, pdf_path):
    build_application_row(session, domain="a.com", cv_pdf_path=pdf_path, status=STATUS_SENT)
    session.commit()

    stats = load_dashboard_stats(session)

    assert any("Toplam şirket" in line for line in format_company_lines(stats))
    assert any("gönderildi" in line for line in format_status_lines(stats))
    assert any("Mülakat/Olumlu" in line for line in format_reply_lines(stats))
