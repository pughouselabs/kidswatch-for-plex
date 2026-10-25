"""Approval contracts on a minimal app, independent of main.py integration."""
import base64
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from starlette.middleware.sessions import SessionMiddleware

from app import config, rules
from app.approval_routes import router
from app.approvals import activate_child_on_connect, migrate
from app.db import DB, now_iso
from app.notify import Notifier
from app.plex import PlexError
from app.security import Sessions
from app.service import Service

from .fakes import ALEX, KID, OTHER, OWNER, FakePlex


class Notifications:
    def __init__(self):
        self.sent = []
        self.fail = False

    def send(self, title, body, path):
        if self.fail:
            raise RuntimeError("private receiver URL must never enter an audit")
        self.sent.append((title, body, path))


@pytest.fixture
def approval_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PK_DB", str(tmp_path / "approvals.db"))
    monkeypatch.setenv("PK_PUBLIC_URL", "http://testserver")
    cfg = config.load()
    db, plex = DB(cfg.db_path), FakePlex()
    with closing(db._connect()) as conn, conn:
        migrate(conn)
    svc = Service(db, plex, cfg)
    svc.sync_inline = True
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="approval-test-key", session_cookie="pk_session")
    app.include_router(router)
    app.state.db, app.state.plex, app.state.svc = db, plex, svc
    app.state.sessions, app.state.notifier = Sessions(db), Notifications()
    for acct, enabled in ((ALEX, 1), (OTHER, 0)):
        db.x("INSERT INTO parents(plex_id,username,title,enabled,created_at) VALUES(?,?,?,?,?)",
             (acct.id, acct.username, acct.title, enabled, now_iso()))
    return SimpleNamespace(app=app, db=db, plex=plex, svc=svc, notifier=app.state.notifier)


def sign_in(env, acct=OWNER, role="owner", *, session_role=None):
    sid = env.app.state.sessions.create(acct.id, session_role or role)
    session = {"user": {"id": acct.id, "username": acct.username, "name": acct.title, "role": role},
               "sid": sid, "epoch": env.db.setting("session_epoch", "1"), "csrf": "approval-form-token"}
    value = TimestampSigner("approval-test-key").sign(base64.b64encode(json.dumps(session).encode())).decode()
    client = TestClient(env.app, follow_redirects=False)
    client.cookies.set("pk_session", value)
    return client


def parent_request(env, message="Please let my family join."):
    return env.db.x("INSERT INTO requests(parent_id,kind,message,created_at) VALUES(?,?,?,?)",
                    (OTHER.id, "access", message, now_iso()))


def decision_form(owner, rid, action="approve"):
    html = owner.get(f"/admin/requests/{rid}").text
    return {"csrf": "approval-form-token", "action": action,
            "version": re.search(r'name="version" value="([^"]+)"', html).group(1)}


