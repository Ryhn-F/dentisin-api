"""Check-in window logic for the brushing streak (PRD FR-63). Pure functions, no I/O.

Rules of the road:
- Timestamps are UTC epoch milliseconds (ints); they are converted to the user's IANA time zone
  only to decide which window a check-in falls into. Store UTC, display/decide in local time.
- Windows are local wall-clock ranges, start inclusive, end exclusive. ``end < start`` means the
  window crosses midnight; a check-in after midnight belongs to the window that started the
  previous local day.
- Nothing here trusts the device clock any more than the caller does: server-side sanity checks on
  sync are out of scope for this module (see README, limitations).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
MINUTES_PER_DAY = 24 * 60


@dataclass(frozen=True)
class CheckinWindow:
    """A named local-time window. ``start_min``/``end_min`` are minutes since local midnight."""

    name: str
    start_min: int
    end_min: int

    @property
    def crosses_midnight(self) -> bool:
        return self.end_min < self.start_min


@dataclass(frozen=True)
class CheckinResult:
    """Outcome of placing one finished session on the check-in calendar."""

    window: str | None  # window name, or None if outside every window
    local_date: date | None  # local date the window STARTED on (None if no window)
    counts: bool  # True only if the session completed AND fell inside a window


def parse_hhmm(text: str) -> int:
    """Parse ``HH:MM`` (24 h) to minutes since midnight."""
    match = _HHMM.match(text)
    if match is None:
        raise ValueError(f"invalid HH:MM time: {text!r}")
    return int(match.group(1)) * 60 + int(match.group(2))


def parse_windows(raw: Iterable[Mapping[str, str]]) -> tuple[CheckinWindow, ...]:
    """Build and validate windows from ``[{name, start, end}, ...]`` (e.g. from the rules YAML)."""
    windows = tuple(
        CheckinWindow(str(item["name"]), parse_hhmm(item["start"]), parse_hhmm(item["end"]))
        for item in raw
    )
    validate_windows(windows)
    return windows


def _intervals(window: CheckinWindow) -> list[tuple[int, int]]:
    if window.crosses_midnight:
        return [(window.start_min, MINUTES_PER_DAY), (0, window.end_min)]
    return [(window.start_min, window.end_min)]


def validate_windows(windows: Sequence[CheckinWindow]) -> None:
    """Raise ValueError if windows are empty, duplicated, degenerate or overlapping."""
    if not windows:
        raise ValueError("at least one check-in window is required")
    names = [w.name for w in windows]
    if len(set(names)) != len(names):
        raise ValueError("window names must be unique")
    for w in windows:
        if not w.name:
            raise ValueError("window name must not be empty")
        if w.start_min == w.end_min:
            raise ValueError(f"window {w.name!r} has zero length (start == end)")
    spans = sorted((s, e, w.name) for w in windows for (s, e) in _intervals(w))
    for (_, prev_end, prev_name), (next_start, _, next_name) in pairwise(spans):
        if next_start < prev_end:
            raise ValueError(f"windows {prev_name!r} and {next_name!r} overlap")


def to_local(utc_ms: int, tz_name: str) -> datetime:
    """Convert UTC epoch milliseconds to an aware datetime in ``tz_name``."""
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown time zone: {tz_name!r}") from exc
    return datetime.fromtimestamp(utc_ms / 1000, tz=UTC).astimezone(tz)


def find_window(
    utc_ms: int, windows: Sequence[CheckinWindow], tz_name: str
) -> tuple[str, date] | None:
    """Return ``(window_name, local_date_window_started)`` for a UTC instant, or None."""
    local = to_local(utc_ms, tz_name)
    # Whole-minute resolution is exact here: windows are HH:MM, start inclusive, end exclusive.
    minute = local.hour * 60 + local.minute
    for w in windows:
        if not w.crosses_midnight:
            if w.start_min <= minute < w.end_min:
                return w.name, local.date()
        elif minute >= w.start_min:
            return w.name, local.date()
        elif minute < w.end_min:
            return w.name, local.date() - timedelta(days=1)
    return None


def evaluate_checkin(
    end_utc_ms: int, completed: bool, windows: Sequence[CheckinWindow], tz_name: str
) -> CheckinResult:
    """Place a finished session (by its END time) on the calendar.

    A session counts for the streak only if the 2-minute timer completed (``completed``) and it
    ended inside a window. Sessions that start before a window and end inside it count.
    """
    found = find_window(end_utc_ms, windows, tz_name)
    if found is None:
        return CheckinResult(window=None, local_date=None, counts=False)
    name, local_date = found
    return CheckinResult(window=name, local_date=local_date, counts=completed)


def daily_status(results: Iterable[CheckinResult]) -> dict[date, set[str]]:
    """Group counting check-ins into ``{local_date: {window names}}``."""
    status: dict[date, set[str]] = {}
    for r in results:
        if r.counts and r.window is not None and r.local_date is not None:
            status.setdefault(r.local_date, set()).add(r.window)
    return status


def day_is_complete(done: set[str], window_names: Sequence[str], requires: str) -> bool:
    """``requires`` is ``"both"`` (every window) or ``"any"`` (at least one)."""
    if requires == "both":
        return all(name in done for name in window_names)
    if requires == "any":
        return any(name in done for name in window_names)
    raise ValueError(f"streak_requires must be 'both' or 'any', got {requires!r}")


def current_streak(
    status: Mapping[date, set[str]],
    today: date,
    window_names: Sequence[str],
    requires: str,
) -> int:
    """Consecutive complete local days ending today, or yesterday if today is still in progress.

    Today never breaks a streak: an unfinished today is skipped, a finished today is counted.
    """
    if not window_names:
        raise ValueError("window_names must not be empty")
    day = today
    if not day_is_complete(status.get(day, set()), window_names, requires):
        day = today - timedelta(days=1)
    streak = 0
    while day_is_complete(status.get(day, set()), window_names, requires):
        streak += 1
        day -= timedelta(days=1)
    return streak
