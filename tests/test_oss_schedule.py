"""Screen time around midnight, in a real time zone, across daylight saving time, and pause edge cases."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import config, rules
from app.main import create_app
from app.plex import Session

from .fakes import KID, FakePlex
from .test_app import active_kid, csrf, env  # noqa: F401  (env is a fixture)

DENVER = ZoneInfo("America/Denver")
UTC = timezone.utc


def sched(**kw):
    base = dict(weekday_minutes=60, weekend_minutes=120, weekday_start="07:00", weekday_end="20:00",
                weekend_start="08:00", weekend_end="21:00")
    base.update(kw)
    return rules.Schedule(**base)


def denver_app(tmp_path, monkeypatch):
    for name, value in (("PK_DB", str(tmp_path / "t.db")), ("PK_PUBLIC_URL", "http://testserver"),
                        ("PK_SEED_PARENTS", "alex,sam"), ("PK_SECURE_COOKIES", "0"), ("PK_START_ENFORCER", "0"),
                        ("PK_OWNER_CODE", "0"), ("PK_HA_WEBHOOK_FILE", str(tmp_path / "missing")),
                        ("PK_TZ", "America/Denver")):
        monkeypatch.setenv(name, value)
    fake = FakePlex()
    app = create_app(config.load(), plex=fake)
    app.state.svc.sync_inline = True
    return app, fake


def at(*parts) -> float:
    """A Denver wall-clock time as a Unix timestamp."""
    return datetime(*parts, tzinfo=DENVER).timestamp()


def stops(fake):
    return [x for x in fake.calls if x[0] == "terminate"]


# ---------- the keeper in a real time zone ----------

def test_usage_is_counted_on_the_local_day_not_the_utc_day(tmp_path, monkeypatch):
    app, fake = denver_app(tmp_path, monkeypatch)
    _, kid_id = active_kid(app, fake)
    db, enf = app.state.db, app.state.enforcer
    fake.live = [Session("s1", KID.id, "movie", "Cloud Garden", "playing", "TV")]
    now = at(2026, 10, 20, 19, 30)  # Tuesday 7:30 PM in Denver is already Wednesday in UTC
    enf.tick(now)
    enf.tick(now + 20)
    assert db.used(kid_id, "2026-10-20") == 20
    assert db.used(kid_id, "2026-10-21") == 0
    assert stops(fake) == []  # 7:30 PM is inside 7 AM to 8 PM local, even though it is 1:30 AM UTC


def test_watch_hours_follow_the_local_clock(tmp_path, monkeypatch):
    app, fake = denver_app(tmp_path, monkeypatch)
    _, kid_id = active_kid(app, fake)
    fake.live = [Session("s1", KID.id, "movie", "Cloud Garden", "playing", "TV")]
    app.state.enforcer.tick(at(2026, 10, 20, 20, 0))  # Tuesday 8:00 PM local: bedtime
    (stop,) = stops(fake)
    assert "Watch time is 7:00 AM to 8:00 PM" in stop[2]
    assert app.state.db.q("SELECT reason FROM stops WHERE kid_id=?", (kid_id,))[0]["reason"] == "hours"


def test_long_gap_between_checks_counts_at_most_three_polls(tmp_path, monkeypatch):
    """After a sleep or a Plex outage the keeper must not charge the whole gap as screen time."""
    app, fake = denver_app(tmp_path, monkeypatch)
    _, kid_id = active_kid(app, fake)
    db, enf = app.state.db, app.state.enforcer
    fake.live = [Session("s1", KID.id, "movie", "Cloud Garden", "playing", "TV")]
    now = at(2026, 10, 20, 12, 0)
    enf.tick(now)
    enf.tick(now + 3600)
    assert db.used(kid_id, "2026-10-20") == 3 * app.state.svc.cfg.poll_seconds


def test_paused_video_is_not_counted(tmp_path, monkeypatch):
    app, fake = denver_app(tmp_path, monkeypatch)
    _, kid_id = active_kid(app, fake)
    fake.live = [Session("s1", KID.id, "movie", "Cloud Garden", "paused", "TV")]
    now = at(2026, 10, 20, 12, 0)
    app.state.enforcer.tick(now)
    app.state.enforcer.tick(now + 20)
    assert app.state.db.used(kid_id, "2026-10-20") == 0


def test_daily_limit_resets_at_local_midnight(tmp_path, monkeypatch):
    app, fake = denver_app(tmp_path, monkeypatch)
    _, kid_id = active_kid(app, fake)
    db, enf = app.state.db, app.state.enforcer
    db.update_kid(kid_id, weekday_start="00:00", weekday_end="00:00")  # any time, so only the limit matters
    db.add_usage(kid_id, "2026-10-20", 2 * 3600)  # Tuesday's two hours are used up
    fake.live = [Session("s1", KID.id, "movie", "Cloud Garden", "playing", "TV")]
    enf.tick(at(2026, 10, 20, 23, 59, 30))
    assert len(stops(fake)) == 1
    enf.tick(at(2026, 10, 21, 0, 0, 10))  # Wednesday: a fresh day
    assert len(stops(fake)) == 1
    assert db.used(kid_id, "2026-10-21") == 40  # the seconds after the last check land on the new day


def test_same_session_is_logged_once_while_it_keeps_being_stopped(env):
    app, fake = env
    _, kid_id = active_kid(app, fake)
    db = app.state.db
    db.update_kid(kid_id, paused=1)
    fake.live = [Session("s1", KID.id, "movie", "Cloud Garden", "playing", "TV")]
    for i in range(3):
        app.state.enforcer.tick(1_800_000_000 + i * 20)
    assert len(stops(fake)) == 3  # Plex is asked every time, in case the first stop didn't stick
    assert db.one("SELECT COUNT(*) n FROM stops WHERE kid_id=?", (kid_id,))["n"] == 1


def test_timed_pause_message_uses_local_time_and_expires(tmp_path, monkeypatch):
    app, fake = denver_app(tmp_path, monkeypatch)
    _, kid_id = active_kid(app, fake)
    db, enf = app.state.db, app.state.enforcer
    until = datetime(2026, 10, 20, 15, 0, tzinfo=DENVER)
    db.update_kid(kid_id, paused_until=until.astimezone(UTC).isoformat(timespec="seconds"))
    fake.live = [Session("s1", KID.id, "movie", "Cloud Garden", "playing", "TV")]
    enf.tick(at(2026, 10, 20, 14, 0))
    (stop,) = stops(fake)
    assert stop[2] == "A parent paused Plex until 3:00 PM."
    enf.tick(at(2026, 10, 20, 15, 0, 1))  # the pause is over
    assert len(stops(fake)) == 1


# ---------- pause end times ----------

def test_pause_until_tomorrow_uses_tomorrows_rules():
    friday_night = datetime(2026, 10, 23, 21, 0, tzinfo=DENVER)
    until = rules.pause_end("tomorrow", friday_night, sched(weekend_start="09:00"))
    assert until == datetime(2026, 10, 24, 9, 0, tzinfo=DENVER)  # Saturday opens at the weekend time
    sunday_night = datetime(2026, 10, 25, 21, 0, tzinfo=DENVER)
    assert rules.pause_end("tomorrow", sunday_night, sched()).hour == 7  # Monday is a school day
    assert rules.pause_end("tomorrow", sunday_night, sched(weekday_start="00:00", weekday_end="00:00")).hour == 0
    assert rules.pause_end("15", sunday_night, sched()) is None


def test_pause_until_tomorrow_across_the_fall_clock_change():
    saturday = datetime(2026, 10, 31, 21, 0, tzinfo=DENVER)  # clocks fall back early Sunday Nov 1
    until = rules.pause_end("tomorrow", saturday, sched())
    assert until.astimezone(UTC) == datetime(2026, 11, 1, 15, 0, tzinfo=UTC)  # 8:00 AM MST
    s = sched(paused_until=until.astimezone(UTC))  # stored and read back in UTC, like the app does
    assert rules.block_reason(s, datetime(2026, 11, 1, 7, 59, tzinfo=DENVER), 0) == "paused"
    assert rules.block_reason(s, datetime(2026, 11, 1, 8, 0, tzinfo=DENVER), 0) is None


def test_pause_for_an_hour_across_the_spring_clock_change():
    before = datetime(2027, 3, 14, 1, 30, tzinfo=DENVER)  # clocks jump from 2:00 to 3:00 AM
    until = rules.pause_end("60", before, sched())
    assert until.astimezone(UTC) - before.astimezone(UTC) <= timedelta(hours=1)


def test_timed_pause_is_compared_in_real_time_not_wall_time():
    """A pause stored in UTC must end at the same instant whatever zone the clock is read in."""
    until = datetime(2026, 10, 20, 21, 0, tzinfo=UTC)  # 3:00 PM in Denver
    s = sched(weekday_start="00:00", weekday_end="00:00", paused_until=until)
    assert rules.block_reason(s, datetime(2026, 10, 20, 14, 59, tzinfo=DENVER), 0) == "paused"
    assert rules.block_reason(s, datetime(2026, 10, 20, 15, 0, tzinfo=DENVER), 0) is None


# ---------- around midnight ----------

def test_overnight_watch_hours_on_one_day():
    s = sched(weekday_start="18:00", weekday_end="01:00")
    assert rules.block_reason(s, datetime(2026, 10, 20, 23, 59), 0) is None
    assert rules.block_reason(s, datetime(2026, 10, 20, 0, 59), 0) is None
    assert rules.block_reason(s, datetime(2026, 10, 20, 1, 0), 0) == "hours"
    assert rules.block_reason(s, datetime(2026, 10, 20, 17, 59), 0) == "hours"


def test_later_bedtime_up_to_midnight_ends_exactly_at_midnight():
    s = sched(weekday_end="23:30", later_day="2026-10-19", later_minutes=30)
    assert rules.effective_window(s, date(2026, 10, 19)) == ("07:00", "00:00")
    assert rules.block_reason(s, datetime(2026, 10, 19, 23, 59), 0) is None
    assert rules.stop_message("hours", s, datetime(2026, 10, 20, 0, 0)) == \
        "Watch time is 7:00 AM to 11:30 PM. See you then."  # the next day is back to normal


def test_later_bedtime_that_reaches_the_start_time_is_ignored():
    s = sched(weekday_start="21:00", weekday_end="20:00", later_day="2026-10-19", later_minutes=60)
    assert rules.effective_window(s, date(2026, 10, 19)) == ("21:00", "20:00")


def test_bonus_counts_on_a_no_limit_day_only_when_there_is_a_limit():
    s = sched(weekend_minutes=None, bonus_day="2026-10-24", bonus_minutes=30)
    assert rules.limit_seconds(s, date(2026, 10, 24)) is None  # no limit stays no limit
    assert rules.remaining_seconds(sched(), date(2026, 10, 19), 99999) == 0  # never negative


def test_later_bedtime_past_midnight_keeps_going_after_midnight():
    s = sched(weekday_end="23:30", later_day="2026-10-19", later_minutes=60)  # Monday, until 12:30 AM
    assert rules.block_reason(s, datetime(2026, 10, 20, 0, 15), 0) is None


def test_friday_overnight_hours_keep_going_after_midnight():
    s = sched(weekday_start="18:00", weekday_end="01:00", weekend_start="08:00", weekend_end="21:00")
    assert rules.block_reason(s, datetime(2026, 10, 24, 0, 30), 0) is None  # Saturday 12:30 AM
    assert rules.block_reason(s, datetime(2026, 10, 24, 1, 0), 0) == "hours"  # Friday night is over
    assert rules.block_reason(s, datetime(2026, 10, 24, 8, 0), 0) is None  # Saturday's own hours


def test_overnight_hours_after_a_day_that_ends_early_do_not_open_after_midnight():
    """Monday 6 PM to 1 AM belongs to Monday night, so it never opens 12 AM to 1 AM after Sunday's 9 PM bedtime."""
    s = sched(weekday_start="18:00", weekday_end="01:00", weekend_start="08:00", weekend_end="21:00")
    assert rules.block_reason(s, datetime(2026, 10, 19, 0, 30), 0) == "hours"  # Monday 12:30 AM, Sunday night
    assert rules.block_reason(s, datetime(2026, 10, 20, 0, 30), 0) is None  # Tuesday 12:30 AM, Monday night
    assert rules.block_reason(s, datetime(2026, 10, 25, 0, 30), 0) == "hours"  # Sunday 12:30 AM, Saturday night


