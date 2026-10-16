from datetime import date, datetime, time, timedelta, timezone

import pytest

from src.core.timeutil import (
    ISTANBUL,
    as_istanbul,
    combine_istanbul,
    istanbul_day_start_utc,
    now_istanbul,
    parse_hhmm,
    to_istanbul,
    to_naive_utc,
)


def test_istanbul_zone_available_and_offset_is_plus_three():
    """`tzdata` kurulu değilse modül import'u zaten patlar; burada iki farklı mevsimde de
    ofsetin +03:00 olduğunu doğruluyoruz (Türkiye'de DST 2016'dan beri yok)."""
    winter = datetime(2026, 1, 15, 12, 0, tzinfo=ISTANBUL)
    summer = datetime(2026, 7, 15, 12, 0, tzinfo=ISTANBUL)
    assert winter.utcoffset().total_seconds() == 3 * 3600
    assert summer.utcoffset().total_seconds() == 3 * 3600


def test_now_istanbul_is_aware_and_in_istanbul():
    result = now_istanbul()
    assert result.tzinfo is not None
    assert result.utcoffset().total_seconds() == 3 * 3600


def test_to_naive_utc_shifts_by_offset():
    istanbul_moment = datetime(2026, 9, 27, 0, 0, tzinfo=ISTANBUL)
    result = to_naive_utc(istanbul_moment)
    assert result == datetime(2026, 9, 26, 21, 0)
    assert result.tzinfo is None


def test_to_naive_utc_leaves_naive_input_unchanged():
    """Zaten naive bir girdi (proje sözleşmesine göre naive UTC) dokunulmadan döner."""
    naive = datetime(2026, 9, 26, 21, 0)
    assert to_naive_utc(naive) == naive


def test_replace_tzinfo_would_be_wrong():
    """`.replace(tzinfo=None)` saati aynı bırakır; doğru dönüşüm 3 saat kaydırır."""
    istanbul_moment = datetime(2026, 9, 27, 0, 0, tzinfo=ISTANBUL)
    wrong = istanbul_moment.replace(tzinfo=None)
    correct = to_naive_utc(istanbul_moment)
    assert wrong != correct
    assert wrong - correct == timedelta(hours=3)


def test_to_istanbul_roundtrip():
    naive_utc = datetime(2026, 9, 26, 21, 0)
    istanbul_moment = to_istanbul(naive_utc)
    assert istanbul_moment == datetime(2026, 9, 27, 0, 0, tzinfo=ISTANBUL)
    assert to_naive_utc(istanbul_moment) == naive_utc


def test_as_istanbul_treats_naive_as_utc():
    naive = datetime(2026, 9, 26, 21, 0)
    result = as_istanbul(naive)
    assert result == datetime(2026, 9, 27, 0, 0, tzinfo=ISTANBUL)


def test_as_istanbul_converts_other_aware_zones():
    utc_moment = datetime(2026, 9, 26, 21, 0, tzinfo=timezone.utc)
    result = as_istanbul(utc_moment)
    assert result == datetime(2026, 9, 27, 0, 0, tzinfo=ISTANBUL)


def test_combine_istanbul_is_aware():
    result = combine_istanbul(date(2026, 9, 28), time(9, 0))
    assert result.tzinfo is ISTANBUL
    assert result.utcoffset().total_seconds() == 3 * 3600
    assert result == datetime(2026, 9, 28, 9, 0, tzinfo=ISTANBUL)


def test_istanbul_day_start_utc_just_after_local_midnight():
    moment = datetime(2026, 9, 27, 0, 10, tzinfo=ISTANBUL)
    start = istanbul_day_start_utc(moment)
    assert start == datetime(2026, 9, 26, 21, 0)


def test_istanbul_day_start_utc_just_before_local_midnight():
    """27'nin 23:50'sinin gün başı, 28'in 00:10'unun gün başından FARKLI (bir gün önceki)."""
    late_27th = datetime(2026, 9, 27, 23, 50, tzinfo=ISTANBUL)
    early_28th = datetime(2026, 9, 28, 0, 10, tzinfo=ISTANBUL)
    assert istanbul_day_start_utc(late_27th) != istanbul_day_start_utc(early_28th)
    assert istanbul_day_start_utc(late_27th) == datetime(2026, 9, 26, 21, 0)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("09:00", time(9, 0)),
        ("00:00", time(0, 0)),
        ("23:59", time(23, 59)),
        (" 09:00 ", time(9, 0)),
    ],
)
def test_parse_hhmm_valid(value, expected):
    assert parse_hhmm(value) == expected


@pytest.mark.parametrize("value", ["0900", 900, "", "25:00", "09:60", None, "abc", "9:0:0"])
def test_parse_hhmm_invalid_raises(value):
    with pytest.raises(ValueError):
        parse_hhmm(value)
