"""The screen-time keeper: counts kids' watch time and stops videos when time is up."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from . import rules
from .db import now_iso
from .plex import PlexError

log = logging.getLogger("kidswatch.enforcer")

RETRY_EVERY = 300  # seconds between retries of settings that Plex rejected


class Enforcer:
    def __init__(self, svc, poll_seconds: int):
        self.svc = svc
        self.db = svc.db
        self.plex = svc.plex
        self.poll = poll_seconds
        self.live: dict[int, list[dict]] = {}  # kid id -> what they are watching right now
        self.last_ok: float | None = None
        self.last_error: str | None = None
        self._last_tick: float | None = None
        self._stopped_sessions: dict[str, float] = {}
        self._warned: set[str] = set()
        self._last_retry = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.on_alert = None  # set by the app: called with (title, message) for the owner's phone
        self._failures = 0
        self._alerted = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="enforcer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def healthy(self) -> bool:
        return self.last_ok is not None and time.time() - self.last_ok < self.poll * 6

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick(time.time())
                self.last_ok = time.time()
                self.last_error = None
                self._healthy_again()
            except PlexError as e:
                self.last_error = str(e)
                log.warning("tick failed: %s", e)
                self._failed()
            except Exception:  # keep the keeper alive no matter what
                self.last_error = "unexpected error"
                log.exception("tick crashed")
                self._failed()
            if time.time() - self._last_retry > RETRY_EVERY:
                self._last_retry = time.time()
                try:
                    self.svc.periodic()
                except Exception:
                    log.exception("retry failed")
            self._stop.wait(self.poll)

    ALERT_AFTER = 600  # seconds of failures before the owner hears about it

    def _failed(self) -> None:
        self._failures += 1
        if not self._alerted and self._failures * self.poll >= self.ALERT_AFTER and self.on_alert:
            self._alerted = True
            self.on_alert("KidsWatch for Plex: screen time is not being enforced",
                          f"The keeper can't reach Plex ({self.last_error}). Kids' limits are paused until it can.")

    def _healthy_again(self) -> None:
        if self._alerted and self.on_alert:
            self.on_alert("KidsWatch for Plex: screen time is back", "The keeper reached Plex again.")
        self._failures, self._alerted = 0, False

    def tick(self, now: float) -> None:
        kids = {k["plex_id"]: k for k in self.db.q(
            "SELECT * FROM kids WHERE status IN ('requested','active') AND plex_id IS NOT NULL")}
        sessions = self.plex.sessions() if kids else []
        elapsed = 0 if self._last_tick is None else int(min(now - self._last_tick, self.poll * 3))
        self._last_tick = now
        local = datetime.fromtimestamp(now, self.svc.tz)
        day = local.date().isoformat()

        by_kid: dict[int, list] = {}
        for s in sessions:
            kid = kids.get(s.user_id)
            if kid is None or s.media_type not in rules.VIDEO_TYPES:
                continue
            by_kid.setdefault(kid["id"], []).append(s)

        live = {}
        for kid_id, kid_sessions in by_kid.items():
            live[kid_id] = [{"title": s.title, "state": s.state, "player": s.player} for s in kid_sessions]
            if elapsed and any(s.state == "playing" for s in kid_sessions):
                self.db.add_usage(kid_id, day, elapsed)
        self.live = live

        for kid in kids.values():
            kid_sessions = by_kid.get(kid["id"])
            if not kid_sessions:
                continue
            schedule = rules.Schedule.from_row(kid)
            reason = rules.block_reason(schedule, local, self.db.used(kid["id"], day))
            if not reason:
                continue
            message = rules.stop_message(reason, schedule, local)
            for s in kid_sessions:
                if not s.session_id:
                    if s.title not in self._warned:
                        self._warned.add(s.title)
                        log.warning("cannot stop a session without an id (kid %s): %s", kid["id"], s.title)
                        if self.on_alert:
                            self.on_alert("KidsWatch for Plex: a video couldn't be stopped",
                                          f"{kid['name']} is watching {s.title} on {s.player or 'a device'}, "
                                          "and Plex won't let the app stop it there.")
                    continue
                try:
                    self.plex.terminate(s.session_id, message)
                except PlexError as e:  # one stubborn session must not keep the other kids' videos running
                    log.warning("could not stop a video (kid %s): %s", kid["id"], e)
                    continue
                if s.session_id not in self._stopped_sessions:
                    self.db.x("INSERT INTO stops(kid_id,at,reason,title,session_id) VALUES(?,?,?,?,?)",
                              (kid["id"], now_iso(), reason, s.title, s.session_id))
                self._stopped_sessions[s.session_id] = now
        # forget old stop records so the set stays small
        self._stopped_sessions = {k: v for k, v in self._stopped_sessions.items() if now - v < 3600}
