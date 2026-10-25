"""Authenticated parent approvals and a waiting page that keeps its sign-in.

Include router before the legacy /waiting handlers. Keep the legacy request
POST for child/ordinary requests, but redirect kind='access' to this module's
detail page without applying a decision; parent decisions use /decision.
Uses the same app.state services, sessions, epoch and CSRF cookie as main.py.
The app's existing owner-code flow is the only way to obtain an owner session.
"""
from __future__ import annotations

import secrets
import time
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from .approvals import (_audit, accepted_parent, notify_parent_request, request_version)
from .db import now_iso
from .plex import PlexError
from .security import SESSION_LIMITS
from .setup import trusted_parents_enabled

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _user(request: Request, *, owner: bool = False):
    state, session = request.app.state, request.session
    u, sid = session.get("user"), session.get("sid")
    if not isinstance(u, dict) or u.get("role") not in SESSION_LIMITS or not isinstance(u.get("id"), int):
        raise HTTPException(401, "Sign in to continue.")
    if session.get("epoch") != state.db.setting("session_epoch", "1") or not state.sessions.valid(sid, u["id"], u["role"]):
        session.clear()
        raise HTTPException(401, "Sign in to continue.")
    row = state.db.one("SELECT role FROM sessions WHERE id=?", (state.sessions._key(sid),))
    if not row or row["role"] != u["role"]:
        raise HTTPException(401, "Sign in to continue.")
    if owner and (u["role"] != "owner" or u["id"] != state.plex.owner().id):
        raise HTTPException(404)
    if not owner and u["role"] not in ("waiting", "parent"):
        raise HTTPException(404)
    return u


def _csrf(request: Request) -> str:
    if not request.session.get("csrf"):
        request.session["csrf"] = secrets.token_urlsafe(24)
    return request.session["csrf"]


async def _form(request: Request) -> dict:
    data = await request.form(max_files=0, max_fields=10, max_part_size=64 * 1024)
    sent = str(data.get("csrf") or request.headers.get("x-csrf-token", ""))
    expected = request.session.get("csrf")
    if not expected or not sent.isascii() or not secrets.compare_digest(sent, expected):
        raise HTTPException(400, "This page expired. Go back, refresh and try again.")
    return {key: str(value) for key, value in data.items()}


def _render(request: Request, name: str, *, status: int = 200, **ctx):
    ctx.update(user=request.session.get("user"), csrf=_csrf(request),
               owner_name=request.app.state.svc.cfg.owner_name, flashes=[], unread=0)
    return templates.TemplateResponse(request, name, ctx, status_code=status,
                                      headers={"Cache-Control": "no-store"})


def _parent_request(db, req_id: int):
    row = db.one("SELECT r.*, p.username, p.title FROM requests r JOIN parents p ON p.plex_id=r.parent_id "
                 "WHERE r.id=? AND r.kind='access' AND r.kid_id IS NULL", (req_id,))
    if not row:
        raise HTTPException(404)
    return row


def _owner_page(request: Request):
    try:
        _user(request, owner=True)
    except HTTPException as exc:
        if exc.status_code == 401:
            return _render(request, "approval_signin.html", status=401)
        raise
    return None


def _signed_at_write(request: Request, conn, u) -> str:
    """Session revocation/epoch changes during a Plex read must win at commit."""
    sid = request.app.state.sessions._key(request.session["sid"])
    signed = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    epoch = conn.execute("SELECT value FROM settings WHERE key='session_epoch'").fetchone()
    idle, total = SESSION_LIMITS[u["role"]]
    if (not signed or signed["role"] != u["role"] or signed["user_id"] != u["id"]
            or time.time() - signed["last_seen"] > idle or time.time() - signed["created_at"] > total
            or request.session.get("epoch") != (epoch[0] if epoch else "1")):
        raise HTTPException(401, "Sign in to continue.")
    return sid


@router.get("/admin/requests")
def inbox(request: Request):
    signin = _owner_page(request)
    if signin is not None:
        return signin
    db = request.app.state.db
    rows = db.q("SELECT r.*,p.username FROM requests r JOIN parents p ON p.plex_id=r.parent_id "
                "WHERE r.kind='access' AND r.kid_id IS NULL ORDER BY r.id DESC")
    with closing(db._connect()) as conn:
        trusted = trusted_parents_enabled(conn)
    return _render(request, "approval_inbox.html", nav="admin", requests=rows,
                   trusted=trusted,
                   pending_children=db.one("SELECT COUNT(*) AS n FROM kids WHERE status='requested'")["n"])


@router.get("/admin/requests/{req_id}")
def detail(request: Request, req_id: int, decline: bool = False):
    signin = _owner_page(request)
    if signin is not None:
        return signin
    row = _parent_request(request.app.state.db, req_id)
    return _render(request, "approval_detail.html", nav="admin", asked=row,
                   version=request_version(row), decline=decline)


