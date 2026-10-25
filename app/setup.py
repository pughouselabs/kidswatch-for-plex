"""Non-secret owner setup settings and atomic, resumable setup saves.

Integration: call migrate(conn) inside DB._migrate's transaction, before creating
client_id. Use effective_kid_sections(conn, cfg.kid_sections) wherever library
policy is read: a saved UI selection overrides PK_KID_SECTIONS; until then the
environment is the default. Never change the read-only Plex mount or host files.

Trust changes apply only to future connections. OFF to ON never approves pending
children; ON to OFF never revokes active children. Existing pending children must
remain visible for explicit owner review. Connection code must read the policy
and its version under the same transaction that authorizes activation, and audit
that version. An in-progress connection claim prevents a policy change.

Rollback: unregister the router and revert the code; additive settings/tables
can stay in place. Reverting settings does not undo previously granted shares.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time

import httpx

from .db import now_iso

TRUSTED_PARENTS = "setup.trusted_parents"
POLICY_VERSION = "setup.policy_version"
KID_SECTIONS = "setup.kid_sections"
TEST_TTL = 1800


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Participate in the caller's transaction; never commit it."""
    conn.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def migrate(conn: sqlite3.Connection) -> None:
    """Idempotent and additive. Run before a new database receives client_id."""
    conn.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("""CREATE TABLE IF NOT EXISTS setup_drafts(
        owner_id INTEGER PRIMARY KEY,
        sections TEXT NOT NULL,
        revision TEXT NOT NULL,
        test_receiver TEXT,
        test_sent_at REAL
    )""")
    if get_setting(conn, "setup.schema_version") is not None:
        return
    # A previous app start, even with no children yet, is an existing install.
    existing = bool(conn.execute("SELECT 1 FROM settings LIMIT 1").fetchone())
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for name in ("parents", "kids", "audit", "sessions", "requests"):
        if name in tables and conn.execute(f"SELECT 1 FROM {name} LIMIT 1").fetchone():
            existing = True
    conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                 (TRUSTED_PARENTS, "0" if existing else "1"))
    conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (POLICY_VERSION, "1"))
    set_setting(conn, "setup.schema_version", "1")


def trusted_parents_enabled(conn: sqlite3.Connection) -> bool:
    """Missing/invalid settings retain the old, stricter behavior."""
    return get_setting(conn, TRUSTED_PARENTS, "0") == "1"


def effective_kid_sections(conn: sqlite3.Connection, defaults) -> tuple[str, ...]:
    value = get_setting(conn, KID_SECTIONS)
    if value is None:
        return tuple(defaults)
    # Corrupt saved policy fails closed, rather than expanding back to defaults.
    try:
        sections = json.loads(value)
        if isinstance(sections, list) and sections and all(isinstance(k, str) and k for k in sections):
            return tuple(dict.fromkeys(sections))
    except (ValueError, TypeError):
        pass
    return ()


def library_policy_warning(conn: sqlite3.Connection, defaults) -> str | None:
    """Owner guidance for a closed library policy, without exposing family data in readiness."""
    sections = set(effective_kid_sections(conn, defaults))
    if not sections:
        return ("Library access needs attention. No permitted libraries are selected, or the saved choices "
                "could not be read. Review and save your library choices in Owner setup.")
    for kid in conn.execute("SELECT sections,sync_error FROM kids WHERE plex_id IS NOT NULL "
                            "AND status IN ('requested','active')"):
        if (not sections.intersection((kid[0] or "").split(","))
                or "No permitted libraries:" in (kid[1] or "")):
            return ("Library access needs attention. One or more connected children have no permitted libraries "
                    "or need to reconnect after a library change. Review Owner setup and each child's library "
                    "settings, then reconnect the affected Plex accounts.")
    return None


