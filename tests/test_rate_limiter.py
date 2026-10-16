import logging
from datetime import datetime, time

import pytest

from src.core.models import Application
from src.core.settings import (
    KEY_DAILY_LIMIT,
    KEY_HOURLY_LIMIT,
    KEY_MAX_DELAY_SECONDS,
    KEY_MIN_DELAY_SECONDS,
    KEY_SENDING_PAUSED,
    KEY_WEEKDAYS_ONLY,
    KEY_WORK_END,
    KEY_WORK_START,
    KEY_WORKING_HOURS_ONLY,
    set_setting,
)
from src.core.timeutil import ISTANBUL, to_naive_utc
from src.sender.rate_limiter import (
    CODE_DAILY_LIMIT,
    CODE_HOURLY_LIMIT,
    CODE_OUTSIDE_HOURS,
    CODE_PAUSED,
    HARD_MAX_DAILY,
    HARD_MAX_HOURLY,
    HARD_MIN_DELAY_SECONDS,
    RateLimits,
    can_send_now,
    is_hard_cap_block,
    load_limits,
    random_delay_seconds,
)
from tests.conftest import build_company

# Sabit tarihler: 2026-09-28 Pazartesi, 09-26 Cumartesi, 09-27 Pazar, 09-25 Cuma, 09-29 Salı.
# (2015 gibi eski tarihler gerçekten DST'li olduğu için kasıtlı olarak kullanılmıyor.)


def ist(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=ISTANBUL)


def _record_send(session, domain, sent_at_istanbul):
    company = build_company(domain=domain)
    session.add(company)
    session.flush()
    application = Application(company_id=company.id, sent_at=to_naive_utc(sent_at_istanbul))
    session.add(application)
    session.flush()
    return application


# --- Acil durdur / sıra ------------------------------------------------------------------------


def test_allows_send_with_empty_history_inside_hours(session):
    window = can_send_now(session, ist(2026, 9, 28, 10, 0))
    assert window.allowed is True
    assert window.reason is None
    assert window.wait_seconds == 0


def test_paused_blocks_with_zero_wait(session):
    set_setting(session, KEY_SENDING_PAUSED, True)
    window = can_send_now(session, ist(2026, 9, 28, 10, 0))
    assert window.allowed is False
    assert window.wait_seconds == 0
    assert "duraklat" in window.reason


def test_paused_is_checked_before_working_hours(session):
    """Pazar + paused: sebep 'hafta sonu' değil, acil durdur olmalı (kontrol sırası)."""
    set_setting(session, KEY_SENDING_PAUSED, True)
    window = can_send_now(session, ist(2026, 9, 27, 12, 0))
    assert "duraklat" in window.reason
    assert "hafta sonu" not in window.reason


# --- Mesai saati / hafta sonu -------------------------------------------------------------------


def test_before_work_start_waits_until_start(session):
    window = can_send_now(session, ist(2026, 9, 28, 7, 12))
    assert window.allowed is False
    assert window.wait_seconds == pytest.approx(108 * 60, abs=1)
    assert "mesai" in window.reason


def test_exactly_work_start_is_allowed(session):
    assert can_send_now(session, ist(2026, 9, 28, 9, 0)).allowed is True


def test_exactly_work_end_is_blocked(session):
    assert can_send_now(session, ist(2026, 9, 28, 18, 0)).allowed is False


def test_one_second_before_work_end_is_allowed(session):
    assert can_send_now(session, ist(2026, 9, 28, 17, 59, 59)).allowed is True


def test_friday_after_work_waits_until_monday(session):
    window = can_send_now(session, ist(2026, 9, 25, 18, 1))
    assert window.allowed is False
    assert "Pazartesi" in window.reason


def test_sunday_blocked_with_monday_wait(session):
    window = can_send_now(session, ist(2026, 9, 27, 12, 0))
    assert window.allowed is False
    assert "hafta sonu" in window.reason
    assert "Pazar" in window.reason


