from datetime import datetime

from src.core.models import Application
from src.core.settings import (
    KEY_BOUNCE_MIN_SAMPLE,
    KEY_BOUNCE_PAUSE_RATE,
    KEY_PAUSE_REASON,
    KEY_SENDING_PAUSED,
    get_setting,
    set_setting,
)
from src.sender.safety_brake import bounce_stats, check_bounce_rate, trigger_pause
from tests.conftest import build_company


def _sent_application(session, domain, *, bounced=False):
    company = build_company(domain=domain)
    session.add(company)
    session.flush()
    application = Application(
        company_id=company.id,
        sent_at=datetime(2026, 9, 20, 10, 0, 0),
        bounced_at=datetime(2026, 9, 21, 10, 0, 0) if bounced else None,
    )
    session.add(application)
    session.flush()
    return application


# --- trigger_pause ---------------------------------------------------------------------------


def test_trigger_pause_sets_paused_and_reason(session):
    changed = trigger_pause(session, "test sebebi")
    assert changed is True
    assert get_setting(session, KEY_SENDING_PAUSED) is True
    assert get_setting(session, KEY_PAUSE_REASON) == "test sebebi"


def test_trigger_pause_does_not_overwrite_existing_reason(session):
    set_setting(session, KEY_SENDING_PAUSED, True)
    set_setting(session, KEY_PAUSE_REASON, "ilk sebep")

    changed = trigger_pause(session, "ikinci sebep")

    assert changed is False
    assert get_setting(session, KEY_PAUSE_REASON) == "ilk sebep"


# --- bounce_stats ------------------------------------------------------------------------------


def test_bounce_stats_ignores_unsent_applications(session):
    company = build_company(domain="hicgonderilmedi.com")
    session.add(company)
    session.flush()
    session.add(Application(company_id=company.id, sent_at=None))
    session.flush()

    bounced, total = bounce_stats(session)

    assert (bounced, total) == (0, 0)


def test_bounce_stats_counts_sent_and_bounced(session):
    _sent_application(session, "ok1.com")
    _sent_application(session, "ok2.com")
    _sent_application(session, "bounced1.com", bounced=True)

    bounced, total = bounce_stats(session)

    assert (bounced, total) == (1, 3)


# --- check_bounce_rate ---------------------------------------------------------------------------


def test_check_bounce_rate_returns_none_below_min_sample(session):
    set_setting(session, KEY_BOUNCE_MIN_SAMPLE, 5)
    set_setting(session, KEY_BOUNCE_PAUSE_RATE, 0.10)
    _sent_application(session, "bounced1.com", bounced=True)
    _sent_application(session, "bounced2.com", bounced=True)

    reason = check_bounce_rate(session)

    assert reason is None
    assert get_setting(session, KEY_SENDING_PAUSED) is False


def test_check_bounce_rate_pauses_when_threshold_exceeded(session):
    set_setting(session, KEY_BOUNCE_MIN_SAMPLE, 4)
    set_setting(session, KEY_BOUNCE_PAUSE_RATE, 0.10)
    for index in range(4):
        _sent_application(session, f"ok{index}.com")
    _sent_application(session, "bounced1.com", bounced=True)

    reason = check_bounce_rate(session)

    assert reason is not None
    assert "bounce oranı" in reason
    assert get_setting(session, KEY_SENDING_PAUSED) is True
    assert get_setting(session, KEY_PAUSE_REASON) == reason


def test_check_bounce_rate_returns_none_when_within_threshold(session):
    set_setting(session, KEY_BOUNCE_MIN_SAMPLE, 4)
    set_setting(session, KEY_BOUNCE_PAUSE_RATE, 0.50)
    for index in range(4):
        _sent_application(session, f"ok{index}.com")
    _sent_application(session, "bounced1.com", bounced=True)

    reason = check_bounce_rate(session)

    assert reason is None
    assert get_setting(session, KEY_SENDING_PAUSED) is False
