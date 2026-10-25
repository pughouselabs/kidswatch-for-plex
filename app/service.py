"""Kid account operations that change things on Plex. Every change is audited.

A verified connection activates immediately for trusted parents or the owner.
In strict mode it stays requested with an empty share until owner approval.
Declining or removing a child retracts the share.
"""
from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from . import rules
from .approvals import activate_child_on_connect
from .db import DB, now_iso
from .plex import PlexClient, PlexError
from .setup import effective_kid_sections

log = logging.getLogger("kidswatch.service")

SHARED = ("requested", "active")  # statuses where the child's Plex share exists
UNSAFE_SHARE_ERROR = "Unsafe share:"
NO_SECTIONS_ERROR = ("No permitted libraries: Review the libraries in Owner setup and this child's settings, "
                     "then reconnect the child's Plex account.")


class ConnectionInProgress(PlexError):
    def __init__(self):
        super().__init__("A connection or account change is in progress for this child. Wait for it to finish, then retry.")


class Service:
    def __init__(self, db: DB, plex: PlexClient, cfg):
        self.db = db
        self.plex = plex
        self.cfg = cfg
        self.tz = ZoneInfo(cfg.tz)
        self.shared_ids: set[int] | None = None  # refreshed by the keeper; None until the first read
        self.shared_at = 0.0  # when shared_ids was last read from Plex
        self._shared_lock = threading.Lock()
        self._shared_observed = 0
        self.notifier = None
        self.sync_inline = False  # tests run title syncs right away instead of in the background
        self.progress: dict[int, tuple[int, int]] = {}  # kid id -> (done, total) while a sync runs
        self._sync_lock = threading.Lock()
        self._pending: set[int] = set()
        self._cv = threading.Condition()
        self._worker: threading.Thread | None = None

    def refresh_shared(self) -> bool:
        """Read who the server is shared with. False if Plex didn't answer (the last list is kept)."""
        try:
            self.read_shared()
        except PlexError:
            return False
        return True

    def read_shared(self):
        """Order observations BEFORE starting Plex reads, including OAuth and keeper reads."""
        observed, started = self.db.next_shared_observation(), time.time()
        ids = self.plex.shared_users()
        return self.publish_shared(ids, started, observed=observed)

    def publish_shared(self, ids, at: float, *, observed: int):
        """Compare-and-set by observation start, never by response completion or cache access time.

        A late caller also receives the winning snapshot so it cannot authorize from an obsolete read.
        Wall time is retained only for the outage grace period.
        """
        with self._shared_lock:
            self._keep_shared_snapshot(self.db.publish_shared(ids, at, observed))
            return set(self.shared_ids)

    def _keep_shared_snapshot(self, row):
        if row["observation"] > self._shared_observed:
            self._shared_observed = row["observation"]
            self.shared_ids = set(json.loads(row["user_ids"]))
            self.shared_at = row["observed_at"]

    @contextmanager
    def sharing_snapshot(self):
        """Keep the authorization decision and any session revocation ordered with publication."""
        with self._shared_lock:
            self._keep_shared_snapshot(self.db.shared_snapshot())
            yield self.shared_ids, self.shared_at, self._shared_observed

    def now_local(self) -> datetime:
        return datetime.now(self.tz)

    def today(self) -> str:
        return self.now_local().date().isoformat()

    def library_sections(self) -> tuple[str, ...]:
        with closing(self.db._connect()) as conn:
            return effective_kid_sections(conn, self.cfg.kid_sections)

    def kid_sections(self, kid) -> list[str]:
        allowed = set(self.library_sections())
        return [k for k in (kid["sections"] or "").split(",") if k in allowed]

    def is_owner_kid(self, kid) -> bool:
        return kid["parent_id"] == self.plex.owner().id

    @staticmethod
    def unsafe_share_pending(kid) -> bool:
        return (kid["sync_error"] or "").startswith(UNSAFE_SHARE_ERROR)

    # ----- push share settings to Plex -----
    @staticmethod
    def share_matches(share, sections: list[str], filters: dict[str, str]) -> bool:
        return (sorted(share.sections) == sorted(sections) and not share.allow_sync
                and share.filter_movies == filters["filterMovies"] and share.filter_tv == filters["filterTelevision"])

    def check_new_share(self, plex_id: int, sections: list[str], filters: dict[str, str]) -> bool:
        """Read a just-made share back. If Plex dropped the filters, set them again; if that fails too,
        remove the share. False means removal succeeded; failure to remove raises an error."""
        if not sections:
            share = self.plex.shares(fresh=True).get(plex_id)
            if share:
                self.plex.remove_share(share.share_id)
            return False
        try:
            share = self.plex.shares(fresh=True).get(plex_id)
        except PlexError as e:
            log.warning("could not read the new share back: %s", e)
            return True  # the periodic check reads it again within a few minutes
        if share is None or self.share_matches(share, sections, filters):
            return True
        try:
            self.plex.update_share(share.share_id, sections, filters)
            return True
        except PlexError as e:
            log.warning("new share has the wrong filters and could not be fixed: %s", e)
        try:
            self.plex.remove_share(share.share_id)
        except PlexError as e:
            log.warning("could not remove the unfiltered share: %s", e)
            raise PlexError("Plex could not remove the unrestricted share") from e
        return False

    def _no_permitted_sections(self, kid, actor: str) -> bool:
        """Never ask Plex to interpret an empty library list, even while repairing drift."""
        if not (kid["sync_error"] or "").startswith(NO_SECTIONS_ERROR):
            log.warning("Child %s has no permitted libraries; retracting its Plex share", kid["id"])
            self.db.audit(actor, "library_access_blocked", kid=kid["id"])
        # Record the reason before the network call so a restart can retry safely.
        self.db.update_kid(kid["id"], sync_state="error", sync_error=NO_SECTIONS_ERROR)
        try:
            self._drop_share(kid)
        except PlexError:
            self.db.update_kid(kid["id"], sync_error=UNSAFE_SHARE_ERROR + " " + NO_SECTIONS_ERROR +
                               " Plex could not confirm removal. Remove this account's server access in Plex now; "
                               "automatic removal will keep retrying.")
            return False
        self.db.update_kid(kid["id"], plex_accepted=0)
        return False

    def apply(self, kid_id: int, actor: str) -> bool:
        """Make the child's Plex share match the saved settings. Returns True when Plex agreed."""
        kid = self.db.kid(kid_id)
        if kid and self.unsafe_share_pending(kid):
            return False  # only a confirmed retraction may clear this failure
        if not kid or kid["status"] not in SHARED or not kid["plex_id"]:
            return True
        filters = rules.build_filters(kid["id"])
        sections = self.kid_sections(kid)
        if not sections:
            return self._no_permitted_sections(kid, actor)
        try:
            share = self.plex.shares(fresh=True).get(kid["plex_id"])
            if not share:
                if (kid["sync_error"] or "").startswith(NO_SECTIONS_ERROR):
                    raise PlexError(NO_SECTIONS_ERROR)
                raise PlexError("The child's share was not found on Plex")
            if not self.share_matches(share, sections, filters):
                self.plex.update_share(share.share_id, sections, filters)
            if share.accepted and not kid["plex_accepted"]:
                self.db.update_kid(kid_id, plex_accepted=1)
        except PlexError as e:
            self.db.update_kid(kid_id, sync_state="error", sync_error=str(e))
            self.db.audit(actor, "apply_failed", kid=kid_id, error=str(e))
            return False
        self.db.update_kid(kid_id, sync_state="ok", sync_error=None, applied_filters=json.dumps(filters))
        self.db.audit(actor, "applied", kid=kid_id, sections=sections)
        return True

    # ----- title labels -----
    def choices(self, kid_id: int) -> dict[int, str]:
        return {r["rating_key"]: r["kind"] for r in self.db.q("SELECT rating_key, kind FROM kid_titles WHERE kid_id=?",
                                                               (kid_id,))}

    def visible_titles(self, kid) -> dict[str, set[int]]:
        """Per library, the titles this child may watch right now."""
        choices = self.choices(kid["id"])
        out = {}
        for sec in self.kid_sections(kid):
            out[sec] = {it.rating_key for it in self.plex.library(sec)
                        if rules.title_visible(kid["age_level"], kid["mode"], it.item_type, it.rating,
                                               choices.get(it.rating_key))}
        return out

    def sync_titles(self, kid_id: int) -> bool:
        """Label exactly the titles the child may watch (none unless the child is active)."""
        kid = self.db.kid(kid_id)
        if not kid:
            return True
        if self.unsafe_share_pending(kid):
            return False  # preserve the durable rollback error until access is revoked
        label = rules.ok_label(kid_id)
        with self._sync_lock:
            try:
                want = self.visible_titles(kid) if kid["status"] == "active" else {}
                plan = []
                # Saved child sections also need cleanup after an owner narrows
                # the library policy. They never contribute to the desired set.
                cleanup_sections = dict.fromkeys((*self.library_sections(),
                                                  *(kid["sections"] or "").split(",")))
                for sec in cleanup_sections:
                    if not sec:
                        continue
                    have = self.plex.items_with_label(sec, label)
                    target = want.get(sec, set())
                    plan.append((sec, sorted(target - have), sorted(have - target)))
                total = sum(len(add) for _, add, _ in plan) + sum(1 for _, _, rem in plan if rem)
                done = 0
                self.progress[kid_id] = (0, total)
                for sec, add, remove in plan:
                    if remove:
                        self.plex.remove_label_bulk(sec, remove, label)
                        done += 1
                    for i in range(0, len(add), 100):  # in batches, so progress still moves on big libraries
                        chunk = add[i:i + 100]
                        self.plex.add_label_bulk(sec, chunk, label)
                        done += len(chunk)
                        self.progress[kid_id] = (done, total)
            except PlexError as e:
                self.db.update_kid(kid_id, sync_state="error", sync_error=f"Titles: {e}")
                self.db.audit("system", "titles_sync_failed", kid=kid_id, error=str(e))
                return False
            finally:
                self.progress.pop(kid_id, None)
        count = sum(len(v) for v in want.values())
        fields = dict(titles_synced_at=now_iso(), titles_count=count)
        if (kid["sync_error"] or "").startswith("Titles: "):
            fields.update(sync_state="ok", sync_error=None)
        self.db.update_kid(kid_id, **fields)
        if total:
            self.db.audit("system", "titles_synced", kid=kid_id, visible=count, changes=total)
        return True

    def queue_sync(self, kid_id: int) -> None:
        if self.sync_inline:
            self.sync_titles(kid_id)
            return
        with self._cv:
            self._pending.add(kid_id)
            self._cv.notify()
        if not self._worker or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._sync_worker, name="title-sync", daemon=True)
            self._worker.start()

    def sync_pending(self, kid_id: int) -> bool:
        return kid_id in self._pending or kid_id in self.progress

    def _sync_worker(self) -> None:
        while True:
            with self._cv:
                while not self._pending:
                    self._cv.wait()
                kid_id = self._pending.pop()
            try:
                self.sync_titles(kid_id)
            except Exception:
                log.exception("title sync crashed for kid %s", kid_id)

    def periodic(self) -> None:
        """Every few minutes: retry failures, notice accepted invites, label newly added titles."""
        self.refresh_shared()
        # Failed rollback must survive a restart, including for unapproved drafts.
        for kid in self.db.q("SELECT * FROM kids WHERE sync_error LIKE ?",
                             (UNSAFE_SHARE_ERROR + "%",)):
            try:
                self._drop_share(kid)
            except PlexError as e:
                log.warning("unsafe share removal will be retried: %s", e)
                continue
            fields = dict(sync_state="ok", sync_error=None, plex_accepted=0)
            if NO_SECTIONS_ERROR in kid["sync_error"]:
                fields.update(sync_state="error", sync_error=NO_SECTIONS_ERROR)
            if kid["status"] == "draft":
                fields.update(plex_id=None, plex_username=None)
            self.db.update_kid(kid["id"], **fields)
            self.db.audit("system", "share_removed_unsafe", kid=kid["id"], plex_user=kid["plex_id"])
        for kid in self.db.q("SELECT id FROM kids WHERE sync_state='error' AND status IN ('requested','active')"):
            self.apply(kid["id"], "system")
        try:
            shares = self.plex.shares()
        except PlexError:
            shares = None
        for kid in self.db.q("SELECT id, plex_id FROM kids WHERE plex_accepted=0 AND plex_id IS NOT NULL "
                             "AND status IN ('requested','active')"):
            share = shares.get(kid["plex_id"]) if shares is not None else None
            if share and share.accepted:
                self.db.update_kid(kid["id"], plex_accepted=1)
                self.db.audit("system", "invite_accepted", kid=kid["id"])
        # Someone (or Plex) may have changed a child's share since the app set it: put the limits back.
        for kid in self.db.q("SELECT * FROM kids WHERE plex_id IS NOT NULL AND sync_state<>'error' "
                             "AND status IN ('requested','active')"):
            share = shares.get(kid["plex_id"]) if shares is not None else None
            sections = self.kid_sections(kid)
            if not sections:
                self.apply(kid["id"], "system")
            elif share and not self.share_matches(share, sections, rules.build_filters(kid["id"])):
                self.db.audit("system", "share_drift", kid=kid["id"])
                self.apply(kid["id"], "system")
        for kid in self.db.q("SELECT id FROM kids WHERE status='active'"):
            self.queue_sync(kid["id"])

    # ----- connect the child's Plex account -----
    def connect(self, kid_id: int, kid_token: str, claim: str | None = None) -> tuple[bool, str, str | None]:
        own_claim = claim is None
        if own_claim:
            claim = secrets.token_urlsafe(24)
            if not self.db.claim_connection(kid_id, claim, self.plex.owner().id):
                return False, "This child is already connected or a connection is in progress.", None
        ok = False
        try:
            if not self.db.one("SELECT 1 FROM kids WHERE id=? AND connection_claim=?", (kid_id, claim)):
                return False, "This sign-in link is no longer current.", None
            result = self._connect(kid_id, kid_token, claim)
            ok = result[0]
            return result
        finally:
            # This runs in the thread doing the Plex work. A canceled HTTP request must not release
            # the claim while its worker thread can still create a share.
            self.db.release_connection(kid_id, claim, retry=not own_claim and not ok)

    def _connect(self, kid_id: int, kid_token: str, claim: str) -> tuple[bool, str, str | None]:
        """Called once, right after a parent signs in as the child. The token is not kept.

        Returns (ok, message for the parent, new status or None)."""
        kid = self.db.kid(kid_id)
        if not kid or not (kid["status"] == "draft" or (kid["status"] in SHARED and not kid["plex_accepted"])):
            return False, "This child is already connected.", None
        if self.unsafe_share_pending(kid):
            return False, ("The unsafe share could not be removed. Access may be unrestricted. "
                           f"Ask {self.cfg.owner_name} to remove this account's server access now. "
                           "Automatic removal will keep retrying."), None
        sections = self.kid_sections(kid)
        if not sections:
            return False, "Choose at least one permitted library in this child's settings before connecting.", None
        acct = self.plex.account(kid_token)
        owner = self.plex.owner()
        if acct.id == owner.id:
            return False, f"That was {self.cfg.owner_name}'s own account. Sign in with the child's account instead.", None
        if self.db.one("SELECT 1 FROM parents WHERE plex_id=?", (acct.id,)):
            return False, ("That signed in as a parent's account, not the child's. Open the link in a private "
                           "(incognito) window, or sign out of Plex there first, then sign in as the child."), None
        if kid["plex_id"] and kid["plex_id"] != acct.id:
            return False, (f"{kid['name']} is already connected to the Plex account {kid['plex_username']}. "
                           "Sign in with that account to finish."), None
        other = self.db.one("SELECT id FROM kids WHERE plex_id=? AND id<>? AND status<>'removed'", (acct.id, kid_id))
        if other:
            return False, "That Plex account is already connected to another child.", None
        shares = self.plex.shares(fresh=True)
        existing = shares.get(acct.id)
        if existing and kid["plex_id"] != acct.id:
            return False, (f"That Plex account already has access to the server. Ask {self.cfg.owner_name} "
                           "to remove it first, or make a new account for your child."), None

        # The share starts empty: it only shows titles carrying this child's label, and none do until approval.
        try:
            attached = self.db.attach_connection(kid_id, claim, acct.id,
                                                  acct.username or acct.title or acct.email, owner.id)
        except sqlite3.IntegrityError:
            return False, "That Plex account is already connected to another child.", None
        if not attached:
            return False, "This connection is no longer authorized. Ask the parent for a new link.", None
        filters = rules.build_filters(kid_id)
        if not existing:
            self.plex.invite(acct.username or acct.email, sections, filters)
            self.db.audit("system", "invited", kid=kid_id, plex_user=acct.id)
            try:
                safe = self.check_new_share(acct.id, self.kid_sections(kid), filters)
            except PlexError:
                text = ("Plex did not keep the limits and could not remove the share. Access may be unrestricted. "
                        f"Do not use this account. Ask {self.cfg.owner_name} to remove its server access now. "
                        "Automatic removal will keep retrying.")
                self.db.update_kid(kid_id, sync_state="error", sync_error=f"{UNSAFE_SHARE_ERROR} {text}")
                self.db.audit("system", "share_removal_failed", kid=kid_id, plex_user=acct.id)
                return False, text, None
            if not safe:
                self.db.x("DELETE FROM tracked_shares WHERE kid_id=? AND plex_id=?", (kid_id, acct.id))
                if kid["status"] == "draft":
                    self.db.update_kid(kid_id, plex_id=None, plex_username=None)
                self.db.audit("system", "share_removed_unsafe", kid=kid_id, plex_user=acct.id)
                return False, ("Plex didn't keep the limits on the new share, so it was taken back. Nothing was "
                               f"shared. Try again in a few minutes. If it keeps happening, tell {self.cfg.owner_name}."), None
        accepted = bool(existing and existing.accepted)
        if not accepted:
            try:
                accepted = self.plex.accept_invite_from_owner(kid_token)
            except PlexError as e:
                log.warning("accept failed: %s", e)
        blocked, online_off = [], kid["online_off"]
        if kid["block_online"] and kid["status"] == "draft":
            try:
                blocked, still_on = self.plex.block_online_sources(kid_token, acct.uuid)
                online_off = 0 if still_on else 1
            except PlexError as e:
                log.warning("opt-out failed: %s", e)
                online_off = 0
        status = kid["status"]
        if status == "draft":
            status = "active" if self.is_owner_kid(kid) else "requested"
        if not self.db.finish_connection(kid_id, claim, kid["status"], acct.id, status=status,
                                         plex_accepted=int(accepted), sync_state="ok", sync_error=None,
                                         applied_filters=json.dumps(filters), online_off=online_off):
            # A tombstone or lost claim must never be overwritten with the pre-network state.
            # Attachment recorded the account separately so cleanup can retry even without plex_id.
            try:
                self._drop_share({"id": kid_id, "plex_id": acct.id})
            except PlexError:
                self.db.update_kid(kid_id, sync_state="error", sync_error=UNSAFE_SHARE_ERROR +
                                   " Connection changed before completion; share removal must be retried.")
                self.db.audit("system", "share_removal_failed", kid=kid_id, plex_user=acct.id)
            else:
                self.db.audit("system", "share_removed_stale_connection", kid=kid_id, plex_user=acct.id)
            return False, "This connection is no longer current. Its share was taken back or queued for removal.", None
        self.db.audit("system", "connected", kid=kid_id, plex_user=acct.id, plex_username=acct.username,
                      accepted=accepted, online_sources_off=blocked, online_ok=online_off)
        if kid["status"] == "draft":
            status = activate_child_on_connect(self, kid_id, "system", self.notifier, claim=claim)
        if status == "active":
            if kid["status"] != "draft":
                self.queue_sync(kid_id)
            text = f"All set. {kid['name']}'s titles show up in Plex within a minute or two."
        else:
            text = (f"{kid['name']}'s account is connected, and your request went to {self.cfg.owner_name}. "
                    "Titles show up once it's approved.")
        if not accepted:
            text += (" Plex still needs the invitation accepted: open the Plex invite email sent to the child's "
                     "address and accept it while signed in as the child.")
        if online_off == 0:
            text += (" Plex's own free movies and Live TV could not be switched off for this account. "
                     "The child's page here explains how to do it by hand.")
        return True, text, status

    @contextmanager
    def _changing_child(self, kid_id: int):
        claim = secrets.token_urlsafe(24)
        if not self.db.claim_kid_change(kid_id, claim):
            if self.db.kid(kid_id):
                raise ConnectionInProgress()
            yield None
            return
        try:
            yield self.db.kid(kid_id)
        finally:
            self.db.release_connection(kid_id, claim)

    def approve(self, kid_id: int, actor: str) -> None:
        with self._changing_child(kid_id) as kid:
            if not kid or kid["status"] != "requested":
                return
            self.db.update_kid(kid_id, status="active")
            self.db.audit(actor, "approved", kid=kid_id)
            self.apply(kid_id, actor)
            self.queue_sync(kid_id)

    def decline(self, kid_id: int, actor: str) -> None:
        with self._changing_child(kid_id) as kid:
            if not kid or kid["status"] not in ("draft", "requested"):
                return
            self._drop_share(kid)
            self.db.update_kid(kid_id, status="declined", plex_id=None)
            self.db.audit(actor, "declined", kid=kid_id, plex_user=kid["plex_id"])

    # ----- titles -----
    def set_title(self, kid_id: int, rating_key: int, kind: str | None, actor: str, sync: bool = True,
                  audit: bool = True) -> None:
        """kind 'allow' or 'block' sets the parent's choice; None goes back to the age level."""
        kid = self.db.kid(kid_id)
        item = next((it for sec in (self.kid_sections(kid) if kid else [])
                     for it in self.plex.library(sec) if it.rating_key == rating_key), None)
        if not kid or not item:
            raise PlexError("That title is not in this child's libraries")
        if kind == "allow" and not rules.allowable(item.item_type, item.rating):
            raise PlexError("Titles rated above PG-13 or TV-14, or not rated, can't be allowed")
        self.db.x("DELETE FROM kid_titles WHERE kid_id=? AND rating_key=?", (kid_id, rating_key))
        if kind in ("allow", "block"):
            self.db.x("INSERT INTO kid_titles(kid_id,rating_key,kind,section_key,item_type,title,year) "
                      "VALUES(?,?,?,?,?,?,?)", (kid_id, rating_key, kind, item.section_key, item.item_type,
                                                item.title, item.year))
        if audit:
            self.db.audit(actor, "title_" + (kind or "reset"), kid=kid_id, rating_key=rating_key, title=item.title)
        if sync:
            self.queue_sync(kid_id)

    # ----- remove -----
    def _drop_share(self, kid) -> None:
        accounts = {r["plex_id"] for r in self.db.q("SELECT plex_id FROM tracked_shares WHERE kid_id=?", (kid["id"],))}
        if kid["plex_id"]:
            accounts.add(kid["plex_id"])
        shares = self.plex.shares(fresh=True) if accounts else {}
        for account in accounts:
            # An old tracking row may outlive retraction and reassignment to another child.
            other = account != kid["plex_id"] and self.db.one(
                "SELECT 1 FROM kids WHERE plex_id=? AND id<>? AND status<>'removed'", (account, kid["id"]))
            if not other:
                share = shares.get(account)
                if share:
                    self.plex.remove_share(share.share_id)
            self.db.x("DELETE FROM tracked_shares WHERE kid_id=? AND plex_id=?", (kid["id"], account))

    def remove(self, kid_id: int, actor: str) -> None:
        with self._changing_child(kid_id) as kid:
            if not kid:
                return
            self._drop_share(kid)
            # Record unfinished cleanup before touching labels, so interruption is retriable too.
            self.db.update_kid(kid_id, status="removed", plex_id=None, sync_state="error",
                               sync_error="Titles: Removal pending")
            if not self.sync_titles(kid_id):  # takes this child's label off every title
                raise PlexError("Plex access was removed, but title labels could not be cleared. "
                                "Run cleanup again to retry.")
            self.db.x("DELETE FROM kid_titles WHERE kid_id=?", (kid_id,))
            self.db.x("DELETE FROM kid_parents WHERE kid_id=?", (kid_id,))
            self.db.audit(actor, "removed", kid=kid_id, plex_user=kid["plex_id"])