def new_connection(env):
    """Pause exactly at the integration hook, before the service releases its claim."""
    kid_id = env.db.x("INSERT INTO kids(parent_id,name,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                      (ALEX.id, "Sprout", "draft", now_iso(), now_iso()))
    env.svc.read_shared()
    assert env.db.claim_connection(kid_id, "connection-claim", OWNER.id)
    # These unit tests inject faults between connection and activation. The
    # integrated hook is covered separately against create_app and HTTP login.
    with patch("app.service.activate_child_on_connect", return_value="requested"):
        assert env.svc._connect(kid_id, "tok-kid1", "connection-claim")[0]
    return kid_id


def activate(env, kid_id):
    return activate_child_on_connect(env.svc, kid_id, "alex", env.notifier, claim="connection-claim")


def test_owner_inbox_is_parent_only_and_get_never_decides(approval_env):
    env = approval_env
    rid = parent_request(env)
    env.db.x("INSERT INTO requests(parent_id,kind,message,created_at) VALUES(?,?,?,?)",
             (ALEX.id, "add_child", "Hidden child approval", now_iso()))
    owner = sign_in(env)
    assert "casey" in owner.get("/admin/requests").text
    assert "Hidden child approval" not in owner.get("/admin/requests").text
    for path in (f"/admin/requests/{rid}?action=approve", f"/admin/requests/{rid}?decline=true"):
        assert owner.get(path).status_code == 200
    assert env.db.one("SELECT status FROM requests WHERE id=?", (rid,))[0] == "open"
    assert env.db.one("SELECT enabled FROM parents WHERE plex_id=?", (OTHER.id,))[0] == 0


@pytest.mark.parametrize("kind", ["anonymous", "parent", "waiting", "forged_owner", "pre_code", "revoked", "expired", "epoch"])
def test_owner_authorization(approval_env, kind):
    env = approval_env
    rid = parent_request(env)
    if kind == "anonymous":
        client = TestClient(env.app)
    elif kind in ("parent", "waiting"):
        client = sign_in(env, ALEX, kind)
    elif kind == "forged_owner":
        client = sign_in(env, ALEX, "owner")
    elif kind == "pre_code":
        client = sign_in(env, OWNER, "owner", session_role="waiting")
    else:
        client = sign_in(env)
        if kind == "revoked":
            env.app.state.sessions.end_user(OWNER.id)
        elif kind == "expired":
            env.db.x("UPDATE sessions SET last_seen=0")
        else:
            env.db.set_setting("session_epoch", "2")
    assert client.get(f"/admin/requests/{rid}").status_code in (401, 404)
    assert client.post(f"/admin/requests/{rid}/decision", data={"csrf": "approval-form-token", "action": "approve"}).status_code in (401, 404)


def test_csrf_and_changed_request_are_rejected(approval_env):
    env = approval_env
    rid, owner = parent_request(env), sign_in(env)
    data = decision_form(owner, rid)
    assert owner.post(f"/admin/requests/{rid}/decision", data={**data, "csrf": "wrong"}).status_code == 400
    assert owner.post(f"/admin/requests/{rid}/decision", data={**data, "csrf": ""}).status_code == 400
    env.db.x("UPDATE requests SET message='Changed details' WHERE id=?", (rid,))
    response = owner.post(f"/admin/requests/{rid}/decision", data=data)
    assert response.status_code == 409 and "This request changed" in response.text
    assert env.db.one("SELECT enabled FROM parents WHERE plex_id=?", (OTHER.id,))[0] == 0


def test_notification_without_owner_session_offers_sign_in_without_request_details(approval_env):
    env = approval_env
    rid = parent_request(env, "Private request details")
    response = TestClient(env.app).get(f"/admin/requests/{rid}")
    assert response.status_code == 401 and 'href="/login"' in response.text
    assert "Sign in as the server owner" in response.text and "Private request details" not in response.text


def test_new_decision_endpoint_does_not_replace_legacy_child_request_posts(approval_env):
    env = approval_env

    @env.app.post("/admin/requests/{req_id}")
    def legacy_child_request(req_id: int):
        return {"legacy_request": req_id}

    assert sign_in(env).post("/admin/requests/123").json() == {"legacy_request": 123}


def test_approval_is_idempotent_and_waiting_session_upgrades(approval_env):
    env = approval_env
    rid, owner = parent_request(env), sign_in(env)
    waiting = sign_in(env, OTHER, "waiting")
    assert "This page will update" in waiting.get("/waiting").text
    assert waiting.get("/waiting/status.json").json()["status"] == "open"
    data = decision_form(owner, rid)
    assert owner.post(f"/admin/requests/{rid}/decision", data=data).status_code == 303
    assert owner.post(f"/admin/requests/{rid}/decision", data={**data, "action": "decline"}).status_code == 303
    assert "Parent approved" in owner.get(f"/admin/requests/{rid}").text
    assert waiting.get("/waiting/status.json").json() == {"status": "approved", "accepted": True}
    assert waiting.get("/waiting").headers["location"] == "/home"
    assert env.db.one("SELECT role FROM sessions WHERE user_id=?", (OTHER.id,))[0] == "parent"
    assert len(env.db.q("SELECT * FROM audit WHERE action='request_approved'")) == 1


def test_decline_reply_is_escaped_and_does_not_reopen(approval_env):
    env = approval_env
    rid, owner = parent_request(env), sign_in(env)
    waiting = sign_in(env, OTHER, "waiting")
    data = decision_form(owner, rid, "decline")
    assert owner.post(f"/admin/requests/{rid}/decision", data={**data, "note": "<script>bad()</script>"}).status_code == 303
    assert owner.post(f"/admin/requests/{rid}/decision", data={**data, "action": "approve"}).status_code == 303
    page = waiting.get("/waiting").text
    assert "Your request was declined" in page and "&lt;script&gt;" in page and "<script>bad()" not in page
    assert waiting.post("/waiting", data={"csrf": "approval-form-token"}).status_code == 303
    assert len(env.db.q("SELECT * FROM requests WHERE kind='access'")) == 1
    assert env.db.one("SELECT enabled FROM parents WHERE plex_id=?", (OTHER.id,))[0] == 0


@pytest.mark.parametrize("failure", ["missing_share", "plex_down", "revoked_during_read", "epoch_during_read"])
def test_approval_rechecks_plex_and_session_at_write(approval_env, monkeypatch, failure):
    env = approval_env
    rid, owner = parent_request(env), sign_in(env)
    data = decision_form(owner, rid)
    read = env.plex.shared_users

    def changed():
        if failure == "plex_down":
            raise PlexError("offline")
        users = read()
        if failure == "missing_share":
            users.pop(OTHER.id)
        elif failure == "revoked_during_read":
            env.app.state.sessions.end_user(OWNER.id)
        else:
            env.db.set_setting("session_epoch", "2")
        return users

    monkeypatch.setattr(env.plex, "shared_users", changed)
    assert owner.post(f"/admin/requests/{rid}/decision", data=data).status_code in (401, 409, 503)
    assert env.db.one("SELECT enabled FROM parents WHERE plex_id=?", (OTHER.id,))[0] == 0
    assert env.db.one("SELECT status FROM requests WHERE id=?", (rid,))[0] == "open"


def test_concurrent_decisions_are_one_atomic_result(approval_env):
    env = approval_env
    rid = parent_request(env)
    clients = [sign_in(env), sign_in(env)]
    forms = [decision_form(clients[0], rid), decision_form(clients[1], rid, "decline")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda pair: pair[0].post(f"/admin/requests/{rid}/decision", data=pair[1]), zip(clients, forms)))
    assert all(r.status_code == 303 for r in responses)
    status = env.db.one("SELECT status FROM requests WHERE id=?", (rid,))[0]
    enabled = env.db.one("SELECT enabled FROM parents WHERE plex_id=?", (OTHER.id,))[0]
    assert enabled == int(status == "approved")
    assert len(env.db.q("SELECT * FROM audit WHERE action IN ('request_approved','request_declined')")) == 1


