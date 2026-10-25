"""Owner setup router. No authentication or first-visitor ownership is created.

Register after existing require/render helpers are defined:
    app.state.setup = SetupRoutes(cfg=cfg, require=require, render=render)
    app.include_router(router)
The existing require(request, 'owner') validates server sessions and owner-code
sign-in; render supplies the existing CSRF token and base template context.
"""
from __future__ import annotations

import json
import secrets
import time
from contextlib import closing
from dataclasses import dataclass, field
from typing import Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from .plex import PlexError
from .security import RateLimiter
from .setup import (TEST_TTL, KID_SECTIONS, available_libraries, effective_kid_sections, finish_setup,
                    get_setting, receiver_fingerprint, save_draft, send_test_notification,
                    trusted_parents_enabled, validate_sections)

router = APIRouter()


@dataclass
class SetupRoutes:
    cfg: object
    require: Callable
    render: Callable
    limiter: RateLimiter = field(default_factory=RateLimiter)


def _context(request):
    return request.app.state.setup


def _owner(request, *, fresh=False):
    if fresh:
        # main.require caches a session check within a request. Network waits
        # must not let a revoked/expired session finish a setup mutation.
        request.state.checked_sid = None
    return _context(request).require(request, "owner")


async def _form(request):
    data = await request.form(max_files=0, max_fields=100, max_part_size=64 * 1024)
    expected = request.session.get("csrf")
    sent = str(data.get("csrf", ""))
    if not expected or not sent.isascii() or not secrets.compare_digest(sent, expected):
        raise HTTPException(400, "This page expired. Go back, refresh and try again.")
    return data


def _libraries_page(request, *, error=None, selected=None, status=200):
    state, ctx = request.app.state, _context(request)
    owner = _owner(request)
    try:
        libraries = available_libraries(state.plex)
    except PlexError:
        libraries, error, status = {}, "Plex isn't answering. Try again in a moment.", 503
    with closing(state.db._connect()) as conn:
        if selected is None:
            draft = conn.execute("SELECT sections FROM setup_drafts WHERE owner_id=?", (owner["id"],)).fetchone()
            selected = json.loads(draft[0]) if draft else (
                effective_kid_sections(conn, ()) if get_setting(conn, KID_SECTIONS) is not None else ())
    return ctx.render(request, "setup_libraries.html", status=status, libraries=libraries,
                      selected=selected, error=error, timezone=ctx.cfg.tz, nav="admin")


def _notifications_page(request, *, error=None, notice=None, trusted=None, status=200):
    state, ctx = request.app.state, _context(request)
    owner = _owner(request)
    with closing(state.db._connect()) as conn:
        draft = conn.execute("SELECT * FROM setup_drafts WHERE owner_id=?", (owner["id"],)).fetchone()
        if not draft:
            return RedirectResponse("/setup/libraries", status_code=303)
        if trusted is None:
            trusted = trusted_parents_enabled(conn)
    receiver = receiver_fingerprint(state.notifier)
    tested = bool(receiver and draft["test_receiver"] == receiver and draft["test_sent_at"]
                  and 0 <= time.time() - draft["test_sent_at"] <= TEST_TTL)
    return ctx.render(request, "setup_notifications.html", status=status, nav="admin", error=error,
                      notice=notice, configured=bool(receiver), tested=tested, trusted=trusted,
                      revision=draft["revision"])


@router.get("/setup")
def setup_start(request: Request):
    return _context(request).render(request, "setup_start.html")


@router.get("/setup/libraries")
def setup_libraries(request: Request):
    return _libraries_page(request)


@router.post("/setup/libraries")
async def setup_libraries_save(request: Request):
    owner = _owner(request)
    data = await _form(request)
    state = request.app.state
    sections = data.getlist("sections")
    try:
        libraries = await run_in_threadpool(available_libraries, state.plex, fresh=True)
        sections = validate_sections(sections, libraries)
    except ValueError as exc:
        return _libraries_page(request, error=str(exc), selected=sections, status=400)
    except PlexError:
        return _libraries_page(request, error="Plex isn't answering. Try again in a moment.", selected=sections, status=503)
    _owner(request, fresh=True)
    with closing(state.db._connect()) as conn, conn:
        save_draft(conn, owner["id"], sections)
    return RedirectResponse("/setup/notifications", status_code=303)


