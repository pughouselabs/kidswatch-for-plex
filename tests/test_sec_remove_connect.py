"""Review three: child lifecycle races and a restored sharing sequence."""
import re
import threading
import time

import pytest

from app import cleanup, rules
from app.db import DB
from app.service import Service

from .fakes import ALEX, KID, SAM
from .test_app import add_kid, client, csrf, env, login  # noqa: F401


@pytest.mark.parametrize("parent_name", ["owner", "alex"])
@pytest.mark.parametrize("pause", ["account", "invite", "accept_invite_from_owner", "block_online_sources"])
def test_remove_refuses_while_connection_owns_child(env, monkeypatch, parent_name, pause):
    app, fake = env
    parent, _ = login(app, fake, parent_name)
    kid_id = add_kid(app, fake, parent)
    token = csrf(parent, f"/kids/{kid_id}")
    path = re.search(r'value="http://testserver(/link/[^\"]+)"', parent.get(f"/kids/{kid_id}").text).group(1)
    browser = client(app)
    fake.next_token = "tok-kid1"
    assert browser.get(path + "/start").status_code == 303
    entered, release = threading.Event(), threading.Event()
    original = getattr(fake, pause)

    def slow(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(fake, pause, slow)
    results = []
    pending = threading.Thread(target=lambda: results.append(browser.get(path + "/done")))
    pending.start()
    try:
        assert entered.wait(10)
        response = parent.post(f"/kids/{kid_id}/remove", data={"csrf": token, "confirm": "Kid1"})
        assert response.status_code == 409 and "in progress" in response.text.lower()
        assert app.state.db.kid(kid_id)["connection_claim"] is not None
    finally:
        release.set()
        pending.join(10)
    assert not pending.is_alive() and results[0].status_code == 200
    kid = app.state.db.kid(kid_id)
    assert kid["status"] == ("active" if parent_name == "owner" else "requested")
    assert kid["plex_id"] == KID.id and kid["connection_claim"] is None
    assert set(fake.share_map) == {KID.id} and fake.share_map[KID.id].accepted
    assert fake.labeled(rules.ok_label(kid_id)) == ([11, 12, 14] if parent_name == "owner" else [])
    response = parent.post(f"/kids/{kid_id}/remove", data={"csrf": token, "confirm": "Kid1"})
    assert response.status_code == 303
    assert app.state.db.kid(kid_id)["status"] == "removed"
    assert app.state.db.kid(kid_id)["plex_id"] is None
    assert not fake.share_map and not fake.labeled(rules.ok_label(kid_id))
    assert not app.state.db.q("SELECT * FROM requests WHERE kid_id=? AND status='open'", (kid_id,))


@pytest.mark.parametrize("action", ["approve", "decline"])
def test_owner_decision_refuses_while_requested_child_reconnects(env, monkeypatch, action):
    app, fake = env
    parent, _ = login(app, fake, "alex")
    owner, _ = login(app, fake, "owner")
    kid_id = add_kid(app, fake, parent)
    fake.accept_ok = False
    assert app.state.svc.connect(kid_id, "tok-kid1")[0]
    request_id = app.state.db.x(
        "INSERT INTO requests(parent_id,kid_id,kind,message,created_at) VALUES(?,?,'add_child','Connect',?)",
        (ALEX.id, kid_id, "2026-10-24"))
    original = fake.accept_invite_from_owner
    entered, release = threading.Event(), threading.Event()

    def slow(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(fake, "accept_invite_from_owner", slow)
    # A second Service/DB represents another application worker.
    other = Service(DB(app.state.db.path), fake, app.state.svc.cfg)
    results = []
    pending = threading.Thread(target=lambda: results.append(other.connect(kid_id, "tok-kid1")))
    pending.start()
    try:
        assert entered.wait(10)
        response = owner.post(f"/admin/requests/{request_id}",
                              data={"csrf": csrf(owner, "/admin"), "action": action})
        assert response.status_code == 409
        assert app.state.db.kid(kid_id)["status"] == "requested"
        assert app.state.db.one("SELECT status FROM requests WHERE id=?", (request_id,))["status"] == "open"
    finally:
        release.set()
        pending.join(10)
    assert not pending.is_alive() and results[0][0]
    assert app.state.db.kid(kid_id)["plex_id"] == KID.id
    response = owner.post(f"/admin/requests/{request_id}",
                          data={"csrf": csrf(owner, "/admin"), "action": action})
    assert response.status_code == 303
    assert app.state.db.kid(kid_id)["status"] == ("active" if action == "approve" else "declined")
    assert bool(fake.share_map) == (action == "approve")


@pytest.mark.parametrize("recovery", ["immediate", "remove", "cleanup", "periodic"])
def test_lost_final_connection_write_retracts_and_preserves_retry_account(env, monkeypatch, recovery):
    app, fake = env
    owner, _ = login(app, fake, "owner")
    kid_id = add_kid(app, fake, owner)
    original = fake.block_online_sources

    def tombstone(*args):
        # Simulate an out-of-band tombstone after attachment and invite acceptance.
        # Normal lifecycle calls must refuse while the claim is held.
        app.state.db.update_kid(kid_id, status="removed", plex_id=None, sync_state="error",
                                sync_error="Titles: Removal pending")
        fake.fail_remove_share = recovery != "immediate"
        return original(*args)

    monkeypatch.setattr(fake, "block_online_sources", tombstone)
    result = app.state.svc.connect(kid_id, "tok-kid1")
    assert result[0] is False
    kid = app.state.db.kid(kid_id)
    assert kid["status"] == "removed" and kid["plex_id"] is None
    assert not fake.labeled(rules.ok_label(kid_id))
    assert bool(fake.share_map) == (recovery != "immediate")
    if recovery != "immediate":
        assert kid["sync_state"] == "error"
        # All recovery paths must find the account even without kids.plex_id.
        fake.fail_remove_share = False
        if recovery == "cleanup":
            # The ledger itself makes removed children discoverable, even without an error flag.
            app.state.db.update_kid(kid_id, sync_state="ok", sync_error=None)
            monkeypatch.setattr(cleanup, "PlexClient", lambda cfg, cid: fake)
            assert cleanup.main(["--apply"]) == 0
        elif recovery == "periodic":
            Service(DB(app.state.db.path), fake, app.state.svc.cfg).periodic()
        else:
            Service(DB(app.state.db.path), fake, app.state.svc.cfg).remove(kid_id, "test")
        assert not fake.share_map
    app.state.svc.remove(kid_id, "test")
    assert not fake.share_map and app.state.db.kid(kid_id)["status"] == "removed"


def test_remove_claim_wins_before_connect_can_start(env, monkeypatch):
    app, fake = env
    owner, _ = login(app, fake, "owner")
    kid_id = add_kid(app, fake, owner)
    entered, release = threading.Event(), threading.Event()
    original = app.state.svc._drop_share

    def slow(kid):
        entered.set()
        assert release.wait(10)
        return original(kid)

    monkeypatch.setattr(app.state.svc, "_drop_share", slow)
    results = []
    pending = threading.Thread(target=lambda: results.append(app.state.svc.remove(kid_id, "test")))
    pending.start()
    try:
        assert entered.wait(10)
        other = Service(DB(app.state.db.path), fake, app.state.svc.cfg)
        assert other.connect(kid_id, "tok-kid1")[0] is False
        assert "invite" not in fake.names()
    finally:
        release.set()
        pending.join(10)
    assert not pending.is_alive() and results == [None]
    assert app.state.db.kid(kid_id)["status"] == "removed"
    assert app.state.db.kid(kid_id)["connection_claim"] is None
    assert not fake.share_map and not fake.labeled(rules.ok_label(kid_id))


def test_restored_database_sequence_returns_503_without_loop_or_session_loss(env, monkeypatch):
    app, fake = env
    parent, _ = login(app, fake, "alex")
    svc, db = app.state.svc, app.state.db
    svc._shared_observed += 100
    svc.shared_ids, svc.shared_at = {SAM.id}, time.time()
    revoke = db.end_session_for_observation
    attempts = []

    def bounded_probe(*args):
        attempts.append(1)
        assert len(attempts) <= 3, "require() retried an obsolete observation indefinitely"
        return revoke(*args)

    monkeypatch.setattr(db, "end_session_for_observation", bounded_probe)
    assert parent.get("/home").status_code == 503
    assert len(attempts) <= 2
    assert db.one("SELECT COUNT(*) AS n FROM sessions WHERE user_id=?", (ALEX.id,))["n"] == 1
