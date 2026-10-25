"""Identity-bound invitations to request site access, never to grant Plex access.

Integration: call migrate(conn) from DB._migrate inside its transaction. The
table is additive and can remain in place when rolling the feature back.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass

from .db import now_iso

EXPIRY_DAYS = (1, 7, 30)
FRESH_SECONDS = 30


def migrate(conn: sqlite3.Connection) -> None:
    """Idempotent, additive migration; do not commit the caller's transaction."""
    conn.execute("""CREATE TABLE IF NOT EXISTS parent_invitations(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token_hash TEXT NOT NULL UNIQUE,
        owner_id INTEGER NOT NULL,
        intended_plex_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        expires_at REAL NOT NULL,
        revoked_at TEXT,
        claimed_at TEXT,
        claimed_by INTEGER,
        request_id INTEGER
    )""")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class InvitationError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Claim:
    request_id: int
    created: bool


class Invitations:
    def __init__(self, db, svc):
        self.db, self.svc = db, svc

    def recipients(self):
        """Fresh accepted Plex users, ordered with existing sharing observations."""
        observed, started = self.db.next_shared_observation(), time.time()
        users = self.svc.plex.shared_users()
        current = self.svc.publish_shared(users, started, observed=observed)
        owner_id = self.svc.plex.owner().id
        children = {r["plex_id"] for r in self.db.q("SELECT plex_id FROM kids WHERE status<>'removed'")}
        enabled = {r["plex_id"] for r in self.db.q("SELECT plex_id FROM parents WHERE enabled=1")}
        return {key: value for key, value in users.items()
                if key in current and key != owner_id and key not in children and key not in enabled}

    @staticmethod
    def _audit(conn, actor, action, **detail):
        conn.execute("INSERT INTO audit(at,actor,action,detail) VALUES(?,?,?,?)",
                     (now_iso(), str(actor), action, json.dumps(detail)))

    @staticmethod
    def _eligible(conn, plex_id):
        snapshot = conn.execute("SELECT user_ids, observed_at FROM sharing_state WHERE id=1").fetchone()
        if (not snapshot or time.time() - snapshot["observed_at"] > FRESH_SECONDS
                or plex_id not in json.loads(snapshot["user_ids"] or "[]")):
            raise InvitationError("share_missing")
        if conn.execute("SELECT 1 FROM kids WHERE plex_id=? AND status<>'removed'", (plex_id,)).fetchone():
            raise InvitationError("wrong_identity")
        parent = conn.execute("SELECT * FROM parents WHERE plex_id=?", (plex_id,)).fetchone()
        if parent and parent["enabled"]:
            raise InvitationError("already_enabled")
        return parent

    def create(self, owner_id: int, recipient_id: int, days: int = 7):
        if days not in EXPIRY_DAYS:
            raise InvitationError("expiry")
        if owner_id != self.svc.plex.owner().id:
            raise InvitationError("owner")
        if recipient_id not in self.recipients():
            raise InvitationError("share_missing")
        token = secrets.token_urlsafe(32)
        with closing(self.db._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._eligible(conn, recipient_id)
            invite_id = conn.execute(
                "INSERT INTO parent_invitations(token_hash,owner_id,intended_plex_id,created_at,expires_at) "
                "VALUES(?,?,?,?,?)", (token_hash(token), owner_id, recipient_id, now_iso(),
                                     time.time() + days * 86400)).lastrowid
            self._audit(conn, owner_id, "parent_invitation_created", invitation=invite_id, parent=recipient_id)
        # This is the only time the capability is returned. No recoverable token is persisted.
        return self.get(invite_id, owner_id), token

    def get(self, invite_id: int, owner_id: int):
        return self.db.one("SELECT * FROM parent_invitations WHERE id=? AND owner_id=?", (invite_id, owner_id))

    def preview(self, token: str):
        """Read only. Public callers must never expose intended_plex_id or request_id."""
        return self.db.one("SELECT * FROM parent_invitations WHERE token_hash=? AND revoked_at IS NULL "
                           "AND claimed_at IS NULL AND expires_at>?", (token_hash(token), time.time()))

    def revoke(self, invite_id: int, owner_id: int) -> bool:
        if owner_id != self.svc.plex.owner().id:
            raise InvitationError("owner")
        with closing(self.db._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute("UPDATE parent_invitations SET revoked_at=? WHERE id=? AND owner_id=? "
                                   "AND revoked_at IS NULL AND claimed_at IS NULL",
                                   (now_iso(), invite_id, owner_id)).rowcount
            if changed:
                self._audit(conn, owner_id, "parent_invitation_revoked", invitation=invite_id)
        return bool(changed)

    def claim(self, token: str, parent_id: int) -> Claim:
        invite = self.preview(token)
        if not invite:
            raise InvitationError("unavailable")
        if invite["intended_plex_id"] != parent_id:
            raise InvitationError("wrong_identity")
        if parent_id not in self.recipients():
            # Distinguish enabled membership without consuming the invitation.
            parent = self.db.one("SELECT enabled FROM parents WHERE plex_id=?", (parent_id,))
            raise InvitationError("already_enabled" if parent and parent["enabled"] else "share_missing")
        with closing(self.db._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            invite = conn.execute("SELECT * FROM parent_invitations WHERE token_hash=? AND revoked_at IS NULL "
                                  "AND claimed_at IS NULL AND expires_at>?", (token_hash(token), time.time())).fetchone()
            if not invite:
                raise InvitationError("unavailable")
            if invite["intended_plex_id"] != parent_id:
                raise InvitationError("wrong_identity")
            if not self._eligible(conn, parent_id):
                raise InvitationError("sign_in")
            last = conn.execute("SELECT id,status FROM requests WHERE parent_id=? AND kind='access' "
                                "ORDER BY id DESC LIMIT 1", (parent_id,)).fetchone()
            if last and last["status"] not in ("open", "cancelled"):
                # An invite never overrides a previous owner decision or re-enables a parent.
                raise InvitationError("reviewed")
            created = not last or last["status"] != "open"
            request_id = (conn.execute("INSERT INTO requests(parent_id,kind,message,created_at) VALUES(?,?,?,?)",
                                      (parent_id, "access", "Would like to use KidsWatch for Plex.", now_iso())).lastrowid
                          if created else last["id"])
            conn.execute("UPDATE parent_invitations SET claimed_at=?,claimed_by=?,request_id=? WHERE id=?",
                         (now_iso(), parent_id, request_id, invite["id"]))
            self._audit(conn, parent_id, "parent_invitation_claimed", invitation=invite["id"], request=request_id)
            return Claim(request_id, created)