def test_same_day_hours_are_unchanged_around_midnight():
    s = sched()
    assert rules.block_reason(s, datetime(2026, 10, 20, 6, 59), 0) == "hours"
    assert rules.block_reason(s, datetime(2026, 10, 20, 7, 0), 0) is None
    assert rules.block_reason(s, datetime(2026, 10, 20, 19, 59), 0) is None
    assert rules.block_reason(s, datetime(2026, 10, 20, 20, 0), 0) == "hours"
    assert rules.block_reason(s, datetime(2026, 10, 21, 0, 0), 0) == "hours"
    any_time = sched(weekday_start="00:00", weekday_end="00:00", weekend_start="00:00", weekend_end="00:00")
    assert rules.block_reason(any_time, datetime(2026, 10, 24, 0, 0), 0) is None
    assert rules.block_reason(any_time, datetime(2026, 10, 23, 23, 59), 0) is None


def test_window_ending_exactly_at_midnight_does_not_spill_into_the_next_day():
    s = sched(weekday_start="18:00", weekday_end="00:00", weekend_start="08:00", weekend_end="21:00")
    assert rules.block_reason(s, datetime(2026, 10, 23, 23, 59), 0) is None  # Friday 11:59 PM
    assert rules.block_reason(s, datetime(2026, 10, 24, 0, 0), 0) == "hours"  # Saturday 12:00 AM
    assert rules.block_reason(s, datetime(2026, 10, 24, 0, 30), 0) == "hours"