def test_saturday_allowed_when_weekdays_only_false(session):
    set_setting(session, KEY_WEEKDAYS_ONLY, False)
    assert can_send_now(session, ist(2026, 9, 26, 10, 0)).allowed is True


def test_working_hours_ignored_when_flag_false(session):
    set_setting(session, KEY_WORKING_HOURS_ONLY, False)
    assert can_send_now(session, ist(2026, 9, 28, 3, 0)).allowed is True


def test_weekdays_only_still_applies_when_working_hours_off(session):
    """weekdays_only ve working_hours_only birbirinden bağımsız çalışmalı."""
    set_setting(session, KEY_WORKING_HOURS_ONLY, False)
    window = can_send_now(session, ist(2026, 9, 27, 3, 0))  # Pazar
    assert window.allowed is False
    assert "hafta sonu" in window.reason


# --- Günlük limit (İstanbul takvim günü) -------------------------------------------------------


def test_daily_limit_uses_istanbul_calendar_day(session):
    """Dünkü (yerel) bir gönderim bugünün sayacına girmemeli — asıl testlenen bu."""
    set_setting(session, KEY_DAILY_LIMIT, 1)
    set_setting(session, KEY_WORKING_HOURS_ONLY, False)
    _record_send(session, "firma1.com", ist(2026, 9, 27, 23, 50))
    window = can_send_now(session, ist(2026, 9, 28, 0, 10))
    assert window.allowed is True


def test_yesterdays_afternoon_send_does_not_count_today(session):
    set_setting(session, KEY_DAILY_LIMIT, 1)
    _record_send(session, "firma1.com", ist(2026, 9, 27, 14, 0))
    assert can_send_now(session, ist(2026, 9, 28, 10, 0)).allowed is True


def test_application_without_sent_at_is_not_counted(session):
    set_setting(session, KEY_DAILY_LIMIT, 1)
    company = build_company(domain="firma1.com")
    session.add(company)
    session.flush()
    session.add(Application(company_id=company.id, sent_at=None))
    session.flush()
    assert can_send_now(session, ist(2026, 9, 28, 10, 0)).allowed is True


def test_daily_limit_blocks_and_reports_numbers(session):
    set_setting(session, KEY_DAILY_LIMIT, 1)
    _record_send(session, "firma1.com", ist(2026, 9, 28, 9, 30))
    window = can_send_now(session, ist(2026, 9, 28, 14, 0))
    assert window.allowed is False
    assert "1/1" in window.reason


def test_daily_limit_wait_points_to_next_work_window(session):
    set_setting(session, KEY_DAILY_LIMIT, 1)
    _record_send(session, "firma1.com", ist(2026, 9, 28, 9, 30))
    window = can_send_now(session, ist(2026, 9, 28, 14, 0))
    expected_wait = (ist(2026, 9, 29, 9, 0) - ist(2026, 9, 28, 14, 0)).total_seconds()
    assert window.wait_seconds == pytest.approx(expected_wait, abs=1)


def test_daily_limit_zero_blocks_everything(session):
    set_setting(session, KEY_DAILY_LIMIT, 0)
    window = can_send_now(session, ist(2026, 9, 28, 10, 0))
    assert window.allowed is False
    assert "günlük" in window.reason


# --- Saatlik limit (son 60 dakika) --------------------------------------------------------------


def test_hourly_limit_counts_last_60_minutes(session):
    set_setting(session, KEY_HOURLY_LIMIT, 2)
    _record_send(session, "firma1.com", ist(2026, 9, 28, 9, 30))
    _record_send(session, "firma2.com", ist(2026, 9, 28, 9, 45))
    window = can_send_now(session, ist(2026, 9, 28, 10, 0))
    assert window.allowed is False
    assert "saatlik" in window.reason
    assert "2/2" in window.reason


