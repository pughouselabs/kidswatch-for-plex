"""Owner invitation pages and public, read-only invitation previews.

Wiring in create_app, after its UI/auth helpers exist:
    configure(app, user=user, require=require, form=form, render=render)
    app.include_router(router)

Call invitations.migrate(conn) in DB._migrate. To reuse normal Plex login,
include return_to=safe_return(request.query_params.get('next')) in pins.put in
/login; after sign_in in /auth/done, redirect to pin['return_to'] when set,
before the waiting/home redirects. Only /join/{43-character token} is accepted.
No invitation capability should be saved in the readable session cookie.
"""
from __future__ import annotations

import io
import re
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import urlencode

import segno
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from markupsafe import Markup
from starlette.concurrency import run_in_threadpool

from .approvals import notify_parent_request
from .invitations import EXPIRY_DAYS, InvitationError, Invitations
from .plex import PlexError

router = APIRouter()
TOKEN = r"[A-Za-z0-9_-]{43}"


def safe_return(value) -> str | None:
    """Accept only a local invitation preview, never a URL or an action."""
    return value if isinstance(value, str) and re.fullmatch(r"/join/" + TOKEN, value) else None


def configure(app, *, user, require, form, render):
    """Reuse the application's session, owner-code, CSRF and rendering checks."""
    app.state.invitation_ui = SimpleNamespace(user=user, require=require, form=form, render=render)
    app.state.invitations = Invitations(app.state.db, app.state.svc)


def _state(request):
    return request.app.state.invitation_ui, request.app.state.invitations


def _render(request, template, status=200, **context):
    ui, _ = _state(request)
    response = ui.render(request, template, status=status, **context)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _unavailable(request):
    return _render(request, "invite_join.html", status=410, unavailable=True)


def _owner(request):
    ui, service = _state(request)
    owner = ui.require(request, "owner")
    if owner["id"] != service.svc.plex.owner().id:
        raise HTTPException(404)
    return owner


def _qr(url):
    output = io.BytesIO()
    segno.make(url, micro=False, error="m").save(output, kind="svg", scale=1, xmldecl=False,
                                                svgclass="qr", omitsize=True)
    # Segno generates only SVG geometry from the string. It never interpolates it into markup.
    svg = output.getvalue().decode("utf-8")
    return Markup(svg.replace("<svg ", '<svg role="img" aria-label="KidsWatch invitation QR code" ', 1))