def test_later_bedtime_past_midnight_ends_on_time():
    s = sched(weekday_end="23:30", later_day="2026-10-19", later_minutes=60)
    assert rules.effective_window(s, date(2026, 10, 19)) == ("07:00", "00:30")
    assert rules.block_reason(s, datetime(2026, 10, 20, 0, 29), 0) is None
    assert rules.block_reason(s, datetime(2026, 10, 20, 0, 30), 0) == "hours"
    assert rules.block_reason(s, datetime(2026, 10, 21, 0, 15), 0) == "hours"  # only the night it was granted


def test_later_bedtime_that_would_pass_the_start_time_is_ignored():
    """00:00 to 23:00 plus two hours must not shrink the day to 12 AM to 1 AM."""
    s = sched(weekday_start="00:00", weekday_end="23:00", later_day="2026-10-19", later_minutes=120)
    assert rules.effective_window(s, date(2026, 10, 19)) == ("00:00", "23:00")
    assert rules.block_reason(s, datetime(2026, 10, 19, 12, 0), 0) is None


def test_overnight_hours_across_the_fall_clock_change():
    s = sched(weekend_start="18:00", weekend_end="01:00")  # Saturday Oct 31 night; clocks fall back at 2 AM
    first = datetime.fromtimestamp(at(2026, 11, 1, 0, 59), DENVER)
    assert rules.block_reason(s, first, 0) is None
    assert rules.block_reason(s, datetime.fromtimestamp(at(2026, 11, 1, 1, 0), DENVER), 0) == "hours"
    repeated = datetime.fromtimestamp(first.timestamp() + 2 * 3600, DENVER)  # 1:59 AM again, now MST
    assert (repeated.hour, repeated.fold) == (1, 1)
    assert rules.block_reason(s, repeated, 0) == "hours"


