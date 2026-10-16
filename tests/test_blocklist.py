"""Engelli alan adı listesi (PLAN.md Adım 10.8b): engelli alan adına HİÇBİR yoldan işlem yapılmaz.

Yollar: CSV/elle ekleme/keşif (`csv_import`), kuyruk seçimi (`queue.next_application`), Otopilot
tarama/hazırlama adayları (`runner._next_*_candidate`) ve gönderim (`send_application` — tek geçit).
"""
import pytest
from sqlmodel import select

from src.autopilot import runner
from src.company_finder.csv_import import REASON_BLOCKED, RowError, add_company, import_companies
from src.company_finder.discovery import import_leads
from src.core.blocklist import (
    block_company,
    block_domain,
    blocked_domains,
    company_domains,
    is_company_blocked,
    is_domain_blocked,
    unblock_company,
    unblock_domain,
)
from src.core.models import (
    Application,
    BLOCK_REASON_MANUAL,
    BLOCK_REASON_OPT_OUT,
    BlockedDomain,
    STATUS_APPROVED,
    STATUS_QUEUED,
    STATUS_REJECTED,
    STATUS_SENT,
    utcnow,
)
from src.core.settings import KEY_TEST_MODE, set_setting
from src.sender.queue import next_application
from src.sender.smtp_sender import BLOCKED_REASON, MailAccount, MailIdentity, send_application
from tests.conftest import FakeTransport, build_application_row, build_company, build_sample_profile


# --- çekirdek ---------------------------------------------------------------------------------


def test_block_domain_normalizes_and_is_idempotent(session):
    first = block_domain(session, "https://www.Firma.com/iletisim", BLOCK_REASON_OPT_OUT)
    second = block_domain(session, "firma.com", BLOCK_REASON_MANUAL)

    assert first.domain == "firma.com"
    assert second.reason == BLOCK_REASON_OPT_OUT  # ilk sebep korunur
    assert len(session.exec(select(BlockedDomain)).all()) == 1
    assert is_domain_blocked(session, "WWW.FIRMA.COM")
    assert blocked_domains(session) == {"firma.com"}


def test_block_domain_ignores_empty_value(session):
    assert block_domain(session, "  ") is None
    assert session.exec(select(BlockedDomain)).all() == []


def test_unblock_domain(session):
    block_domain(session, "firma.com")
    assert unblock_domain(session, "firma.com") is True
    assert unblock_domain(session, "firma.com") is False
    assert not is_domain_blocked(session, "firma.com")


def test_company_is_blocked_by_email_domain_too(session):
    company = build_company(domain="firma.com", email="ik@firma.com.tr")
    session.add(company)
    session.flush()
    assert company_domains(company) == {"firma.com", "firma.com.tr"}

    block_domain(session, "firma.com.tr")
    assert is_company_blocked(session, company)


def test_block_company_blocks_both_domains_and_rejects_pending_application(session, pdf_path):
    application, company = build_application_row(
        session,
        domain="firma.com",
        cv_pdf_path=pdf_path,
        company_overrides={"email": "ik@firma.com.tr"},
    )
    block_company(session, company, BLOCK_REASON_OPT_OUT)

    assert blocked_domains(session) == {"firma.com", "firma.com.tr"}
    assert application.status == STATUS_REJECTED


def test_block_company_keeps_already_sent_application(session, pdf_path):
    application, company = build_application_row(
        session, domain="firma.com", cv_pdf_path=pdf_path, status=STATUS_SENT, sent_at=utcnow()
    )
    block_company(session, company)
    assert application.status == STATUS_SENT


def test_unblock_company_does_not_restore_rejected_application(session, pdf_path):
    application, company = build_application_row(session, domain="firma.com", cv_pdf_path=pdf_path)
    block_company(session, company)
    assert unblock_company(session, company) is True
    assert not is_company_blocked(session, company)
    assert application.status == STATUS_REJECTED


# --- CSV / keşif ------------------------------------------------------------------------------


def test_add_company_refuses_blocked_domain(session):
    block_domain(session, "firma.com")
    with pytest.raises(RowError, match="engelli"):
        add_company(session, "Firma", website="https://www.firma.com/iletisim")
    assert session.exec(select(Application)).all() == []


def test_add_company_refuses_blocked_email_domain(session):
    block_domain(session, "firma.com.tr")
    with pytest.raises(RowError) as error:
        add_company(session, "Firma", website="firma.com", email="ik@firma.com.tr")
    assert str(error.value) == REASON_BLOCKED


def test_add_company_allows_unrelated_domain(session):
    block_domain(session, "baska.com")
    company = add_company(session, "Firma", website="firma.com")
    assert company.domain == "firma.com"


def test_blocked_domain_is_refused_even_after_company_row_is_gone(session):
    """Engel ayrı tabloda: şirket kaydı silinse de aynı alan adı tekrar gelirse atlanır."""
    company = add_company(session, "Firma", website="firma.com")
    block_domain(session, company.domain)
    session.delete(company)
    session.flush()

    with pytest.raises(RowError):
        add_company(session, "Firma", website="firma.com")


