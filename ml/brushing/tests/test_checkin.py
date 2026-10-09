from __future__ import annotations

import calendar
from datetime import date, datetime

import pytest

from checkin import (
    CheckinResult,
    CheckinWindow,
    current_streak,
    daily_status,
    day_is_complete,
    evaluate_checkin,
    find_window,
    parse_hhmm,
    parse_windows,
    to_local,
    validate_windows,
)

MORNING = CheckinWindow("morning", 6 * 60, 10 * 60)
EVENING = CheckinWindow("evening", 19 * 60, 23 * 60)
WINDOWS = (MORNING, EVENING)
LATE = (MORNING, CheckinWindow("evening", 20 * 60, 1 * 60))  # crosses midnight


def utc_ms(y: int, mo: int, d: int, h: int, mi: int, s: int = 0, ms: int = 0) -> int:
    """UTC epoch ms computed independently of the code under test."""
    return calendar.timegm((y, mo, d, h, mi, s)) * 1000 + ms


def test_parse_hhmm() -> None:
    assert parse_hhmm("00:00") == 0
    assert parse_hhmm("23:59") == 1439
    for bad in ("24:00", "9:00", "12:60", "noon", ""):
        with pytest.raises(ValueError, match="invalid HH:MM"):
            parse_hhmm(bad)


def test_parse_windows_ok_and_midnight_flag() -> None:
    ws = parse_windows([{"name": "n", "start": "20:00", "end": "01:00"}])
    assert ws[0].crosses_midnight
    assert not MORNING.crosses_midnight


