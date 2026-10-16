from datetime import timedelta

from src.core.db import get_session
from src.core.models import (
    Application,
    STATUS_APPROVED,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_REJECTED,
    STATUS_SENDING,
    STATUS_SENT,
    utcnow,
)
from src.sender.queue import (
    approve_application,
    find_application_by_domain,
    guard_auto_mode,
    is_auto_eligible,
    list_queue,
    mark_queued,
    mark_sent,
    next_application,
    reject_application,
)
from tests.conftest import build_company


def _company(session, domain, **overrides):
    company = build_company(domain=domain, **overrides)
    session.add(company)
    session.flush()
    return company


def _application(session, company, *, status=STATUS_QUEUED, **overrides):
    application = Application(company_id=company.id, status=status, **overrides)
    session.add(application)
    session.flush()
    return application


# --- Onay / red ----------------------------------------------------------------------------


def test_approve_moves_queued_to_approved(session):
    company = _company(session, "a.com")
    application = _application(session, company)
    assert approve_application(session, application) is None
    assert application.status == STATUS_APPROVED


def test_reject_moves_queued_to_rejected(session):
    company = _company(session, "a.com")
    application = _application(session, company)
    assert reject_application(session, application) is None
    assert application.status == STATUS_REJECTED


def test_approve_sent_application_is_refused(session):
    company = _company(session, "a.com")
    application = _application(session, company, status=STATUS_SENT, sent_at=utcnow())
    assert approve_application(session, application) is not None
    assert application.status == STATUS_SENT


def test_approve_sending_application_is_refused(session):
    company = _company(session, "a.com")
    application = _application(session, company, status=STATUS_SENDING)
    reason = approve_application(session, application)
    assert reason is not None
    assert "sürüyor" in reason
    assert application.status == STATUS_SENDING


def test_reject_sent_application_is_refused(session):
    company = _company(session, "a.com")
    application = _application(session, company, status=STATUS_SENT, sent_at=utcnow())
    assert reject_application(session, application) is not None


def test_rejected_can_be_approved_again(session):
    """Fikir değiştirme serbest: reddedilmiş bir başvuru sonradan onaylanabilir."""
    company = _company(session, "a.com")
    application = _application(session, company, status=STATUS_REJECTED)
    assert approve_application(session, application) is None
    assert application.status == STATUS_APPROVED


def test_approve_does_not_commit(engine, session):
    """Commit çağırana ait: rollback sonrası kayıt eski durumda kalmalı."""
    company = _company(session, "a.com")
    application = _application(session, company)
    session.commit()  # başlangıç durumu kalıcı olsun; asıl testlenen approve'un commit'i

    approve_application(session, application)
    session.rollback()

    with get_session(engine=engine) as other:
        assert other.get(Application, application.id).status == STATUS_QUEUED


# --- Kurtarma komutları (sending) -----------------------------------------------------------


def test_mark_sent_moves_sending_to_sent_and_sets_sent_at(session):
    company = _company(session, "a.com")
    application = _application(session, company, status=STATUS_SENDING)
    assert mark_sent(session, application) is None
    assert application.status == STATUS_SENT
    assert application.sent_at is not None


def test_mark_sent_refuses_non_sending(session):
    company = _company(session, "a.com")
    application = _application(session, company, status=STATUS_QUEUED)
    reason = mark_sent(session, application)
    assert reason is not None
    assert application.status == STATUS_QUEUED


def test_mark_queued_moves_sending_to_queued(session):
    company = _company(session, "a.com")
    application = _application(session, company, status=STATUS_SENDING)
    assert mark_queued(session, application) is None
    assert application.status == STATUS_QUEUED


def test_mark_queued_refuses_non_sending(session):
    company = _company(session, "a.com")
    application = _application(session, company, status=STATUS_SENT, sent_at=utcnow())
    reason = mark_queued(session, application)
    assert reason is not None
    assert application.status == STATUS_SENT


# --- is_auto_eligible / guard_auto_mode ------------------------------------------------------


def test_is_auto_eligible_true_only_when_both_confirmed():
    assert is_auto_eligible(build_company(domain="a.com", email_is_role=True, mx_valid=True)) is True
    assert is_auto_eligible(build_company(domain="b.com", email_is_role=True, mx_valid=None)) is False
    assert is_auto_eligible(build_company(domain="c.com", email_is_role=None, mx_valid=True)) is False
    assert is_auto_eligible(build_company(domain="d.com", email_is_role=False, mx_valid=True)) is False
    assert is_auto_eligible(build_company(domain="e.com", email_is_role=True, mx_valid=False)) is False


def test_guard_auto_mode_false_for_approved(session):
    company = _company(session, "a.com")
    application = _application(session, company, status=STATUS_APPROVED)
    assert guard_auto_mode(application) is False


def test_guard_auto_mode_true_for_queued(session):
    company = _company(session, "a.com")
    application = _application(session, company, status=STATUS_QUEUED)
    assert guard_auto_mode(application) is True


def test_guard_auto_mode_true_for_sending(session):
    company = _company(session, "a.com")
    application = _application(session, company, status=STATUS_SENDING)
    assert guard_auto_mode(application) is True


# --- next_application: manuel mod --------------------------------------------------------------


def test_manual_mode_selects_only_approved(session):
    company = _company(session, "role.com", email_is_role=True, mx_valid=True)
    _application(session, company, status=STATUS_QUEUED)  # uygun olsa da manuelde seçilmez
    assert next_application(session, auto_mode=False) is None


def test_manual_mode_returns_none_without_approved(session):
    assert next_application(session, auto_mode=False) is None


def test_manual_mode_selects_approved(session):
    company = _company(session, "a.com")
    application = _application(session, company, status=STATUS_APPROVED)
    result = next_application(session, auto_mode=False)
    assert result.id == application.id