def test_sends_older_than_60_minutes_do_not_count(session):
    set_setting(session, KEY_HOURLY_LIMIT, 1)
    _record_send(session, "firma1.com", ist(2026, 9, 28, 8, 30))
    assert can_send_now(session, ist(2026, 9, 28, 10, 0)).allowed is True


def test_hourly_wait_is_until_oldest_leaves_window(session):
    set_setting(session, KEY_HOURLY_LIMIT, 1)
    _record_send(session, "firma1.com", ist(2026, 9, 28, 9, 10))
    window = can_send_now(session, ist(2026, 9, 28, 10, 0))
    assert window.allowed is False
    assert window.wait_seconds == pytest.approx(10 * 60, abs=1)


def test_hourly_window_spans_local_midnight(session):
    """Yerel 00:30'da saatlik pencere dünkü 23:30'dan başlamalı (günlük pencereden erken)."""
    set_setting(session, KEY_HOURLY_LIMIT, 1)
    set_setting(session, KEY_WORKING_HOURS_ONLY, False)
    _record_send(session, "firma1.com", ist(2026, 9, 27, 23, 45))
    window = can_send_now(session, ist(2026, 9, 28, 0, 30))
    assert window.allowed is False
    assert "saatlik" in window.reason


# --- extra_sent_at (test modu sayacı) ------------------------------------------------------------


def test_extra_sent_at_counts_toward_daily_limit(session):
    set_setting(session, KEY_DAILY_LIMIT, 1)
    now = ist(2026, 9, 28, 10, 0)
    window = can_send_now(session, now, extra_sent_at=[to_naive_utc(now)])
    assert window.allowed is False
    assert "günlük" in window.reason


def test_extra_sent_at_counts_toward_hourly_limit(session):
    set_setting(session, KEY_HOURLY_LIMIT, 1)
    now = ist(2026, 9, 28, 10, 0)
    window = can_send_now(session, now, extra_sent_at=[to_naive_utc(now)])
    assert window.allowed is False
    assert "saatlik" in window.reason


def test_extra_sent_at_can_be_oldest_in_window(session):
    set_setting(session, KEY_HOURLY_LIMIT, 1)
    old = ist(2026, 9, 28, 9, 20)
    window = can_send_now(session, ist(2026, 9, 28, 10, 0), extra_sent_at=[to_naive_utc(old)])
    assert window.allowed is False
    assert window.wait_seconds == pytest.approx(20 * 60, abs=1)


# --- SendWindow.code (Faz 9.4 — Otopilot Türkçe metne bakmadan karar versin) --------------------


def test_code_is_none_when_allowed(session):
    window = can_send_now(session, ist(2026, 9, 28, 10, 0))
    assert window.allowed is True
    assert window.code is None


def test_code_is_paused_when_sending_paused(session):
    set_setting(session, KEY_SENDING_PAUSED, True)
    window = can_send_now(session, ist(2026, 9, 28, 10, 0))
    assert window.code == CODE_PAUSED


def test_code_is_outside_hours_outside_work_window(session):
    window = can_send_now(session, ist(2026, 9, 28, 7, 12))
    assert window.code == CODE_OUTSIDE_HOURS


def test_code_is_outside_hours_on_weekend(session):
    window = can_send_now(session, ist(2026, 9, 27, 12, 0))
    assert window.code == CODE_OUTSIDE_HOURS


def test_code_is_daily_limit_when_daily_limit_reached(session):
    set_setting(session, KEY_DAILY_LIMIT, 1)
    _record_send(session, "firma1.com", ist(2026, 9, 28, 9, 30))
    window = can_send_now(session, ist(2026, 9, 28, 14, 0))
    assert window.code == CODE_DAILY_LIMIT


def test_code_is_hourly_limit_when_hourly_limit_reached(session):
    set_setting(session, KEY_HOURLY_LIMIT, 1)
    _record_send(session, "firma1.com", ist(2026, 9, 28, 9, 10))
    window = can_send_now(session, ist(2026, 9, 28, 10, 0))
    assert window.code == CODE_HOURLY_LIMIT


