"""Pure rules: age levels, Plex share filters and screen-time decisions.

Nothing here talks to the network or the database, so it is easy to test.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from urllib.parse import quote

# Allow-lists, not block-lists: anything unrated or rated above the level stays hidden.
AGE_LEVELS: dict[str, dict] = {
    "little": {
        "label": "Little kids",
        "ages": "about 2 to 6",
        "movies": ["G", "TV-Y", "TV-G"],
        "tv": ["TV-Y", "TV-G"],
        "summary": "G movies and preschool shows (TV-Y, TV-G).",
    },
    "kids": {
        "label": "Kids",
        "ages": "about 7 to 12",
        "movies": ["G", "PG", "TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG"],
        "tv": ["TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG"],
        "summary": "G and PG movies, plus shows rated up to TV-PG.",
    },
    "teens": {
        "label": "Teens",
        "ages": "13 and up",
        "movies": ["G", "PG", "PG-13", "TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG", "TV-14"],
        "tv": ["TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG", "TV-14"],
        "summary": "Movies up to PG-13 and shows up to TV-14.",
    },
}
DEFAULT_AGE_LEVEL = "kids"
MODES = ("age", "picked")
VIDEO_TYPES = {"movie", "episode", "clip"}


def ok_label(kid_id: int) -> str:
    """The one Plex label that marks every title this child may watch."""
    return f"pk-ok-{kid_id}"


def build_filters(kid_id: int) -> dict[str, str]:
    """Share filters for one child, in the format plex.tv stores them.

    The site works out each child's exact title list itself (age level, plus titles the parent allowed,
    minus titles the parent blocked) and labels those titles, so the share only needs "has this label".
    That keeps the rules out of Plex's own filter logic, and anything not labeled stays hidden.
    """
    only = f"label={quote(ok_label(kid_id), safe='')}"
    return {"filterMovies": only, "filterTelevision": only, "filterMusic": ""}


def rating_allowed(age_level: str, item_type: str, rating: str | None) -> bool:
    """Does this rating fit the age level?"""
    level = AGE_LEVELS.get(age_level) or AGE_LEVELS[DEFAULT_AGE_LEVEL]
    allowed = level["tv"] if item_type == "show" else level["movies"]
    return bool(rating) and rating in allowed


def allowable(item_type: str, rating: str | None) -> bool:
    """Titles a parent may allow are capped at the Teens level: never R, NC-17, TV-MA or unrated."""
    return rating_allowed("teens", item_type, rating)


def title_visible(age_level: str, mode: str, item_type: str, rating: str | None, choice: str | None) -> bool:
    """Can the child see this title? choice is the parent's 'allow', 'block' or None."""
    if not allowable(item_type, rating) or choice == "block":
        return False
    if choice == "allow":
        return True
    return mode != "picked" and rating_allowed(age_level, item_type, rating)


# ---------- screen time ----------

@dataclass
class Schedule:
    weekday_minutes: int | None  # None means no daily limit
    weekend_minutes: int | None
    weekday_start: str  # "HH:MM"; start == end means any time
    weekday_end: str
    weekend_start: str
    weekend_end: str
    paused: bool = False
    bonus_day: str | None = None
    bonus_minutes: int = 0
    paused_until: datetime | None = None
    later_day: str | None = None   # "later bedtime tonight": the day it applies to
    later_minutes: int = 0
    friday_weekend: bool = False   # Friday uses weekend rules
    sunday_school: bool = False    # Sunday uses weekday (school night) rules
    break_until: str | None = None  # school break: weekend rules every day through this date

    @classmethod
    def from_row(cls, row) -> "Schedule":
        return cls(
            weekday_minutes=row["weekday_minutes"],
            weekend_minutes=row["weekend_minutes"],
            weekday_start=row["weekday_start"],
            weekday_end=row["weekday_end"],
            weekend_start=row["weekend_start"],
            weekend_end=row["weekend_end"],
            paused=bool(row["paused"]),
            bonus_day=row["bonus_day"],
            bonus_minutes=row["bonus_minutes"] or 0,
            paused_until=datetime.fromisoformat(row["paused_until"]) if row["paused_until"] else None,
            later_day=row["later_day"],
            later_minutes=row["later_minutes"] or 0,
            friday_weekend=bool(row["friday_weekend"]),
            sunday_school=bool(row["sunday_school"]),
            break_until=row["break_until"],
        )


def is_weekend(d: date, s: "Schedule | None" = None) -> bool:
    """Does this day follow weekend rules? Parents can move Friday and Sunday, and set a school break."""
    if s is not None:
        if s.break_until and d.isoformat() <= s.break_until:
            return True
        if d.weekday() == 4 and s.friday_weekend:
            return True
        if d.weekday() == 6 and s.sunday_school:
            return False
    return d.weekday() >= 5


def parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def valid_hhmm(value: str) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{2}:[0-9]{2}", value):
        return False
    try:
        parse_hhmm(value)
        return True
    except ValueError:
        return False