@pytest.mark.parametrize("failure", ["missing_share", "disabled", "revoked_session", "epoch"])
def test_waiting_does_not_promote_from_obsolete_access(approval_env, monkeypatch, failure):
    env = approval_env
    parent_request(env)
    waiting = sign_in(env, OTHER, "waiting")
    env.db.x("UPDATE parents SET enabled=1 WHERE plex_id=?", (OTHER.id,))
    read = env.plex.shared_users

    def changed():
        users = read()
        if failure == "missing_share":
            users.pop(OTHER.id)
        elif failure == "disabled":
            env.db.x("UPDATE parents SET enabled=0 WHERE plex_id=?", (OTHER.id,))
        elif failure == "revoked_session":
            env.app.state.sessions.end_user(OTHER.id)
        else:
            env.db.set_setting("session_epoch", "2")
        return users

    monkeypatch.setattr(env.plex, "shared_users", changed)
    response = waiting.get("/waiting/status.json")
    assert response.status_code == 401 or response.json()["status"] == "needs_share"
    assert not env.db.one("SELECT 1 FROM sessions WHERE user_id=? AND role='parent'", (OTHER.id,))


def test_waiting_request_is_one_time_and_has_private_deep_link(approval_env):
    env = approval_env
    waiting = sign_in(env, OTHER, "waiting")
    assert waiting.post("/waiting", data={}).status_code == 400
    for _ in range(2):
        assert waiting.post("/waiting", data={"csrf": "approval-form-token", "message": "Personal details"}).status_code == 303
    rows = env.db.q("SELECT * FROM requests WHERE kind='access'")
    assert len(rows) == 1 and len(env.notifier.sent) == 1
    _, body, path = env.notifier.sent[0]
    assert path == f"/admin/requests/{rows[0]['id']}"
    assert "Personal details" not in body and OTHER.username not in body


