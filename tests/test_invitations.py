"""The invitation router can be mounted without changing the main application."""
import hashlib
import html
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app.db import DB, now_iso
from app.invitation_routes import configure, router, safe_return
from app.invitations import InvitationError, Invitations, migrate
from app.plex import PlexError
from app.security import Sessions
from app.service import Service
from tests.fakes import ACCOUNTS, ALEX, FakePlex
from tests.test_quality import accessibility_problems, strict_ok


@pytest.fixture
def env(tmp_path):
    db = DB(str(tmp_path / "invitations.db"))
    with closing(db._connect()) as conn, conn:
        migrate(conn)
        migrate(conn)
    fake = FakePlex()
    cfg = SimpleNamespace(public_url="http://testserver", owner_name="Admin", tz="UTC", kid_sections=("1", "2"))
    svc = Service(db, fake, cfg)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-signature-only")
    app.state.db, app.state.svc, app.state.sessions = db, svc, Sessions(db)
    app.state.notifier = Mock()
    templates = Jinja2Templates(directory=str(Path(__file__).parents[1] / "app" / "templates"))

    def user(request):
        u = request.session.get("user")
        if not u or request.session.get("epoch") != db.setting("session_epoch", "1"):
            return None
        return u if app.state.sessions.valid(request.session.get("sid"), u["id"], u["role"]) else None

    def require(request, *roles):
        u = user(request)
        if not u or u["role"] not in roles:
            raise HTTPException(404)
        return u

    async def form(request):
        fields = await request.form()
        csrf = request.session.get("csrf")
        if not csrf or not secrets.compare_digest(str(fields.get("csrf", "")), csrf):
            raise HTTPException(400)
        return dict(fields)

    def render(request, name, status=200, **context):
        csrf = request.session.setdefault("csrf", secrets.token_urlsafe(24))
        context.update(user=user(request), csrf=csrf, owner_name=cfg.owner_name, flashes=[], unread=0)
        return templates.TemplateResponse(request, name, context, status_code=status)

    @app.get("/test/sign-in/{username}")
    def sign_in(request: Request, username: str):
        account = ACCOUNTS[f"tok-{username}"]
        row = db.one("SELECT enabled FROM parents WHERE plex_id=?", (account.id,))
        role = "owner" if account.id == 1 else "parent" if row and row["enabled"] else "waiting"
        if account.id != 1 and not row:
            db.x("INSERT INTO parents(plex_id,username,title,enabled,created_at) VALUES(?,?,?,?,?)",
                 (account.id, account.username, account.title, 0, now_iso()))
        request.session.clear()
        request.session.update(user={"id": account.id, "username": account.username, "name": account.name,
                                     "role": role}, sid=app.state.sessions.create(account.id, role),
                               epoch=db.setting("session_epoch", "1"))
        return RedirectResponse("/", status_code=303)

    configure(app, user=user, require=require, form=form, render=render)
    app.include_router(router)
    return app, fake, db, app.state.invitations


def client(app, username=None):
    c = TestClient(app, follow_redirects=False)
    if username:
        c.get("/test/sign-in/" + username)
    return c


def csrf(c, path):
    return re.search(r'name="csrf" value="([^"]+)"', c.get(path).text).group(1)


def create(app, recipient=ALEX.id, days=7):
    owner = client(app, "owner")
    response = owner.post("/admin/invites/new", data={"csrf": csrf(owner, "/admin/invites/new"),
                                                   "parent_id": recipient, "days": days})
    assert response.status_code == 201, response.text
    url = re.search(r'id="invite-link" readonly value="([^"]+)"', response.text).group(1)
    return owner, urlparse(url).path, response