def test_csv_import_skips_blocked_rows(session, tmp_path):
    block_domain(session, "engelli.com")
    path = tmp_path / "list.csv"
    path.write_text(
        "name,website\nEngelli A.Ş.,https://engelli.com\nSerbest A.Ş.,https://serbest.com\n",
        encoding="utf-8",
    )

    report = import_companies(session, path)

    assert [row.domain for row in report.added] == ["serbest.com"]
    assert [(note.name, note.reason) for note in report.skipped] == [("Engelli A.Ş.", REASON_BLOCKED)]


class _Lead:
    def __init__(self, name, website):
        self.name, self.website, self.email, self.city = name, website, None, "Ankara"


def test_discovery_import_leads_skips_blocked_domain(session):
    """Teknokent/OSM/Google kaynaklarının ortak yolu (`import_leads` -> `add_company`)."""
    block_domain(session, "engelli.com")

    report = import_leads(
        session,
        [_Lead("Engelli", "https://engelli.com"), _Lead("Serbest", "https://serbest.com")],
        company_source="osm",
    )

    assert [row.domain for row in report.added] == ["serbest.com"]
    assert report.skipped[0].reason == REASON_BLOCKED


# --- kuyruk / Otopilot ------------------------------------------------------------------------


def test_next_application_skips_blocked_approved_and_auto_queued(session, pdf_path):
    blocked_app, blocked_company = build_application_row(
        session, domain="engelli.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED
    )
    queued_blocked, queued_company = build_application_row(
        session, domain="engelli2.com", cv_pdf_path=pdf_path, status=STATUS_QUEUED
    )
    free_app, _ = build_application_row(
        session, domain="serbest.com", cv_pdf_path=pdf_path, status=STATUS_QUEUED
    )
    # Engel, başvuru durumunu DEĞİŞTİRMEDEN (doğrudan alan adı) konmuş olsun.
    block_domain(session, blocked_company.domain)
    block_domain(session, queued_company.domain)

    assert next_application(session, auto_mode=True) == free_app
    assert next_application(session, auto_mode=False) is None


def test_autopilot_prepare_candidate_skips_blocked_company(session):
    blocked = build_company(domain="engelli.com", email="kariyer@engelli.com")
    free = build_company(domain="serbest.com", email="kariyer@serbest.com")
    session.add(blocked)
    session.add(free)
    session.flush()
    block_domain(session, "engelli.com")

    assert runner._next_prepare_candidate(session, set()) == free


def test_autopilot_crawl_candidate_skips_blocked_company(session):
    blocked = build_company(domain="engelli.com")
    session.add(blocked)
    session.flush()
    block_domain(session, "engelli.com")
    assert runner._next_crawl_candidate(session) is None

    free = build_company(domain="serbest.com")
    session.add(free)
    session.flush()
    assert runner._next_crawl_candidate(session) == free


# --- gönderim (son savunma) -------------------------------------------------------------------


@pytest.fixture
def account() -> MailAccount:
    return MailAccount(identity=MailIdentity(email="ben@gmail.com", name="Ben"), password="x")


@pytest.mark.parametrize("test_mode", [True, False])
def test_send_application_refuses_blocked_domain_in_both_modes(
    session, pdf_path, account, test_mode
):
    set_setting(session, KEY_TEST_MODE, test_mode)
    application, company = build_application_row(
        session, domain="firma.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED
    )
    block_domain(session, "firma.com")
    transport = FakeTransport()

    result = send_application(
        session, application, build_sample_profile(), auto_mode=False, transport=transport, account=account
    )

    assert result.refused_reason == BLOCKED_REASON
    assert not result.sent
    assert transport.calls == []
    assert application.status == STATUS_APPROVED  # kayda dokunulmadı


def test_send_application_refuses_when_only_email_domain_is_blocked(session, pdf_path, account):
    set_setting(session, KEY_TEST_MODE, False)
    application, _ = build_application_row(
        session,
        domain="firma.com",
        cv_pdf_path=pdf_path,
        status=STATUS_APPROVED,
        company_overrides={"email": "ik@firma.com.tr"},
    )
    block_domain(session, "firma.com.tr")
    transport = FakeTransport()

    result = send_application(
        session, application, build_sample_profile(), auto_mode=False, transport=transport, account=account
    )

    assert result.refused_reason == BLOCKED_REASON
    assert transport.calls == []


def test_send_application_sends_to_unblocked_company(session, pdf_path, account):
    set_setting(session, KEY_TEST_MODE, False)
    application, _ = build_application_row(
        session, domain="firma.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED
    )
    block_domain(session, "baska.com")
    transport = FakeTransport()

    result = send_application(
        session, application, build_sample_profile(), auto_mode=False, transport=transport, account=account
    )

    assert result.sent
    assert len(transport.calls) == 1