def test_default_trusted_connection_activates_with_safe_share_and_labels(approval_env):
    env = approval_env
    kid_id = new_connection(env)
    assert env.plex.labeled(rules.ok_label(kid_id)) == []
    assert activate(env, kid_id) == "active"
    assert env.db.kid(kid_id)["status"] == "active"
    assert env.plex.labeled(rules.ok_label(kid_id))
    assert env.svc.share_matches(env.plex.share_map[KID.id], ["1", "2"], rules.build_filters(kid_id))
    assert not env.db.q("SELECT * FROM requests WHERE kind='add_child'")
    path = env.notifier.sent[0][2]
    assert path == f"/admin/children/{kid_id}"
    assert TestClient(env.app).get(path).status_code == 401
    page = sign_in(env).get(path).text
    assert f'href="/kids/{kid_id}"' in page and f'href="/kids/{kid_id}/settings#remove"' in page
    assert "Sprout" not in env.notifier.sent[0][1]
    assert activate(env, kid_id) == "active" and len(env.notifier.sent) == 1
    receipt = env.db.one("SELECT claim_hash FROM approval_connections WHERE kid_id=?", (kid_id,))[0]
    assert receipt != "connection-claim" and len(receipt) == 64


def test_strict_mode_keeps_empty_share_and_policy_receipt(approval_env):
    env = approval_env
    env.db.set_setting("setup.trusted_parents", "0")
    kid_id = new_connection(env)
    assert activate(env, kid_id) == "requested"
    assert env.db.kid(kid_id)["status"] == "requested"
    assert env.plex.labeled(rules.ok_label(kid_id)) == [] and env.notifier.sent == []
    env.db.set_setting("setup.trusted_parents", "1")
    assert activate(env, kid_id) == "requested"
    assert "Earlier child requests" in sign_in(env).get("/admin/requests").text
    # Optional mode deliberately reuses the existing owner approval service.
    env.db.release_connection(kid_id, "connection-claim")
    env.svc.approve(kid_id, "owner")
    assert env.db.kid(kid_id)["status"] == "active" and env.plex.labeled(rules.ok_label(kid_id))
    env.db.set_setting("setup.trusted_parents", "0")
    assert env.db.kid(kid_id)["status"] == "active"


def test_migration_preserves_preexisting_pending_children_and_is_repeatable(approval_env):
    env = approval_env
    kid_id = new_connection(env)
    with closing(env.db._connect()) as conn, conn:
        migrate(conn)
        migrate(conn)
    assert activate(env, kid_id) == "requested"
    assert env.db.kid(kid_id)["status"] == "requested"
    assert env.db.one("SELECT trusted FROM approval_connections WHERE kid_id=?", (kid_id,))[0] == 0


