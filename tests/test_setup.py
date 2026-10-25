"""Exercise the standalone router with real SQLite and server-side sessions."""
import json
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app import config, setup
from app.db import DB, SCHEMA, now_iso
from app.main import BodyLimit
from app.security import Sessions
from app.setup_routes import SetupRoutes, router
from .fakes import FakePlex, OWNER
from .test_quality import accessibility_problems, strict_ok


class SetupPlex(FakePlex):
    kinds = {"1": "movie", "2": "show", "3": "artist"}

    def readiness_sections(self):
        return dict(self.kinds)

    def section_type(self, key):
        return self.kinds.get(key, "")


class PrivateReceiver:
    public_url = "http://testserver"
    endpoint = "https://receiver.invalid/private-capability"

    def _url(self):
        return self.endpoint


def build_app(db_path):
    from dataclasses import replace

    cfg = replace(config.load(), db_path=str(db_path), tz="America/Denver")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-only-session-key")
    app.add_middleware(BodyLimit)
    app.state.db = db = DB(str(db_path))
    with closing(db._connect()) as conn, conn:
        setup.migrate(conn)
    app.state.plex, app.state.notifier = SetupPlex(), PrivateReceiver()
    app.state.sessions = sessions = Sessions(db)
    templates = Jinja2Templates(directory=str(Path(__file__).parents[1] / "app/templates"))
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parents[1] / "app/static")))

    def require(request, *roles):
        user = request.session.get("user")
        if (not user or request.session.get("epoch") != db.setting("session_epoch", "1")
                or not sessions.valid(request.session.get("sid"), user["id"], user["role"])):
            raise HTTPException(401)
        if user["role"] not in roles or user["id"] != OWNER.id:
            raise HTTPException(404)
        return user

    def render(request, name, status=200, **ctx):
        request.session.setdefault("csrf", "test-csrf-token")
        return templates.TemplateResponse(request, name, {"user": request.session.get("user"),
                     "csrf": request.session["csrf"], "flashes": [], **ctx}, status_code=status)

    app.state.setup = SetupRoutes(cfg=cfg, require=require, render=render)
    app.include_router(router)

    # This test-only endpoint stands in for the app's established sign-in flow.
    # Pending owner-code sign-ins deliberately have no authenticated owner session.
    @app.get("/test/session/{role}")
    def session(request: Request, role: str):
        request.session.clear()
        if role == "pending-owner-code":
            request.session["owner_nonce"] = "test-pending-code"
        else:
            user_id = OWNER.id if role == "owner" else 101
            request.session.update(user={"id": user_id, "role": role, "username": role, "name": role},
                                   sid=sessions.create(user_id, role), epoch=db.setting("session_epoch", "1"))
        return RedirectResponse("/setup", status_code=303)

    return app


@pytest.fixture
def env(tmp_path, monkeypatch):
    app = build_app(tmp_path / "setup.db")
    sent = []

    def post(url, **kwargs):
        sent.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(setup.httpx, "post", post)
    app.state.sent = sent
    return app


def client(app, role="owner"):
    cl = TestClient(app, follow_redirects=False)
    if role:
        cl.get(f"/test/session/{role}")
    return cl


def form(cl, path="/setup/notifications"):
    html = cl.get(path).text
    values = {"csrf": re.search(r'name="csrf" value="([^"]+)"', html).group(1)}
    revision = re.search(r'name="revision" value="([^"]+)"', html)
    if revision:
        values.update(revision=revision.group(1), method="configured", trusted="1")
    return values


def draft(cl, sections=("1", "2")):
    return cl.post("/setup/libraries", data={**form(cl, "/setup/libraries"), "sections": list(sections)})


def send_receipt_test(cl):
    assert draft(cl).status_code == 303
    assert cl.post("/setup/notifications", data={**form(cl), "action": "test"}).status_code == 200
    return {**form(cl), "action": "finish", "confirmed": "1"}


@pytest.mark.parametrize("role", [None, "parent", "waiting", "pending-owner-code"])
@pytest.mark.parametrize("path", ["/setup/libraries", "/setup/notifications", "/setup/ready"])
def test_owner_and_completed_code_required(env, role, path):
    cl = client(env, role)
    assert cl.get(path).status_code in (401, 404)
    if path != "/setup/ready":
        assert cl.post(path, data={"sections": "1", "action": "finish"}).status_code in (401, 404)
    assert not env.state.db.one("SELECT 1 FROM setup_drafts")