# --- next_application: otomatik mod ------------------------------------------------------------


def test_auto_mode_selects_role_address_with_mx(session):
    company = _company(session, "role.com", email_is_role=True, mx_valid=True)
    application = _application(session, company, status=STATUS_QUEUED)
    result = next_application(session, auto_mode=True)
    assert result.id == application.id


def test_auto_mode_skips_person_address(session):
    company = _company(session, "person.com", email_is_role=False, mx_valid=True)
    _application(session, company, status=STATUS_QUEUED)
    assert next_application(session, auto_mode=True) is None


def test_auto_mode_skips_unknown_role(session):
    company = _company(session, "unknown-role.com", email_is_role=None, mx_valid=True)
    _application(session, company, status=STATUS_QUEUED)
    assert next_application(session, auto_mode=True) is None


def test_auto_mode_skips_unknown_mx(session):
    company = _company(session, "unknown-mx.com", email_is_role=True, mx_valid=None)
    _application(session, company, status=STATUS_QUEUED)
    assert next_application(session, auto_mode=True) is None


def test_auto_mode_prefers_approved_over_older_queued(session):
    role_company = _company(session, "role.com", email_is_role=True, mx_valid=True)
    approved_company = _company(session, "approved.com", email_is_role=True, mx_valid=True)
    now = utcnow()
    _application(session, role_company, status=STATUS_QUEUED, created_at=now - timedelta(minutes=10))
    approved_app = _application(session, approved_company, status=STATUS_APPROVED, created_at=now)

    result = next_application(session, auto_mode=True)
    assert result.id == approved_app.id


# --- next_application: sıra ve dışlama ---------------------------------------------------------


def test_fifo_order_by_created_at(session):
    company_a = _company(session, "a.com")
    company_b = _company(session, "b.com")
    now = utcnow()
    _application(session, company_b, status=STATUS_APPROVED, created_at=now)
    app_a = _application(
        session, company_a, status=STATUS_APPROVED, created_at=now - timedelta(minutes=5)
    )

    result = next_application(session, auto_mode=False)
    assert result.id == app_a.id


def test_fifo_tiebreak_by_id_when_created_at_equal(session):
    company_a = _company(session, "a.com")
    company_b = _company(session, "b.com")
    now = utcnow()
    app_a = _application(session, company_a, status=STATUS_APPROVED, created_at=now)
    app_b = _application(session, company_b, status=STATUS_APPROVED, created_at=now)

    result = next_application(session, auto_mode=False)
    assert result.id == min(app_a.id, app_b.id)


def test_exclude_ids_skips_given_applications(session):
    company = _company(session, "role.com", email_is_role=True, mx_valid=True)
    application = _application(session, company, status=STATUS_QUEUED)
    assert next_application(session, auto_mode=True, exclude_ids={application.id}) is None


def test_exclude_ids_falls_through_to_next_candidate(session):
    first = _company(session, "first.com", email_is_role=True, mx_valid=True)
    second = _company(session, "second.com", email_is_role=True, mx_valid=True)
    now = utcnow()
    app_first = _application(session, first, status=STATUS_QUEUED, created_at=now)
    app_second = _application(
        session, second, status=STATUS_QUEUED, created_at=now + timedelta(minutes=1)
    )

    result = next_application(session, auto_mode=True, exclude_ids={app_first.id})
    assert result.id == app_second.id


# --- next_application: hiçbir modda seçilmeyen durumlar -----------------------------------------


def test_rejected_is_never_selected(session):
    company = _company(session, "role.com", email_is_role=True, mx_valid=True)
    _application(session, company, status=STATUS_REJECTED)
    assert next_application(session, auto_mode=True) is None


def test_sending_is_never_selected(session):
    company = _company(session, "role.com", email_is_role=True, mx_valid=True)
    _application(session, company, status=STATUS_SENDING)
    assert next_application(session, auto_mode=True) is None


def test_sent_is_never_selected(session):
    company = _company(session, "role.com", email_is_role=True, mx_valid=True)
    _application(session, company, status=STATUS_SENT, sent_at=utcnow())
    assert next_application(session, auto_mode=True) is None


def test_failed_is_never_selected(session):
    company = _company(session, "role.com", email_is_role=True, mx_valid=True)
    _application(session, company, status=STATUS_FAILED)
    assert next_application(session, auto_mode=True) is None


# --- find_application_by_domain -----------------------------------------------------------------


def test_find_application_by_domain_normalizes(session):
    company = _company(session, "firma.com")
    application = _application(session, company)
    result = find_application_by_domain(session, "https://www.Firma.com/iletisim")
    assert result.id == application.id


def test_find_application_by_domain_returns_none_when_company_missing(session):
    assert find_application_by_domain(session, "yok.com") is None


def test_find_application_by_domain_returns_none_when_no_application(session):
    _company(session, "firma.com")
    assert find_application_by_domain(session, "firma.com") is None


# --- list_queue ------------------------------------------------------------------------------


def test_list_queue_default_statuses_includes_sending(session):
    company = _company(session, "a.com")
    application = _application(session, company, status=STATUS_SENDING)
    result = list_queue(session)
    assert application.id in {a.id for a in result}


def test_list_queue_excludes_sent_and_rejected_by_default(session):
    company1 = _company(session, "a.com")
    company2 = _company(session, "b.com")
    _application(session, company1, status=STATUS_SENT, sent_at=utcnow())
    _application(session, company2, status=STATUS_REJECTED)
    assert list_queue(session) == []


def test_list_queue_custom_statuses(session):
    company = _company(session, "a.com")
    application = _application(session, company, status=STATUS_REJECTED)
    result = list_queue(session, statuses=(STATUS_REJECTED,))
    assert [a.id for a in result] == [application.id]