@pytest.mark.parametrize("failure", ["disabled", "unshared", "claim_changed", "unsafe", "missing_share", "share_drift"])
def test_trusted_activation_rejects_revocation_or_unsafe_share(approval_env, monkeypatch, failure):
    env = approval_env
    kid_id = new_connection(env)
    if failure == "disabled":
        # The normal control refuses while this claim is held. Also cover an
        # independent revocation written before the activation transaction.
        assert not env.db.set_parent_enabled(ALEX.id, 0)
        env.db.x("UPDATE parents SET enabled=0 WHERE plex_id=?", (ALEX.id,))
    elif failure == "unshared":
        read = env.plex.shared_users
        monkeypatch.setattr(env.plex, "shared_users", lambda: {k: v for k, v in read().items() if k != ALEX.id})
    elif failure == "claim_changed":
        env.db.update_kid(kid_id, connection_claim="new-claim")
    elif failure == "unsafe":
        env.db.update_kid(kid_id, sync_error="Unsafe share: removal is pending")
    elif failure == "missing_share":
        env.plex.share_map.clear()
    else:
        env.plex.share_map[KID.id].filter_movies = ""
        env.plex.fail_filters = True
    with pytest.raises(PlexError):
        activate(env, kid_id)
    assert env.db.kid(kid_id)["status"] == "requested"
    assert env.plex.labeled(rules.ok_label(kid_id)) == [] and env.notifier.sent == []


def test_policy_is_read_after_network_work_and_persisted(approval_env, monkeypatch):
    env = approval_env
    kid_id = new_connection(env)
    apply = env.svc.apply

    def changed(*args):
        result = apply(*args)
        env.db.set_setting("setup.trusted_parents", "0")
        return result

    monkeypatch.setattr(env.svc, "apply", changed)
    assert activate(env, kid_id) == "requested"
    receipt = json.loads(env.db.one("SELECT detail FROM audit WHERE action='child_connection_policy'")[0])
    assert receipt["trusted"] is False and receipt["policy_version"] == 1


def test_failed_notification_does_not_undo_active_child(approval_env):
    env = approval_env
    kid_id = new_connection(env)
    env.notifier.fail = True
    assert activate(env, kid_id) == "active"
    assert env.db.kid(kid_id)["status"] == "active" and env.plex.labeled(rules.ok_label(kid_id))
    failure = env.db.one("SELECT detail FROM audit WHERE action='approval_notification_failed'")[0]
    assert json.loads(failure) == {"kid": kid_id} and "receiver" not in failure


def test_background_notification_delivery_failure_is_audited(approval_env, monkeypatch):
    env = approval_env
    kid_id = new_connection(env)
    env.notifier = Notifier("unused", "http://testserver")
    monkeypatch.setattr(env.notifier, "_url", lambda: "https://receiver.invalid/private-path")
    finished = threading.Event()
    audit = env.db.audit

    def record(actor, action, **detail):
        audit(actor, action, **detail)
        if action == "approval_notification_failed":
            finished.set()

    def failed(*args, **kwargs):
        raise RuntimeError("https://receiver.invalid/private-path")

    monkeypatch.setattr(env.db, "audit", record)
    monkeypatch.setattr("app.approvals.httpx.post", failed)
    assert activate(env, kid_id) == "active"
    assert finished.wait(5)
    failure = env.db.one("SELECT detail FROM audit WHERE action='approval_notification_failed'")[0]
    assert json.loads(failure) == {"kid": kid_id} and "private-path" not in failure
    assert env.db.kid(kid_id)["status"] == "active"


def test_waiting_uses_external_polling_and_accessible_forms(approval_env):
    env = approval_env
    parent_request(env)
    html = sign_in(env, OTHER, "waiting").get("/waiting").text
    assert 'data-wait-url="/waiting/status.json"' in html and 'aria-live="polite"' in html
    assert 'href="/waiting">Check access' in html
    assert not re.search(r'<script(?![^>]*\bsrc=)', html)
    assert not re.search(r'\s(?:style|on\w+)\s*=', html)
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in html