def test_hash_only_default_expiry_qr_message_and_migration(env):
    app, fake, db, invitations = env
    owner, path, response = create(app)
    invite = db.one("SELECT * FROM parent_invitations")
    token = path.rsplit("/", 1)[1]
    assert invite["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert abs(invite["expires_at"] - time.time() - 7 * 86400) < 10
    assert token not in str(dict(invite))
    assert token not in str([dict(row) for row in db.q("SELECT * FROM audit")])
    with closing(db._connect()) as conn:
        assert token.encode() not in conn.serialize()
    assert "<svg" in response.text and 'aria-label="KidsWatch invitation QR code"' in response.text
    assert "sign in with your own Plex account" in response.text
    assert "I'll approve your access" in html.unescape(response.text)
    assert "Share this privately" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert not fake.calls  # no Plex invitations, restriction writes or online-source changes
    detail = owner.get(f"/admin/invites/{invite['id']}")
    assert token not in detail.text  # no recoverable capability in cookies or persisted state
    assert "shown only when" in detail.text


def test_scanner_gets_never_claim_or_create_requests(env):
    app, _, db, invitations = env
    _, path, _ = create(app)
    before = dict(db.one("SELECT * FROM parent_invitations"))
    scanner = client(app)
    parent = client(app, "alex")
    for c in (scanner, parent):
        for _ in range(3):
            response = c.get(path)
            assert response.status_code == 200
            assert "Movies" not in response.text and "TV Shows" not in response.text
    assert db.q("SELECT * FROM requests") == []
    assert dict(db.one("SELECT * FROM parent_invitations")) == before
    assert db.one("SELECT enabled FROM parents WHERE plex_id=?", (ALEX.id,))["enabled"] == 0
    sign_in_link = re.search(r'href="(/login\?[^"]+)"', scanner.get(path).text).group(1)
    assert parse_qs(urlparse(sign_in_link).query)["next"] == [path]


def test_claim_requires_csrf_and_authentication_and_never_enables_parent(env):
    app, _, db, invitations = env
    _, path, _ = create(app)
    parent = client(app, "alex")
    assert parent.post(path).status_code == 400
    assert parent.post(path, data={"csrf": "wrong"}).status_code == 400
    assert not db.q("SELECT * FROM requests")
    token = csrf(parent, path)
    response = parent.post(path, data={"csrf": token})
    assert response.status_code == 303 and response.headers["location"] == "/waiting"
    invite = db.one("SELECT * FROM parent_invitations")
    req = db.one("SELECT * FROM requests")
    assert invite["claimed_by"] == ALEX.id and invite["request_id"] == req["id"]
    assert req["kind"] == "access" and req["status"] == "open" and req["kid_id"] is None
    assert db.one("SELECT enabled FROM parents WHERE plex_id=?", (ALEX.id,))["enabled"] == 0
    app.state.notifier.send.assert_called_once_with("KidsWatch for Plex: parent access request",
                                                  "A parent access request needs your review.",
                                                  f"/admin/requests/{req['id']}")
    assert parent.post(path, data={"csrf": token}).status_code == 410
    assert len(db.q("SELECT * FROM requests")) == 1


@pytest.mark.parametrize("expired,revoked", [(True, False), (False, True)])
def test_expired_and_revoked_cannot_be_claimed(env, expired, revoked):
    app, _, db, invitations = env
    owner, path, _ = create(app)
    invite = db.one("SELECT * FROM parent_invitations")
    parent = client(app, "alex")
    token = csrf(parent, path)
    if expired:
        db.x("UPDATE parent_invitations SET expires_at=?", (time.time() - 1,))
        assert "Invitation expired" in owner.get(f"/admin/invites/{invite['id']}").text
    if revoked:
        confirm_path = f"/admin/invites/{invite['id']}/revoke"
        assert owner.get(confirm_path).status_code == 200
        assert db.one("SELECT revoked_at FROM parent_invitations")["revoked_at"] is None
        assert owner.post(confirm_path).status_code == 400
        assert owner.post(confirm_path, data={"csrf": csrf(owner, confirm_path)}).status_code == 303
        assert "Invitation revoked" in owner.get(f"/admin/invites/{invite['id']}").text
    response = parent.get(path)
    assert response.status_code == 410 and "Invitation unavailable" in response.text
    assert parent.post(path, data={"csrf": token}).status_code == 410
    assert not db.q("SELECT * FROM requests")


@pytest.mark.parametrize("username", ["sam", "owner", "stranger", "kid1"])
def test_wrong_identity_does_not_consume_invitation(env, username):
    app, _, db, invitations = env
    _, path, _ = create(app)
    wrong = client(app, username)
    response = wrong.post(path, data={"csrf": csrf(wrong, path)})
    assert response.status_code == 403
    assert "alex" not in response.text.lower()
    assert not db.one("SELECT claimed_at FROM parent_invitations")["claimed_at"]
    assert not db.q("SELECT * FROM requests")


def test_missing_share_and_upstream_failure_preserve_invitation(env, monkeypatch):
    app, fake, db, invitations = env
    _, path, _ = create(app)
    parent = client(app, "alex")
    token = csrf(parent, path)
    original = fake.shared_users
    monkeypatch.setattr(fake, "shared_users", lambda: {})
    response = parent.post(path, data={"csrf": token})
    assert response.status_code == 403 and "Plex library invitation first" in response.text
    assert not db.q("SELECT * FROM requests")
    monkeypatch.setattr(fake, "shared_users", Mock(side_effect=PlexError("unavailable")))
    assert parent.post(path, data={"csrf": token}).status_code == 503
    assert not db.one("SELECT claimed_at FROM parent_invitations")["claimed_at"]
    monkeypatch.setattr(fake, "shared_users", original)
    assert parent.post(path, data={"csrf": token}).status_code == 303


def test_fresh_owner_decisions_checked_at_claim(env):
    app, _, db, invitations = env
    _, path, _ = create(app)
    parent = client(app, "alex")
    token = csrf(parent, path)
    db.x("UPDATE parents SET enabled=1 WHERE plex_id=?", (ALEX.id,))
    assert parent.post(path, data={"csrf": token}).status_code == 403
    assert not db.q("SELECT * FROM requests")
    db.x("UPDATE parents SET enabled=0 WHERE plex_id=?", (ALEX.id,))
    db.x("INSERT INTO requests(parent_id,kind,message,status,created_at) VALUES(?,?,?,?,?)",
         (ALEX.id, "access", "previous request", "declined", now_iso()))
    response = parent.post(path, data={"csrf": token})
    assert response.status_code == 403 and "already been reviewed" in response.text
    assert len(db.q("SELECT * FROM requests")) == 1
    assert not db.one("SELECT claimed_at FROM parent_invitations")["claimed_at"]


def test_existing_request_reused_without_duplicate_notification(env):
    app, _, db, invitations = env
    _, path, _ = create(app)
    parent = client(app, "alex")
    request_id = db.x("INSERT INTO requests(parent_id,kind,message,created_at) VALUES(?,?,?,?)",
                      (ALEX.id, "access", "already requested", now_iso()))
    assert parent.post(path, data={"csrf": csrf(parent, path)}).status_code == 303
    assert db.one("SELECT request_id FROM parent_invitations")["request_id"] == request_id
    assert len(db.q("SELECT * FROM requests")) == 1
    app.state.notifier.send.assert_not_called()


def test_atomic_double_claim_across_services(env):
    app, fake, db, invitations = env
    _, path, _ = create(app)
    client(app, "alex")
    token = path.rsplit("/", 1)[1]
    service2 = Invitations(db, Service(db, fake, app.state.svc.cfg))

    def attempt(service):
        try:
            return service.claim(token, ALEX.id)
        except InvitationError as exc:
            return exc.reason

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (invitations, service2)))
    assert sum(result == "unavailable" for result in results) == 1
    assert len(db.q("SELECT * FROM requests")) == 1


