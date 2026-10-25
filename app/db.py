"""SQLite storage. One short-lived connection per call; WAL keeps readers and the enforcer apart."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sharing_state(
  id INTEGER PRIMARY KEY CHECK(id=1),
  next_observation INTEGER NOT NULL DEFAULT 0,
  observation INTEGER NOT NULL DEFAULT 0,
  observed_at REAL NOT NULL DEFAULT 0,
  user_ids TEXT
);
INSERT OR IGNORE INTO sharing_state(id) VALUES(1);
CREATE TABLE IF NOT EXISTS parents(
  plex_id INTEGER PRIMARY KEY,
  username TEXT NOT NULL,
  title TEXT,
  thumb TEXT,
  enabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  last_login TEXT,
  requests_seen_at TEXT
);
CREATE TABLE IF NOT EXISTS kids(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  parent_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL,
  plex_id INTEGER UNIQUE,
  plex_username TEXT,
  age_level TEXT NOT NULL DEFAULT 'kids',
  mode TEXT NOT NULL DEFAULT 'age',
  sections TEXT NOT NULL DEFAULT '1,2',
  weekday_minutes INTEGER,
  weekend_minutes INTEGER,
  weekday_start TEXT NOT NULL DEFAULT '07:00',
  weekday_end TEXT NOT NULL DEFAULT '20:00',
  weekend_start TEXT NOT NULL DEFAULT '07:00',
  weekend_end TEXT NOT NULL DEFAULT '21:00',
  paused INTEGER NOT NULL DEFAULT 0,
  bonus_day TEXT,
  bonus_minutes INTEGER NOT NULL DEFAULT 0,
  block_online INTEGER NOT NULL DEFAULT 1,
  online_off INTEGER,
  plex_accepted INTEGER NOT NULL DEFAULT 0,
  titles_synced_at TEXT,
  titles_count INTEGER,
  paused_until TEXT,
  titles_seen_at TEXT,
  color INTEGER,
  later_day TEXT,
  later_minutes INTEGER NOT NULL DEFAULT 0,
  friday_weekend INTEGER NOT NULL DEFAULT 0,
  sunday_school INTEGER NOT NULL DEFAULT 0,
  break_until TEXT,
  sync_state TEXT NOT NULL DEFAULT 'ok',
  sync_error TEXT,
  applied_filters TEXT,
  connection_claim TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kid_titles(
  kid_id INTEGER NOT NULL REFERENCES kids(id) ON DELETE CASCADE,
  rating_key INTEGER NOT NULL,
  kind TEXT NOT NULL,
  section_key TEXT NOT NULL,
  item_type TEXT NOT NULL,
  title TEXT NOT NULL,
  year INTEGER,
  PRIMARY KEY(kid_id, rating_key, kind)
);
CREATE TABLE IF NOT EXISTS usage(
  kid_id INTEGER NOT NULL,
  day TEXT NOT NULL,
  seconds INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(kid_id, day)
);
CREATE TABLE IF NOT EXISTS stops(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kid_id INTEGER NOT NULL,
  at TEXT NOT NULL,
  reason TEXT NOT NULL,
  title TEXT,
  session_id TEXT
);
CREATE TABLE IF NOT EXISTS requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  parent_id INTEGER NOT NULL,
  kid_id INTEGER,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  owner_note TEXT,
  created_at TEXT NOT NULL,
  resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS kid_parents(
  kid_id INTEGER NOT NULL REFERENCES kids(id) ON DELETE CASCADE,
  parent_id INTEGER NOT NULL,
  PRIMARY KEY(kid_id, parent_id)
);
CREATE TABLE IF NOT EXISTS tracked_shares(
  kid_id INTEGER NOT NULL REFERENCES kids(id) ON DELETE CASCADE,
  plex_id INTEGER NOT NULL,
  PRIMARY KEY(kid_id, plex_id)
);
CREATE TABLE IF NOT EXISTS link_tokens(
  token TEXT PRIMARY KEY,
  kid_id INTEGER NOT NULL,
  parent_id INTEGER NOT NULL,
  expires_at REAL NOT NULL,
  used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  role TEXT NOT NULL,
  created_at REAL NOT NULL,
  last_seen REAL NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Parameters: owner ID, oldest trusted sharing observation. Used inside the atomic child writes.
PARENT_AUTHORIZED = """(parent_id=? OR (
    EXISTS (SELECT 1 FROM parents WHERE plex_id=kids.parent_id AND enabled=1)
    AND EXISTS (SELECT 1 FROM sharing_state, json_each(sharing_state.user_ids) shared
                WHERE sharing_state.id=1 AND shared.value=kids.parent_id AND observed_at>=?)))"""
SHARED_TRUST_MAX = 1800  # maximum outage grace, shared by request checks and child claims


class DB:
    def __init__(self, path: str):
        self.path = path
        with closing(self._connect()) as c, c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            c.execute("BEGIN IMMEDIATE")  # workers may start together against the same older schema
            self._migrate(c)

    @staticmethod
    def _migrate(c: sqlite3.Connection) -> None:
        """Bring a database from an older version up to date. Safe to run every start."""
        if "requests_seen_at" not in {row[1] for row in c.execute("PRAGMA table_info(parents)")}:
            c.execute("ALTER TABLE parents ADD COLUMN requests_seen_at TEXT")
        cols = {row[1] for row in c.execute("PRAGMA table_info(kids)")}
        for name, ddl in (("online_off", "INTEGER"), ("plex_accepted", "INTEGER NOT NULL DEFAULT 0"),
                          ("titles_synced_at", "TEXT"), ("titles_count", "INTEGER"),
                          ("paused_until", "TEXT"), ("titles_seen_at", "TEXT"), ("color", "INTEGER"),
                          ("later_day", "TEXT"), ("later_minutes", "INTEGER NOT NULL DEFAULT 0"),
                          ("friday_weekend", "INTEGER NOT NULL DEFAULT 0"), ("sunday_school", "INTEGER NOT NULL DEFAULT 0"),
                          ("break_until", "TEXT"), ("connection_claim", "TEXT")):
            if name not in cols:
                c.execute(f"ALTER TABLE kids ADD COLUMN {name} {ddl}")
        # v1 -> v2: allow/block titles, and the account is linked before the request goes out.
        c.execute("UPDATE kid_titles SET kind='block' WHERE kind='hide'")
        c.execute("UPDATE kid_titles SET kind='allow' WHERE kind='pick'")
        c.execute("UPDATE kids SET status='draft' WHERE status IN ('requested','approved') AND plex_id IS NULL")
        c.execute("UPDATE kids SET status='active' WHERE status='invited'")
        # Additive onboarding migrations precede create_app's client_id creation.
        from . import approvals, invitations, setup
        setup.migrate(c)
        invitations.migrate(c)
        approvals.migrate(c)
        # plex_accepted for older rows is re-read from Plex by the keeper's periodic check.

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with closing(self._connect()) as c:
            return c.execute(sql, args).fetchall()

    def one(self, sql: str, args=()) -> sqlite3.Row | None:
        with closing(self._connect()) as c:
            return c.execute(sql, args).fetchone()

    def x(self, sql: str, args=()) -> int:
        with closing(self._connect()) as c, c:
            return c.execute(sql, args).lastrowid

    def changes(self, sql: str, args=()) -> int:
        """Run a change and return how many rows it touched (for one-time claims)."""
        with closing(self._connect()) as c, c:
            return c.execute(sql, args).rowcount

    def next_shared_observation(self) -> int:
        """Allocate a persistent monotonic sequence BEFORE the Plex read, shared by all workers."""
        with closing(self._connect()) as c, c:
            return c.execute("UPDATE sharing_state SET next_observation=next_observation+1 WHERE id=1 "
                             "RETURNING next_observation").fetchone()[0]

    def publish_shared(self, ids, at: float, observed: int):
        with closing(self._connect()) as c, c:
            c.execute("UPDATE sharing_state SET observation=?, observed_at=?, user_ids=? "
                      "WHERE id=1 AND observation<?", (observed, at, json.dumps(sorted(ids)), observed))
            return c.execute("SELECT * FROM sharing_state WHERE id=1").fetchone()

    def shared_snapshot(self):
        return self.one("SELECT * FROM sharing_state WHERE id=1")

    def end_session_for_observation(self, session_key: str, observed: int) -> bool:
        """Revoke only if the negative snapshot is still current at the actual database write."""
        with closing(self._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT observation FROM sharing_state WHERE id=1").fetchone()[0] != observed:
                return False
            c.execute("DELETE FROM sessions WHERE id=?", (session_key,))
            return True

    # A claim belongs to the CHILD, not just a link. Short SQLite write transactions serialize
    # independent workers without holding a database lock across any Plex network operation.
    def claim_connection(self, kid_id: int, claim: str, owner_id: int, *, link: bool = False) -> bool:
        with closing(self._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            kid = c.execute("SELECT * FROM kids WHERE id=?", (kid_id,)).fetchone()
            if not kid:
                return False
            if link and not c.execute(
                    "SELECT 1 FROM link_tokens t WHERE token=? AND kid_id=? AND parent_id=? "
                    "AND used=0 AND expires_at>=? AND NOT EXISTS "
                    "(SELECT 1 FROM link_tokens newer WHERE newer.kid_id=t.kid_id AND newer.rowid>t.rowid)",
                    (claim, kid_id, kid["parent_id"], time.time())).fetchone():
                return False
            changed = c.execute(
                "UPDATE kids SET connection_claim=? WHERE id=? AND connection_claim IS NULL "
                "AND (status='draft' OR (status IN ('requested','active') AND plex_accepted=0)) "
                "AND " + PARENT_AUTHORIZED, (claim, kid_id, owner_id, time.time() - SHARED_TRUST_MAX)).rowcount
            if changed and link:
                c.execute("UPDATE link_tokens SET used=1 WHERE token=?", (claim,))
            return bool(changed)

    def attach_connection(self, kid_id: int, claim: str, plex_id: int, username: str, owner_id: int) -> bool:
        with closing(self._connect()) as c, c:
            changed = c.execute(
                "UPDATE kids SET plex_id=?, plex_username=?, updated_at=? WHERE id=? AND connection_claim=? "
                "AND (plex_id IS NULL OR plex_id=?) "
                "AND (status='draft' OR (status IN ('requested','active') AND plex_accepted=0)) AND " + PARENT_AUTHORIZED,
                (plex_id, username, now_iso(), kid_id, claim, plex_id, owner_id, time.time() - SHARED_TRUST_MAX)).rowcount
            if changed:
                # Keep the account discoverable even if an interrupted/out-of-band removal clears plex_id.
                c.execute("INSERT OR IGNORE INTO tracked_shares(kid_id,plex_id) VALUES(?,?)", (kid_id, plex_id))
            return bool(changed)

    def claim_kid_change(self, kid_id: int, claim: str) -> bool:
        """Use the connection lock for lifecycle changes, across processes and network waits."""
        return bool(self.changes("UPDATE kids SET connection_claim=? WHERE id=? AND connection_claim IS NULL",
                                 (claim, kid_id)))

    def finish_connection(self, kid_id: int, claim: str, previous_status: str, plex_id: int, **fields) -> bool:
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k}=?" for k in fields)
        return bool(self.changes(f"UPDATE kids SET {cols} WHERE id=? AND connection_claim=? AND status=? AND plex_id=?",
                                 (*fields.values(), kid_id, claim, previous_status, plex_id)))

    def release_connection(self, kid_id: int, claim: str, *, retry: bool = False) -> None:
        with closing(self._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            changed = c.execute("UPDATE kids SET connection_claim=NULL WHERE id=? AND connection_claim=?",
                                (kid_id, claim)).rowcount
            if changed and retry:
                c.execute("UPDATE link_tokens SET used=0 WHERE token=? AND kid_id=? AND NOT EXISTS "
                          "(SELECT 1 FROM link_tokens newer WHERE newer.kid_id=? AND "
                          "newer.rowid>(SELECT rowid FROM link_tokens WHERE token=?))",
                          (claim, kid_id, kid_id, claim))

    def rotate_link(self, kid_id: int, parent_id: int, token: str, expires_at: float) -> bool:
        with closing(self._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            if not c.execute("SELECT 1 FROM kids WHERE id=? AND parent_id=? AND connection_claim IS NULL",
                             (kid_id, parent_id)).fetchone():
                return False
            c.execute("UPDATE link_tokens SET used=1 WHERE kid_id=? AND used=0", (kid_id,))
            c.execute("INSERT INTO link_tokens(token,kid_id,parent_id,expires_at) VALUES(?,?,?,?)",
                      (token, kid_id, parent_id, expires_at))
            return True

    def set_parent_enabled(self, parent_id: int, enabled: int) -> bool:
        """Disabling refuses an in-progress connection, using the same lock as its authorization claim."""
        with closing(self._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            if not enabled and c.execute("SELECT 1 FROM kids WHERE parent_id=? AND connection_claim IS NOT NULL",
                                         (parent_id,)).fetchone():
                return False
            c.execute("UPDATE parents SET enabled=? WHERE plex_id=?", (enabled, parent_id))
            return True

    # ----- settings -----
    def setting(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, value))

    # ----- audit -----
    def audit(self, actor: str, action: str, **detail) -> None:
        self.x("INSERT INTO audit(at,actor,action,detail) VALUES(?,?,?,?)",
               (now_iso(), actor, action, json.dumps(detail, default=str) if detail else None))

    # ----- usage -----
    def add_usage(self, kid_id: int, day: str, seconds: int) -> None:
        self.x("INSERT INTO usage(kid_id,day,seconds) VALUES(?,?,?) "
               "ON CONFLICT(kid_id,day) DO UPDATE SET seconds=seconds+excluded.seconds",
               (kid_id, day, seconds))

    def used(self, kid_id: int, day: str) -> int:
        row = self.one("SELECT seconds FROM usage WHERE kid_id=? AND day=?", (kid_id, day))
        return row["seconds"] if row else 0

    # ----- kids -----
    def kid(self, kid_id: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM kids WHERE id=?", (kid_id,))

    def update_kid(self, kid_id: int, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.x(f"UPDATE kids SET {cols} WHERE id=?", (*fields.values(), kid_id))