def test_overnight_hours_across_the_spring_clock_change():
    s = sched(weekend_start="18:00", weekend_end="02:30")  # Saturday Mar 13 night; 2:00 AM jumps to 3:00 AM
    before = datetime.fromtimestamp(at(2027, 3, 14, 1, 59), DENVER)
    assert rules.block_reason(s, before, 0) is None
    after = datetime.fromtimestamp(before.timestamp() + 60, DENVER)
    assert after.hour == 3 and rules.block_reason(s, after, 0) == "hours"


def test_timed_pause_that_spans_midnight():
    s = sched(weekday_start="18:00", weekday_end="01:00")
    friday = datetime(2026, 10, 23, 23, 30, tzinfo=DENVER)
    s.paused_until = rules.pause_end("60", friday, s).astimezone(UTC)
    assert rules.block_reason(s, datetime(2026, 10, 24, 0, 29, tzinfo=DENVER), 0) == "paused"
    assert rules.block_reason(s, datetime(2026, 10, 24, 0, 30, tzinfo=DENVER), 0) is None  # still Friday night


def test_pause_until_tomorrow_after_midnight_on_a_late_night():
    s = sched(weekday_start="18:00", weekday_end="01:00")
    late = datetime(2026, 10, 24, 0, 30, tzinfo=DENVER)  # Saturday 12:30 AM, still Friday night
    assert rules.pause_end("tomorrow", late, s) == datetime(2026, 10, 24, 8, 0, tzinfo=DENVER)  # not Sunday
    assert rules.pause_end("tomorrow", late, sched()) == datetime(2026, 10, 25, 8, 0, tzinfo=DENVER)