def test_revocation_wins_while_fresh_share_check_is_in_flight(env, monkeypatch):
    app, fake, db, invitations = env
    _, path, _ = create(app)
    client(app, "alex")
    original = fake.shared_users

    def revoke_during_read():
        db.x("UPDATE parent_invitations SET revoked_at=?", (now_iso(),))
        return original()

    monkeypatch.setattr(fake, "shared_users", revoke_during_read)
    with pytest.raises(InvitationError, match="unavailable"):
        invitations.claim(path.rsplit("/", 1)[1], ALEX.id)
    assert not db.q("SELECT * FROM requests")


def test_newer_removed_share_observation_wins(env, monkeypatch):
    app, fake, db, invitations = env
    _, path, _ = create(app)
    client(app, "alex")
    original = fake.shared_users

    def stale_response():
        db.publish_shared([], time.time(), db.next_shared_observation())
        return original()

    monkeypatch.setattr(fake, "shared_users", stale_response)
    with pytest.raises(InvitationError, match="share_missing"):
        invitations.claim(path.rsplit("/", 1)[1], ALEX.id)
    assert not db.q("SELECT * FROM requests")


def test_notification_failure_does_not_lose_request(env):
    app, _, db, invitations = env
    _, path, _ = create(app)
    parent = client(app, "alex")
    app.state.notifier.send.side_effect = RuntimeError("private receiver address")
    assert parent.post(path, data={"csrf": csrf(parent, path)}).status_code == 303
    assert len(db.q("SELECT * FROM requests")) == 1
    audits = str([dict(row) for row in db.q("SELECT * FROM audit")])
    assert "notification_failed" in audits and "private receiver" not in audits


