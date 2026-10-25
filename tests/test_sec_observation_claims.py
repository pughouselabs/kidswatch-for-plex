"""Real sharing reads and post-claim link races from security review round two."""
import asyncio
import multiprocessing
import re
import threading
import time
from types import MethodType

import httpx
import pytest

from app.plex import PlexClient, _Cache
from app.db import DB
from app.service import Service

from .fakes import ALEX, KID, OWNER, SAM
from .test_app import add_kid, client, csrf, env, login  # noqa: F401


def claim_in_worker(path, kid_id, claim, start, results):
    """Each process owns a separate DB object, like separate server workers."""
    db = DB(path)
    if start.wait(10):
        results.put((claim, db.claim_connection(kid_id, claim, OWNER.id)))


def test_child_claim_is_exclusive_across_processes(env):
    app, fake = env
    owner, _ = login(app, fake, "owner")
    kid_id = add_kid(app, fake, owner)
    app.state.db.x("ALTER TABLE kids DROP COLUMN connection_claim")  # concurrent upgrade of an existing DB too
    ctx = multiprocessing.get_context("spawn")
    start, results = ctx.Event(), ctx.Queue()
    workers = [ctx.Process(target=claim_in_worker,
                           args=(app.state.db.path, kid_id, f"claim-{n}", start, results)) for n in range(3)]
    try:
        for worker in workers:
            worker.start()
        start.set()
        answers = [results.get(timeout=15) for _ in workers]
        assert sum(ok for _, ok in answers) == 1
        winner = next(claim for claim, ok in answers if ok)
        assert app.state.db.kid(kid_id)["connection_claim"] == winner
        # A different DB object also honors the claim when rotating a link.
        assert not DB(app.state.db.path).rotate_link(kid_id, OWNER.id, "replacement", time.time() + 600)
        app.state.db.release_connection(kid_id, winner)
    finally:
        for worker in workers:
            worker.join(15)
            if worker.is_alive():
                worker.terminate()
                worker.join(5)
    assert all(worker.exitcode == 0 for worker in workers)


@pytest.mark.parametrize("invalid", ["rotated", "expired", "disabled", "unshared", "stale"])
def test_atomic_link_claim_rechecks_current_link_and_parent(env, invalid):
    app, fake = env
    parent, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, parent)
    db = app.state.db
    old = db.one("SELECT token FROM link_tokens WHERE kid_id=?", (kid_id,))["token"]
    if invalid == "rotated":
        assert db.rotate_link(kid_id, ALEX.id, "replacement", time.time() + 600)
    elif invalid == "expired":
        db.x("UPDATE link_tokens SET expires_at=0 WHERE token=?", (old,))
    elif invalid == "disabled":
        assert db.set_parent_enabled(ALEX.id, 0)
    else:
        users, at = ([SAM.id], time.time()) if invalid == "unshared" else ([ALEX.id], time.time() - 86400)
        db.publish_shared(users, at, db.next_shared_observation())
    assert not db.claim_connection(kid_id, old, OWNER.id, link=True)
    assert db.kid(kid_id)["connection_claim"] is None


@pytest.mark.parametrize("separate_worker", [False, True])
def test_out_of_order_http_then_second_parent_login_preserves_first_session(env, monkeypatch, separate_worker):
    app, fake = env
    entered, release = threading.Event(), threading.Event()
    calls = []

    def request(*args, **kwargs):
        calls.append(1)
        older = len(calls) == 1
        if older:
            entered.set()
            assert release.wait(10)
        people = [SAM] if older else [ALEX, SAM]
        xml = '<MediaContainer>' + ''.join(
            f'<User id="{a.id}" username="{a.username}"><Server machineIdentifier="machine"/></User>'
            for a in people) + '</MediaContainer>'
        return httpx.Response(200, text=xml)

    fake.cache = _Cache()
    monkeypatch.setattr(fake, "shared_users", MethodType(PlexClient.shared_users, fake))
    monkeypatch.setattr(fake, "_xml", MethodType(PlexClient._xml, fake), raising=False)
    monkeypatch.setattr(fake, "_req", request, raising=False)
    svc = app.state.svc
    if separate_worker:
        svc = Service(DB(app.state.db.path), fake, svc.cfg)
    task = threading.Thread(target=svc.refresh_shared)
    task.start()
    try:
        assert entered.wait(10)
        alex, r = login(app, fake, "alex")
        assert r.status_code == 303 and alex.get("/home").status_code == 200
    finally:
        release.set()
        task.join(10)
    assert not task.is_alive()
    sam, r = login(app, fake, "sam")
    assert r.status_code == 303 and sam.get("/home").status_code == 200
    assert alex.get("/home").status_code == 200
    assert app.state.db.one("SELECT COUNT(*) AS n FROM sessions WHERE user_id=?", (ALEX.id,))["n"] == 1