def test_keeper_lets_friday_night_run_past_midnight(tmp_path, monkeypatch):
    app, fake = denver_app(tmp_path, monkeypatch)
    _, kid_id = active_kid(app, fake)
    app.state.db.update_kid(kid_id, weekday_start="18:00", weekday_end="01:00", weekend_start="08:00",
                            weekend_end="21:00", weekday_minutes=None, weekend_minutes=None)
    fake.live = [Session("s1", KID.id, "movie", "Cloud Garden", "playing", "TV")]
    app.state.enforcer.tick(at(2026, 10, 24, 0, 30))  # Saturday 12:30 AM
    assert stops(fake) == []
    app.state.enforcer.tick(at(2026, 10, 24, 1, 0))
    (stop,) = stops(fake)
    assert "Watch time is 8:00 AM to 9:00 PM" in stop[2]  # Saturday's hours are next


# ---------- controls in the app ----------

def test_pause_controls_switch_between_kinds_and_resume_clears_both(env):
    app, fake = env
    db = app.state.db
    c, kid_id = active_kid(app, fake)
    tok = csrf(c, f"/kids/{kid_id}")
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "pause"})
    assert db.kid(kid_id)["paused"] == 1 and db.kid(kid_id)["paused_until"] is None
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "pause:60"})
    kid = db.kid(kid_id)
    assert kid["paused"] == 0 and kid["paused_until"].endswith("+00:00")
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "resume"})
    kid = db.kid(kid_id)
    assert kid["paused"] == 0 and kid["paused_until"] is None
    assert c.get(f"/kids/{kid_id}/status.json").json()["paused"] is False


def test_expired_timed_pause_no_longer_counts_as_paused(env):
    app, fake = env
    db = app.state.db
    c, kid_id = active_kid(app, fake)
    db.update_kid(kid_id, paused_until="2000-01-01T00:00:00+00:00", weekday_start="00:00", weekday_end="00:00",
                  weekend_start="00:00", weekend_end="00:00")
    st = c.get(f"/kids/{kid_id}/status.json").json()
    assert st["paused"] is False and st["blocked"] is None
    tok = csrf(c, f"/kids/{kid_id}")
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "pause:30", "back": f"/kids/{kid_id}"})
    assert 'value="resume">Undo</button>' in c.get(f"/kids/{kid_id}").text  # an old pause doesn't block Undo


def test_resume_everyone_also_ends_timed_pauses(env):
    app, fake = env
    db = app.state.db
    c, kid_id = active_kid(app, fake)
    c.post(f"/kids/{kid_id}/control", data={"csrf": csrf(c), "action": "pause:60"})
    c.post("/kids/pause-all", data={"csrf": csrf(c), "action": "resume"})
    assert db.kid(kid_id)["paused_until"] is None


def test_extra_time_and_later_bedtime_are_capped_and_reset_each_day(env):
    app, fake = env
    db = app.state.db
    c, kid_id = active_kid(app, fake)
    tok = csrf(c, f"/kids/{kid_id}")
    for _ in range(12):
        c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "bonus:60"})
    for _ in range(5):
        c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "later:60"})
    kid = db.kid(kid_id)
    assert kid["bonus_minutes"] == 600 and kid["later_minutes"] == 180
    db.update_kid(kid_id, bonus_day="2000-01-01", bonus_minutes=45, later_day="2000-01-01", later_minutes=60)
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "bonus:15"})
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "later:30"})
    kid = db.kid(kid_id)
    today = app.state.svc.today()
    assert (kid["bonus_day"], kid["bonus_minutes"]) == (today, 15)  # yesterday's extra time doesn't carry over
    assert (kid["later_day"], kid["later_minutes"]) == (today, 30)