@pytest.mark.parametrize(
    ("windows", "message"),
    [
        ((), "at least one"),
        ((MORNING, CheckinWindow("morning", 700, 800)), "unique"),
        ((CheckinWindow("", 1, 2),), "empty"),
        ((CheckinWindow("x", 5, 5),), "zero length"),
        ((MORNING, CheckinWindow("late", 9 * 60, 11 * 60)), "overlap"),
        ((CheckinWindow("a", 20 * 60, 2 * 60), CheckinWindow("b", 1 * 60, 3 * 60)), "overlap"),
    ],
)
def test_validate_windows_rejects(windows: tuple[CheckinWindow, ...], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_windows(windows)


def test_adjacent_windows_do_not_overlap() -> None:
    validate_windows((MORNING, CheckinWindow("brunch", 10 * 60, 11 * 60)))


def test_timezone_conversion() -> None:
    # 2026-10-07 23:00Z == 2026-10-08 06:00 in Jakarta (UTC+7, no DST)
    local = to_local(utc_ms(2026, 10, 7, 23, 0), "Asia/Jakarta")
    assert (local.hour, local.minute, local.day) == (6, 0, 8)
    assert to_local(utc_ms(2026, 10, 7, 23, 0), "Asia/Makassar").hour == 7
    assert to_local(utc_ms(2026, 10, 7, 23, 0), "Asia/Jayapura").hour == 8
    with pytest.raises(ValueError, match="unknown time zone"):
        to_local(0, "Mars/Olympus")
    with pytest.raises(ValueError, match="unknown time zone"):
        to_local(0, "../etc")


def jakarta_ms(h: int, mi: int, s: int = 0, ms: int = 0) -> int:
    """2026-10-08 local Jakarta wall-clock (UTC+7, no DST) as UTC ms, computed by hand."""
    return utc_ms(2026, 10, 8, h, mi, s, ms) - 7 * 3600 * 1000


@pytest.mark.parametrize(
    ("h", "mi", "s", "ms", "expected"),
    [
        (5, 59, 59, 999, None),
        (6, 0, 0, 0, "morning"),  # start inclusive
        (9, 59, 59, 999, "morning"),
        (10, 0, 0, 0, None),  # end exclusive
        (13, 0, 0, 0, None),
        (18, 59, 59, 999, None),
        (19, 0, 0, 0, "evening"),
        (22, 59, 59, 999, "evening"),
        (23, 0, 0, 0, None),
    ],
)
def test_window_boundaries_jakarta(h: int, mi: int, s: int, ms: int, expected: str | None) -> None:
    found = find_window(jakarta_ms(h, mi, s, ms), WINDOWS, "Asia/Jakarta")
    assert (found[0] if found else None) == expected
    if found:
        assert found[1] == date(2026, 10, 8)


def test_local_date_beats_utc_date() -> None:
    # 2026-10-08 23:30Z is 06:30 on the 9th in Jakarta
    found = find_window(utc_ms(2026, 10, 8, 23, 30), WINDOWS, "Asia/Jakarta")
    assert found == ("morning", date(2026, 10, 9))


def test_other_indonesian_zones_shift_the_window() -> None:
    instant = utc_ms(2026, 10, 7, 23, 0)  # 06:00 Jakarta, 07:00 Makassar, 08:00 Jayapura
    assert find_window(instant, WINDOWS, "Asia/Jakarta") == ("morning", date(2026, 10, 8))
    assert find_window(instant, WINDOWS, "Asia/Makassar") == ("morning", date(2026, 10, 8))
    late = utc_ms(2026, 10, 8, 2, 0)  # 09:00 / 10:00 / 11:00
    assert find_window(late, WINDOWS, "Asia/Jakarta") == ("morning", date(2026, 10, 8))
    assert find_window(late, WINDOWS, "Asia/Makassar") is None


def test_end_of_day_is_outside_default_windows() -> None:
    assert find_window(jakarta_ms(23, 59, 59, 999), WINDOWS, "Asia/Jakarta") is None
    assert find_window(jakarta_ms(0, 0), WINDOWS, "Asia/Jakarta") is None


def test_window_crossing_midnight() -> None:
    # Jakarta 23:59 on the 8th (16:59Z), 00:00 / 00:30 on the 9th (17:00Z / 17:30Z of the 8th)
    for utc in (
        utc_ms(2026, 10, 8, 16, 59),
        utc_ms(2026, 10, 8, 17, 0),
        utc_ms(2026, 10, 8, 17, 30),
    ):
        assert find_window(utc, LATE, "Asia/Jakarta") == ("evening", date(2026, 10, 8))
    assert find_window(utc_ms(2026, 10, 8, 18, 0), LATE, "Asia/Jakarta") is None  # 01:00 exclusive
    assert find_window(utc_ms(2026, 10, 8, 12, 59, 59, 999), LATE, "Asia/Jakarta") is None  # 19:59
    assert find_window(utc_ms(2026, 10, 8, 13, 0), LATE, "Asia/Jakarta") == (
        "evening",
        date(2026, 10, 8),
    )


def test_dst_zone_uses_wall_clock_not_fixed_offset() -> None:
    # Europe/London is UTC+1 in summer: 05:30Z on 2026-07-01 is 06:30 local -> morning.
    summer = utc_ms(2026, 7, 1, 5, 30)
    winter = utc_ms(2026, 1, 1, 5, 30)  # 05:30 local -> outside
    assert find_window(summer, WINDOWS, "Europe/London") == ("morning", date(2026, 7, 1))
    assert find_window(winter, WINDOWS, "Europe/London") is None


def test_evaluate_checkin_requires_completed_session() -> None:
    inside = utc_ms(2026, 10, 8, 0, 0)  # 07:00 Jakarta
    done = evaluate_checkin(inside, True, WINDOWS, "Asia/Jakarta")
    unfinished = evaluate_checkin(inside, False, WINDOWS, "Asia/Jakarta")
    outside = evaluate_checkin(utc_ms(2026, 10, 8, 6, 0), True, WINDOWS, "Asia/Jakarta")  # 13:00
    assert done == CheckinResult("morning", date(2026, 10, 8), True)
    assert unfinished == CheckinResult("morning", date(2026, 10, 8), False)
    assert outside == CheckinResult(None, None, False)


def test_daily_status_ignores_non_counting() -> None:
    results = [
        CheckinResult("morning", date(2026, 10, 8), True),
        CheckinResult("evening", date(2026, 10, 8), False),
        CheckinResult(None, None, False),
        CheckinResult("evening", date(2026, 10, 7), True),
    ]
    assert daily_status(results) == {
        date(2026, 10, 8): {"morning"},
        date(2026, 10, 7): {"evening"},
    }


def test_day_is_complete_modes() -> None:
    names = ["morning", "evening"]
    assert day_is_complete({"morning", "evening"}, names, "both")
    assert not day_is_complete({"morning"}, names, "both")
    assert day_is_complete({"morning"}, names, "any")
    assert not day_is_complete(set(), names, "any")
    with pytest.raises(ValueError, match="streak_requires"):
        day_is_complete(set(), names, "some")


def test_current_streak_rules() -> None:
    names = ["morning", "evening"]
    both = {"morning", "evening"}
    today = date(2026, 10, 8)
    yesterday, before = date(2026, 10, 7), date(2026, 10, 6)
    assert current_streak({}, today, names, "both") == 0
    assert current_streak({today: {"morning"}, yesterday: both}, today, names, "both") == 1
    assert current_streak({today: both, yesterday: both}, today, names, "both") == 2
    assert current_streak({yesterday: {"morning"}, before: both}, today, names, "both") == 0
    assert current_streak({yesterday: {"morning"}, before: {"evening"}}, today, names, "any") == 2
    with pytest.raises(ValueError, match="window_names"):
        current_streak({}, today, [], "both")


def test_parse_windows_from_rules_roundtrip() -> None:
    ws = parse_windows([{"name": "morning", "start": "06:00", "end": "10:00"}])
    assert ws == (MORNING,)
    assert isinstance(to_local(0, "UTC"), datetime)