def test_negative_session_deletion_rechecks_newer_publication_from_another_worker(env, monkeypatch):
    app, fake = env
    alex, _ = login(app, fake, "alex")
    db = app.state.db
    db.publish_shared([SAM.id], time.time(), db.next_shared_observation())
    other_worker = DB(db.path)
    revoke = db.end_session_for_observation
    attempted = []

    def newer_before_delete(key, observed):
        attempted.append(observed)
        other_worker.publish_shared([ALEX.id, SAM.id], time.time(), other_worker.next_shared_observation())
        return revoke(key, observed)

    monkeypatch.setattr(db, "end_session_for_observation", newer_before_delete)
    assert alex.get("/home").status_code == 200
    assert len(attempted) == 1
    assert db.one("SELECT COUNT(*) AS n FROM sessions WHERE user_id=?", (ALEX.id,))["n"] == 1


def test_post_claim_rotation_and_two_accounts_on_one_event_loop(env, monkeypatch):
    app, fake = env
    owner, _ = login(app, fake, "owner")
    kid_id = add_kid(app, fake, owner)
    page = owner.get(f"/kids/{kid_id}").text
    old = re.search(r'value="http://testserver(/link/[^\"]+)"', page).group(1)
    token = csrf(owner, f"/kids/{kid_id}")
    entered, release = threading.Event(), threading.Event()
    account = fake.account

    def slow(value):
        if value == "tok-kid1":
            entered.set()
            assert release.wait(10)
        return account(value)

    monkeypatch.setattr(fake, "account", slow)

    async def race():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as first, \
                httpx.AsyncClient(transport=transport, base_url="http://testserver") as second, \
                httpx.AsyncClient(transport=transport, base_url="http://testserver", cookies=owner.cookies) as parent:
            fake.next_token = "tok-kid1"
            assert (await first.get(old + "/start")).status_code == 303
            pending = asyncio.create_task(first.get(old + "/done"))
            try:
                assert await asyncio.to_thread(entered.wait, 10)
                rotation = await parent.post(f"/kids/{kid_id}/connect", data={"csrf": token})
                page = await parent.get(f"/kids/{kid_id}")
                match = re.search(r'value="http://testserver(/link/[^\"]+)"', page.text)
                new = match.group(1) if match else old
                fake.next_token = "tok-stranger"
                await second.get(new + "/start")
                second_result = await second.get(new + "/done")
            finally:
                release.set()
                result = await pending
            assert rotation.status_code == 409
            assert "in progress" in rotation.text.lower()
            assert second_result.status_code == 410
            assert result.status_code == 200

    asyncio.run(race())
    assert app.state.db.kid(kid_id)["plex_id"] == KID.id
    assert set(fake.share_map) == {KID.id}
    assert len([c for c in fake.calls if c[0] == "invite"]) == 1