@router.post("/admin/requests/{req_id}/decision")
async def decide(request: Request, req_id: int):
    u = _user(request, owner=True)
    data = await _form(request)
    action = data.get("action")
    if action not in ("approve", "decline", "deny"):
        raise HTTPException(400, "Choose approve or decline.")
    db, svc = request.app.state.db, request.app.state.svc
    row = _parent_request(db, req_id)
    if row["status"] != "open":
        return RedirectResponse(f"/admin/requests/{req_id}", status_code=303)
    if data.get("version") != request_version(row):
        return _render(request, "approval_detail.html", status=409, nav="admin", asked=row,
                       version=request_version(row), changed=True)
    if action == "approve":
        try:
            await run_in_threadpool(svc.read_shared)
        except PlexError:
            raise HTTPException(503, "Plex isn't answering. Try again.") from None
    # All authority and version checks are repeated at the actual write. There
    # is no SQLite transaction held across a Plex request.
    with closing(db._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _signed_at_write(request, conn, u)
        latest = conn.execute("SELECT * FROM requests WHERE id=?", (req_id,)).fetchone()
        if latest["status"] != "open":
            return RedirectResponse(f"/admin/requests/{req_id}", status_code=303)
        if data.get("version") != request_version(latest):
            raise HTTPException(409, "This request changed. Review the latest details.")
        if action == "approve":
            if not accepted_parent(conn, latest["parent_id"]):
                raise HTTPException(409, "This parent must accept the Plex library invitation first.")
            conn.execute("UPDATE parents SET enabled=1 WHERE plex_id=?", (latest["parent_id"],))
        status = "approved" if action == "approve" else "declined"
        conn.execute("UPDATE requests SET status=?,owner_note=?,resolved_at=? WHERE id=?",
                     (status, data.get("note", "").strip()[:500] or None, now_iso(), req_id))
        _audit(conn, u["username"], "request_" + status, request=req_id, kind="access", parent=latest["parent_id"])
    return RedirectResponse(f"/admin/requests/{req_id}", status_code=303)


def _waiting_state(request: Request, u) -> tuple[str, object]:
    state, db = request.app.state, request.app.state.db
    parent = db.one("SELECT * FROM parents WHERE plex_id=?", (u["id"],))
    if not parent:
        raise HTTPException(404)
    asked = db.one("SELECT * FROM requests WHERE parent_id=? AND kind='access' AND kid_id IS NULL "
                   "ORDER BY id DESC LIMIT 1", (u["id"],))
    if not parent["enabled"]:
        if u["role"] == "parent":
            state.sessions.end(request.session["sid"])
            request.session.clear()
            raise HTTPException(401, "Sign in to continue.")
        return (asked["status"] if asked else "none"), asked
    try:
        state.svc.read_shared()
    except PlexError:
        raise HTTPException(503, "Plex isn't answering. Try Check access again.") from None
    with closing(db._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        sid = _signed_at_write(request, conn, u)
        parent = conn.execute("SELECT enabled FROM parents WHERE plex_id=?", (u["id"],)).fetchone()
        if not parent or not parent[0] or not accepted_parent(conn, u["id"]):
            return "needs_share", asked
        changed = conn.execute("UPDATE sessions SET role='parent' WHERE id=? AND user_id=? AND role=?",
                               (sid, u["id"], u["role"])).rowcount
        if not changed:
            raise HTTPException(401, "Sign in to continue.")
    request.session["user"] = {**u, "role": "parent"}
    return "approved", asked


@router.get("/waiting")
def waiting(request: Request):
    try:
        u = _user(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return RedirectResponse("/login", status_code=303)
        raise
    status, asked = _waiting_state(request, u)
    if status == "approved":
        return RedirectResponse("/home", status_code=303)
    return _render(request, "approval_waiting.html", asked=asked, waiting_status=status)


@router.get("/waiting/status.json")
def waiting_status(request: Request):
    status, _ = _waiting_state(request, _user(request))
    return JSONResponse({"status": status, "accepted": status == "approved"}, headers={"Cache-Control": "no-store"})


@router.post("/waiting")
async def request_access(request: Request):
    u = _user(request)
    data = await _form(request)
    db = request.app.state.db
    created = None
    with closing(db._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _signed_at_write(request, conn, u)
        parent = conn.execute("SELECT enabled FROM parents WHERE plex_id=?", (u["id"],)).fetchone()
        if not parent or parent[0] or u["role"] != "waiting":
            return RedirectResponse("/waiting", status_code=303)
        last = conn.execute("SELECT status FROM requests WHERE parent_id=? AND kind='access' "
                            "AND kid_id IS NULL ORDER BY id DESC LIMIT 1", (u["id"],)).fetchone()
        if not last or last[0] not in ("open", "declined"):
            created = conn.execute("INSERT INTO requests(parent_id,kind,message,created_at) VALUES(?,?,?,?)",
                                   (u["id"], "access", data.get("message", "").strip()[:500]
                                    or "Would like to use KidsWatch for Plex.", now_iso())).lastrowid
    if created:
        notify_parent_request(db, getattr(request.app.state, "notifier", None), created)
    return RedirectResponse("/waiting", status_code=303)


@router.get("/admin/children/{kid_id}")
def manage_child(request: Request, kid_id: int):
    signin = _owner_page(request)
    if signin is not None:
        return signin
    kid = request.app.state.db.kid(kid_id)
    if not kid or kid["status"] in ("declined", "removed"):
        raise HTTPException(404)
    return _render(request, "approval_child.html", kid=kid, nav="admin")