def test_owner_auth_csrf_and_recipient_checks(env):
    app, _, db, invitations = env
    for c in (client(app), client(app, "alex")):
        assert c.get("/admin/invites/new").status_code == 404
        assert c.post("/admin/invites/new").status_code == 404
    owner = client(app, "owner")
    token = csrf(owner, "/admin/invites/new")
    assert owner.post("/admin/invites/new", data={"parent_id": ALEX.id}).status_code == 400
    for recipient, days in [(999, 7), (1, 7), (101, 0), (101, 365), ("bad", 7)]:
        assert owner.post("/admin/invites/new", data={"csrf": token, "parent_id": recipient, "days": days}).status_code == 400
    assert not db.q("SELECT * FROM parent_invitations")
    app.state.sessions.end_all()
    assert owner.post("/admin/invites/new", data={"csrf": token, "parent_id": ALEX.id}).status_code == 404


def test_public_unavailable_copy_is_indistinguishable_and_private(env):
    app, _, db, invitations = env
    _, path, _ = create(app)
    public = client(app)
    db.x("UPDATE parent_invitations SET revoked_at=?", (now_iso(),))
    revoked = public.get(path)
    unknown = public.get("/join/" + "x" * 43)
    assert revoked.status_code == unknown.status_code == 410
    assert revoked.text == unknown.text
    assert "alex" not in revoked.text.lower() and "Movies" not in revoked.text


def test_markup_accessible_valid_and_csp_compatible(env):
    app, _, db, invitations = env
    owner, path, ready = create(app)
    parent = client(app, "alex")
    pages = [owner.get("/admin/invites/new"), ready, parent.get(path), client(app).get(path),
             owner.get("/admin/invites/1"), owner.get("/admin/invites/1/revoke"),
             client(app).get("/join/" + "x" * 43)]
    for response in pages:
        assert not strict_ok(response.text)
        assert not accessibility_problems(response.text)
        assert not re.search(r"\sstyle=|<style|<script(?![^>]*\ssrc=)", response.text)
        assert 'name="viewport"' in response.text


@pytest.mark.parametrize("url", ["https://evil.example/join/" + "x" * 43, "//evil.example", "/admin", "/waiting",
                                "/join/" + "x" * 43 + "/revoke", "/join/" + "x" * 43 + "?next=//evil.example",
                                "/join/%2F", "\\evil.example", None])
def test_safe_sign_in_return_rejects_external_or_action_urls(url):
    assert safe_return(url) is None


def test_safe_sign_in_return_accepts_only_preview():
    value = "/join/" + secrets.token_urlsafe(32)
    assert safe_return(value) == value
