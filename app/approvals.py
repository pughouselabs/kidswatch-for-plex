"""Parent approval and the connection-time trusted-parent policy.

Integration:
* Call migrate(conn) from DB._migrate, inside its existing transaction.
* Include approval_routes.router before the legacy /waiting handlers (or
  replace those waiting handlers). Parent decisions POST to
  /admin/requests/{req_id}/decision. In the legacy admin_request handler,
  redirect kind='access' to /admin/requests/{req_id} without deciding it;
  keep its existing child/ordinary request handling. Link the parent inbox
  from Admin and route parent cards to the new authenticated detail page.
* In Service._connect, immediately after the successful update_kid and
  connected audit, if the ORIGINAL kid status was draft, call
  status = activate_child_on_connect(self, kid_id, "system", notifier,
                                     claim=claim).
  Do this before the status-dependent success message/queue_sync branch and
  while connection_claim is still held. Pass the application's notifier.
  Keep all existing account, share, invitation and online-source safety checks.
* The link_done handler must use that returned status to skip add_child
  requests for active children, and suppress its second child-connected
  notification in that case. Keep strict-mode add_child requests unchanged.
  Only show child approval language for requested children. Existing pending
  children must never be activated merely by turning the setting on.
* Send parent-request notifications with notify_parent_request(db, notifier,
  req_id); this supplies the authenticated detail URL and private preview copy.

No code here creates a Plex account or stores a child authorization token.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import closing

import httpx

from . import rules
from .db import SHARED_TRUST_MAX, now_iso
from .notify import Notifier
from .plex import PlexError
from .setup import POLICY_VERSION, get_setting, trusted_parents_enabled


def migrate(conn: sqlite3.Connection) -> None:
    """Add policy receipts; never change a parent's or child's existing access."""
    conn.execute("""CREATE TABLE IF NOT EXISTS approval_connections(
        kid_id INTEGER PRIMARY KEY REFERENCES kids(id) ON DELETE CASCADE,
        claim_hash TEXT,
        trusted INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )""")
    # A previously pending child stays pending even if reconnected after upgrade.
    conn.execute("INSERT OR IGNORE INTO approval_connections(kid_id,claim_hash,trusted,created_at) "
                 "SELECT id,NULL,0,? FROM kids WHERE status='requested'", (now_iso(),))


def request_version(row) -> str:
    """Detect identity/message changes between opening a request and deciding it."""
    value = [row[k] for k in ("id", "parent_id", "kind", "kid_id", "message", "created_at")]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def accepted_parent(conn, parent_id: int) -> bool:
    row = conn.execute("SELECT user_ids,observed_at FROM sharing_state WHERE id=1").fetchone()
    return bool(row and row[0] is not None and time.time() - row[1] <= SHARED_TRUST_MAX
                and parent_id in json.loads(row[0]))


def _audit(conn, actor: str, action: str, **detail) -> None:
    conn.execute("INSERT INTO audit(at,actor,action,detail) VALUES(?,?,?,?)",
                 (now_iso(), actor, action, json.dumps(detail)))


def _notify(db, notifier, title: str, body: str, path: str, **detail) -> None:
    def failed():
        # Receiver exception text can contain credentials. Never persist it.
        db.audit("system", "approval_notification_failed", **detail)

    try:
        if notifier is None:
            raise RuntimeError("No notifier")
        if isinstance(notifier, Notifier):
            # Notifier.send dispatches in a background thread and only logs
            # failures. This identical payload needs a durable failure receipt.
            # Keep the receiver in memory, never in an audit or exception text.
            receiver = notifier._url()
            if not receiver:
                failed()
                return
            payload = {"title": title, "message": body, "url": notifier.public_url + path}

            def deliver():
                try:
                    httpx.post(receiver, json=payload, timeout=10).raise_for_status()
                except Exception:
                    failed()

            threading.Thread(target=deliver, name="approval-notification", daemon=True).start()
            return
        notifier.send(title, body, path)
    except Exception:
        failed()


def notify_parent_request(db, notifier, req_id: int) -> None:
    row = db.one("SELECT * FROM requests WHERE id=? AND kind='access' AND kid_id IS NULL", (req_id,))
    if row and row["status"] == "open":
        _notify(db, notifier, "KidsWatch for Plex: parent access request",
                "A parent access request needs your review.", f"/admin/requests/{req_id}", request=req_id)


def activate_child_on_connect(svc, kid_id: int, actor: str = "system", notifier=None, *, claim: str) -> str:
    """Activate a NEW connection only, under its still-held connection claim.

    Returns active/requested for the caller's success copy and notification
    branching. Strict connections retain today's empty, restricted share. A
    receipt makes retries idempotent and freezes the chosen policy. The caller
    must not use this as a general approval function for previously pending kids.
    """
    db = svc.db
    kid = db.kid(kid_id)
    if not kid or not claim or kid["connection_claim"] != claim or not kid["plex_id"]:
        raise PlexError("This child connection is no longer current.")
    previous = db.one("SELECT * FROM approval_connections WHERE kid_id=?", (kid_id,))
    if previous:
        return kid["status"]
    if kid["status"] not in ("requested", "active") or svc.unsafe_share_pending(kid):
        raise PlexError("This child connection needs attention before activation.")

    # Always refresh the accepted parent list; an outage cannot expand access.
    if not svc.is_owner_kid(kid):
        svc.read_shared()
    # Reuse the service's restricted-share application, then require readback
    # before title labels can make any content visible.
    if not svc.apply(kid_id, actor):
        raise PlexError("Plex could not confirm this child's restrictions. Try again.")
    share = svc.plex.shares(fresh=True).get(kid["plex_id"])
    if not share or not svc.share_matches(share, svc.kid_sections(kid), rules.build_filters(kid_id)):
        raise PlexError("Plex did not confirm this child's restrictions. Try again.")

    with closing(db._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT * FROM kids WHERE id=?", (kid_id,)).fetchone()
        if (not current or current["connection_claim"] != claim or current["plex_id"] != kid["plex_id"]
                or current["parent_id"] != kid["parent_id"] or current["status"] not in ("requested", "active")
                or svc.unsafe_share_pending(current)):
            raise PlexError("This child connection is no longer current.")
        previous = conn.execute("SELECT 1 FROM approval_connections WHERE kid_id=?", (kid_id,)).fetchone()
        if previous:
            return current["status"]
        owner_kid = svc.is_owner_kid(current)
        parent = conn.execute("SELECT enabled FROM parents WHERE plex_id=?", (current["parent_id"],)).fetchone()
        if not owner_kid and (not parent or not parent[0] or not accepted_parent(conn, current["parent_id"])):
            raise PlexError("The parent no longer has access to connect this child.")
        trusted = bool(trusted_parents_enabled(conn))
        status = "active" if owner_kid or trusted else "requested"
        conn.execute("INSERT INTO approval_connections(kid_id,claim_hash,trusted,created_at) VALUES(?,?,?,?)",
                     (kid_id, hashlib.sha256(claim.encode()).hexdigest(), int(trusted), now_iso()))
        conn.execute("UPDATE kids SET status=?,updated_at=? WHERE id=?", (status, now_iso(), kid_id))
        _audit(conn, actor, "child_connection_policy", kid=kid_id, parent=current["parent_id"],
               trusted=trusted, policy_version=int(get_setting(conn, POLICY_VERSION, "1")), status=status)
    if status == "active":
        try:
            svc.queue_sync(kid_id)
        except Exception:
            db.update_kid(kid_id, sync_state="error", sync_error="Titles: Sync could not be queued")
            db.audit("system", "titles_queue_failed", kid=kid_id)
        _notify(db, notifier, "KidsWatch for Plex: child connected",
                "An approved parent connected a child. Open child management to pause or remove access.",
                f"/admin/children/{kid_id}", kid=kid_id)
    return status
