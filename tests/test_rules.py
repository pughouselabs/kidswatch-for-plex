from datetime import date, datetime

from app import rules


def sched(**kw):
    base = dict(weekday_minutes=60, weekend_minutes=None, weekday_start="07:00", weekday_end="20:00",
                weekend_start="08:00", weekend_end="08:00")
    base.update(kw)
    return rules.Schedule(**base)


def test_unrated_never_allowed():
    assert not rules.rating_allowed("teens", "movie", None)
    assert not rules.rating_allowed("teens", "movie", "Not Rated")
    assert rules.rating_allowed("kids", "show", "TV-PG")
    assert not rules.rating_allowed("kids", "movie", "PG-13")


def test_window_same_day_and_overnight():
    t = datetime(2026, 10, 19, 21, 0).time()
    assert not rules.within_window("07:00", "20:00", t)
    assert rules.within_window("18:00", "01:00", t)
    assert rules.within_window("18:00", "01:00", datetime(2026, 10, 19, 0, 30).time())
    assert rules.within_window("08:00", "08:00", t)  # same start and end means any time


def test_block_reasons_in_priority_order():
    monday_noon = datetime(2026, 10, 19, 12, 0)
    assert rules.block_reason(sched(), monday_noon, 0) is None
    assert rules.block_reason(sched(), monday_noon, 3600) == "limit"
    assert rules.block_reason(sched(), datetime(2026, 10, 19, 21, 0), 0) == "hours"
    assert rules.block_reason(sched(paused=True), monday_noon, 0) == "paused"


def test_bonus_only_counts_today():
    monday = datetime(2026, 10, 19, 12, 0)
    s = sched(bonus_day="2026-10-19", bonus_minutes=30)
    assert rules.limit_seconds(s, monday.date()) == 90 * 60
    assert rules.block_reason(s, monday, 3600) is None
    assert rules.limit_seconds(s, date(2026, 10, 20)) == 60 * 60


def test_weekend_no_limit():
    saturday = datetime(2026, 10, 24, 12, 0)
    assert rules.limit_seconds(sched(), saturday.date()) is None
    assert rules.block_reason(sched(), saturday, 99999) is None


def test_messages_are_friendly():
    s = sched()
    assert "7:00 AM to 8:00 PM" in rules.stop_message("hours", s, datetime(2026, 10, 19, 21, 0))
    assert "today" in rules.stop_message("limit", s, datetime(2026, 10, 19, 12, 0))
    assert rules.minutes_text(5400) == "1 hr 30 min"
    assert rules.minutes_text(None) == "No limit"


def test_share_filter_is_one_label():
    f = rules.build_filters(7)
    assert f["filterMovies"] == f["filterTelevision"] == "label=pk-ok-7"
    assert f["filterMusic"] == ""


def test_title_visibility():
    v = rules.title_visible
    assert v("kids", "age", "movie", "PG", None)
    assert not v("kids", "age", "movie", "PG-13", None)
    assert v("kids", "age", "movie", "PG-13", "allow")         # parent allowed it
    assert not v("kids", "age", "movie", "PG", "block")         # parent blocked it
    assert not v("teens", "age", "movie", "R", "allow")         # never above the cap
    assert not v("teens", "age", "movie", None, "allow")        # never unrated
    assert not v("kids", "picked", "movie", "G", None)          # picked mode: only allowed titles
    assert v("kids", "picked", "show", "TV-14", "allow")
    assert v("nope", "age", "movie", "PG", None)                # unknown level falls back to Kids


def test_later_bedtime_tonight():
    s = sched(weekday_start="07:00", weekday_end="20:00", later_day="2026-10-19", later_minutes=60)
    monday = datetime(2026, 10, 19, 20, 30)
    assert rules.effective_window(s, monday.date()) == ("07:00", "21:00")
    assert rules.block_reason(s, monday, 0) is None                          # 8:30 PM is fine tonight
    assert rules.block_reason(s, datetime(2026, 10, 20, 20, 30), 0) == "hours"  # tomorrow is back to normal
    late = sched(weekday_end="23:30", later_day="2026-10-19", later_minutes=60)
    assert rules.effective_window(late, monday.date()) == ("07:00", "00:30")   # wraps past midnight
    anytime = sched(weekday_start="00:00", weekday_end="00:00", later_day="2026-10-19", later_minutes=60)
    assert rules.effective_window(anytime, monday.date()) == ("00:00", "00:00")


def test_no_watching_day():
    s = sched(weekday_minutes=0)
    assert rules.block_reason(s, datetime(2026, 10, 19, 12, 0), 0) == "limit"
    assert rules.block_reason(sched(weekday_minutes=0, bonus_day="2026-10-19", bonus_minutes=30),
                              datetime(2026, 10, 19, 12, 0), 0) is None


def test_special_days():
    friday, sunday, tuesday = date(2026, 10, 23), date(2026, 10, 25), date(2026, 10, 20)
    s = sched(weekday_minutes=60, weekend_minutes=180, friday_weekend=True, sunday_school=True)
    assert rules.limit_seconds(s, friday) == 180 * 60
    assert rules.limit_seconds(s, sunday) == 60 * 60
    assert rules.limit_seconds(s, tuesday) == 60 * 60
    on_break = sched(weekday_minutes=60, weekend_minutes=180, break_until="2026-10-21")
    assert rules.limit_seconds(on_break, tuesday) == 180 * 60          # during the break
    assert rules.limit_seconds(on_break, date(2026, 10, 22)) == 60 * 60  # school is back
    assert rules.window_for(on_break, tuesday) == (on_break.weekend_start, on_break.weekend_end)