@pytest.mark.parametrize("pause", ["connect", "account", "invite"])
def test_parent_disable_is_serialized_with_claim(env, monkeypatch, pause):
    app, fake = env
    parent, _ = login(app, fake, "alex")
    owner, _ = login(app, fake, "owner")
    kid_id = add_kid(app, fake, parent)
    path = re.search(r'value="http://testserver(/link/[^\"]+)"', parent.get(f"/kids/{kid_id}").text).group(1)
    browser = client(app)
    fake.next_token = "tok-kid1"
    browser.get(path + "/start")
    entered, release = threading.Event(), threading.Event()
    target = app.state.svc if pause == "connect" else fake
    original = getattr(target, pause)

    def slow(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(target, pause, slow)
    results = []
    pending = threading.Thread(target=lambda: results.append(browser.get(path + "/done")))
    pending.start()
    try:
        assert entered.wait(10)
        response = owner.post(f"/admin/parents/{ALEX.id}", data={"csrf": csrf(owner, "/admin")})
        assert response.status_code == 409 and "in progress" in response.text.lower()
        assert app.state.db.one("SELECT enabled FROM parents WHERE plex_id=?", (ALEX.id,))["enabled"] == 1
    finally:
        release.set()
        pending.join(10)
    assert not pending.is_alive() and results[0].status_code == 200
    assert set(fake.share_map) == {KID.id}
    assert app.state.db.kid(kid_id)["plex_id"] == KID.id
    response = owner.post(f"/admin/parents/{ALEX.id}", data={"csrf": csrf(owner, "/admin")})
    assert response.status_code == 303


def test_rotation_refuses_immediately_after_claim(env, monkeypatch):
    app, fake = env
    owner, _ = login(app, fake, "owner")
    kid_id = add_kid(app, fake, owner)
    path = re.search(r'value="http://testserver(/link/[^\"]+)"', owner.get(f"/kids/{kid_id}").text).group(1)
    browser = client(app)
    fake.next_token = "tok-kid1"
    browser.get(path + "/start")
    entered, release = threading.Event(), threading.Event()
    connect = app.state.svc.connect

    def slow(*args):
        entered.set()
        assert release.wait(10)
        return connect(*args)

    monkeypatch.setattr(app.state.svc, "connect", slow)
    results = []
    pending = threading.Thread(target=lambda: results.append(browser.get(path + "/done")))
    pending.start()
    try:
        assert entered.wait(10)
        response = owner.post(f"/kids/{kid_id}/connect", data={"csrf": csrf(owner, f"/kids/{kid_id}")})
        assert response.status_code == 409
    finally:
        release.set()
        pending.join(10)
    assert not pending.is_alive() and results[0].status_code == 200
    assert set(fake.share_map) == {KID.id}


def test_canceled_callback_keeps_claim_until_plex_thread_finishes(env, monkeypatch):
    app, fake = env
    owner, _ = login(app, fake, "owner")
    kid_id = add_kid(app, fake, owner)
    path = re.search(r'value="http://testserver(/link/[^\"]+)"', owner.get(f"/kids/{kid_id}").text).group(1)
    token = csrf(owner, f"/kids/{kid_id}")
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    account, connect = fake.account, app.state.svc.connect

    def slow(value):
        entered.set()
        assert release.wait(10)
        return account(value)

    def tracked(*args):
        try:
            return connect(*args)
        finally:
            completed.set()

    monkeypatch.setattr(fake, "account", slow)
    monkeypatch.setattr(app.state.svc, "connect", tracked)

    async def race():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as browser, \
                httpx.AsyncClient(transport=transport, base_url="http://testserver", cookies=owner.cookies) as parent:
            fake.next_token = "tok-kid1"
            assert (await browser.get(path + "/start")).status_code == 303
            pending = asyncio.create_task(browser.get(path + "/done"))
            try:
                assert await asyncio.to_thread(entered.wait, 10)
                pending.cancel()
                response = await parent.post(f"/kids/{kid_id}/connect", data={"csrf": token})
                assert response.status_code == 409
                assert app.state.db.kid(kid_id)["connection_claim"] is not None
            finally:
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                assert await asyncio.to_thread(completed.wait, 10)

    asyncio.run(race())
    assert app.state.db.kid(kid_id)["connection_claim"] is None
    assert set(fake.share_map) == {KID.id}
