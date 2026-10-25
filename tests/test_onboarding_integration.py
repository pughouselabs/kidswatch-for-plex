"""Wave 1 onboarding through the real app, sessions and child connection path."""
import json
import re
import sqlite3
from contextlib import closing
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
import pytest

from app import approvals, config, rules, setup
from app.db import DB, SCHEMA, now_iso
from app.main import create_app
from tests.fakes import FakePlex, KID
from tests.test_app import add_kid, client, connect, csrf, login
from tests.test_quality import accessibility_problems, strict_ok


@pytest.fixture
def onboarding(tmp_path, monkeypatch):
    for key, value in {
        "PK_DB": str(tmp_path / "fresh.db"), "PK_PUBLIC_URL": "http://testserver",
        "PK_SEED_PARENTS": "alex,sam", "PK_SECURE_COOKIES": "0", "PK_OWNER_CODE": "0",
        "PK_START_ENFORCER": "0", "PK_HA_WEBHOOK_FILE": str(tmp_path / "missing"),
    }.items():
        monkeypatch.setenv(key, value)
    plex = FakePlex()
    app = create_app(config.load(), plex=plex)
    app.state.svc.sync_inline = True
    notifications = []
    notifier = SimpleNamespace(send=lambda *args: notifications.append(args))
    app.state.notifier.send = notifier.send  # main notify helper retains the provisioned instance
    app.state.notifier = app.state.svc.notifier = notifier
    return app, plex, notifications


@pytest.mark.parametrize("existing", [None, "client_id", "app_rows"])
def test_install_trust_default_and_restart_preserve_policy(tmp_path, existing):
    path = str(tmp_path / "migration.db")
    if existing:
        with sqlite3.connect(path) as conn:
            conn.executescript(SCHEMA)
            if existing == "client_id":
                conn.execute("INSERT INTO settings VALUES('client_id','old-install')")
            else:
                conn.execute("INSERT INTO parents(plex_id,username,created_at) VALUES(101,'alex',?)", (now_iso(),))
    db = DB(path)
    with closing(db._connect()) as conn:
        assert setup.trusted_parents_enabled(conn) is (existing is None)
    db.set_setting(setup.TRUSTED_PARENTS, "0" if existing is None else "1")
    restarted = DB(path)
    with closing(restarted._connect()) as conn:
        assert setup.trusted_parents_enabled(conn) is (existing is not None)


@pytest.mark.parametrize("trusted", [True, False])
def test_connect_uses_trust_and_notifies_once_without_changing_strict_flow(onboarding, trusted):
    app, plex, notifications = onboarding
    db = app.state.db
    db.set_setting(setup.TRUSTED_PARENTS, "1" if trusted else "0")
    db.set_setting(setup.POLICY_VERSION, "7")
    parent, _ = login(app, plex, "alex")
    kid_id = add_kid(app, plex, parent, "Maya")
    page = parent.get(f"/kids/{kid_id}")
    assert ("titles show up right away" in page.text) is trusted
    response, _ = connect(app, plex, parent, kid_id)
    assert response.status_code == 200
    assert db.kid(kid_id)["status"] == ("active" if trusted else "requested")
    assert db.kid(kid_id)["connection_claim"] is None
    assert len(notifications) == 1
    assert app.state.svc.share_matches(plex.share_map[KID.id], ["1", "2"], rules.build_filters(kid_id))
    requests = db.q("SELECT * FROM requests WHERE kind='add_child'")
    receipt = json.loads(db.one("SELECT detail FROM audit WHERE action='child_connection_policy'")[0])
    assert receipt["trusted"] is trusted and receipt["policy_version"] == 7
    if trusted:
        assert not requests and plex.labeled(rules.ok_label(kid_id))
        assert notifications[0][2] == f"/admin/children/{kid_id}"
        assert "once it's approved" not in response.text
    else:
        assert len(requests) == 1 and not plex.labeled(rules.ok_label(kid_id))
        owner, _ = login(app, plex, "owner")
        assert owner.post(f"/admin/requests/{requests[0]['id']}", data={
            "csrf": csrf(owner, "/admin"), "action": "approve"}).status_code == 303
        assert db.kid(kid_id)["status"] == "active"