def test_public_start_never_claims_ownership(env):
    cl = client(env, None)
    assert "Sign in with Plex" in cl.get("/setup").text
    assert not env.state.db.one("SELECT 1 FROM sessions")
    assert not env.state.db.one("SELECT 1 FROM parents")


@pytest.mark.parametrize("key", ["session_epoch", "revoke"])
def test_revoked_owner_cannot_use_setup(env, key):
    cl = client(env)
    if key == "revoke":
        env.state.sessions.end_all()
    else:
        env.state.db.set_setting("session_epoch", "2")
    assert cl.get("/setup/libraries").status_code == 401
    assert cl.post("/setup/libraries", data={"sections": "1"}).status_code == 401


def test_library_chooser_starts_empty_and_only_shows_movies_tv(env):
    html = client(env).get("/setup/libraries").text
    assert "Movies" in html and "TV Shows" in html
    assert "Music" not in html
    assert " checked" not in html
    assert "America/Denver" in html and "Managed by your installation" in html


@pytest.mark.parametrize("sections", [[], ["3"], ["999"], ["1", "3"], ["1 OR 1=1"]])
def test_empty_invalid_and_non_video_libraries_rejected(env, sections):
    cl = client(env)
    response = draft(cl, sections)
    assert response.status_code == 400
    assert 'aria-invalid="true"' in response.text
    assert not env.state.db.one("SELECT 1 FROM setup_drafts")
    assert env.state.db.setting(setup.KID_SECTIONS) is None


def test_csrf_required_on_both_steps(env):
    cl = client(env)
    assert cl.post("/setup/libraries", data={"sections": "1"}).status_code == 400
    assert draft(cl).status_code == 303
    assert cl.post("/setup/notifications", data={"action": "test"}).status_code == 400
    assert not env.state.sent


def test_draft_resume_and_final_persistence_across_restart(env):
    cl = client(env)
    assert draft(cl, ["2"]).status_code == 303
    assert env.state.db.setting(setup.KID_SECTIONS) is None
    restarted = build_app(env.state.db.path)
    resumed = client(restarted)
    assert 'value="2" checked' in resumed.get("/setup/libraries").text
    assert 'value="1" checked' not in resumed.get("/setup/libraries").text
    assert resumed.post("/setup/notifications", data={**form(resumed), "action": "test"}).status_code == 200
    response = resumed.post("/setup/notifications", data={**form(resumed), "action": "finish", "confirmed": "1"})
    assert response.status_code == 303 and response.headers["location"] == "/setup/ready"
    final = build_app(env.state.db.path)
    with closing(final.state.db._connect()) as conn:
        assert setup.effective_kid_sections(conn, ("1",)) == ("2",)
        assert setup.trusted_parents_enabled(conn)
        assert setup.get_setting(conn, "setup.completed_at")
    assert "TV Shows" in client(final).get("/setup/ready").text
    assert not final.state.db.one("SELECT 1 FROM setup_drafts")


def test_test_requires_transport_and_human_receipt(env):
    cl = client(env)
    assert draft(cl).status_code == 303
    data = {**form(cl), "action": "finish", "confirmed": "1"}
    assert cl.post("/setup/notifications", data=data).status_code == 400
    assert cl.post("/setup/notifications", data={**form(cl), "action": "test"}).status_code == 200
    assert cl.post("/setup/notifications", data={**form(cl), "action": "finish"}).status_code == 400
    assert not env.state.db.setting("setup.completed_at")
    assert env.state.sent[0][1]["follow_redirects"] is False
    assert env.state.sent[0][1]["json"]["url"] == "http://testserver/setup/notifications"


def test_no_receiver_never_completes_and_never_exposes_credentials(env):
    env.state.notifier.endpoint = None
    cl = client(env)
    assert draft(cl).status_code == 303
    html = cl.get("/setup/notifications").text
    assert "Finish private notification setup on your server" in html
    assert 'type="password"' not in html and 'name="webhook"' not in html
    assert cl.post("/setup/notifications", data={**form(cl), "action": "test"}).status_code == 503
    assert not env.state.sent