def valid_date(value: str) -> bool:
    """A real calendar date written as YYYY-MM-DD (2026-19-39 matches the pattern but is no date)."""
    if not isinstance(value, str) or not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def window_for(s: Schedule, d: date) -> tuple[str, str]:
    return (s.weekend_start, s.weekend_end) if is_weekend(d, s) else (s.weekday_start, s.weekday_end)


def effective_window(s: Schedule, d: date) -> tuple[str, str]:
    """Today's watch hours, with tonight's later bedtime added when a parent granted one."""
    start, end = window_for(s, d)
    if start == end or s.later_day != d.isoformat() or not s.later_minutes:
        return start, end
    a, e = parse_hhmm(start), parse_hhmm(end)
    length = (e.hour * 60 + e.minute - a.hour * 60 - a.minute) % (24 * 60)
    if length + s.later_minutes >= 24 * 60:
        return start, end  # a bedtime that would reach the next morning's start is ignored
    total = (e.hour * 60 + e.minute + s.later_minutes) % (24 * 60)
    return start, f"{total // 60:02d}:{total % 60:02d}"


def within_window(start: str, end: str, t: time) -> bool:
    a, b = parse_hhmm(start), parse_hhmm(end)
    if a == b:
        return True
    if a < b:
        return a <= t < b
    return t >= a or t < b  # window runs past midnight


def open_window(s: Schedule, now_local: datetime) -> tuple[date, str, str] | None:
    """The watch hours open right now, as (the day they belong to, start, end), or None outside watch hours.

    Hours that run past midnight belong to the evening they start on: with Friday hours of 6 PM to 1 AM,
    12:30 AM Saturday is still Friday night, and a later bedtime granted on Monday keeps going after midnight.
    """
    t = now_local.time()
    yesterday = now_local.date() - timedelta(days=1)
    start, end = effective_window(s, yesterday)
    if parse_hhmm(start) > parse_hhmm(end) and t < parse_hhmm(end):
        return yesterday, start, end
    today = now_local.date()
    start, end = effective_window(s, today)
    a, b = parse_hhmm(start), parse_hhmm(end)
    if a == b or (a <= t < b if a < b else t >= a):  # the part after midnight belongs to tomorrow
        return today, start, end
    return None


def limit_seconds(s: Schedule, d: date) -> int | None:
    minutes = s.weekend_minutes if is_weekend(d, s) else s.weekday_minutes
    if minutes is None:
        return None
    bonus = s.bonus_minutes if s.bonus_day == d.isoformat() else 0
    return (minutes + bonus) * 60


def block_reason(s: Schedule, now_local: datetime, used_seconds: int) -> str | None:
    """Why a child may not watch right now, or None if they may."""
    if s.paused or (s.paused_until and now_local < s.paused_until):
        return "paused"
    if open_window(s, now_local) is None:
        return "hours"
    limit = limit_seconds(s, now_local.date())
    if limit is not None and used_seconds >= limit:
        return "limit"
    return None


def remaining_seconds(s: Schedule, d: date, used_seconds: int) -> int | None:
    limit = limit_seconds(s, d)
    if limit is None:
        return None
    return max(0, limit - used_seconds)


def clock(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def pause_end(kind: str, now_local: datetime, s: "Schedule") -> datetime | None:
    """When a timed pause ends: '30' or '60' minutes from now, or 'tomorrow' (start of tomorrow's Watch hours)."""
    if kind in ("30", "60"):
        return now_local + timedelta(minutes=int(kind))
    if kind == "tomorrow":
        tonight = open_window(s, now_local)  # just after midnight on a late night, "tomorrow" is this morning
        day = (tonight[0] if tonight else now_local.date()) + timedelta(days=1)
        start = parse_hhmm(window_for(s, day)[0])
        return datetime.combine(day, start, tzinfo=now_local.tzinfo)
    return None


def friendly_time(value: str) -> str:
    t = parse_hhmm(value)
    hour = t.hour % 12 or 12
    suffix = "AM" if t.hour < 12 else "PM"
    return f"{hour}:{t.minute:02d} {suffix}"


def stop_message(reason: str, s: Schedule, now_local: datetime) -> str:
    """The text Plex shows on the child's screen when we stop a video."""
    if reason == "paused":
        if s is not None and s.paused_until and not s.paused:
            return f"A parent paused Plex until {clock(s.paused_until.astimezone(now_local.tzinfo))}."
        return "A parent paused Plex for now. Ask them when you can watch again."
    if reason == "hours":
        start, end = effective_window(s, now_local.date())
        return f"Watch time is {friendly_time(start)} to {friendly_time(end)}. See you then."
    return "That's all the screen time for today. See you tomorrow."


def short_minutes(seconds: int) -> str:
    """Compact form for charts: 45m, 1h, 1h 30m."""
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, rest = divmod(minutes, 60)
    return f"{hours}h" + (f" {rest}m" if rest else "")


def minutes_text(seconds: int | None) -> str:
    if seconds is None:
        return "No limit"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} hr" + (f" {rest} min" if rest else "")