def _date(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%B %d, %Y at %H:%M UTC")


@router.get("/admin/invites")
@router.get("/admin/invites/new")
def new_invite(request: Request):
    _, service = _state(request)
    owner = _owner(request)
    try:
        recipients = service.recipients()
    except PlexError:
        return _render(request, "invite_new.html", status=503, recipients={}, invitations=[],
                       error="Plex isn't answering. Try again in a moment.", expiry_days=EXPIRY_DAYS)
    invites = service.db.q("SELECT i.*, p.username FROM parent_invitations i LEFT JOIN parents p "
                           "ON p.plex_id=i.intended_plex_id WHERE i.owner_id=? ORDER BY i.id DESC LIMIT 100",
                           (owner["id"],))
    return _render(request, "invite_new.html", recipients=recipients, invitations=invites,
                   expiry_days=EXPIRY_DAYS, now=time.time(), nav="admin")


@router.post("/admin/invites/new")
async def create_invite(request: Request):
    ui, service = _state(request)
    owner = await run_in_threadpool(_owner, request)
    fields = await ui.form(request)
    recipient, days = fields.get("parent_id", ""), fields.get("days", "7")
    if not re.fullmatch(r"[0-9]{1,12}", recipient) or days not in {str(d) for d in EXPIRY_DAYS}:
        raise HTTPException(400, "Choose a Plex user and a link expiration.")
    try:
        invite, token = await run_in_threadpool(service.create, owner["id"], int(recipient), int(days))
    except InvitationError:
        raise HTTPException(400, "Choose a parent who has accepted Plex access and still needs site approval.") from None
    except PlexError:
        raise HTTPException(503) from None
    url = service.svc.cfg.public_url.rstrip("/") + "/join/" + token
    message = (f"I'd like to invite your family to KidsWatch for Plex. Open {url}, sign in with your own Plex "
               "account, and I'll approve your access. Then we'll check which setup works for your children.")
    qr = _qr(url)
    qr_large = Markup(str(qr).replace('class="qr"', 'width="256" height="256"'))
    return _render(request, "invite_ready.html", status=201, invite=invite, invite_url=url, message=message,
                   qr=qr, qr_large=qr_large, expires=_date(invite["expires_at"]), nav="admin")


@router.get("/admin/invites/{invite_id}")
def invite_detail(request: Request, invite_id: int):
    _, service = _state(request)
    owner = _owner(request)
    invite = service.get(invite_id, owner["id"])
    if not invite:
        raise HTTPException(404)
    return _render(request, "invite_ready.html", invite=invite, expires=_date(invite["expires_at"]),
                   expired=invite["expires_at"] <= time.time(), nav="admin")


@router.get("/admin/invites/{invite_id}/revoke")
def revoke_confirmation(request: Request, invite_id: int):
    _, service = _state(request)
    owner = _owner(request)
    invite = service.get(invite_id, owner["id"])
    if not invite:
        raise HTTPException(404)
    return _render(request, "invite_revoke.html", invite=invite, nav="admin")


@router.post("/admin/invites/{invite_id}/revoke")
async def revoke_invite(request: Request, invite_id: int):
    ui, service = _state(request)
    owner = await run_in_threadpool(_owner, request)
    await ui.form(request)
    if not service.get(invite_id, owner["id"]):
        raise HTTPException(404)
    try:
        await run_in_threadpool(service.revoke, invite_id, owner["id"])
    except PlexError:
        raise HTTPException(503) from None
    return RedirectResponse(f"/admin/invites/{invite_id}", status_code=303)


@router.get("/join/{token}")
def join_invite(request: Request, token: str):
    ui, service = _state(request)
    if not re.fullmatch(TOKEN, token) or not service.preview(token):
        return _unavailable(request)
    user = ui.user(request)
    return _render(request, "invite_join.html", token=token, signed_in=bool(user),
                   login_url="/login?" + urlencode({"next": "/join/" + token}))


@router.post("/join/{token}")
async def claim_invite(request: Request, token: str):
    ui, service = _state(request)
    await ui.form(request)
    user = ui.user(request)
    if not user:
        return RedirectResponse("/login?" + urlencode({"next": "/join/" + token}), status_code=303,
                                headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})
    if user["role"] not in ("parent", "waiting"):
        return _render(request, "invite_join.html", status=403, token=token, signed_in=True,
                       error="Use your own adult Plex account. The server owner cannot claim this invitation.")
    try:
        result = await run_in_threadpool(service.claim, token, user["id"])
    except InvitationError as exc:
        if exc.reason == "unavailable":
            return _unavailable(request)
        errors = {
            "wrong_identity": "This invitation is for a different Plex account. Sign out and use your own invited account.",
            "share_missing": "Accept the server owner's Plex library invitation first, then check again.",
            "already_enabled": "Your account already has site access. Open My kids to continue.",
            "reviewed": "Your access request has already been reviewed. Contact the server owner if you need help.",
            "sign_in": "Sign in with Plex again to confirm your account.",
        }
        return _render(request, "invite_join.html", status=403, token=token, signed_in=True,
                       error=errors.get(exc.reason, "This account cannot request access."))
    except PlexError:
        return _render(request, "invite_join.html", status=503, token=token, signed_in=True,
                       error="Plex isn't answering. Your invitation is still available. Try again in a moment.")
    if result.created:
        notify_parent_request(service.db, request.app.state.notifier, result.request_id)
    return RedirectResponse("/waiting", status_code=303,
                            headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})