def test_activation_reads_policy_and_version_after_network_work(onboarding, monkeypatch):
    app, plex, _ = onboarding
    svc, db = app.state.svc, app.state.db
    parent, _ = login(app, plex, "alex")
    kid_id = add_kid(app, plex, parent)
    apply = svc.apply

    def policy_changed(*args):
        result = apply(*args)
        db.set_setting(setup.TRUSTED_PARENTS, "0")
        db.set_setting(setup.POLICY_VERSION, "8")
        return result

    policy_reads = []
    read_trust, read_setting = approvals.trusted_parents_enabled, approvals.get_setting

    def transactional_trust(conn):
        assert isinstance(conn, sqlite3.Connection) and conn.in_transaction
        assert conn.execute("SELECT connection_claim FROM kids WHERE id=?", (kid_id,)).fetchone()[0]
        policy_reads.append("trust")
        return read_trust(conn)

    def transactional_version(conn, key, default=None):
        assert conn.in_transaction and key == setup.POLICY_VERSION
        policy_reads.append("version")
        return read_setting(conn, key, default)

    monkeypatch.setattr(approvals, "trusted_parents_enabled", transactional_trust)
    monkeypatch.setattr(approvals, "get_setting", transactional_version)
    monkeypatch.setattr(svc, "apply", policy_changed)
    assert connect(app, plex, parent, kid_id)[0].status_code == 200
    receipt = json.loads(db.one("SELECT detail FROM audit WHERE action='child_connection_policy'")[0])
    assert policy_reads == ["trust", "version"]
    assert receipt["trusted"] is False and receipt["policy_version"] == 8
    assert db.kid(kid_id)["status"] == "requested"


def test_invite_signin_claim_review_and_waiting_session_upgrade(onboarding):
    app, plex, notifications = onboarding
    owner, _ = login(app, plex, "owner")
    response = owner.post("/admin/invites/new", data={"csrf": csrf(owner, "/admin/invites/new"),
                                                     "parent_id": "103", "days": "7"})
    assert response.status_code == 201
    join = re.search(r'value="http://testserver(/join/[A-Za-z0-9_-]{43})"', response.text).group(1)
    parent = client(app)
    assert "Sign in" in parent.get(join).text
    plex.next_token = "tok-casey"
    assert parent.get("/login?" + urlencode({"next": join})).status_code == 303
    assert parent.get("/auth/done").headers["location"] == join
    assert parent.post(join, data={"csrf": csrf(parent, join)}).headers["location"] == "/waiting"
    db = app.state.db
    request = db.one("SELECT * FROM requests WHERE kind='access'")
    rid = request["id"]
    assert request["status"] == "open" and not db.one("SELECT enabled FROM parents WHERE plex_id=103")[0]
    assert notifications[0][2] == f"/admin/requests/{rid}"
    assert parent.get("/waiting/status.json").json() == {"status": "open", "accepted": False}
    detail = owner.get(f"/admin/requests/{rid}")
    assert not strict_ok(detail.text) and not accessibility_problems(detail.text)
    version = re.search(r'name="version" value="([^"]+)"', detail.text).group(1)
    assert owner.post(f"/admin/requests/{rid}/decision", data={"csrf": csrf(owner, "/admin"),
                       "version": version, "action": "approve"}).status_code == 303
    assert parent.get("/waiting/status.json").json() == {"status": "approved", "accepted": True}
    assert parent.get("/waiting").headers["location"] == "/home"
    assert parent.get("/home").status_code == 200
    assert db.one("SELECT role FROM sessions WHERE user_id=103")[0] == "parent"


def test_legacy_parent_post_redirects_without_deciding(onboarding):
    app, plex, _ = onboarding
    waiting, _ = login(app, plex, "casey")
    waiting.post("/waiting", data={"csrf": csrf(waiting, "/waiting")})
    db = app.state.db
    rid = db.one("SELECT id FROM requests WHERE kind='access'")[0]
    owner, _ = login(app, plex, "owner")
    for action in ("approve", "decline", "done"):
        response = owner.post(f"/admin/requests/{rid}", data={"csrf": csrf(owner, "/admin"), "action": action})
        assert response.headers["location"] == f"/admin/requests/{rid}"
        assert db.one("SELECT status FROM requests WHERE id=?", (rid,))[0] == "open"
        assert db.one("SELECT enabled FROM parents WHERE plex_id=103")[0] == 0


@pytest.mark.parametrize("target", ["https://evil.example/join/" + "a" * 43, "//evil.example", "/admin", "/join/short",
                                    "/join/" + "a" * 43 + "/claim", "/join/" + "a" * 43 + "?x=1"])
def test_signin_return_rejects_everything_except_local_invite(onboarding, target):
    app, plex, _ = onboarding
    parent = client(app)
    plex.next_token = "tok-alex"
    parent.get("/login?" + urlencode({"next": target}))
    assert parent.get("/auth/done").headers["location"] == "/home"