def test_failed_test_invalidates_old_receipt_without_leaking_url(env, monkeypatch, caplog):
    cl = client(env)
    send_receipt_test(cl)

    def fail(url, **kwargs):
        raise httpx.ConnectError(url)

    monkeypatch.setattr(setup.httpx, "post", fail)
    response = cl.post("/setup/notifications", data={**form(cl), "action": "test"})
    assert response.status_code == 503
    assert "private-capability" not in response.text + caplog.text
    assert env.state.db.one("SELECT test_sent_at FROM setup_drafts")[0] is None
    assert cl.post("/setup/notifications", data={**form(cl), "action": "finish", "confirmed": "1"}).status_code == 400


@pytest.mark.parametrize("change", ["receiver", "expired", "library_removed", "library_type_changed", "draft"])
def test_finish_rechecks_test_libraries_and_draft(env, change):
    cl = client(env)
    data = send_receipt_test(cl)
    if change == "receiver":
        env.state.notifier.endpoint = "https://receiver.invalid/replacement"
    elif change == "expired":
        env.state.db.x("UPDATE setup_drafts SET test_sent_at=?", (time.time() - setup.TEST_TTL - 1,))
    elif change == "library_removed":
        env.state.plex.kinds = {"2": "show", "3": "artist"}
    elif change == "library_type_changed":
        env.state.plex.kinds = {"1": "artist", "2": "show", "3": "artist"}
    else:
        assert draft(cl, ("2",)).status_code == 303
    assert cl.post("/setup/notifications", data=data).status_code in (400, 409)
    assert env.state.db.setting(setup.KID_SECTIONS) is None
    assert env.state.db.one("SELECT 1 FROM setup_drafts")


def test_test_send_rate_limited(env):
    cl = client(env)
    assert draft(cl).status_code == 303
    for _ in range(3):
        assert cl.post("/setup/notifications", data={**form(cl), "action": "test"}).status_code == 200
    assert cl.post("/setup/notifications", data={**form(cl), "action": "test"}).status_code == 429
    assert len(env.state.sent) == 3


def test_owner_revoked_during_plex_read_cannot_finish(env, monkeypatch):
    cl = client(env)
    data = send_receipt_test(cl)
    read = env.state.plex.readiness_sections

    def revoke_during_read():
        env.state.sessions.end_all()
        return read()

    monkeypatch.setattr(env.state.plex, "readiness_sections", revoke_during_read)
    assert cl.post("/setup/notifications", data=data).status_code == 401
    assert env.state.db.setting("setup.completed_at") is None


def test_fresh_and_existing_migration_defaults_and_idempotence(tmp_path):
    for existing in (False, True):
        path = tmp_path / f"{existing}.db"
        if existing:
            with sqlite3.connect(path) as conn:
                conn.executescript(SCHEMA)
                conn.execute("INSERT INTO settings(key,value) VALUES('client_id','already-started')")
        db = DB(str(path))
        with closing(db._connect()) as conn, conn:
            setup.migrate(conn)
            assert setup.trusted_parents_enabled(conn) is (not existing)
            setup.set_setting(conn, setup.TRUSTED_PARENTS, "0" if not existing else "1")
            setup.migrate(conn)
            assert setup.trusted_parents_enabled(conn) is existing


def test_environment_is_fallback_until_setup_override_is_saved(env, monkeypatch):
    monkeypatch.setenv("PK_KID_SECTIONS", "2")
    defaults = config.load().kid_sections
    with closing(env.state.db._connect()) as conn:
        assert setup.effective_kid_sections(conn, defaults) == ("2",)
    cl = client(env)
    assert cl.post("/setup/notifications", data=send_receipt_test(cl)).status_code == 303
    with closing(env.state.db._connect()) as conn:
        assert setup.effective_kid_sections(conn, defaults) == ("1", "2")
    assert config.load().kid_sections == ("2",)


