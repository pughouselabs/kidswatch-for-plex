"""KidsWatch for Plex: a small site where parents manage their children's Plex accounts."""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import mimetypes
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager, closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from markupsafe import Markup, escape
import segno
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import config as config_mod
from . import approval_routes, invitation_routes, rules, setup_routes
from .branding import OwnerArt, SLOTS, bundled_art
from .email_tip import register_email_tip
from .invitation_routes import safe_return
from .db import DB, SHARED_TRUST_MAX, now_iso
from .enforcer import Enforcer
from .notify import Notifier
from .plex import PlexClient, PlexError
from .security import ClientAddress, Pending, RateLimiter, Sessions, rate_key
from .service import ConnectionInProgress, Service
from .setup import library_policy_warning, trusted_parents_enabled

log = logging.getLogger("kidswatch")
HERE = Path(__file__).parent

REQUEST_KINDS = {  # what a parent can ask for, in form order
    "title": "Add a movie or show",
    "feature": "Suggest a feature",
    "more_access": "Ask for access",
    "problem": "Report a problem",
    "other": "Something else",
}
REQUEST_HINTS = {
    "title": "Something that isn't on the server yet.",
    "feature": "An idea that would make this site better for your family.",
    "more_access": "Another library, a co-parent, or more children.",
    "problem": "Something isn't working the way it should.",
    "other": "Anything else for your server admin.",
}
KIND_LABELS = {**REQUEST_KINDS, "change": "Change a setting", "add_child": "New child", "access": "Site access"}
STATUS_TEXT = {
    "draft": "Not connected",
    "requested": "Waiting for approval",
    "active": "Active",
    "declined": "Declined",
    "removed": "Removed",
}
DEFAULTS = dict(weekday_minutes=120, weekend_minutes=180, weekday_start="07:00", weekday_end="20:00",
                weekend_start="07:00", weekend_end="21:00")
LINK_TTL = 1800
PIN_MAX_AGE = 180  # a sign-in must finish within 3 minutes (limits phished sign-in links)
LINK_PIN_MAX_AGE = 900
MAX_BODY = 64 * 1024
OWNER_CODE_TTL = 300
OWNER_CODE_TRIES = 5
PIN_STARTS_PER_MINUTE = 60  # Plex sign-ins started across all visitors, so the server can't be used to flood plex.tv
SHARED_MAX_AGE = 600  # re-read who the server is shared with when the keeper hasn't for this long
SHARED_RETRY = 30


class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


def is_num(value, digits: int = 9) -> bool:
    """Plain ASCII digits only (str.isdigit also accepts things like '²', which int() rejects)."""
    return isinstance(value, str) and re.fullmatch(r"[0-9]{1,%d}" % digits, value) is not None


class BodyLimit:
    """Read POST bodies up front and refuse anything over MAX_BODY.

    The Content-Length check in security_headers misses chunked bodies, and request.form() would buffer
    those in full, so one huge upload could push the container past its memory limit (and stop the keeper)."""

    def __init__(self, app, limit: int = MAX_BODY):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        chunks, size, more = [], 0, True
        while more:
            msg = await receive()
            if msg["type"] != "http.request":  # the client went away
                return
            body = msg.get("body", b"")
            size += len(body)
            if size > self.limit:
                return await Response("Request too large", status_code=413)(scope, receive, send)
            chunks.append(body)
            more = msg.get("more_body", False)
        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, replay, send)


