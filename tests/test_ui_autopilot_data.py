"""src/ui/autopilot_data.py testleri (Qt YOK -- saf biçimlendiriciler)."""
from datetime import datetime

from src.autopilot.runner import STAGE_CRAWL, STAGE_PAUSED, STAGE_SEND, AutopilotEvent, AutopilotStats
from src.core.timeutil import ISTANBUL
from src.ui.autopilot_data import (
    DEFAULT_STAGE_TEXT,
    DEFAULT_STOP_REASON_TEXT,
    STAT_TITLES,
    format_log_line,
    format_real_send_confirm_text,
    format_stage_text,
    format_stat_values,
    format_stop_reason_text,
    remaining_today,
)


def ist(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=ISTANBUL)


def _stats(**overrides) -> AutopilotStats:
    defaults = dict(
        companies_found=10,
        companies_with_email=6,
        role_address_count=4,
        person_address_count=2,
        sent_today=3,
        daily_limit=20,
        queued_count=5,
        next_send_at=ist(2026, 9, 28, 10, 0),
        next_send_reason=None,
    )
    defaults.update(overrides)
    return AutopilotStats(**defaults)


# ---------------------------------------------------------------------------
# format_stage_text
# ---------------------------------------------------------------------------


def test_format_stage_text_none_returns_default():
    assert format_stage_text(None) == DEFAULT_STAGE_TEXT


def test_format_stage_text_known_stage_uses_turkish_label():
    assert format_stage_text(STAGE_CRAWL) == "Aşama: Tarama"


def test_format_stage_text_unknown_stage_falls_back_to_raw_value():
    assert format_stage_text("bilinmeyen") == "Aşama: bilinmeyen"


# ---------------------------------------------------------------------------
# format_log_line
# ---------------------------------------------------------------------------


def test_format_log_line_uses_default_symbol_for_pipeline_stages():
    event = AutopilotEvent(stage=STAGE_SEND, message="firma.com: gönderildi")
    line = format_log_line(event, at=ist(2026, 9, 28, 14, 5))

    assert line == "[14:05:00] • firma.com: gönderildi"


def test_format_log_line_uses_distinct_symbol_for_paused_stage():
    event = AutopilotEvent(stage=STAGE_PAUSED, message="acil durdur açık")
    line = format_log_line(event, at=ist(2026, 9, 28, 14, 5))

    assert line.startswith("[14:05:00] ⛔")


# ---------------------------------------------------------------------------
# format_stop_reason_text
# ---------------------------------------------------------------------------


def test_format_stop_reason_text_none_returns_default():
    assert format_stop_reason_text(None) == DEFAULT_STOP_REASON_TEXT


def test_format_stop_reason_text_prefixes_reason():
    assert format_stop_reason_text("Bugünlük iş bitti") == "Durma sebebi: Bugünlük iş bitti"


# ---------------------------------------------------------------------------
# format_stat_values / STAT_TITLES
# ---------------------------------------------------------------------------


def test_format_stat_values_matches_stat_titles_length():
    values = format_stat_values(_stats())

    assert len(values) == len(STAT_TITLES)


def test_format_stat_values_reports_counts_and_daily_limit_fraction():
    values = format_stat_values(_stats(companies_found=10, sent_today=3, daily_limit=20))

    assert values[0] == "10"
    assert values[4] == "3/20"


def test_format_stat_values_next_send_now_when_allowed():
    stats = _stats(next_send_at=ist(2026, 9, 28, 10, 0), next_send_reason=None)

    assert format_stat_values(stats)[-1] == "şu an"


def test_format_stat_values_next_send_shows_time_when_waiting():
    stats = _stats(next_send_at=ist(2026, 9, 28, 11, 30), next_send_reason="saatlik limit doldu")

    assert format_stat_values(stats)[-1] == "11:30"


def test_format_stat_values_next_send_shows_manual_text_when_paused():
    stats = _stats(next_send_at=None, next_send_reason="acil durdur açık")

    assert format_stat_values(stats)[-1] == "elle müdahale gerekiyor"


# ---------------------------------------------------------------------------
# remaining_today / format_real_send_confirm_text
# ---------------------------------------------------------------------------


def test_remaining_today_subtracts_sent_from_daily_limit():
    assert remaining_today(_stats(sent_today=3, daily_limit=20)) == 17


def test_remaining_today_never_negative():
    assert remaining_today(_stats(sent_today=25, daily_limit=20)) == 0


def test_format_real_send_confirm_text_includes_remaining_count():
    text = format_real_send_confirm_text(_stats(sent_today=3, daily_limit=20))

    assert text == "GERÇEK GÖNDERİM: bugün en fazla 17 mail gidecek. Devam?"