def test_setup_keeps_read_only_bootstrap_and_fixed_timezone_unchanged(env, tmp_path):
    from dataclasses import replace

    prefs = tmp_path / "Preferences.xml"
    prefs.write_bytes(b"<Preferences/>")
    prefs.chmod(0o444)
    env.state.setup.cfg = replace(env.state.setup.cfg, plex_prefs=str(prefs), tz="UTC")
    cl = client(env)
    assert "UTC" in cl.get("/setup/libraries").text
    data = send_receipt_test(cl)
    data["timezone"] = "America/New_York"
    data["plex_prefs"] = "untrusted-path"
    assert cl.post("/setup/notifications", data=data).status_code == 303
    assert prefs.read_bytes() == b"<Preferences/>"
    assert prefs.stat().st_mode & 0o777 == 0o444
    assert env.state.setup.cfg.tz == "UTC"
    assert not env.state.db.one("SELECT 1 FROM settings WHERE key IN ('timezone','plex_prefs')")


def test_missing_or_corrupt_settings_fail_closed(env):
    with closing(env.state.db._connect()) as conn, conn:
        conn.execute("DELETE FROM settings WHERE key=?", (setup.TRUSTED_PARENTS,))
        assert not setup.trusted_parents_enabled(conn)
        assert setup.effective_kid_sections(conn, ("2",)) == ("2",)
        setup.set_setting(conn, setup.KID_SECTIONS, "invalid-json")
        assert setup.effective_kid_sections(conn, ("2",)) == ()


def test_atomic_finish_rolls_back_settings_and_audit_on_write_failure(env):
    cl = client(env)
    data = send_receipt_test(cl)
    env.state.db.set_setting(setup.KID_SECTIONS, '["2"]')
    env.state.db.set_setting(setup.TRUSTED_PARENTS, "0")
    with closing(env.state.db._connect()) as conn, conn:
        conn.execute("CREATE TRIGGER fail_setup BEFORE INSERT ON audit "
                     "WHEN NEW.action='setup_saved' BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        cl.post("/setup/notifications", data=data)
    assert env.state.db.setting(setup.KID_SECTIONS) == '["2"]'
    assert env.state.db.setting(setup.TRUSTED_PARENTS) == "0"
    assert env.state.db.setting(setup.POLICY_VERSION) == "1"
    assert env.state.db.setting("setup.completed_at") is None
    assert env.state.db.one("SELECT 1 FROM setup_drafts")


def test_trust_toggle_only_changes_future_policy_and_records_version(env):
    for status in ("requested", "active"):
        env.state.db.x("INSERT INTO kids(parent_id,name,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                       (101, status, status, now_iso(), now_iso()))
    cl = client(env)
    data = send_receipt_test(cl)
    data.pop("trusted")
    assert cl.post("/setup/notifications", data=data).status_code == 303
    assert env.state.db.setting(setup.TRUSTED_PARENTS) == "0"
    assert env.state.db.setting(setup.POLICY_VERSION) == "2"
    assert cl.post("/setup/notifications", data=send_receipt_test(cl)).status_code == 303
    assert env.state.db.setting(setup.TRUSTED_PARENTS) == "1"
    assert env.state.db.setting(setup.POLICY_VERSION) == "3"
    assert [r[0] for r in env.state.db.q("SELECT status FROM kids ORDER BY id")] == ["requested", "active"]
    assert len(env.state.db.q("SELECT 1 FROM audit WHERE action='setup_saved'")) == 2


def test_policy_change_waits_for_connection_claim(env):
    cl = client(env)
    data = send_receipt_test(cl)
    data.pop("trusted")
    env.state.db.x("INSERT INTO kids(parent_id,name,status,connection_claim,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                   (101, "Test", "draft", "in-progress", now_iso(), now_iso()))
    response = cl.post("/setup/notifications", data=data)
    assert response.status_code == 400 and "A child is connecting" in response.text
    assert env.state.db.setting(setup.TRUSTED_PARENTS) == "1"
    assert env.state.db.setting(setup.KID_SECTIONS) is None


def test_pages_are_valid_accessibly_named_and_have_no_inline_code(env):
    cl = client(env)
    pages = [cl.get("/setup"), cl.get("/setup/libraries"), draft(cl, ())]
    data = send_receipt_test(cl)
    pages += [cl.get("/setup/notifications")]
    assert cl.post("/setup/notifications", data=data).status_code == 303
    pages += [cl.get("/setup/ready")]
    for response in pages:
        html = response.text
        assert not strict_ok(html)
        assert not accessibility_problems(html)
        assert not re.search(r"\sstyle=|<style|<script(?![^>]*\ssrc=)|\son\w+=", html)
        assert "private-capability" not in html