def create_app(cfg=None, plex: PlexClient | None = None) -> FastAPI:
    cfg = cfg or config_mod.load()
    mimetypes.add_type("image/webp", ".webp")  # slim images have no /etc/mime.types, and Python 3.12 lacks it
    db = DB(cfg.db_path)
    client_id = db.setting("client_id")
    if not client_id:
        client_id = str(uuid.uuid4())
        db.set_setting("client_id", client_id)
    plex = plex or PlexClient(cfg, client_id)
    svc = Service(db, plex, cfg)
    enforcer = Enforcer(svc, cfg.poll_seconds)
    notifier = Notifier(cfg.ha_webhook_file, cfg.public_url)
    svc.notifier = notifier
    enforcer.on_alert = lambda title, body: notifier.send(title, body, "/admin")
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    register_email_tip(templates.env)
    owner_art = OwnerArt(cfg.landing_art_dir)
    # Keep the limits even for callers constructing Config directly.
    app_name = cfg.app_name.strip()[:80]
    footer_text = cfg.footer_text.strip()[:240]
    addr = ClientAddress(cfg.trusted_proxies, cfg.trust_cf_headers)
    sessions = Sessions(db)
    pins = Pending(ttl=2 * LINK_PIN_MAX_AGE)  # Plex PIN codes stay on the server, not in the cookie

    def _ip(request: Request) -> str:
        return addr.ip(request)

    # The session key is derived from the owner token, so no extra secret has to be stored anywhere.
    session_key = hmac.new(plex.owner_token().encode(), b"plex-for-kids-session-v1", hashlib.sha256).hexdigest()

    @asynccontextmanager
    async def lifespan(_app):
        if cfg.start_enforcer:
            enforcer.start()
        yield
        enforcer.stop()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=session_key, session_cookie="pk_session",
                       max_age=30 * 24 * 3600, same_site="lax", https_only=cfg.secure_cookies)
    app.add_middleware(BodyLimit)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.state.db, app.state.plex, app.state.svc, app.state.enforcer = db, plex, svc, enforcer
    app.state.notifier, app.state.sessions = notifier, sessions

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        length = request.headers.get("content-length")
        if request.method == "POST" and length and (not is_num(length, 12) or int(length) > MAX_BODY):
            response = Response("Request too large", status_code=413)
        else:
            response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: https://plex.tv https://*.plex.tv; "
            "style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer" if request.url.path.startswith("/mobile/") else "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if cfg.secure_cookies:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        # Pages carry the sign-in cookie and a form token, so no page is kept by the browser or a proxy.
        # Posters and static files keep their own caching.
        if request.url.path.startswith(("/kids", "/admin", "/home", "/link", "/requests", "/verify", "/waiting",
                                        "/auth")) or response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Redirect)
    async def _redirect(request: Request, exc: Redirect):
        return RedirectResponse(exc.url, status_code=303)

    @app.exception_handler(RequestValidationError)
    async def _bad_input(request: Request, exc: RequestValidationError):
        """A malformed link (like /kids/abc) is just a page that doesn't exist."""
        return await _http_error(request, StarletteHTTPException(404))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        """Friendly pages instead of raw JSON; JSON stays JSON for the page's own data calls."""
        if request.url.path.endswith(".json") or request.headers.get("x-requested-with") == "fetch":
            return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)
        if exc.status_code == 400 and exc.detail == "This page expired. Go back, refresh and try again.":
            path = request.url.path
            back = "/requests?restore=1#new" if path == "/requests" else (
                path if re.fullmatch(r"/kids/[0-9]+/(schedule|settings)", path) else "/home")
            return message(request, "This form expired",
                           "Refresh the form and try again. Your changes were not submitted. "
                           "On Requests, you can restore your unsent draft.", 400,
                           link=(back, "Return to your request" if path == "/requests" else "Refresh the form"))
        titles = {404: ("Page not found", "That page doesn't exist, or it isn't one you can open."),
                  405: ("Page not found", "That page doesn't exist, or it isn't one you can open."),
                  413: ("Request too large", "Try again with a shorter message."),
                  409: ("Connection in progress", str(exc.detail)),
                  503: ("Plex isn't answering", "Plex isn't answering right now. Try again in a minute.")}
        title, body = titles.get(exc.status_code, ("Something went wrong",
                                                   str(exc.detail) if exc.status_code == 400 and exc.detail
                                                   and exc.detail != "Bad Request" else "That didn't work. Go back and try again."))
        return message(request, title, body, exc.status_code, link=("/", "Go to the start page"))

    # ---------- helpers ----------
    def user(request: Request) -> dict | None:
        u = request.session.get("user")
        if not u:
            return None
        sid = request.session.get("sid")
        if not isinstance(sid, str) or not sid:
            request.session.clear()  # a cookie from before server-side sessions: sign in again
            return None
        if getattr(request.state, "checked_sid", None) == sid:
            return u  # already checked during this request
        if request.session.get("epoch") != db.setting("session_epoch", "1") or not sessions.valid(sid, u["id"], u["role"]):
            # Signed out, timed out, or "Sign everyone out" was pressed.
            request.session.clear()
            return None
        request.state.checked_sid = sid
        return u

    def sign_in(request: Request, u: dict) -> None:
        request.session.clear()
        request.session["user"] = u
        request.session["sid"] = sessions.create(u["id"], u["role"])
        request.session["epoch"] = db.setting("session_epoch", "1")

    shared_failed = [0.0]

    def shared_ids() -> set[int] | None:
        """Who the server is shared with. Read now if the keeper hasn't (or isn't running); None if unknown."""
        now = time.time()
        with svc.sharing_snapshot() as (ids, at, _):
            stale = ids is None or now - at > SHARED_MAX_AGE
        if stale and now - shared_failed[0] > SHARED_RETRY:  # after a failed read, give Plex a moment
            if not svc.refresh_shared():
                shared_failed[0] = now
        with svc.sharing_snapshot() as (ids, at, _):
            if ids is None or time.time() - at > SHARED_TRUST_MAX:
                return None  # too old to trust while Plex isn't answering
            return ids

    def still_shared(parent_id: int) -> bool:
        ids = shared_ids()
        if ids is None:
            raise HTTPException(503)  # can't confirm the parent still has access: refuse, but keep them signed in
        return parent_id in ids

    def require(request: Request, *roles: str) -> dict:
        u = user(request)
        if not u:
            raise Redirect("/")
        if u["role"] == "waiting":
            raise Redirect("/waiting")
        if u["role"] == "parent":
            row = db.one("SELECT enabled FROM parents WHERE plex_id=?", (u["id"],))
            if not row or not row["enabled"]:
                sessions.end(request.session.get("sid"))
                request.session.clear()
                raise Redirect("/")
            shared_ids()  # refresh outside the publication lock; Plex may take time to answer
            for _ in range(2):
                with svc.sharing_snapshot() as (ids, at, observed):
                    if ids is None or time.time() - at > SHARED_TRUST_MAX:
                        raise HTTPException(503)
                    if u["id"] in ids:
                        break
                    if sessions.end_for_observation(request.session["sid"], observed):
                        request.session.clear()
                        raise Redirect("/")
                    # Another worker published a newer list before deletion. Check that list first.
            else:
                # A restored DB can have a sequence below memory; never spin on the event loop.
                raise HTTPException(503)
        if roles and u["role"] not in roles:
            raise HTTPException(404)
        return u

    def csrf_token(request: Request) -> str:
        tok = request.session.get("csrf")
        if not tok:
            tok = request.session["csrf"] = secrets.token_urlsafe(24)
        return tok

    async def form(request: Request) -> dict:
        data = await request.form(max_files=0, max_fields=100, max_part_size=64 * 1024)
        sent = str(data.get("csrf") or request.headers.get("x-csrf-token", ""))
        expected = request.session.get("csrf")
        if not expected or not sent.isascii() or not secrets.compare_digest(sent, expected):
            raise HTTPException(400, "This page expired. Go back, refresh and try again.")
        return {k: str(v) for k, v in data.items()}

    def flash(request: Request, text: str, kind: str = "ok", undo: dict | None = None) -> None:
        item = {"text": text, "kind": kind}
        if undo:
            item["undo"] = undo  # {"url", "action", "back"}: the toast shows an Undo button that posts it
        request.session.setdefault("flash", []).append(item)

    def unread_replies(u) -> int:
        if not u or u["role"] != "parent":
            return 0
        row = db.one("SELECT requests_seen_at FROM parents WHERE plex_id=?", (u["id"],))
        seen = (row["requests_seen_at"] if row else None) or ""
        return db.one("SELECT COUNT(*) AS n FROM requests WHERE parent_id=? AND status<>'open' AND status<>'cancelled' "
                      "AND resolved_at>?", (u["id"], seen))["n"]

    media_request_host = cfg.media_request_url.split("://")[-1]
    # With a request site, movies and shows are asked for there, not in the Requests form.
    form_kinds = {k: v for k, v in REQUEST_KINDS.items() if not (k == "title" and cfg.media_request_url)}

    def render(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
        ctx.setdefault("unread", unread_replies(user(request)))
        with closing(db._connect()) as conn:
            trusted = trusted_parents_enabled(conn)
            if name in ("admin.html", "setup_start.html", "setup_libraries.html",
                        "setup_notifications.html", "setup_ready.html") and user(request) and user(request)["role"] == "owner":
                ctx["library_warning"] = library_policy_warning(conn, cfg.kid_sections)
        ctx.update(user=user(request), csrf=csrf_token(request), owner_name=cfg.owner_name,
                   app_name=app_name, footer_text=footer_text, owner_art=owner_art,
                   trusted_parents=trusted, mobile_enabled=cfg.mobile_enabled,
                   flashes=request.session.pop("flash", []), rules=rules, STATUS_TEXT=STATUS_TEXT,
                   REQUEST_KINDS=REQUEST_KINDS, REQUEST_HINTS=REQUEST_HINTS, KIND_LABELS=KIND_LABELS,
                   media_request_url=cfg.media_request_url, media_request_host=media_request_host)
        return templates.TemplateResponse(request, name, ctx, status_code=status)

    app.state.setup = setup_routes.SetupRoutes(cfg=cfg, require=require, render=render)
    app.include_router(setup_routes.router)
    invitation_routes.configure(app, user=user, require=require, form=form, render=render)
    app.include_router(invitation_routes.router)
    app.include_router(approval_routes.router)

    def message(request: Request, title: str, body: str, status: int = 200, link=("/", "Back to start")):
        return render(request, "message.html", status=status, title=title, body=body, link=link)

    def is_parent_of(user_id: int, kid) -> bool:
        """The parent who added the child, or a co-parent the owner added."""
        return kid["parent_id"] == user_id or bool(
            db.one("SELECT 1 FROM kid_parents WHERE kid_id=? AND parent_id=?", (kid["id"], user_id)))

    def kid_for(request: Request, kid_id: int):
        u = require(request, "parent", "owner")
        kid = db.kid(kid_id)
        if not kid or (u["role"] != "owner" and not is_parent_of(u["id"], kid)):
            raise HTTPException(404)
        return u, kid

    def co_parents(kid_id: int) -> list:
        return db.q("SELECT p.plex_id, p.title, p.username FROM kid_parents kp JOIN parents p ON p.plex_id=kp.parent_id "
                    "WHERE kp.kid_id=? ORDER BY p.title", (kid_id,))

    def parent_name(plex_id: int) -> str:
        row = db.one("SELECT title, username FROM parents WHERE plex_id=?", (plex_id,))
        if row:
            return row["title"] or row["username"]
        try:
            owner = plex.owner()
            if owner.id == plex_id:
                return f"{owner.name} (you)" if owner.name else cfg.owner_name
        except PlexError:
            pass
        return "Unknown parent"

    CHANGE_TEXT = {
        "kid_added": "added {name}", "settings_saved": "changed the settings", "schedule_saved": "changed the schedule",
        "control_pause": "paused {name}", "control_pause_timed": "paused {name} until {until}",
        "control_resume": "let {name} watch again", "control_bonus": "added {minutes} minutes for the day",
        "control_reset_bonus": "removed today's extra time", "control_later": "made bedtime {minutes} minutes later",
        "control_reset_later": "removed tonight's later bedtime", "control_dayoff": "gave {name} weekend rules today",
        "control_reset_dayoff": "put {name} back on school-day rules", "rules_copied": "copied {source_name}'s rules to {name}", "title_allow": "allowed {title}",
        "title_block": "blocked {title}", "title_reset": "put {title} back to the age level",
        "titles_batch": "", "approved": "approved the Plex account", "connected": "The Plex account was connected",
        "co_parent_added": "added {parent} as a co-parent", "co_parent_removed": "removed {parent} as a co-parent",
    }

    def kid_changes(kid, viewer: str, limit: int = 12) -> list[dict]:
        """Recent changes to one child by any of their parents (or the owner), newest first, in plain words."""
        rows = db.q(f"SELECT * FROM audit WHERE json_extract(detail, '$.kid')=? AND action IN "
                    f"({','.join('?' * len(CHANGE_TEXT))}) ORDER BY id DESC LIMIT ?",
                    (kid["id"], *CHANGE_TEXT, limit))
        people = {r["username"]: r["title"] or r["username"] for r in db.q("SELECT username, title FROM parents")}
        by_id = {r["plex_id"]: r["title"] or r["username"] for r in db.q("SELECT plex_id, username, title FROM parents")}
        out = []
        for r in rows:
            d = json.loads(r["detail"] or "{}")
            if r["action"] == "titles_batch":
                n = int(d.get("changed") or 0)
                if not n:
                    continue
                what = f"{n} title{'s' if n != 1 else ''}"
                text = {"allow": f"allowed {what}", "block": f"blocked {what}"}.get(d.get("kind"),
                                                                                   f"put {what} back to the age level")
            else:
                if "parent" in d:
                    d["parent"] = by_id.get(d["parent"], "another parent")
                try:
                    text = CHANGE_TEXT[r["action"]].format_map({"name": kid["name"], "title": "a title", **d})
                except (KeyError, ValueError):
                    continue
            who = "" if r["actor"] == "system" else ("You" if r["actor"] == viewer else people.get(r["actor"], cfg.owner_name))
            out.append({"at": r["at"], "who": who, "text": text})
        return out

    def ends_text(sched, now) -> str:
        """'Watch time ends at 8:00 PM (in 2 hr 13 min)' while inside today's watch hours."""
        start, end = rules.effective_window(sched, now.date())
        if start == end or not rules.within_window(start, end, now.time()):
            return ""
        end_t = rules.parse_hhmm(end)
        end_dt = now.replace(hour=end_t.hour, minute=end_t.minute, second=0, microsecond=0)
        if end_dt <= now:
            end_dt += timedelta(days=1)  # window runs past midnight
        left = int((end_dt - now).total_seconds())
        return f"Ends {rules.friendly_time(end)}"

    def summary_line(sched, now, used, limit) -> str:
        """One short line under the meter, like '1 hr 13 min left · Ends 8:00 PM'."""
        today = now.date()
        if limit == 0:
            first = "No watching today"
        elif limit is None:
            first = "No daily limit"
        else:
            first = f"{rules.minutes_text(max(0, limit - used))} left"
        start, end = rules.effective_window(sched, today)
        if start == end:
            second = "Any time today"
        elif rules.within_window(start, end, now.time()):
            second = ends_text(sched, now)
        else:
            second = f"Opens {rules.friendly_time(start)}"
        return f"{first} · {second}"

    def kid_status(kid) -> dict:
        today = svc.now_local().date()
        used = db.used(kid["id"], today.isoformat())
        sched = rules.Schedule.from_row(kid)
        limit = rules.limit_seconds(sched, today)
        now = svc.now_local()
        reason = rules.block_reason(sched, now, used) if kid["status"] in ("requested", "active") else None
        done_total = svc.progress.get(kid["id"])
        watching = enforcer.live.get(kid["id"], [])
        timed = sched.paused_until if sched.paused_until and now < sched.paused_until else None
        if kid["status"] != "active":
            state, state_text = "inactive", ""
        elif reason == "paused":
            state, state_text = "paused", ("Until you resume" if kid["paused"]
                                           else f"Until {rules.clock(timed.astimezone(svc.tz))}")
        elif reason == "hours":
            start = rules.effective_window(sched, today)[0]
            state, state_text = "hours", f"Outside watch hours. Opens at {rules.friendly_time(start)}"
        elif reason == "limit":
            state, state_text = "limit", "Out of screen time for today"
        elif watching:
            state, state_text = "watching", f"{watching[0]['title']} on {watching[0]['player']}"
        else:
            state, state_text = "idle", "Not watching right now"
        return {
            "status": kid["status"], "status_text": STATUS_TEXT.get(kid["status"], kid["status"]),
            "used": used, "used_text": rules.minutes_text(used),
            "limit_text": rules.minutes_text(limit),
            "remaining_text": rules.minutes_text(rules.remaining_seconds(sched, today, used)),
            "percent": min(100, int(used * 100 / limit)) if limit else 0,
            "blocked": reason,
            "blocked_text": {"paused": "Paused by a parent", "hours": "Outside watch hours",
                             "limit": "Out of time for today"}.get(reason or "", ""),
            "paused": bool(kid["paused"]) or bool(timed),
            "state": state, "state_text": state_text,
            "over": limit is not None and used >= limit,
            "hours_text": ("Any time today" if rules.effective_window(sched, today)[0] == rules.effective_window(sched, today)[1]
                           else "Watch hours today: " + " to ".join(rules.friendly_time(t) for t in rules.effective_window(sched, today))
                           + (" (later bedtime tonight)" if sched.later_day == today.isoformat() and sched.later_minutes else "")),
            "later": sched.later_minutes if sched.later_day == today.isoformat() else 0,
            # past tonight's bedtime (not a morning before watch hours open): offer 30 more minutes
            "evening": reason == "hours" and (lambda w: w[0] < w[1] and now.strftime("%H:%M") >= w[1])(
                rules.effective_window(sched, today)),
            # "Weekend rules today" for a sick day or snow day: shown on school days only
            "school_day": not rules.is_weekend(today, sched),
            "day_off": sched.break_until == today.isoformat() and not rules.is_weekend(today, replace(sched, break_until=None)),
            "ends_text": ends_text(sched, now),
            "of_limit": "" if limit is None or limit == 0 else f"of {rules.minutes_text(limit)}",
            "summary_line": summary_line(sched, now, used, limit),
            "no_watching": limit == 0,
            "bonus": kid["bonus_minutes"] if kid["bonus_day"] == today.isoformat() else 0,
            "watching": watching,
            "keeper_ok": enforcer.healthy() or not cfg.start_enforcer,
            "syncing": svc.sync_pending(kid["id"]),
            "sync_text": (f"Updating Plex: {done_total[0]} of {done_total[1]}" if done_total
                          else "Updating Plex" if svc.sync_pending(kid["id"]) else ""),
            "titles_count": kid["titles_count"],
            "accepted": bool(kid["plex_accepted"]),
        }

    def needs_link(kid) -> bool:
        """The parent still has to sign in as the child: never connected, or the Plex invite isn't accepted."""
        return kid["status"] == "draft" or (kid["status"] in ("requested", "active") and not kid["plex_accepted"])

    def new_link(kid_id: int, kid_parent: int) -> str:
        token = secrets.token_urlsafe(24)
        if not db.rotate_link(kid_id, kid_parent, token, time.time() + LINK_TTL):
            raise HTTPException(409, "Connection in progress. Wait for the sign-in to finish, then try again.")
        return token

    def notify(title: str, body: str, path: str = "/admin") -> None:
        notifier.send(title, body, path)

    # Visitors' addresses and signed-in users' keys are counted apart, so strangers can't push out user limits.
    rate_by_ip, rate_by_key = RateLimiter(), RateLimiter()
    app.state.rate_limits = (rate_by_ip, rate_by_key)

    def rate_limited(request: Request, limit: int = 10, window: int = 60, key: str | None = None) -> bool:
        if key:
            return rate_by_key.hit(key, limit, window)
        return rate_by_ip.hit(rate_key(_ip(request)), limit, window)

    def pin_flood() -> bool:
        return rate_by_key.hit("pins:all", PIN_STARTS_PER_MINUTE, 60)

    app.state.helpers = dict(kid_status=kid_status)

    # ---------- public ----------
    @app.get("/healthz")
    def healthz():
        # Liveness only: an empty database can have successful ticks without contacting Plex.
        keeper = "disabled" if not cfg.start_enforcer else "ok" if enforcer.healthy() else "starting-or-stale"
        return {"ok": True, "keeper": keeper}

    @app.get("/readyz")
    def readyz():
        keeper = "disabled"
        if cfg.start_enforcer:
            if enforcer.last_error:
                keeper = "error"
            elif enforcer._thread is None or not enforcer._thread.is_alive() or not enforcer.healthy():
                keeper = "starting-or-stale"
            else:
                keeper = "ok"
        plex_status, libraries = "unavailable", "unavailable"
        try:
            sections = plex.readiness_sections()
            plex_status = "ok"
            allowed_sections = svc.library_sections()
            libraries = "ok" if allowed_sections and all(
                sections.get(key) in ("movie", "show") for key in allowed_sections) else "invalid"
            with closing(db._connect()) as conn:
                if library_policy_warning(conn, cfg.kid_sections):
                    libraries = "invalid"
        except Exception:
            # Never expose exception text, URLs, credentials, library names, or family data.
            pass
        ok = plex_status == libraries == keeper == "ok"
        return JSONResponse({"ok": ok, "plex": plex_status, "libraries": libraries, "keeper": keeper},
                            status_code=200 if ok else 503, headers={"Cache-Control": "no-store"})

    @app.get("/manifest.webmanifest")
    def manifest():
        return JSONResponse({
            "name": app_name or "KidsWatch for Plex", "short_name": app_name or "KidsWatch", "start_url": "/", "scope": "/",
            "display": "standalone", "background_color": "#f7f4f0", "theme_color": "#ffffff",
            "icons": [{"src": "/static/icon-192.png?v=4", "sizes": "192x192", "type": "image/png"},
                      {"src": "/static/icon-512.png?v=4", "sizes": "512x512", "type": "image/png"},
                      {"src": "/static/icon-maskable-512.png?v=4", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
                      {"src": "/static/icon.svg?v=4", "sizes": "any", "type": "image/svg+xml"}],
        }, media_type="application/manifest+json")

    @app.get("/owner-art/{slot}")
    def owner_poster(slot: str):
        if slot not in SLOTS:
            raise HTTPException(404)
        if slot not in owner_art.images:
            return RedirectResponse(bundled_art(slot), status_code=307)
        data, content_type = owner_art.images[slot]
        return Response(data, media_type=content_type, headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        u = user(request)
        if u:
            return RedirectResponse("/admin" if u["role"] == "owner" else "/home", status_code=303)
        return render(request, "landing.html")

    @app.get("/help", response_class=HTMLResponse)
    def help_page(request: Request):
        return render(request, "help.html", nav="help", site_host=cfg.public_url.split("://")[-1].rstrip("/"))

    @app.get("/login")
    async def login(request: Request):
        if rate_limited(request) or pin_flood():
            return message(request, "Please wait before signing in again", "Too many sign-in attempts. Wait a minute and try again.", 429)
        try:
            pin_id, code = await run_in_threadpool(plex.create_pin)
        except PlexError:
            return message(request, "Plex isn't answering", "Plex sign-in is not reachable right now. Try again soon.", 503)
        request.session["pin"] = pins.put({"id": pin_id, "code": code, "at": time.time(),
                                         "return_to": safe_return(request.query_params.get("next"))})
        return RedirectResponse(plex.auth_url(code, cfg.public_url + "/auth/done"), status_code=303)

    @app.get("/auth/done")
    async def auth_done(request: Request):
        pin = pins.pop(request.session.pop("pin", None))
        if not pin:
            raise Redirect("/")
        if time.time() - pin.get("at", 0) > PIN_MAX_AGE:
            return message(request, "Sign-in took too long", "This sign-in link expired. Sign in again to get a new one.",
                           link=("/login", "Sign in with Plex"))
        try:
            token = await run_in_threadpool(plex.check_pin, pin["id"], pin["code"])
            if not token:
                return message(request, "Sign-in didn't finish",
                               "Plex didn't confirm the sign-in. Try again.", link=("/login", "Sign in with Plex"))
            acct = await run_in_threadpool(plex.account, token)
            owner = await run_in_threadpool(plex.owner)
            shared = await run_in_threadpool(svc.read_shared) if acct.id != owner.id else {}
        except PlexError as e:
            return message(request, "Plex isn't answering", str(e), 503)
        finally:
            token = None  # the parent's token is never stored
        sessions.end(request.session.get("sid"))  # signing in again replaces any earlier sign-in in this browser
        request.session.clear()
        if acct.id != owner.id and db.one("SELECT 1 FROM kids WHERE plex_id=? AND status<>'removed'", (acct.id,)):
            return message(request, "That's a child's account",
                           "This site is for parents. Sign in with your own Plex account.",
                           link=("/login", "Sign in again"))
        if acct.id == owner.id:
            role = "owner"
            if cfg.owner_code:
                return start_owner_code(request, acct)
        elif acct.id in shared:
            row = db.one("SELECT enabled FROM parents WHERE plex_id=?", (acct.id,))
            enabled = row["enabled"] if row else int((acct.username or "").lower() in cfg.seed_parents)
            if row:
                db.x("UPDATE parents SET username=?, title=?, thumb=?, last_login=? WHERE plex_id=?",
                     (acct.username, acct.title, acct.thumb, now_iso(), acct.id))
            else:
                db.x("INSERT INTO parents(plex_id,username,title,thumb,enabled,created_at,last_login) "
                     "VALUES(?,?,?,?,?,?,?)", (acct.id, acct.username, acct.title, acct.thumb, enabled,
                                               now_iso(), now_iso()))
            role = "parent" if enabled else "waiting"
        else:
            return message(request, "This site is private",
                           f"KidsWatch for Plex is only for families that {cfg.owner_name} shares Plex with. "
                           f"If you think you should have access, ask {cfg.owner_name} directly.", 403)
        sign_in(request, {"id": acct.id, "name": acct.name, "username": acct.username,
                          "thumb": acct.thumb, "role": role})
        db.audit(acct.username or str(acct.id), "signed_in", role=role, ip=_ip(request))
        if return_to := safe_return(pin.get("return_to")):
            raise Redirect(return_to)
        if role == "waiting":
            raise Redirect("/waiting")
        raise Redirect("/admin" if role == "owner" else "/home")

    # ----- admin sign-in code (second step, so a phished Plex sign-in link is not enough) -----
    pending_codes: dict[str, dict] = {}

    def start_owner_code(request: Request, acct):
        if not notifier.configured():
            return message(request, "Admin sign-in is not set up",
                           "Admin sign-in needs a notification receiver, which isn't configured yet.", 503)
        for k in [k for k, v in pending_codes.items() if time.time() - v["at"] > OWNER_CODE_TTL]:
            pending_codes.pop(k, None)
        nonce, code = secrets.token_urlsafe(16), f"{secrets.randbelow(10**6):06d}"
        pending_codes[nonce] = {"code": code, "at": time.time(), "tries": 0,
                                "user": {"id": acct.id, "name": acct.name, "username": acct.username,
                                         "thumb": acct.thumb, "role": "owner"}}
        request.session["owner_nonce"] = nonce
        where = addr.country(request)
        notifier.send("KidsWatch for Plex admin sign-in code",
                      f"Code {code}. Requested from {_ip(request)}{' (' + where + ')' if where else ''}. "
                      "If you didn't just sign in, ignore this.", "/verify")
        db.audit(acct.username or str(acct.id), "owner_code_sent", ip=_ip(request))
        raise Redirect("/verify")

    @app.get("/verify", response_class=HTMLResponse)
    def verify_page(request: Request):
        if request.session.get("owner_nonce") not in pending_codes:
            raise Redirect("/")
        return render(request, "verify.html")

    @app.post("/verify")
    async def verify_code(request: Request):
        f = await form(request)
        nonce = request.session.get("owner_nonce")
        p = pending_codes.get(nonce or "")
        if not p or time.time() - p["at"] > OWNER_CODE_TTL:
            pending_codes.pop(nonce or "", None)
            request.session.pop("owner_nonce", None)
            return message(request, "That code expired", "Sign in again to get a new code.",
                           link=("/login", "Sign in with Plex"))
        if not secrets.compare_digest(re.sub(r"[^0-9]", "", f.get("code", "")), p["code"]):
            p["tries"] += 1
            if p["tries"] >= OWNER_CODE_TRIES:
                pending_codes.pop(nonce, None)
                request.session.pop("owner_nonce", None)
                db.audit("system", "owner_code_locked", ip=_ip(request))
                return message(request, "Too many wrong codes", "Sign in again to get a new code.", 403,
                               link=("/login", "Sign in with Plex"))
            flash(request, "That code isn't right. Check your newest notification.", "error")
            raise Redirect("/verify")
        pending_codes.pop(nonce, None)
        sign_in(request, p["user"])
        db.audit(p["user"]["username"], "signed_in", role="owner", ip=_ip(request))
        raise Redirect("/admin")

    @app.post("/logout")
    async def logout(request: Request):
        await form(request)
        sessions.end(request.session.get("sid"))  # a copy of this cookie stops working too
        request.session.clear()
        raise Redirect("/")

    # ---------- parent ----------
    # What copying rules between children carries over. Never the age level or "Only titles I allow" mode (that
    # would widen or narrow what the child sees) and never the free-movies block (only settable before connecting).
    COPY_FIELDS = ("sections", "weekday_minutes", "weekend_minutes", "weekday_start", "weekday_end",
                   "weekend_start", "weekend_end", "friday_weekend", "sunday_school", "break_until")

    def kid_rows(parent_id: int):
        return db.q("SELECT * FROM kids WHERE (parent_id=? OR id IN (SELECT kid_id FROM kid_parents WHERE parent_id=?)) "
                    "AND status NOT IN ('removed','declined') ORDER BY id", (parent_id, parent_id))

    @app.get("/home", response_class=HTMLResponse)
    def home(request: Request):
        u = require(request, "parent", "owner")
        kids = kid_rows(u["id"])
        active = [k for k in kids if k["status"] == "active"]
        open_reqs = db.one("SELECT COUNT(*) AS n FROM requests WHERE parent_id=? AND status='open'", (u["id"],))["n"]
        replies = db.q("SELECT r.*, k.name AS kid_name FROM requests r LEFT JOIN kids k ON k.id=r.kid_id "
                       "WHERE r.parent_id=? AND r.status NOT IN ('open','cancelled') AND r.resolved_at>=? ORDER BY r.id DESC LIMIT 3",
                       (u["id"], (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")))
        news = {k["id"]: new_title_count(k) for k in active}
        return render(request, "home.html", nav="home", kids=[(k, kid_status(k)) for k in kids], news=news,
                      can_pause_all=len(active) >= 2, all_paused=bool(active) and all(k["paused"] for k in active),
                      open_reqs=open_reqs, replies=replies)

    @app.post("/kids/pause-all")
    async def pause_all(request: Request):
        u = require(request, "parent", "owner")
        f = await form(request)
        on = f.get("action") == "pause"
        for k in kid_rows(u["id"]):
            if k["status"] == "active":
                db.update_kid(k["id"], paused=1 if on else 0, paused_until=None)
        db.audit(u["username"], "pause_all" if on else "resume_all")
        flash(request, "Everyone is paused. Videos stop within a minute." if on else "Everyone can watch again.")
        raise Redirect("/home")

    @app.get("/kids/new", response_class=HTMLResponse)
    def kid_new(request: Request):
        u = require(request, "parent", "owner")
        return render(request, "kid_new.html", nav="home", siblings=kid_rows(u["id"]))

    @app.post("/kids/new")
    async def kid_create(request: Request):
        u = require(request, "parent", "owner")
        f = await form(request)
        name = re.sub(r"\s+", " ", f.get("name", "")).strip()[:40]
        level = f.get("age_level", rules.DEFAULT_AGE_LEVEL)
        if not name or level not in rules.AGE_LEVELS:
            flash(request, "Add your child's first name and pick an age level.", "error")
            raise Redirect("/kids/new")
        if db.one("SELECT COUNT(*) AS n FROM kids WHERE parent_id=? AND status NOT IN ('removed','declined')",
                  (u["id"],))["n"] >= 8:
            flash(request, "One parent account can add up to 8 children.", "error")
            raise Redirect("/home")
        kid_id = db.x("INSERT INTO kids(parent_id,name,status,age_level,sections,block_online,weekday_minutes,"
                      "weekend_minutes,weekday_start,weekday_end,weekend_start,weekend_end,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (u["id"], name, "draft", level, ",".join(svc.library_sections()), 1,
                       DEFAULTS["weekday_minutes"], DEFAULTS["weekend_minutes"], DEFAULTS["weekday_start"],
                       DEFAULTS["weekday_end"], DEFAULTS["weekend_start"], DEFAULTS["weekend_end"],
                       now_iso(), now_iso()))
        source = next((k for k in kid_rows(u["id"]) if is_num(f.get("copy_from", "")) and k["id"] == int(f["copy_from"])),
                      None)
        if source:
            db.update_kid(kid_id, **{c: source[c] for c in COPY_FIELDS})
            db.x("INSERT OR IGNORE INTO kid_titles(kid_id,rating_key,kind,section_key,item_type,title,year) "
                 "SELECT ?,rating_key,kind,section_key,item_type,title,year FROM kid_titles WHERE kid_id=?",
                 (kid_id, source["id"]))
        db.audit(u["username"], "kid_added", kid=kid_id, name=name, **({"copied_from": source["id"]} if source else {}))
        new_link(kid_id, kid_parent=u["id"])
        flash(request, f"{name} added with {source['name']}'s schedule and picks. Next, connect their Plex account."
              if source else f"{name} added. Next, connect their Plex account.")
        raise Redirect(f"/kids/{kid_id}")

    def kid_ctx(request: Request, kid, tab: str) -> dict:
        """What every child tab needs: the header, tab counts and status."""
        titles = db.q("SELECT kind FROM kid_titles WHERE kid_id=?", (kid["id"],))
        return dict(nav="home", kid=kid, tab=tab, st=kid_status(kid), parent=parent_name(kid["parent_id"]),
                    co_parents=co_parents(kid["id"]),
                    needs_link=needs_link(kid), allow_n=sum(t["kind"] == "allow" for t in titles),
                    block_n=sum(t["kind"] == "block" for t in titles), new_titles=new_title_count(kid),
                    level_text=level_text(kid))

    def level_text(kid) -> str:
        if kid["mode"] == "picked":
            return "Only titles you allow"
        return rules.AGE_LEVELS.get(kid["age_level"], rules.AGE_LEVELS[rules.DEFAULT_AGE_LEVEL])["label"] + " level"

    def new_since(kid) -> float:
        """'New' means added since the parent last opened the titles page (or in the last 14 days before that)."""
        if kid["titles_seen_at"]:
            return datetime.fromisoformat(kid["titles_seen_at"]).timestamp()
        return time.time() - 14 * 86400

    def new_title_count(kid) -> int:
        """How many titles a parent could review: listed on the titles page and new since their last look.

        Reads the library only from memory, so a slow Plex never holds up a page."""
        if kid["status"] not in ("requested", "active"):
            return 0
        cutoff = new_since(kid)
        count = 0
        for sec in svc.kid_sections(kid):
            items = plex.library_cached(sec) or []
            count += sum(1 for it in items if it.added_at > cutoff and rules.allowable(it.item_type, it.rating))
        return count

    def link_url(kid) -> str | None:
        row = db.one("SELECT token FROM link_tokens WHERE kid_id=? AND used=0 AND expires_at>? "
                     "ORDER BY expires_at DESC LIMIT 1", (kid["id"], time.time()))
        return f"{cfg.public_url}/link/{row['token']}" if row else None

    def week_chart(kid) -> dict:
        """Last 7 days of screen time as SVG geometry (bars are attributes, so the strict CSP stays happy)."""
        today = svc.now_local().date()
        sched = rules.Schedule.from_row(kid)
        days = [today - timedelta(days=i) for i in range(6, -1, -1)]
        used = {r["day"]: r["seconds"] for r in db.q("SELECT day, seconds FROM usage WHERE kid_id=? AND day>=?",
                                                      (kid["id"], days[0].isoformat()))}
        limits = [rules.limit_seconds(sched, d) for d in days]
        top = max([used.get(d.isoformat(), 0) for d in days] + [l or 0 for l in limits] + [3600])
        slack = max(120, 3 * cfg.poll_seconds)  # the keeper stops videos within a poll or two of the limit
        width, height, pad = 420, 150, 22
        slot = width / 7
        bars = []
        for i, d in enumerate(days):
            sec = used.get(d.isoformat(), 0)
            h = round((height - pad * 2) * sec / top) if sec else 0
            limit = limits[i]
            bars.append({
                "x": round(i * slot + slot * .2, 1), "w": round(slot * .6, 1), "y": height - pad - max(h, 2),
                "h": max(h, 2), "cls": "empty" if not sec else ("over" if limit is not None and sec > limit + slack else ""),
                "label": d.strftime("%a"), "cx": round(i * slot + slot / 2, 1), "today": d == today,
                "value": rules.short_minutes(sec) if sec else "", "vy": height - pad - max(h, 2) - 6,
                "title": f"{d.strftime('%A')}: {rules.minutes_text(sec)}" + (f" of {rules.minutes_text(limit)}" if limit is not None else ""),
                "ly": None if limit is None else round(height - pad - (height - pad * 2) * limit / top, 1),
                "lx1": round(i * slot + slot * .1, 1), "lx2": round(i * slot + slot * .9, 1),
            })
        total = sum(used.get(d.isoformat(), 0) for d in days)
        prev_start = (days[0] - timedelta(days=7)).isoformat()
        prev = db.one("SELECT COALESCE(SUM(seconds), 0) AS s FROM usage WHERE kid_id=? AND day>=? AND day<?",
                      (kid["id"], prev_start, days[0].isoformat()))["s"]
        if prev >= 1800 and total:
            change = round((total - prev) * 100 / prev)
            trend = ("About the same as the week before" if abs(change) < 5
                     else f"{abs(change)}% {'more' if change > 0 else 'less'} than the week before")
        else:
            trend = None
        created = datetime.fromisoformat(kid["created_at"]).astimezone(svc.tz).date()
        span = max(1, min(7, (today - created).days + 1))  # don't average over days before the child was added
        return {"bars": bars, "width": width, "height": height, "base": height - pad, "total": rules.minutes_text(total),
                "average": rules.minutes_text(total // span) if span >= 2 else None, "trend": trend}

    @app.get("/kids/{kid_id}", response_class=HTMLResponse)
    async def kid_page(request: Request, kid_id: int):
        u, kid = kid_for(request, kid_id)
        recent, history_error = [], None
        if kid["plex_id"] and kid["status"] == "active":
            try:
                recent = (await run_in_threadpool(plex.history, kid["plex_id"], 4))[:4]
            except PlexError as e:
                history_error = str(e)
        return render(request, "kid_overview.html", **kid_ctx(request, kid, "overview"), link=link_url(kid),
                      chart=week_chart(kid), recent=recent, history_error=history_error, picks=svc.choices(kid_id))

    def week_ahead(kid, any_times=None) -> list[dict]:
        """The saved schedule spelled out for today and the next six days, special days included."""
        sched, start_day = rules.Schedule.from_row(kid), svc.now_local().date()
        out = []
        for i in range(7):
            d = start_day + timedelta(days=i)
            weekend = rules.is_weekend(d, sched)
            base = sched.weekend_minutes if weekend else sched.weekday_minutes
            limit = rules.limit_seconds(sched, d) if i == 0 else (None if base is None else base * 60)
            start, end = rules.effective_window(sched, d) if i == 0 else rules.window_for(sched, d)
            note = ""
            if i == 0 and sched.break_until == d.isoformat() and d.weekday() < 5:
                note = "Day off"
            elif sched.break_until and d.isoformat() <= sched.break_until and d.weekday() < 5:
                note = "School break"
            elif d.weekday() == 4 and sched.friday_weekend:
                note = "Weekend rules"
            elif d.weekday() == 6 and sched.sunday_school:
                note = "School night"
            out.append({"label": "Today" if i == 0 else ("Tomorrow" if i == 1 else d.strftime("%A")),
                        "day": d.strftime("%A") if i < 2 else "",
                        "limit": "No watching" if limit == 0 else rules.minutes_text(limit),
                        "hours": ("Choose different times, or turn on Any time." if start == end and any_times is not None
                                  and not any_times["weekend" if weekend else "weekday"] else
                                  "Any time" if start == end else f"{rules.friendly_time(start)} to {rules.friendly_time(end)}"),
                        "note": note, "none": limit == 0, "date": d.isoformat(), "dow": d.weekday(),
                        "bonus": sched.bonus_minutes if i == 0 and sched.bonus_day == d.isoformat() else 0,
                        "later": sched.later_minutes if i == 0 and sched.later_day == d.isoformat() else 0})
        return out

    @app.get("/kids/{kid_id}/schedule", response_class=HTMLResponse)
    def kid_schedule(request: Request, kid_id: int):
        u, kid = kid_for(request, kid_id)
        draft = request.session.pop("schedule_draft", None)
        any_times = {p: kid[p + "_start"] == kid[p + "_end"] for p in ("weekday", "weekend")}
        if draft and draft.get("kid") == kid_id:
            kid = dict(kid, **draft["values"])
            any_times = draft["any_times"]
        return render(request, "kid_schedule.html", **kid_ctx(request, kid, "schedule"), any_times=any_times,
                      minute_options=[15, 30, 45, 60, 90, 120, 150, 180, 240, 300, 360],
                      today=svc.today(), on_break=bool(kid["break_until"]) and kid["break_until"] >= svc.today(),
                      ahead=week_ahead(kid, any_times))

    @app.get("/kids/{kid_id}/activity", response_class=HTMLResponse)
    async def kid_activity(request: Request, kid_id: int):
        u, kid = kid_for(request, kid_id)
        history, history_error = [], None
        if kid["plex_id"] and kid["status"] == "active":
            try:
                history = await run_in_threadpool(plex.history, kid["plex_id"], 30)
            except PlexError as e:
                history_error = str(e)
        stops = db.q("SELECT * FROM stops WHERE kid_id=? ORDER BY id DESC LIMIT 10", (kid_id,))
        devices, top = {}, {}
        for h in history:
            if h.get("player"):
                devices[h["player"]] = devices.get(h["player"], 0) + int(h.get("seconds") or 0)
            key = h.get("poster") or h.get("title")
            name = (h.get("title") or "").split(" - ")[0].split(": ")[0] if h.get("poster") else h.get("title")
            entry = top.setdefault(key, {"title": name, "poster": h.get("poster"), "seconds": 0, "plays": 0})
            entry["seconds"] += int(h.get("seconds") or 0)
            entry["plays"] += 1
        return render(request, "kid_activity.html", **kid_ctx(request, kid, "activity"), history=history,
                      history_error=history_error, stops=stops, chart=week_chart(kid), changes=kid_changes(kid, u["username"]),
                      picks=svc.choices(kid_id),
                      devices=sorted(devices.items(), key=lambda x: -x[1])[:4],
                      top=sorted(top.values(), key=lambda x: -x["seconds"])[:3])

    @app.get("/kids/{kid_id}/settings", response_class=HTMLResponse)
    async def kid_settings_page(request: Request, kid_id: int):
        u, kid = kid_for(request, kid_id)
        try:
            section_titles = await run_in_threadpool(plex.section_titles)
        except PlexError:
            section_titles = {k: f"Library {k}" for k in svc.library_sections()}
        return render(request, "kid_settings.html", **kid_ctx(request, kid, "settings"),
                      sections=[(k, section_titles.get(k, k)) for k in svc.library_sections()], chosen=svc.kid_sections(kid),
                      others=[k for k in kid_rows(u["id"]) if k["id"] != kid_id])

    @app.get("/kids/{kid_id}/status.json")
    def kid_status_json(request: Request, kid_id: int):
        _, kid = kid_for(request, kid_id)
        return kid_status(kid)

    @app.post("/kids/{kid_id}/copy-to")
    async def kid_copy_to(request: Request, kid_id: int):
        """Give another of this parent's children the same schedule, libraries and title picks."""
        u, kid = kid_for(request, kid_id)
        f = await form(request)
        if not is_num(f.get("target", "")):
            raise HTTPException(400)
        _, target = kid_for(request, int(f["target"]))
        if target["id"] == kid["id"] or target["status"] in ("removed", "declined"):
            raise HTTPException(400)
        db.update_kid(target["id"], **{c: kid[c] for c in COPY_FIELDS})
        db.x("DELETE FROM kid_titles WHERE kid_id=?", (target["id"],))
        db.x("INSERT INTO kid_titles(kid_id,rating_key,kind,section_key,item_type,title,year) "
             "SELECT ?,rating_key,kind,section_key,item_type,title,year FROM kid_titles WHERE kid_id=?",
             (target["id"], kid["id"]))
        db.audit(u["username"], "rules_copied", kid=target["id"], source=kid["id"], source_name=kid["name"])
        ok = await run_in_threadpool(svc.apply, target["id"], u["username"])
        svc.queue_sync(target["id"])
        flash(request, f"{target['name']} now has {kid['name']}'s schedule, libraries and title picks."
              if ok else "Rules copied. Plex hasn't applied them yet. KidsWatch will keep retrying.", "ok" if ok else "error")
        raise Redirect(f"/kids/{target['id']}")

    @app.post("/kids/{kid_id}/settings")
    async def kid_settings(request: Request, kid_id: int):
        u, kid = kid_for(request, kid_id)
        f = await form(request)
        name = re.sub(r"\s+", " ", f.get("name", kid["name"])).strip()[:40] or kid["name"]
        choice = f.get("level_choice", "")
        if choice == "picked":
            mode, level = "picked", kid["age_level"]
        elif choice.startswith("age:") and choice[4:] in rules.AGE_LEVELS:
            mode, level = "age", choice[4:]
        else:
            flash(request, "Pick what your child can watch.", "error")
            raise Redirect(f"/kids/{kid_id}/settings")
        sections = [k for k in svc.library_sections() if f.get(f"section_{k}")]
        if not sections:
            flash(request, "Choose at least one library.", "error")
            raise Redirect(f"/kids/{kid_id}/settings")
        fields = dict(name=name, age_level=level, mode=mode, sections=",".join(sections))
        if is_num(f.get("color", ""), 1) and int(f["color"]) < 6:
            fields["color"] = int(f["color"])
        if kid["status"] == "draft":
            fields["block_online"] = 1 if f.get("block_online") else 0
        db.update_kid(kid_id, **fields)
        db.audit(u["username"], "settings_saved", kid=kid_id, **fields)
        ok = await run_in_threadpool(svc.apply, kid_id, u["username"])
        svc.queue_sync(kid_id)
        flash(request, "Settings saved." if ok else "Settings saved. Plex hasn't applied them yet. KidsWatch will keep retrying.",
              "ok" if ok else "error")
        raise Redirect(f"/kids/{kid_id}/settings")

    @app.post("/kids/{kid_id}/schedule")
    async def kid_schedule_save(request: Request, kid_id: int):
        u, kid = kid_for(request, kid_id)
        f = await form(request)
        errors = []

        def minutes(field: str):
            raw = f.get(field, "")
            if raw in ("", "none"):
                return None
            if not is_num(raw, 4) or not 0 <= int(raw) <= 1440:  # 0 means "no watching"
                errors.append("Enter screen time in minutes, from 0 to 1440 (24 hours).")
                return None
            return int(raw)

        weekday, weekend = minutes("weekday_minutes"), minutes("weekend_minutes")
        hours = {k: f.get(k, kid[k]) for k in ("weekday_start", "weekday_end", "weekend_start", "weekend_end")}
        if f.get("any_time_weekday"):
            hours["weekday_start"] = hours["weekday_end"] = "00:00"
        if f.get("any_time_weekend"):
            hours["weekend_start"] = hours["weekend_end"] = "00:00"
        if not all(rules.valid_hhmm(v) for v in hours.values()):
            errors.append("Choose both a start and an end time for watch hours.")
        equal_days = [p for p in ("weekday", "weekend") if not f.get("any_time_" + p)
                      and rules.valid_hhmm(hours[p + "_start"]) and hours[p + "_start"] == hours[p + "_end"]]
        if equal_days:
            for p in equal_days:
                errors.append(f"{'Weekdays' if p == 'weekday' else 'Weekends'}: Choose different times, or turn on Any time.")
            # Keep only bounded schedule values, never a form token, in the signed session.
            values = {k: v if rules.valid_hhmm(v) else kid[k] for k, v in hours.items()}
            values.update(weekday_minutes=weekday, weekend_minutes=weekend,
                          friday_weekend=bool(f.get("friday_weekend")), sunday_school=bool(f.get("sunday_school")),
                          break_until=f.get("break_until", "") if rules.valid_date(f.get("break_until", "")) else None)
            request.session["schedule_draft"] = {"kid": kid_id, "values": values,
                "any_times": {p: bool(f.get("any_time_" + p)) for p in ("weekday", "weekend")}}
        if errors:
            for e in errors:
                flash(request, e, "error")
            raise Redirect(f"/kids/{kid_id}/schedule")
        break_until = f.get("break_until", "").strip() or None
        if break_until and not rules.valid_date(break_until):
            flash(request, "That school break date isn't a real date. Pick one from the calendar, or leave it empty.",
                  "error")
            raise Redirect(f"/kids/{kid_id}/schedule")
        extra = dict(friday_weekend=1 if f.get("friday_weekend") else 0, sunday_school=1 if f.get("sunday_school") else 0,
                     break_until=break_until)
        db.update_kid(kid_id, weekday_minutes=weekday, weekend_minutes=weekend, **hours, **extra)
        db.audit(u["username"], "schedule_saved", kid=kid_id, weekday=weekday, weekend=weekend, **hours, **extra)
        flash(request, "Schedule saved.")
        raise Redirect(f"/kids/{kid_id}/schedule")

    @app.post("/kids/{kid_id}/control")
    async def kid_control(request: Request, kid_id: int):
        u, kid = kid_for(request, kid_id)
        f = await form(request)
        action = f.get("action", "")
        today = svc.today()
        detail: dict = {}
        changed = True  # False when the button turned out to have nothing to do; then nothing is logged
        back = f.get("back", "")
        back = back if back in ("/home", "/admin", f"/kids/{kid_id}") else f"/kids/{kid_id}"
        # Undo is offered only when it puts things back exactly as they were
        was_paused = bool(kid["paused"]) or bool(kid["paused_until"] and kid["paused_until"] > now_iso())
        undo_for = {"pause": "resume" if not was_paused else None,
                    "pause:30": "resume" if not was_paused else None,
                    "pause:60": "resume" if not was_paused else None,
                    "pause:tomorrow": "resume" if not was_paused else None,
                    "bonus:15": "reset_bonus" if kid["bonus_day"] != today or not kid["bonus_minutes"] else None,
                    "bonus:30": "reset_bonus" if kid["bonus_day"] != today or not kid["bonus_minutes"] else None,
                    "bonus:60": "reset_bonus" if kid["bonus_day"] != today or not kid["bonus_minutes"] else None,
                    "later:30": "reset_later" if kid["later_day"] != today or not kid["later_minutes"] else None,
                    "later:60": "reset_later" if kid["later_day"] != today or not kid["later_minutes"] else None,
                    "dayoff": "reset_dayoff" if not kid["break_until"] or kid["break_until"] < today else None,
                    }.get(action)
        if action == "pause":
            db.update_kid(kid_id, paused=1, paused_until=None)
            flash(request, f"{kid['name']} is paused until you resume. Videos stop within a minute.")
        elif action in ("pause:30", "pause:60", "pause:tomorrow"):
            until = rules.pause_end(action.split(":")[1], svc.now_local(), rules.Schedule.from_row(kid))
            db.update_kid(kid_id, paused=0, paused_until=until.astimezone(timezone.utc).isoformat(timespec="seconds"))
            when = rules.clock(until) if until.date() == svc.now_local().date() else f"tomorrow at {rules.clock(until)}"
            flash(request, f"{kid['name']} is paused until {when}.")
            action, detail = "pause_timed", {"until": when}
        elif action == "resume":
            db.update_kid(kid_id, paused=0, paused_until=None)
            flash(request, f"{kid['name']} can watch again.")
        elif action in ("bonus:15", "bonus:30", "bonus:60"):
            extra = int(action.split(":")[1])
            action, detail = "bonus", {"minutes": extra}
            db.increment_daily(kid_id, "bonus", today, extra, 600)
            flash(request, f"Added {extra} minutes for today.")
        elif action in ("later:30", "later:60"):
            extra = int(action.split(":")[1])
            action, detail = "later", {"minutes": extra}
            db.increment_daily(kid_id, "later", today, extra, 180)
            flash(request, f"Bedtime is {extra} minutes later tonight.")
        elif action == "dayoff":
            if rules.is_weekend(svc.now_local().date(), rules.Schedule.from_row(kid)):
                flash(request, f"{kid['name']} already has weekend rules today.")
                changed = False
            else:
                db.update_kid(kid_id, break_until=today)
                flash(request, f"{kid['name']} gets weekend rules today: weekend screen time and watch hours.")
        elif action == "reset_dayoff":
            if kid["break_until"] == today:
                db.update_kid(kid_id, break_until=None)
                flash(request, "Back to school-day rules for today.")
            else:
                flash(request, "No one-day break to undo. To change a longer school break, use the Schedule tab.")
                changed = False
        elif action == "reset_later":
            db.update_kid(kid_id, later_day=None, later_minutes=0)
            flash(request, "Tonight's later bedtime was removed.")
        elif action == "reset_bonus":
            db.update_kid(kid_id, bonus_day=None, bonus_minutes=0)
            flash(request, "Today's extra time was removed.")
        else:
            raise HTTPException(400)
        if changed:
            db.audit(u["username"], "control_" + action, kid=kid_id, **detail)
            flashes = request.session.get("flash", [])
            if undo_for and flashes:
                flashes[-1]["undo"] = {"url": f"/kids/{kid_id}/control", "action": undo_for, "back": back}
        raise Redirect(back)

    @app.get("/kids/{kid_id}/connect-qr.svg")
    def kid_connect_qr(request: Request, kid_id: int):
        """The connect link as a QR code, so a parent can open it on the child's own tablet or phone."""
        _, kid = kid_for(request, kid_id)
        url = link_url(kid) if needs_link(kid) else None
        if not url:
            raise HTTPException(404)
        buf = io.BytesIO()
        segno.make(url, error="m").save(buf, kind="svg", scale=4, border=2, dark="#15171c", light="#ffffff",
                                        xmldecl=False, svgclass=None, lineclass=None)
        return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    @app.post("/kids/{kid_id}/connect")
    async def kid_connect_link(request: Request, kid_id: int):
        u, kid = kid_for(request, kid_id)
        await form(request)
        if not needs_link(kid):
            raise HTTPException(400)
        new_link(kid_id, kid["parent_id"])
        flash(request, "New sign-in link ready. Copy it or scan the QR code.")
        raise Redirect(f"/kids/{kid_id}#connect")

    @app.post("/kids/{kid_id}/remove")
    async def kid_remove(request: Request, kid_id: int):
        u, kid = kid_for(request, kid_id)
        f = await form(request)
        if u["role"] != "owner" and kid["parent_id"] != u["id"]:
            raise HTTPException(404)  # co-parents can't remove a child
        if f.get("confirm", "").strip().lower() != kid["name"].strip().lower():
            flash(request, f"To remove, type {kid['name']} in the box.", "error")
            raise Redirect(f"/kids/{kid_id}/settings#remove")
        try:
            await run_in_threadpool(svc.remove, kid_id, u["username"])
        except ConnectionInProgress as e:
            raise HTTPException(409, str(e)) from e
        except PlexError as e:
            flash(request, f"Plex didn't accept the removal: {e}", "error")
            raise Redirect(f"/kids/{kid_id}/settings")
        db.x("UPDATE requests SET status='declined', resolved_at=? WHERE kid_id=? AND status='open'",
             (now_iso(), kid_id))
        notify("KidsWatch for Plex: child removed", f"{u['name']} removed {kid['name']}.")
        flash(request, f"{kid['name']} was removed and no longer has access.")
        raise Redirect("/admin?tab=kids" if u["role"] == "owner" and kid["parent_id"] != u["id"] else "/home")

    # ----- titles -----
    @app.get("/kids/{kid_id}/titles", response_class=HTMLResponse)
    async def kid_titles_page(request: Request, kid_id: int):
        u, kid = kid_for(request, kid_id)
        ctx = kid_ctx(request, kid, "titles")
        choices = svc.choices(kid_id)
        try:
            items = []
            for key in svc.kid_sections(kid):
                items += await run_in_threadpool(plex.library, key)
        except PlexError as e:
            return render(request, "kid_titles.html", **ctx, rows=[], counts={}, error=str(e))
        recent_cutoff = new_since(kid)
        rows = []
        for it in sorted(items, key=lambda i: i.title.casefold()):
            if not rules.allowable(it.item_type, it.rating):
                continue  # R, TV-MA and unrated titles are never shown to kids, so they aren't listed
            choice = choices.get(it.rating_key)
            rows.append({"key": it.rating_key, "title": it.title, "year": it.year, "rating": it.rating,
                         "type": it.item_type, "choice": choice, "new": it.added_at > recent_cutoff, "added": it.added_at,
                         "genres": " ".join(it.genres).casefold(),
                         "base": kid["mode"] != "picked" and rules.rating_allowed(kid["age_level"], it.item_type, it.rating),
                         "visible": rules.title_visible(kid["age_level"], kid["mode"], it.item_type, it.rating, choice)})
        counts = {"all": len(rows), "visible": sum(r["visible"] for r in rows), "new": sum(r["new"] for r in rows)}
        if is_parent_of(u["id"], kid):  # only the child's parents clear the "new" badge, not the owner
            db.update_kid(kid_id, titles_seen_at=now_iso())
        return render(request, "kid_titles.html", **ctx, rows=rows, counts=counts, error=None)

    @app.get("/kids/{kid_id}/titles/{rating_key}.json")
    async def kid_title_detail(request: Request, kid_id: int, rating_key: int):
        _, kid = kid_for(request, kid_id)
        item = None
        try:
            for key in svc.kid_sections(kid):
                item = next((it for it in await run_in_threadpool(plex.library, key) if it.rating_key == rating_key), item)
        except PlexError:
            return JSONResponse({"error": "Plex isn't answering right now."}, status_code=503)
        if not item or not rules.allowable(item.item_type, item.rating):
            raise HTTPException(404)
        choice = svc.choices(kid_id).get(rating_key)
        minutes = item.duration_ms // 60000
        if item.item_type == "show":
            length = " · ".join(x for x in (f"{item.seasons} season{'s' if item.seasons != 1 else ''}" if item.seasons else "",
                                            f"{item.episodes} episodes" if item.episodes else "") if x)
        else:
            length = rules.minutes_text(minutes * 60) if minutes else ""
        return {"key": rating_key, "title": item.title, "year": item.year, "rating": item.rating,
                "type": "Show" if item.item_type == "show" else "Movie", "length": length,
                "genres": ", ".join(item.genres), "summary": item.summary, "choice": choice,
                "visible": rules.title_visible(kid["age_level"], kid["mode"], item.item_type, item.rating, choice)}

    @app.post("/kids/{kid_id}/titles")
    async def kid_title_set(request: Request, kid_id: int):
        u, kid = kid_for(request, kid_id)
        f = await form(request)
        kind = f.get("kind") or None
        try:
            rating_key = int(f.get("key", ""))
        except ValueError:
            raise HTTPException(400)
        if kind not in ("allow", "block", None) or kid["status"] in ("declined", "removed"):
            raise HTTPException(400)
        try:
            await run_in_threadpool(svc.set_title, kid_id, rating_key, kind, u["username"])
        except PlexError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return {"ok": True}

    @app.post("/kids/{kid_id}/titles/batch")
    async def kid_title_batch(request: Request, kid_id: int):
        u, kid = kid_for(request, kid_id)
        f = await form(request)
        kind = f.get("kind") or None
        keys = [int(k.strip()) for k in f.get("keys", "").split(",") if is_num(k.strip())][:1000]
        if kind not in ("allow", "block", None) or not keys or kid["status"] in ("declined", "removed"):
            raise HTTPException(400)
        if rate_limited(request, limit=20, window=60, key=f"batch:{u['id']}"):
            raise HTTPException(429, "Too many changes at once. Wait a minute and try again.")
        done, skipped = 0, 0
        for key in keys:
            try:
                await run_in_threadpool(svc.set_title, kid_id, key, kind, u["username"], False, False)
                done += 1
            except PlexError:
                skipped += 1
        db.audit(u["username"], "titles_batch", kid=kid_id, kind=kind or "reset", changed=done, skipped=skipped)
        svc.queue_sync(kid_id)
        return {"ok": True, "done": done, "skipped": skipped}

    @app.get("/poster/{rating_key}")
    async def poster(request: Request, rating_key: int):
        require(request, "parent", "owner")
        try:
            known = set()
            for key in svc.library_sections():
                known |= {it.rating_key for it in await run_in_threadpool(plex.library, key)
                          if rules.allowable(it.item_type, it.rating)}
        except PlexError:
            raise HTTPException(503)
        if rating_key not in known:
            raise HTTPException(404)
        try:
            body, ctype = await run_in_threadpool(plex.poster, rating_key)
        except PlexError:
            raise HTTPException(404)
        if not ctype.startswith("image/"):
            raise HTTPException(404)
        return Response(body, media_type=ctype, headers={"Cache-Control": "private, max-age=86400"})

    # ----- requests -----
    @app.get("/requests", response_class=HTMLResponse)
    def requests_page(request: Request, kid: int | None = None, kind: str = "title", message: str = "", sent: str = ""):
        u = require(request, "parent", "owner")
        reqs = db.q("SELECT r.*, k.name AS kid_name FROM requests r LEFT JOIN kids k ON k.id=r.kid_id "
                    "WHERE r.parent_id=? ORDER BY r.id DESC LIMIT 50", (u["id"],))
        db.x("UPDATE parents SET requests_seen_at=? WHERE plex_id=?", (now_iso(), u["id"]))
        known = kind in form_kinds
        draft_sent = sent if sent and sent == request.session.get("request_sent") else ""
        if draft_sent:
            request.session.pop("request_sent", None)
        return render(request, "requests.html", nav="requests", kids=kid_rows(u["id"]), reqs=reqs, kid_id=kid,
                      form_kinds=form_kinds, kind=kind if known else next(iter(form_kinds)),
                      prefill=message[:200] if known else "", draft_sent=draft_sent)

    @app.post("/requests/{req_id}/cancel")
    async def request_cancel(request: Request, req_id: int):
        u = require(request, "parent", "owner")
        await form(request)
        r = db.one("SELECT * FROM requests WHERE id=? AND parent_id=?", (req_id, u["id"]))
        if not r or r["status"] != "open" or r["kind"] == "add_child":
            raise HTTPException(404)
        db.x("UPDATE requests SET status='cancelled', resolved_at=? WHERE id=?", (now_iso(), req_id))
        db.audit(u["username"], "request_cancelled", request=req_id)
        flash(request, "Request canceled.")
        raise Redirect("/requests")

    @app.get("/requests/new")
    def request_new(request: Request, kid: int | None = None, kind: str = "title"):
        require(request, "parent", "owner")
        raise Redirect("/requests" + (f"?kid={int(kid)}&kind={kind}" if kid else f"?kind={kind}") + "#new")

    @app.post("/requests")
    async def request_create(request: Request):
        u = require(request, "parent", "owner")
        f = await form(request)
        kind, text = f.get("kind"), f.get("message", "").strip()[:1000]
        kid_id = int(f["kid"]) if is_num(f.get("kid", "")) else None
        kid_row = db.kid(kid_id) if kid_id else None
        if kid_id and (not kid_row or not is_parent_of(u["id"], kid_row)):
            raise HTTPException(400)
        if kind not in REQUEST_KINDS or not text:
            flash(request, "Choose what you need and add a short message.", "error")
            raise Redirect("/requests#new")
        day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        open_count = db.one("SELECT COUNT(*) AS n FROM requests WHERE parent_id=? AND (status='open' OR "
                            "(status='cancelled' AND resolved_at>?))", (u["id"], day_ago))["n"]
        if open_count >= 15:
            flash(request, f"You've reached the request limit. Check for {cfg.owner_name}'s replies and try again later.", "error")
            raise Redirect("/requests")
        db.x("INSERT INTO requests(parent_id,kid_id,kind,message,created_at) VALUES(?,?,?,?,?)",
             (u["id"], kid_id, kind, text, now_iso()))
        if not rate_limited(request, limit=3, window=600, key=f"notify:{u['id']}"):  # at most 3 alerts per 10 min
            notify(f"KidsWatch for Plex: {REQUEST_KINDS[kind].lower()}", f"{u['name']}: {text[:180]}")
        draft_id = f.get("draft_id", "")
        if re.fullmatch(r"[a-f0-9]{32}", draft_id):
            request.session["request_sent"] = draft_id
        else:
            draft_id = ""
        flash(request, f"Request sent to {cfg.owner_name}.")
        raise Redirect("/requests" + (f"?sent={draft_id}" if draft_id else ""))

    # ---------- link the child's account ----------
    def link_row(token: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", token):
            return None
        row = db.one("SELECT * FROM link_tokens WHERE token=?", (token,))
        if not row or row["used"] or row["expires_at"] < time.time():
            return None
        if row["parent_id"] != plex.owner().id:  # the parent must still be switched on and shared with
            p = db.one("SELECT enabled FROM parents WHERE plex_id=?", (row["parent_id"],))
            if not p or not p["enabled"] or not still_shared(row["parent_id"]):
                return None
        return row

    def unavailable_link(request: Request):
        # Used, expired, revoked and unknown tokens reveal the same public information.
        return message(request, "This link is no longer available",
                       "If you already finished signing in, return to your child's page in your regular window "
                       "to check their status. If you still need to connect, get a new sign-in link there.", 410)

    @app.get("/link/{token}", response_class=HTMLResponse)
    def link_page(request: Request, token: str):
        row = link_row(token)
        if not row:
            return unavailable_link(request)
        kid = db.kid(row["kid_id"])
        return render(request, "link.html", kid=kid, token=token, parent=parent_name(row["parent_id"]))

    @app.get("/link/{token}/start")
    async def link_start(request: Request, token: str):
        if not link_row(token) or rate_limited(request) or pin_flood():
            raise Redirect(f"/link/{token}")
        try:
            pin_id, code = await run_in_threadpool(plex.create_pin)
        except PlexError:
            return message(request, "Plex isn't answering", "Try again in a minute.", 503)
        request.session["link_pin"] = pins.put({"id": pin_id, "code": code, "token": token, "at": time.time()})
        return RedirectResponse(plex.auth_url(code, f"{cfg.public_url}/link/{token}/done"), status_code=303)

    @app.get("/link/{token}/done", response_class=HTMLResponse)
    async def link_done(request: Request, token: str):
        row = link_row(token)
        pin = pins.pop(request.session.pop("link_pin", None))
        if not row or not pin or pin.get("token") != token or time.time() - pin.get("at", 0) > LINK_PIN_MAX_AGE:
            return unavailable_link(request)
        kid = db.kid(row["kid_id"])
        claimed = False
        ok = False
        try:
            kid_token = await run_in_threadpool(plex.check_pin, pin["id"], pin["code"])
            if not kid_token:
                return message(request, "Sign-in didn't finish", "Plex didn't confirm the sign-in.",
                               link=(f"/link/{token}", "Try again"))
            # The wait for Plex can be long: check the link again and claim it in one step, so a link that
            # was replaced (or a parent switched off or unshared) in the meantime can't still connect.
            if not link_row(token) or not db.claim_connection(row["kid_id"], token, plex.owner().id, link=True):
                return unavailable_link(request)
            claimed = True
            ok, text, new_status = await run_in_threadpool(svc.connect, row["kid_id"], kid_token, token)
        except PlexError as e:
            ok, text, new_status = False, f"Plex had a problem: {e}. Try again in a minute.", None
        finally:
            kid_token = None
        if claimed:
            db.release_connection(row["kid_id"], token, retry=not ok)
        if ok:
            linked = db.kid(row["kid_id"])["plex_username"]
            who = parent_name(row["parent_id"])
            if new_status == "requested" and kid["status"] == "draft":
                level = "Only titles they allow" if kid["mode"] == "picked" else \
                    rules.AGE_LEVELS.get(kid["age_level"], {}).get("label", kid["age_level"])
                db.x("INSERT INTO requests(parent_id,kid_id,kind,message,created_at) VALUES(?,?,?,?,?)",
                     (row["parent_id"], row["kid_id"], "add_child",
                      f"Add {kid['name']} ({level}). Plex account: {linked}.", now_iso()))
                notify("KidsWatch for Plex: new child", f"{who} asked to add {kid['name']} (Plex account: {linked}).")
            elif kid["status"] != "draft":
                notify("KidsWatch for Plex: child connected", f"{who} connected {kid['name']} (Plex account: {linked}).")
            return render(request, "link_done.html", kid=kid, text=text)
        return message(request, "That didn't work", text, 400, link=(f"/link/{token}", "Try again"))

    # ---------- owner ----------
    @app.get("/admin", response_class=HTMLResponse)
    async def admin(request: Request, tab: str = "requests"):
        require(request, "owner")
        tab = tab if tab in ("requests", "kids", "people", "activity") else "requests"
        open_reqs = db.q("SELECT r.*, k.name AS kid_name, k.status AS kid_status, p.title AS p_title, "
                         "p.username AS p_user FROM requests r LEFT JOIN kids k ON k.id=r.kid_id "
                         "LEFT JOIN parents p ON p.plex_id=r.parent_id WHERE r.status='open' ORDER BY r.id")
        done_reqs = db.q("SELECT r.*, k.name AS kid_name, p.title AS p_title, p.username AS p_user FROM requests r "
                         "LEFT JOIN kids k ON k.id=r.kid_id LEFT JOIN parents p ON p.plex_id=r.parent_id "
                         "WHERE r.status<>'open' ORDER BY r.id DESC LIMIT 15")
        kids = db.q("SELECT k.*, p.title AS p_title, p.username AS p_user FROM kids k "
                    "LEFT JOIN parents p ON p.plex_id=k.parent_id WHERE k.status<>'removed' ORDER BY k.id")
        rows = {r["plex_id"]: r for r in db.q("SELECT * FROM parents")}
        try:
            shared = await run_in_threadpool(plex.shared_users)
            kid_ids = {k["plex_id"] for k in kids if k["plex_id"]}
            people = [(s, rows.get(s.id)) for s in sorted(shared.values(), key=lambda s: (s.title or s.username).lower())
                      if s.id not in kid_ids]
            shared_error = None
        except PlexError as e:
            people, shared_error = [], str(e)
        audit = [dict(a, info=json.loads(a["detail"]) if a["detail"] else {})
                 for a in db.q("SELECT * FROM audit ORDER BY id DESC LIMIT 40")]
        kid_names = {k["id"]: k["name"] for k in kids}
        names = {r["username"]: r["title"] or r["username"] for r in db.q("SELECT username, title FROM parents")}
        enabled_parents = db.q("SELECT plex_id, title, username FROM parents WHERE enabled=1 ORDER BY title")
        statuses = [kid_status(k) for k in kids]
        stats = {"active": sum(k["status"] == "active" for k in kids), "watching": sum(s["state"] == "watching" for s in statuses),
                 "waiting": sum(k["status"] == "requested" for k in kids), "open": len(open_reqs)}
        co = {k["id"]: co_parents(k["id"]) for k in kids}
        return render(request, "admin.html", nav="admin", tab=tab, open_reqs=open_reqs, done_reqs=done_reqs, names=names, kid_names=kid_names,
                      enabled_parents=enabled_parents, co=co, stats=stats,
                      kids=[(k, kid_status(k)) for k in kids], people=people, shared_error=shared_error,
                      audit=audit, keeper={"ok": enforcer.healthy(), "error": enforcer.last_error})

    @app.post("/admin/requests/{req_id}")
    async def admin_request(request: Request, req_id: int):
        u = require(request, "owner")
        f = await form(request)
        r = db.one("SELECT * FROM requests WHERE id=?", (req_id,))
        if r and r["kind"] == "access":
            raise Redirect(f"/admin/requests/{req_id}")
        action = f.get("action")
        if not r or r["status"] != "open" or action not in ("approve", "decline", "done"):
            raise HTTPException(400)
        note = f.get("note", "").strip()[:500] or None
        status = {"approve": "approved", "decline": "declined", "done": "done"}[action]
        kid = db.kid(r["kid_id"]) if r["kid_id"] else None
        if r["kind"] == "add_child" and kid and kid["status"] == "requested":
            try:
                if action == "approve":
                    await run_in_threadpool(svc.approve, kid["id"], u["username"])
                else:
                    await run_in_threadpool(svc.decline, kid["id"], u["username"])
            except ConnectionInProgress as e:
                raise HTTPException(409, str(e)) from e
            except PlexError as e:
                flash(request, f"Plex didn't accept that: {e}. Try again.", "error")
                raise Redirect("/admin")
        db.x("UPDATE requests SET status=?, owner_note=?, resolved_at=? WHERE id=?", (status, note, now_iso(), req_id))
        db.audit(u["username"], "request_" + status, request=req_id, kind=r["kind"])
        flash(request, "Request updated.")
        raise Redirect("/admin")

    @app.post("/admin/kids/{kid_id}/co-parents")
    async def admin_co_parent(request: Request, kid_id: int):
        u = require(request, "owner")
        f = await form(request)
        kid = db.kid(kid_id)
        if not kid or not is_num(f.get("parent", "")):
            raise HTTPException(400)
        pid = int(f["parent"])
        if f.get("action") == "remove":
            db.x("DELETE FROM kid_parents WHERE kid_id=? AND parent_id=?", (kid_id, pid))
            db.audit(u["username"], "co_parent_removed", kid=kid_id, parent=pid)
            flash(request, "Co-parent removed.")
        else:
            if pid == kid["parent_id"] or not db.one("SELECT 1 FROM parents WHERE plex_id=? AND enabled=1", (pid,)):
                flash(request, "Pick a parent who can use the site and isn't already this child's parent.", "error")
                raise Redirect("/admin?tab=kids")
            db.x("INSERT OR IGNORE INTO kid_parents(kid_id,parent_id) VALUES(?,?)", (kid_id, pid))
            db.audit(u["username"], "co_parent_added", kid=kid_id, parent=pid)
            flash(request, f"{parent_name(pid)} can now manage {kid['name']}.")
        raise Redirect("/admin?tab=kids")

    @app.post("/admin/sign-out-all")
    async def admin_sign_out_all(request: Request):
        u = require(request, "owner")
        await form(request)
        db.set_setting("session_epoch", str(int(db.setting("session_epoch", "1")) + 1))
        sessions.end_all()
        db.audit(u["username"], "signed_out_everyone")
        request.session.clear()
        raise Redirect("/")

    @app.post("/admin/parents/{plex_id}")
    async def admin_parent(request: Request, plex_id: int):
        u = require(request, "owner")
        f = await form(request)
        enabled = 1 if f.get("enabled") == "1" else 0
        row = db.one("SELECT plex_id FROM parents WHERE plex_id=?", (plex_id,))
        if row:
            if not db.set_parent_enabled(plex_id, enabled):
                raise HTTPException(409, "Connection in progress. Wait for the child's sign-in to finish, then try again.")
        else:
            shared = await run_in_threadpool(plex.shared_users)
            s = shared.get(plex_id)
            if not s:
                raise HTTPException(404)
            db.x("INSERT INTO parents(plex_id,username,title,thumb,enabled,created_at) VALUES(?,?,?,?,?,?)",
                 (s.id, s.username, s.title, s.thumb, enabled, now_iso()))
        if not enabled:
            sessions.end_user(plex_id)  # switched off: their open sign-ins end now
        db.audit(u["username"], "parent_" + ("enabled" if enabled else "disabled"), parent=plex_id)
        flash(request, "Saved.")
        raise Redirect("/admin?tab=people")

    # ---------- template helpers ----------
    def local_time(value) -> str:
        if value in (None, ""):
            return ""
        if isinstance(value, (int, float)) or str(value).isdigit():
            dt = datetime.fromtimestamp(int(value), timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value))
        dt = dt.astimezone(svc.tz)
        today = svc.now_local().date()
        hour = dt.hour % 12 or 12
        clock = f"{hour}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"
        if dt.date() == today:
            return f"Today {clock}"
        if (today - dt.date()).days == 1:
            return f"Yesterday {clock}"
        return f"{dt.strftime('%a %b')} {dt.day}, {clock}"

    templates.env.filters["local_time"] = local_time
    templates.env.filters["minutes"] = rules.minutes_text
    templates.env.filters["friendly_time"] = rules.friendly_time

    def ratings(text: str) -> Markup:
        """Keep ratings like TV-PG and PG-13 from breaking across lines."""
        return Markup(re.sub(r"\b(TV-[A-Z0-9]+(?:-FV)?|PG-13|NC-17)\b",
                             lambda m: f'<span class="nowrap">{m.group(1)}</span>', str(escape(text))))

    templates.env.filters["ratings"] = ratings
    from .mobile_protocol import canonical_origin
    from .mobile_plex import PlexAdapter
    from .mobile_routes import MobileAPI, register as register_mobile
    from .mobile_web import register_web
    try:
        mobile_origin = canonical_origin(cfg.public_url)
    except ValueError:
        if cfg.mobile_enabled:
            raise ValueError('Mobile API requires a canonical root origin') from None
        mobile_origin = ''
    # Invalid legacy origins keep web behavior intact; mobile requires a canonical root origin.
    if mobile_origin:
        mobile_api = MobileAPI(PlexAdapter(app, cfg, mobile_origin), cfg.mobile_enabled, cfg.mobile_lan_origin)
        app.state.mobile = mobile_api
        register_web(app, mobile_api, render)
        register_mobile(app, mobile_api)
    else:
        from .mobile_routes import MobileBoundary
        app.add_middleware(MobileBoundary)
    return app


def app_factory() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app()