def test_controls_use_the_local_date(tmp_path, monkeypatch):
    app, fake = denver_app(tmp_path, monkeypatch)
    c, kid_id = active_kid(app, fake)
    svc = app.state.svc
    late = datetime(2026, 10, 20, 22, 30, tzinfo=DENVER)  # already Oct 21 in UTC
    monkeypatch.setattr(svc, "now_local", lambda: late)
    c.post(f"/kids/{kid_id}/control", data={"csrf": csrf(c, f"/kids/{kid_id}"), "action": "bonus:15"})
    assert app.state.db.kid(kid_id)["bonus_day"] == "2026-10-20"
    c.post(f"/kids/{kid_id}/control", data={"csrf": csrf(c, f"/kids/{kid_id}"), "action": "pause:tomorrow"})
    until = datetime.fromisoformat(app.state.db.kid(kid_id)["paused_until"])
    assert until == datetime(2026, 10, 21, 7, 0, tzinfo=DENVER)


def test_control_back_link_cannot_leave_the_site(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    tok = csrf(c, f"/kids/{kid_id}")
    for back in ("https://evil.example/", "//evil.example", "/kids/999", "javascript:alert(1)"):
        r = c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "resume", "back": back})
        assert r.status_code == 303 and r.headers["location"] == f"/kids/{kid_id}", back
    r = c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "resume", "back": "/home"})
    assert r.headers["location"] == "/home"


def test_unknown_control_is_refused_and_not_logged(env):
    app, fake = env
    db = app.state.db
    c, kid_id = active_kid(app, fake)
    before = db.one("SELECT COUNT(*) n FROM audit")["n"]
    for action in ("bonus:600", "pause:1440", "later:999", "", "delete"):
        assert c.post(f"/kids/{kid_id}/control", data={"csrf": csrf(c), "action": action}).status_code == 400
    assert db.one("SELECT COUNT(*) n FROM audit")["n"] == before
    assert db.kid(kid_id)["bonus_minutes"] == 0


def test_schedule_form_rejects_out_of_range_values(env):
    app, fake = env
    db = app.state.db
    c, kid_id = active_kid(app, fake)
    tok = csrf(c, f"/kids/{kid_id}")
    good = {"csrf": tok, "weekday_minutes": "90", "weekend_minutes": "none", "weekday_start": "07:00",
            "weekday_end": "20:00", "weekend_start": "08:00", "weekend_end": "21:00"}
    for bad in ({"weekday_minutes": "1441"}, {"weekday_minutes": "-5"}, {"weekday_start": "24:00"},
                {"weekend_end": "7:00"}, {"weekday_end": "20:60"}, {"break_until": "next week"}):
        c.post(f"/kids/{kid_id}/schedule", data={**good, **bad})
        assert db.kid(kid_id)["weekday_minutes"] == 120, bad  # unchanged from the default
    c.post(f"/kids/{kid_id}/schedule", data=good)
    kid = db.kid(kid_id)
    assert kid["weekday_minutes"] == 90 and kid["weekend_minutes"] is None and kid["weekend_start"] == "08:00"
    c.post(f"/kids/{kid_id}/schedule", data={**good, "weekday_minutes": "0"})
    assert db.kid(kid_id)["weekday_minutes"] == 0  # zero means "no watching", not "no limit"


def test_schedule_form_rejects_impossible_break_dates(env):
    app, fake = env
    db = app.state.db
    c, kid_id = active_kid(app, fake)
    c.post(f"/kids/{kid_id}/schedule", data={"csrf": csrf(c), "weekday_minutes": "90", "weekday_start": "07:00",
                                             "weekday_end": "20:00", "weekend_start": "08:00", "weekend_end": "21:00",
                                             "break_until": "2026-19-39"})
    assert db.kid(kid_id)["break_until"] is None
    assert "isn&#39;t a real date" in c.get(f"/kids/{kid_id}/schedule").text
    for bad in ("2026-02-30", "2027-02-29", "2026-00-10", "20261101", "2026-11-1"):
        c.post(f"/kids/{kid_id}/schedule", data={"csrf": csrf(c), "weekday_minutes": "90", "weekday_start": "07:00",
                                                 "weekday_end": "20:00", "weekend_start": "08:00",
                                                 "weekend_end": "21:00", "break_until": bad})
        assert db.kid(kid_id)["break_until"] is None, bad
    c.post(f"/kids/{kid_id}/schedule", data={"csrf": csrf(c), "weekday_minutes": "90", "weekday_start": "07:00",
                                             "weekday_end": "20:00", "weekend_start": "08:00", "weekend_end": "21:00",
                                             "break_until": "2028-02-29"})
    assert db.kid(kid_id)["break_until"] == "2028-02-29"  # a real leap day is fine