def test_saved_library_override_reaches_service_forms_and_readiness(onboarding):
    app, plex, _ = onboarding
    app.state.db.set_setting(setup.KID_SECTIONS, '["2"]')
    parent, _ = login(app, plex, "alex")
    kid_id = add_kid(app, plex, parent)
    assert app.state.db.kid(kid_id)["sections"] == "2"
    assert connect(app, plex, parent, kid_id)[0].status_code == 200
    assert plex.share_map[KID.id].sections == ["2"]
    assert plex.labeled(rules.ok_label(kid_id)) == [12]
    settings = parent.get(f"/kids/{kid_id}/settings").text
    assert 'name="section_2"' in settings and 'name="section_1"' not in settings
    assert parent.get("/poster/11").status_code == 404
    plex.readiness_sections = lambda: {"2": "show"}
    assert client(app).get("/readyz").json()["libraries"] == "ok"


def test_email_tip_only_on_draft_regular_account_step_and_uses_child_name(onboarding):
    app, plex, _ = onboarding
    parent, _ = login(app, plex, "alex")
    kid_id = add_kid(app, plex, parent, "Maya")
    page = parent.get(f"/kids/{kid_id}")
    assert "Make a free Plex account for Maya" in page.text
    assert "Add <code>+mayaplex</code> right before the @ in your own email address." in page.text and "jane+mayaplex@gmail.com" in page.text
    assert not strict_ok(page.text) and not accessibility_problems(page.text)
    assert "email-tip-address" not in parent.get("/home").text
    assert connect(app, plex, parent, kid_id)[0].status_code == 200
    assert "email-tip-address" not in parent.get(f"/kids/{kid_id}").text


def test_owner_setup_persists_override_only_after_test_receipt(onboarding, monkeypatch):
    app, plex, _ = onboarding
    notifier = SimpleNamespace(_url=lambda: "https://receiver.invalid/test", public_url="http://testserver")
    app.state.notifier = notifier
    monkeypatch.setattr(setup.httpx, "post", lambda url, **kwargs:
                        httpx.Response(200, request=httpx.Request("POST", url)))
    owner, _ = login(app, plex, "owner")
    assert owner.post("/setup/libraries", data={"csrf": csrf(owner, "/setup/libraries"),
                                               "sections": ["2"]}).headers["location"] == "/setup/notifications"
    assert app.state.svc.library_sections() == ("1", "2")
    page = owner.get("/setup/notifications")
    revision = re.search(r'name="revision" value="([^"]+)"', page.text).group(1)
    data = {"csrf": csrf(owner, "/setup/notifications"), "revision": revision, "method": "configured", "trusted": "1"}
    assert owner.post("/setup/notifications", data={**data, "action": "finish", "confirmed": "1"}).status_code == 400
    assert owner.post("/setup/notifications", data={**data, "action": "test"}).status_code == 200
    response = owner.post("/setup/notifications", data={**data, "action": "finish", "confirmed": "1"})
    assert response.headers["location"] == "/setup/ready"
    assert app.state.svc.library_sections() == ("2",)
    ready = owner.get("/setup/ready")
    assert ready.status_code == 200 and "TV Shows" in ready.text
    assert not strict_ok(ready.text) and not accessibility_problems(ready.text)


def test_narrowed_policy_cleans_existing_labels_and_empty_child_library_share(onboarding):
    app, plex, _ = onboarding
    parent, _ = login(app, plex, "alex")
    kid_id = add_kid(app, plex, parent)
    assert connect(app, plex, parent, kid_id)[0].status_code == 200
    assert set(plex.labeled(rules.ok_label(kid_id))) == {11, 12, 14}
    app.state.db.set_setting(setup.KID_SECTIONS, '["2"]')
    app.state.svc.periodic()
    assert plex.share_map[KID.id].sections == ["2"]
    assert plex.labeled(rules.ok_label(kid_id)) == [12]
    # A child with no overlap must lose its old libraries and labels too.
    app.state.db.update_kid(kid_id, sections="2")
    app.state.db.set_setting(setup.KID_SECTIONS, '["1"]')
    app.state.svc.periodic()
    assert KID.id not in plex.share_map
    assert not plex.labeled(rules.ok_label(kid_id))


def test_invalid_saved_library_policy_cannot_create_a_share(onboarding):
    app, plex, _ = onboarding
    parent, _ = login(app, plex, "alex")
    kid_id = add_kid(app, plex, parent)
    app.state.db.set_setting(setup.KID_SECTIONS, "invalid-json")
    response, _ = connect(app, plex, parent, kid_id)
    assert response.status_code == 400
    assert "Choose at least one permitted library" in response.text
    assert not plex.share_map and app.state.db.kid(kid_id)["status"] == "draft"