@router.get("/setup/notifications")
def setup_notifications(request: Request):
    return _notifications_page(request)


@router.post("/setup/notifications")
async def setup_notifications_save(request: Request):
    owner = _owner(request)
    data = await _form(request)
    state, ctx = request.app.state, _context(request)
    trusted = data.get("trusted") == "1"
    with closing(state.db._connect()) as conn:
        draft = conn.execute("SELECT * FROM setup_drafts WHERE owner_id=?", (owner["id"],)).fetchone()
    if not draft:
        return RedirectResponse("/setup/libraries", status_code=303)
    if data.get("revision") != draft["revision"]:
        return _notifications_page(request, error="Setup changed in another window. Review the latest settings.", status=409)
    if data.get("method") != "configured":
        return _notifications_page(request, error="Choose your configured private notification receiver.", trusted=trusted, status=400)
    if data.get("action") == "test":
        if ctx.limiter.hit(f"setup-test:{owner['id']}", 3, 60):
            return _notifications_page(request, error="Wait a minute before sending another test.", trusted=trusted, status=429)
        receiver = receiver_fingerprint(state.notifier)
        # A failed retry invalidates a previous successful test.
        with closing(state.db._connect()) as conn, conn:
            conn.execute("UPDATE setup_drafts SET test_receiver=NULL,test_sent_at=NULL WHERE owner_id=? AND revision=?",
                         (owner["id"], draft["revision"]))
        ok = await run_in_threadpool(send_test_notification, state.notifier)
        if not ok or receiver is None or receiver != receiver_fingerprint(state.notifier):
            return _notifications_page(request, error="The test could not be sent. Check your receiver and try again.", trusted=trusted, status=503)
        _owner(request, fresh=True)
        with closing(state.db._connect()) as conn, conn:
            changed = conn.execute("UPDATE setup_drafts SET test_receiver=?,test_sent_at=? WHERE owner_id=? AND revision=?",
                                   (receiver, time.time(), owner["id"], draft["revision"])).rowcount
        if not changed:
            return _notifications_page(request, error="Setup changed in another window. Send a new test.", status=409)
        return _notifications_page(request, notice="Test sent. Check your device.", trusted=trusted)
    if data.get("action") != "finish":
        raise HTTPException(400, "Choose Send test notification or Finish setup.")
    try:
        libraries = await run_in_threadpool(available_libraries, state.plex, fresh=True)
        _owner(request, fresh=True)
        with closing(state.db._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            finish_setup(conn, owner_id=owner["id"], actor=owner["username"], revision=draft["revision"],
                         available=libraries, receiver=receiver_fingerprint(state.notifier),
                         confirmed=data.get("confirmed") == "1", trusted=trusted)
    except ValueError as exc:
        return _notifications_page(request, error=str(exc), trusted=trusted, status=400)
    except PlexError:
        return _notifications_page(request, error="Plex isn't answering. Your setup is saved. Try again in a moment.", trusted=trusted, status=503)
    return RedirectResponse("/setup/ready", status_code=303)


@router.get("/setup/ready")
def setup_ready(request: Request):
    _owner(request)
    state, ctx = request.app.state, _context(request)
    with closing(state.db._connect()) as conn:
        if not get_setting(conn, "setup.completed_at"):
            return RedirectResponse("/setup/libraries", status_code=303)
        sections = effective_kid_sections(conn, ())
        trusted = trusted_parents_enabled(conn)
    try:
        titles = state.plex.section_titles()
    except PlexError:
        titles = {}
    return ctx.render(request, "setup_ready.html", nav="admin",
                      libraries=[titles.get(key, "Unavailable library") for key in sections], trusted=trusted)