def available_libraries(plex, *, fresh: bool = False) -> dict[str, str]:
    titles = plex.section_titles()
    kinds = plex.readiness_sections() if fresh else {k: plex.section_type(k) for k in titles}
    return {str(k): title for k, title in titles.items() if kinds.get(k) in ("movie", "show")}


def validate_sections(sections, available) -> list[str]:
    sections = list(dict.fromkeys(sections))
    if not sections:
        raise ValueError("Choose at least one movie or TV library.")
    if any(key not in available for key in sections):
        raise ValueError("Choose only the movie and TV libraries shown here.")
    return sections


def save_draft(conn: sqlite3.Connection, owner_id: int, sections) -> None:
    """Drafts survive restarts but do not change the running library policy."""
    conn.execute("INSERT INTO setup_drafts(owner_id,sections,revision) VALUES(?,?,?) "
                 "ON CONFLICT(owner_id) DO UPDATE SET sections=excluded.sections, "
                 "revision=excluded.revision,test_receiver=NULL,test_sent_at=NULL",
                 (owner_id, json.dumps(sections), secrets.token_urlsafe(24)))


def receiver_fingerprint(notifier) -> str | None:
    # Use the existing read-in-place helper; persist only a one-way fingerprint.
    url = notifier._url()
    return hashlib.sha256(url.encode()).hexdigest() if url else None


def send_test_notification(notifier) -> bool:
    """Unlike the normal fire-and-forget sender, report transport failure here.

    HTTP success is not proof of receipt; the owner must still confirm receipt.
    Receiver credentials and exception text must never enter logs or pages.
    """
    url = notifier._url()
    if not url:
        return False
    try:
        httpx.post(url, json={"title": "KidsWatch for Plex: setup test",
                             "message": "Confirm you received this test on your private device in KidsWatch.",
                             "url": notifier.public_url + "/setup/notifications"},
                   timeout=10, follow_redirects=False).raise_for_status()
    except (httpx.HTTPError, ValueError):
        return False
    return True


def finish_setup(conn: sqlite3.Connection, *, owner_id: int, actor: str, revision: str,
                 available, receiver: str | None, confirmed: bool, trusted: bool) -> None:
    """Caller holds BEGIN IMMEDIATE; settings, audit and draft removal are atomic."""
    row = conn.execute("SELECT sections,revision,test_receiver,test_sent_at FROM setup_drafts WHERE owner_id=?",
                       (owner_id,)).fetchone()
    if not row or not secrets.compare_digest(row[1], revision):
        raise ValueError("Setup changed in another window. Review your libraries and try again.")
    sections = validate_sections(json.loads(row[0]), available)
    if not confirmed or not receiver or row[2] != receiver or not row[3] or not 0 <= time.time() - row[3] <= TEST_TTL:
        raise ValueError("Send a test and confirm you received it on your private device.")
    before = trusted_parents_enabled(conn)
    if before != trusted:
        if conn.execute("SELECT 1 FROM kids WHERE connection_claim IS NOT NULL LIMIT 1").fetchone():
            raise ValueError("A child is connecting. Wait for the connection to finish before changing this setting.")
        set_setting(conn, POLICY_VERSION, str(int(get_setting(conn, POLICY_VERSION, "1")) + 1))
    set_setting(conn, KID_SECTIONS, json.dumps(sections))
    set_setting(conn, TRUSTED_PARENTS, "1" if trusted else "0")
    set_setting(conn, "setup.notification_method", "configured")
    set_setting(conn, "setup.notifications_confirmed_at", now_iso())
    set_setting(conn, "setup.completed_at", now_iso())
    conn.execute("INSERT INTO audit(at,actor,action,detail) VALUES(?,?,?,?)",
                 (now_iso(), actor, "setup_saved", json.dumps({"sections": sections, "trusted_parents": trusted,
                   "previous_trusted_parents": before, "policy_version": get_setting(conn, POLICY_VERSION)})))
    conn.execute("DELETE FROM setup_drafts WHERE owner_id=?", (owner_id,))