def test_paused_code_wins_over_outside_hours_code(session):
    """Kontrol sırası (acil durdur önce): Pazar + paused -> code PAUSED olmalı, OUTSIDE_HOURS değil."""
    set_setting(session, KEY_SENDING_PAUSED, True)
    window = can_send_now(session, ist(2026, 9, 27, 12, 0))
    assert window.code == CODE_PAUSED


# --- random_delay_seconds -------------------------------------------------------------------------


class _FixedChoiceRandom:
    def __init__(self, pick: str):
        self.pick = pick

    def uniform(self, low, high):
        return low if self.pick == "low" else high


def test_random_delay_endpoints_map_to_min_and_max():
    limits = RateLimits(
        daily_limit=20, hourly_limit=5, min_delay_seconds=180, max_delay_seconds=720,
        working_hours_only=True, work_start=time(9, 0), work_end=time(18, 0), weekdays_only=True,
    )
    assert random_delay_seconds(limits, _FixedChoiceRandom("low")) == 180
    assert random_delay_seconds(limits, _FixedChoiceRandom("high")) == 720


def test_random_delay_when_max_below_min():
    limits = RateLimits(
        daily_limit=20, hourly_limit=5, min_delay_seconds=720, max_delay_seconds=180,
        working_hours_only=True, work_start=time(9, 0), work_end=time(18, 0), weekdays_only=True,
    )
    assert random_delay_seconds(limits, _FixedChoiceRandom("high")) == 720


# --- load_limits doğrulaması -----------------------------------------------------------------


def test_load_limits_falls_back_on_invalid_work_start(session, caplog):
    set_setting(session, KEY_WORK_START, "abc")
    with caplog.at_level(logging.WARNING):
        limits = load_limits(session)
    assert limits.work_start == time(9, 0)
    assert "work_start" in caplog.text


def test_load_limits_falls_back_on_negative_limit(session, caplog):
    set_setting(session, KEY_DAILY_LIMIT, -5)
    with caplog.at_level(logging.WARNING):
        limits = load_limits(session)
    assert limits.daily_limit == 20


def test_load_limits_falls_back_when_work_start_after_work_end(session, caplog):
    set_setting(session, KEY_WORK_START, "20:00")
    set_setting(session, KEY_WORK_END, "08:00")
    with caplog.at_level(logging.WARNING):
        limits = load_limits(session)
    assert limits.work_start == time(9, 0)
    assert limits.work_end == time(18, 0)


# --- Kodda sabit üst sınırlar (PLAN.md Adım 10.8a, CLAUDE.md kural 5) --------------------------


def test_load_limits_clamps_daily_limit_to_hard_cap(session, caplog):
    set_setting(session, KEY_DAILY_LIMIT, 500)
    with caplog.at_level(logging.WARNING):
        limits = load_limits(session)
    assert limits.daily_limit == HARD_MAX_DAILY
    assert "daily_limit" in caplog.text


def test_load_limits_clamps_hourly_limit_to_hard_cap(session, caplog):
    set_setting(session, KEY_HOURLY_LIMIT, 500)
    with caplog.at_level(logging.WARNING):
        limits = load_limits(session)
    assert limits.hourly_limit == HARD_MAX_HOURLY
    assert "hourly_limit" in caplog.text


def test_load_limits_clamps_min_delay_to_hard_floor(session, caplog):
    set_setting(session, KEY_MIN_DELAY_SECONDS, 5)
    set_setting(session, KEY_MAX_DELAY_SECONDS, 10)
    with caplog.at_level(logging.WARNING):
        limits = load_limits(session)
    assert limits.min_delay_seconds == HARD_MIN_DELAY_SECONDS
    assert limits.max_delay_seconds == HARD_MIN_DELAY_SECONDS  # max < kırpılmış min -> min'e eşitlenir
    assert "min_delay_seconds" in caplog.text


def test_load_limits_does_not_touch_values_within_hard_caps(session):
    """Kullanıcının kendi (tavanın altındaki) ayarı DEĞİŞMEMELİ — 10.8a mevcut kullanıcıyı bozmaz."""
    set_setting(session, KEY_DAILY_LIMIT, 10)
    set_setting(session, KEY_HOURLY_LIMIT, 20)
    set_setting(session, KEY_MIN_DELAY_SECONDS, 60)
    set_setting(session, KEY_MAX_DELAY_SECONDS, 180)
    limits = load_limits(session)
    assert limits.daily_limit == 10
    assert limits.hourly_limit == 20
    assert limits.min_delay_seconds == 60
    assert limits.max_delay_seconds == 180


def test_can_send_now_blocks_51st_send_even_when_daily_limit_set_to_500(session):
    """Ayarda daily=500 yazılsa bile 51. mail engellenir (10.8a-4)."""
    set_setting(session, KEY_DAILY_LIMIT, 500)
    set_setting(session, KEY_WORKING_HOURS_ONLY, False)
    now = ist(2026, 9, 28, 10, 0)
    for i in range(HARD_MAX_DAILY):
        _record_send(session, f"firma{i}.com", now)
    window = can_send_now(session, now)
    assert window.allowed is False
    assert window.code == CODE_DAILY_LIMIT
    assert f"{HARD_MAX_DAILY}/{HARD_MAX_DAILY}" in window.reason


def test_random_delay_respects_hard_floor_after_clamp(session):
    """min_delay=5 yazılsa bile bekleme >=30 sn olmalı (10.8a-4)."""
    set_setting(session, KEY_MIN_DELAY_SECONDS, 5)
    set_setting(session, KEY_MAX_DELAY_SECONDS, 10)
    limits = load_limits(session)
    for _ in range(20):
        assert random_delay_seconds(limits) >= HARD_MIN_DELAY_SECONDS


def test_is_hard_cap_block_true_when_daily_limit_at_hard_cap(session):
    set_setting(session, KEY_DAILY_LIMIT, 500)  # kırpılınca HARD_MAX_DAILY'e eşit olur
    limits = load_limits(session)
    window = can_send_now(session, ist(2026, 9, 28, 10, 0))  # boş geçmiş -> allowed
    fake_blocked_window = window._replace(allowed=False, code=CODE_DAILY_LIMIT)
    assert is_hard_cap_block(fake_blocked_window, limits) is True


def test_is_hard_cap_block_false_when_daily_limit_below_hard_cap(session):
    """Kullanıcının kendi düşük ayarı yüzünden engellenmişse (10.8a-2) aşılabilir olmalı."""
    set_setting(session, KEY_DAILY_LIMIT, 5)
    limits = load_limits(session)
    window = can_send_now(session, ist(2026, 9, 28, 10, 0))
    fake_blocked_window = window._replace(allowed=False, code=CODE_DAILY_LIMIT)
    assert is_hard_cap_block(fake_blocked_window, limits) is False


def test_is_hard_cap_block_true_when_hourly_limit_at_hard_cap(session):
    set_setting(session, KEY_HOURLY_LIMIT, 500)
    limits = load_limits(session)
    window = can_send_now(session, ist(2026, 9, 28, 10, 0))
    fake_blocked_window = window._replace(allowed=False, code=CODE_HOURLY_LIMIT)
    assert is_hard_cap_block(fake_blocked_window, limits) is True


def test_is_hard_cap_block_false_for_outside_hours_and_paused(session):
    limits = load_limits(session)
    outside_hours = can_send_now(session, ist(2026, 9, 28, 10, 0))._replace(
        allowed=False, code=CODE_OUTSIDE_HOURS
    )
    paused = can_send_now(session, ist(2026, 9, 28, 10, 0))._replace(allowed=False, code=CODE_PAUSED)
    assert is_hard_cap_block(outside_hours, limits) is False
    assert is_hard_cap_block(paused, limits) is False
