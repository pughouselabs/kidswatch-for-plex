import re
import sqlite3
import time
import pytest
from fastapi.testclient import TestClient

from app import config, rules
from app.main import create_app
from app.db import SCHEMA
from app.plex import PlexError, Session, Share

from .fakes import KID, FakePlex


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PK_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("PK_PUBLIC_URL", "http://testserver")
    monkeypatch.setenv("PK_SEED_PARENTS", "alex,sam")
    monkeypatch.setenv("PK_SECURE_COOKIES", "0")
    monkeypatch.setenv("PK_START_ENFORCER", "0")
    monkeypatch.setenv("PK_OWNER_CODE", "0")
    monkeypatch.setenv("PK_HA_WEBHOOK_FILE", str(tmp_path / "missing"))
    fake = FakePlex()
    # The established regression suite exercises upgraded installs, which keep
    # per-child approval. Fresh-install behavior has separate integration tests.
    with sqlite3.connect(str(tmp_path / "t.db")) as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO settings(key,value) VALUES('client_id','existing-install')")
    app = create_app(config.load(), plex=fake)
    app.state.svc.sync_inline = True
    return app, fake


def client(app):
    return TestClient(app, base_url="http://testserver", follow_redirects=False)


def login(app, fake, username):
    c = client(app)
    fake.next_token = f"tok-{username}"
    r = c.get("/login")
    assert r.status_code == 303 and "app.plex.tv/auth" in r.headers["location"]
    r = c.get("/auth/done")
    return c, r


def csrf(c, path="/help"):
    html = c.get(path).text
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def add_kid(app, fake, c, name="Kid1", level="kids"):
    tok = csrf(c)
    r = c.post("/kids/new", data={"csrf": tok, "name": name, "age_level": level})
    assert r.status_code == 303
    return int(r.headers["location"].split("#")[0].rsplit("/", 1)[1])


def approve_all(app, fake):
    o, _ = login(app, fake, "owner")
    tok = csrf(o, "/admin")
    for rid in re.findall(r'href="/admin/requests/(\d+)"', o.get("/admin").text):
        page = o.get(f"/admin/requests/{rid}").text
        version = re.search(r'name="version" value="([^"]+)"', page)
        if version:
            assert o.post(f"/admin/requests/{rid}/decision", data={"csrf": tok, "action": "approve",
                                                                  "version": version.group(1)}).status_code == 303
    for rid in re.findall(r'action="/admin/requests/(\d+)"', o.get("/admin").text):
        assert o.post(f"/admin/requests/{rid}", data={"csrf": tok, "action": "approve"}).status_code == 303
    return o


def connect(app, fake, c, kid_id, as_token="tok-kid1"):
    page = c.get(f"/kids/{kid_id}").text
    if 'value="http://testserver/link/' not in page:
        tok = csrf(c, f"/kids/{kid_id}")
        c.post(f"/kids/{kid_id}/connect", data={"csrf": tok})
    link = re.search(r'value="(http://testserver/link/[^"]+)"', c.get(f"/kids/{kid_id}").text).group(1)
    path = link.replace("http://testserver", "")
    private = client(app)  # a private window: no parent cookie
    assert private.get(path).status_code == 200
    fake.next_token = as_token
    assert private.get(path + "/start").status_code == 303
    return private.get(path + "/done"), path


def active_kid(app, fake):
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    r, _ = connect(app, fake, c, kid_id)
    assert r.status_code == 200, r.text
    approve_all(app, fake)
    assert app.state.db.kid(kid_id)["status"] == "active"
    return c, kid_id


def test_landing_and_headers(env):
    app, fake = env
    r = client(app).get("/")
    assert r.status_code == 200 and "Sign in with Plex" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_stranger_is_turned_away(env):
    app, fake = env
    c, r = login(app, fake, "stranger")
    assert r.status_code == 403
    assert c.get("/home").status_code == 303


def test_unlisted_shared_user_waits_then_owner_approves(env):
    app, fake = env
    c, r = login(app, fake, "casey")
    assert r.headers["location"] == "/waiting"
    tok = csrf(c, "/waiting")
    c.post("/waiting", data={"csrf": tok, "message": "please"})
    assert "Your request was sent" in c.get("/waiting").text
    assert c.get("/home").status_code == 303  # still not allowed
    approve_all(app, fake)
    c2, r2 = login(app, fake, "casey")
    assert r2.headers["location"] == "/home"


def test_seeded_parent_lands_home(env):
    app, fake = env
    c, r = login(app, fake, "alex")
    assert r.headers["location"] == "/home"
    assert "Hi, Alex" in c.get("/home").text


def test_owner_gets_admin_and_parents_cannot(env):
    app, fake = env
    o, r = login(app, fake, "owner")
    assert r.headers["location"] == "/admin"
    assert "Who can use this site" in o.get("/admin?tab=people").text
    assert "No open requests" in o.get("/admin").text
    c, _ = login(app, fake, "alex")
    assert c.get("/admin").status_code == 404


def test_post_without_csrf_is_rejected(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    r = c.post("/kids/new", data={"name": "Kid1", "age_level": "kids"})
    assert r.status_code == 400


def test_full_child_flow(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    invite = next(x for x in fake.calls if x[0] == "invite")
    assert invite[1] == "kid1" and invite[2] == ("1", "2")
    assert invite[3] == rules.build_filters(kid_id)
    assert "accept" in fake.names() and "block_online" in fake.names()
    page = c.get(f"/kids/{kid_id}").text
    assert 'data-field="used_text"' in page and "Cloud Garden" in page  # today card + history from Tautulli
    # tokens are never stored
    db = app.state.db
    assert "tok-kid1" not in str([dict(r) for r in db.q("SELECT * FROM audit")])


def test_link_works_once(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    approve_all(app, fake)
    r, path = connect(app, fake, c, kid_id)
    assert r.status_code == 200
    again = client(app)
    assert again.get(path).status_code == 410


def test_connect_as_parent_account_is_refused(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    approve_all(app, fake)
    r, _ = connect(app, fake, c, kid_id, as_token="tok-alex")
    assert r.status_code == 400 and "private" in r.text
    assert "invite" not in fake.names()


def test_existing_friend_account_is_refused(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    approve_all(app, fake)
    fake.share_map[KID.id] = Share(9, KID.id, "kid1", True, ["1"], "", "", True)
    r, _ = connect(app, fake, c, kid_id)
    assert r.status_code == 400 and "already has access" in r.text




def test_other_parent_cannot_see_or_change_kid(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    a, _ = login(app, fake, "sam")
    assert a.get(f"/kids/{kid_id}").status_code == 404
    assert a.get(f"/kids/{kid_id}/status.json").status_code == 404
    tok = csrf(a)
    assert a.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "pause"}).status_code == 404




def test_settings_validation(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    tok = csrf(c, f"/kids/{kid_id}")
    c.post(f"/kids/{kid_id}/settings", data={"csrf": tok, "level_choice": "age:kids", "weekday_minutes": "abc",
                                             "weekday_start": "07:00", "weekday_end": "20:00",
                                             "weekend_start": "07:00", "weekend_end": "21:00"})
    assert "Choose at least one library" in c.get(f"/kids/{kid_id}").text






def test_poster_only_for_kid_libraries(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    assert c.get("/poster/11").status_code == 200
    assert c.get("/poster/15").status_code == 404
    assert client(app).get("/poster/11").status_code == 303


def test_enforcer_counts_and_stops(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    db, enf = app.state.db, app.state.enforcer
    db.update_kid(kid_id, weekday_minutes=1, weekend_minutes=1, weekday_start="00:00", weekday_end="00:00",
                  weekend_start="00:00", weekend_end="00:00")
    fake.live = [Session("s1", KID.id, "movie", "Cloud Garden", "playing", "Living Room TV"),
                 Session("s2", 555, "movie", "Other user", "playing", "TV")]
    now = time.time()
    enf.tick(now)
    enf.tick(now + 30)
    assert db.used(kid_id, app.state.svc.today()) == 30
    assert "terminate" not in fake.names()
    enf.tick(now + 60)
    stops = [x for x in fake.calls if x[0] == "terminate"]
    assert stops and stops[0][1] == "s1" and "screen time" in stops[0][2]
    assert all(x[1] != "s2" for x in stops)  # other people are never touched
    assert db.q("SELECT reason FROM stops WHERE kid_id=?", (kid_id,))[0]["reason"] == "limit"
    st = c.get(f"/kids/{kid_id}/status.json").json()
    assert st["blocked"] == "limit"


def test_pause_stops_even_with_time_left(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    tok = csrf(c, f"/kids/{kid_id}")
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "pause"})
    app.state.db.update_kid(kid_id, weekday_start="00:00", weekday_end="00:00", weekend_start="00:00",
                            weekend_end="00:00")
    fake.live = [Session("s9", KID.id, "episode", "Maple Club: Camping", "paused", "iPad")]
    app.state.enforcer.tick(time.time())
    assert ("terminate", "s9", rules.stop_message("paused", None, None)) in fake.calls
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "bonus:30"})
    assert app.state.db.kid(kid_id)["bonus_minutes"] == 30


def test_music_is_not_counted(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    fake.live = [Session(None, KID.id, "track", "Song", "playing", "Phone")]
    enf = app.state.enforcer
    enf.tick(time.time())
    enf.tick(time.time() + 20)
    assert app.state.db.used(kid_id, app.state.svc.today()) == 0


def test_requests_reach_owner_and_reply_shows(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    tok = csrf(c)
    c.post("/requests", data={"csrf": tok, "kind": "title", "message": "Please add Maple Club season 3"})
    o, _ = login(app, fake, "owner")
    admin = o.get("/admin").text
    assert "Please add Maple Club season 3" in admin
    rid = re.search(r'action="/admin/requests/(\d+)"', admin).group(1)
    otok = csrf(o, "/admin")
    o.post(f"/admin/requests/{rid}", data={"csrf": otok, "action": "done", "note": "Added!"})
    page = c.get("/requests").text
    assert "Done" in page and "Added!" in page
    assert "Admin replied: “Added!”" in c.get("/home").text




def test_disabled_parent_is_signed_out(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    app.state.db.x("UPDATE parents SET enabled=0")
    assert c.get("/home").status_code == 303


def test_cleanup_removes_every_kid(env, monkeypatch, capsys):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    from app import cleanup
    monkeypatch.setattr(cleanup, "PlexClient", lambda cfg, cid: fake)
    assert cleanup.main([]) == 0 and "would remove: Kid1" in capsys.readouterr().out
    assert "remove_share" not in fake.names()
    assert cleanup.main(["--apply"]) == 0
    assert ("remove_share", 500 + KID.id) in fake.calls
    assert app.state.db.kid(kid_id)["status"] == "removed"


def test_cleanup_retries_labels_after_access_is_revoked(env, monkeypatch, capsys):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    from app import cleanup
    monkeypatch.setattr(cleanup, "PlexClient", lambda cfg, cid: fake)
    app.state.svc.set_title(kid_id, 16, "allow", "alex")
    label = rules.ok_label(kid_id)
    original_labels = fake.labeled(label)
    assert original_labels
    fake.fail_remove_labels = True

    assert cleanup.main(["--apply"]) == 1
    output = capsys.readouterr().out
    assert "FAILED: Kid1" in output and "Run cleanup again to retry" in output
    assert "removed: Kid1" not in output
    assert KID.id not in fake.share_map  # access stays revoked despite leftover labels
    kid = app.state.db.kid(kid_id)
    assert kid["status"] == "removed" and kid["plex_id"] is None
    assert kid["sync_state"] == "error" and kid["sync_error"].startswith("Titles:")
    assert fake.labeled(label) == original_labels
    assert app.state.db.q("SELECT 1 FROM kid_titles WHERE kid_id=?", (kid_id,))

    assert cleanup.main([]) == 0
    assert "would remove: Kid1" in capsys.readouterr().out
    fake.fail_remove_labels = False
    # Each CLI invocation creates a fresh Service and reopens the persistent database.
    assert cleanup.main(["--apply"]) == 0
    assert "removed: Kid1" in capsys.readouterr().out
    assert not fake.labeled(label)
    assert app.state.db.kid(kid_id)["sync_state"] == "ok"
    assert not app.state.db.q("SELECT 1 FROM kid_titles WHERE kid_id=?", (kid_id,))
    assert len([call for call in fake.calls if call[0] == "remove_share"]) == 1
    assert cleanup.main(["--apply"]) == 0
    assert "No kids' accounts to remove." in capsys.readouterr().out




def test_old_pin_is_refused(env, monkeypatch):
    app, fake = env
    c = client(app)
    fake.next_token = "tok-owner"
    c.get("/login")
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 400)
    r = c.get("/auth/done")
    assert r.status_code == 200 and "took too long" in r.text
    monkeypatch.setattr(time, "time", real)
    assert c.get("/admin").status_code == 303


def test_csrf_needs_a_real_session_token(env):
    app, fake = env
    c = client(app)
    assert c.post("/logout", data={"csrf": "x"}).status_code == 400
    assert c.post("/logout", data={"csrf": ""}).status_code == 400


def test_big_posts_are_refused(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    r = c.post("/requests", content=b"a=" + b"x" * 70000,
               headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413


def test_child_account_cannot_sign_in_even_when_shared(env, monkeypatch):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    from app.plex import SharedUser
    users = fake.shared_users()
    users[KID.id] = SharedUser(KID.id, "kid1", "kid1", "")
    monkeypatch.setattr(fake, "shared_users", lambda: users)
    k, r = login(app, fake, "kid1")
    assert "This site is for parents" in r.text
    assert k.get("/waiting").status_code == 303


def test_declined_access_cannot_be_requested_again(env):
    app, fake = env
    c, _ = login(app, fake, "casey")
    tok = csrf(c, "/waiting")
    c.post("/waiting", data={"csrf": tok})
    o, _ = login(app, fake, "owner")
    otok = csrf(o, "/admin")
    rid = re.search(r'href="/admin/requests/(\d+)"', o.get("/admin").text).group(1)
    version = re.search(r'name="version" value="([^"]+)"', o.get(f"/admin/requests/{rid}").text).group(1)
    o.post(f"/admin/requests/{rid}/decision", data={"csrf": otok, "action": "decline", "version": version})
    c.post("/waiting", data={"csrf": tok})
    n = app.state.db.one("SELECT COUNT(*) AS n FROM requests WHERE kind='access'")["n"]
    assert n == 1




def test_sign_everyone_out(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    o, _ = login(app, fake, "owner")
    assert c.get("/home").status_code == 200
    o.post("/admin/sign-out-all", data={"csrf": csrf(o, "/admin")})
    assert c.get("/home").status_code == 303
    assert o.get("/admin").status_code == 303


def test_parent_removed_from_plex_is_locked_out(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    app.state.svc.shared_ids = {102}  # Alex no longer shares the server
    assert c.get("/home").status_code == 303


def test_failed_optout_is_reported(env):
    app, fake = env
    fake.optout_fails = True
    c, kid_id = active_kid(app, fake)
    assert app.state.db.kid(kid_id)["online_off"] == 0
    assert "Online Media Sources" in c.get(f"/kids/{kid_id}").text




def test_admin_sign_in_needs_the_phone_code(tmp_path, monkeypatch):
    monkeypatch.setenv("PK_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("PK_PUBLIC_URL", "http://testserver")
    monkeypatch.setenv("PK_SECURE_COOKIES", "0")
    monkeypatch.setenv("PK_START_ENFORCER", "0")
    hook = tmp_path / "hook"
    hook.write_text("http://ha.invalid/api/webhook/x")
    monkeypatch.setenv("PK_HA_WEBHOOK_FILE", str(hook))
    fake = FakePlex()
    app = create_app(config.load(), plex=fake)
    sent = []
    monkeypatch.setattr(app.state.notifier, "send", lambda title, msg, path="/admin": sent.append(msg))
    c, r = login(app, fake, "owner")
    assert r.headers["location"] == "/verify"
    assert c.get("/admin").status_code == 303  # not signed in yet
    code = re.search(r"Code (\d{6})", sent[-1]).group(1)
    tok = csrf(c, "/verify")
    wrong = "000000" if code != "000000" else "111111"
    c.post("/verify", data={"csrf": tok, "code": wrong})
    assert c.get("/admin").status_code == 303
    r = c.post("/verify", data={"csrf": tok, "code": code})
    assert r.headers["location"] == "/admin" and c.get("/admin").status_code == 200
    # an attacker holding a phished sign-in gets a code request, never the code
    a, r = login(app, fake, "owner")
    atok = csrf(a, "/verify")
    for _ in range(5):
        a.post("/verify", data={"csrf": atok, "code": "123456" if code != "123456" else "654321"})
    assert a.get("/verify").status_code == 303 and a.get("/admin").status_code == 303


def test_admin_sign_in_refused_without_phone_alerts(tmp_path, monkeypatch):
    monkeypatch.setenv("PK_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("PK_PUBLIC_URL", "http://testserver")
    monkeypatch.setenv("PK_SECURE_COOKIES", "0")
    monkeypatch.setenv("PK_START_ENFORCER", "0")
    monkeypatch.setenv("PK_HA_WEBHOOK_FILE", str(tmp_path / "none"))
    fake = FakePlex()
    app = create_app(config.load(), plex=fake)
    c, r = login(app, fake, "owner")
    assert r.status_code == 503 and c.get("/admin").status_code == 303


def test_request_goes_out_only_after_the_account_is_connected(env):
    app, fake = env
    db = app.state.db
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    assert db.kid(kid_id)["status"] == "draft"
    assert db.one("SELECT COUNT(*) n FROM requests")["n"] == 0
    assert "invite" not in fake.names()
    r, _ = connect(app, fake, c, kid_id)
    assert r.status_code == 200 and "request went to" in r.text
    kid = db.kid(kid_id)
    assert kid["status"] == "requested" and kid["plex_accepted"] == 1
    req = db.one("SELECT * FROM requests WHERE kid_id=?", (kid_id,))
    assert req["kind"] == "add_child" and "Plex account: kid1" in req["message"]
    # the share exists but shows nothing until approval
    assert fake.share_map[KID.id].filter_movies == "label=pk-ok-%d" % kid_id
    assert fake.labeled(rules.ok_label(kid_id)) == []
    approve_all(app, fake)
    assert db.kid(kid_id)["status"] == "active"
    assert fake.labeled(rules.ok_label(kid_id)) == [11, 12, 14]  # Cloud Garden PG, Maple Club TV-Y, Sunny Wheels G
    assert db.kid(kid_id)["titles_count"] == 3


def test_decline_removes_the_share(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    connect(app, fake, c, kid_id)
    o, _ = login(app, fake, "owner")
    rid = app.state.db.one("SELECT id FROM requests WHERE kid_id=?", (kid_id,))["id"]
    o.post(f"/admin/requests/{rid}", data={"csrf": csrf(o, "/admin"), "action": "decline"})
    assert app.state.db.kid(kid_id)["status"] == "declined"
    assert KID.id not in fake.share_map


def test_settings_change_sections_and_relabel(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    tok = csrf(c, f"/kids/{kid_id}")
    r = c.post(f"/kids/{kid_id}/settings", data={"csrf": tok, "name": "Kid1", "level_choice": "age:little",
                                                 "section_1": "1"})
    assert r.status_code == 303
    r = c.post(f"/kids/{kid_id}/schedule", data={
        "csrf": tok, "weekday_minutes": "90", "weekend_minutes": "none", "weekday_start": "07:00",
        "weekday_end": "19:30", "weekend_start": "08:00", "weekend_end": "21:00"})
    assert r.status_code == 303
    kid = app.state.db.kid(kid_id)
    assert kid["age_level"] == "little" and kid["weekday_minutes"] == 90 and kid["weekend_minutes"] is None
    assert any(c[0] == "update_share" and c[2] == ("1",) for c in fake.calls)
    assert fake.labeled(rules.ok_label(kid_id)) == [14]  # Little kids, Movies only: just Sunny Wheels (G)


def test_titles_grid_allow_and_block(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    page = c.get(f"/kids/{kid_id}/titles").text
    for title in ("Cloud Garden", "Maple Club", "Sunny Wheels", "Mountain Puzzle"):
        assert title in page
    assert "Midnight Mystery" not in page and "Home Video" not in page  # R and unrated are never listed
    assert 'src="/poster/11"' in page
    tok = csrf(c, f"/kids/{kid_id}")
    label = rules.ok_label(kid_id)
    assert c.post(f"/kids/{kid_id}/titles", data={"csrf": tok, "key": "16", "kind": "allow"}).json() == {"ok": True}
    assert c.post(f"/kids/{kid_id}/titles", data={"csrf": tok, "key": "11", "kind": "block"}).json() == {"ok": True}
    assert fake.labeled(label) == [12, 14, 16]
    c.post(f"/kids/{kid_id}/titles", data={"csrf": tok, "key": "11", "kind": ""})  # back to the age level
    assert fake.labeled(label) == [11, 12, 14, 16]
    r = c.post(f"/kids/{kid_id}/titles", data={"csrf": tok, "key": "13", "kind": "allow"})
    assert r.status_code == 400 and "PG-13" in r.json()["error"]
    r = c.post(f"/kids/{kid_id}/titles", data={"csrf": tok, "key": "15", "kind": "allow"})
    assert r.status_code == 400  # not in this child's libraries
    overview = c.get(f"/kids/{kid_id}").text
    assert "plus 1 allowed and 0 blocked by you" in overview


def test_only_allowed_titles_mode(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    tok = csrf(c, f"/kids/{kid_id}")
    c.post(f"/kids/{kid_id}/titles", data={"csrf": tok, "key": "12", "kind": "allow"})
    c.post(f"/kids/{kid_id}/settings", data={
        "csrf": tok, "level_choice": "picked", "section_1": "1", "section_2": "1", "weekday_minutes": "60",
        "weekend_minutes": "60", "weekday_start": "07:00", "weekday_end": "20:00", "weekend_start": "07:00",
        "weekend_end": "21:00"})
    assert fake.labeled(rules.ok_label(kid_id)) == [12]


def test_title_sync_failure_is_shown_and_retried(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    fake.fail_labels = True
    tok = csrf(c, f"/kids/{kid_id}")
    c.post(f"/kids/{kid_id}/titles", data={"csrf": tok, "key": "11", "kind": "block"})
    assert app.state.db.kid(kid_id)["sync_state"] == "error"
    assert "KidsWatch retries every few minutes" in c.get(f"/kids/{kid_id}").text
    fake.fail_labels = False
    app.state.svc.periodic()
    assert app.state.db.kid(kid_id)["sync_state"] == "ok"
    assert 11 not in fake.labeled(rules.ok_label(kid_id))


def test_remove_kid(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    tok = csrf(c, f"/kids/{kid_id}")
    c.post(f"/kids/{kid_id}/remove", data={"csrf": tok, "confirm": "wrong"})
    assert app.state.db.kid(kid_id)["status"] == "active"
    r = c.post(f"/kids/{kid_id}/remove", data={"csrf": tok, "confirm": "kid1"})
    assert r.status_code == 303
    assert ("remove_share", 500 + KID.id) in fake.calls
    assert fake.labeled(rules.ok_label(kid_id)) == []
    kid = app.state.db.kid(kid_id)
    assert kid["status"] == "removed" and kid["plex_id"] is None


def test_unaccepted_invite_cannot_be_switched_to_another_account(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    fake.accept_ok = False
    connect(app, fake, c, kid_id)
    kid = app.state.db.kid(kid_id)
    assert kid["status"] == "requested" and kid["plex_accepted"] == 0
    assert "Finish connecting" in c.get(f"/kids/{kid_id}").text
    r, _ = connect(app, fake, c, kid_id, as_token="tok-stranger")
    assert r.status_code == 400 and "already connected" in r.text
    assert [x[1] for x in fake.calls if x[0] == "invite"] == ["kid1"]
    fake.accept_ok = True
    r, _ = connect(app, fake, c, kid_id)
    assert r.status_code == 200 and app.state.db.kid(kid_id)["plex_accepted"] == 1
    assert app.state.db.one("SELECT COUNT(*) n FROM requests WHERE kid_id=?", (kid_id,))["n"] == 1


def test_owner_kid_goes_active_without_a_request_and_shows_owner_name(env):
    app, fake = env
    o, _ = login(app, fake, "owner")
    kid_id = add_kid(app, fake, o, name="Lia")
    r, _ = connect(app, fake, o, kid_id)
    assert r.status_code == 200
    assert app.state.db.kid(kid_id)["status"] == "active"
    assert app.state.db.one("SELECT COUNT(*) n FROM requests")["n"] == 0
    page = o.get(f"/kids/{kid_id}").text
    assert "Parent: Admin (you)" in page and "Parent: 1" not in page
    # a stale request never flips an active child
    app.state.db.x("INSERT INTO requests(parent_id,kid_id,kind,message,created_at) VALUES(1,?,'add_child','x','2026-10-19T12:00:00+00:00')",
                   (kid_id,))
    rid = app.state.db.one("SELECT id FROM requests WHERE kid_id=?", (kid_id,))["id"]
    o.post(f"/admin/requests/{rid}", data={"csrf": csrf(o, "/admin"), "action": "decline"})
    assert app.state.db.kid(kid_id)["status"] == "active"


def test_old_database_is_migrated(tmp_path):
    import sqlite3
    from app.db import DB
    path = tmp_path / "old.db"
    DB(str(path))
    c = sqlite3.connect(path)
    c.execute("INSERT INTO kids(parent_id,name,status,created_at,updated_at) VALUES(1,'a','approved','t','t')")
    c.execute("INSERT INTO kid_titles(kid_id,rating_key,kind,section_key,item_type,title) VALUES(1,5,'hide','1','movie','x')")
    c.commit()
    c.close()
    db = DB(str(path))
    assert db.kid(1)["status"] == "draft"
    assert db.one("SELECT kind FROM kid_titles")["kind"] == "block"


def test_timed_pause_and_resume(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    tok = csrf(c, f"/kids/{kid_id}")
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "pause:30"})
    kid = app.state.db.kid(kid_id)
    assert kid["paused"] == 0 and kid["paused_until"]
    st = c.get(f"/kids/{kid_id}/status.json").json()
    assert st["state"] == "paused" and st["state_text"].startswith("Until ") and st["paused"]
    app.state.db.update_kid(kid_id, weekday_start="00:00", weekday_end="00:00", weekend_start="00:00",
                            weekend_end="00:00")
    fake.live = [Session("s1", KID.id, "movie", "Cloud Garden", "playing", "TV")]
    app.state.enforcer.tick(time.time())
    stop = [x for x in fake.calls if x[0] == "terminate"][-1]
    assert "paused Plex until" in stop[2]
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "resume"})
    assert c.get(f"/kids/{kid_id}/status.json").json()["state"] != "paused"
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "pause:tomorrow"})
    until = app.state.db.kid(kid_id)["paused_until"]
    assert until > app.state.svc.now_local().isoformat()[:10]


def test_pause_everyone(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    from .fakes import ACCOUNTS
    from app.plex import Account
    second = Account(id=202, uuid="u-kid2", username="kid2", title="kid2", email="alex+kid2@example.com", thumb="")
    ACCOUNTS["tok-kid2"] = second
    try:
        kid2 = add_kid(app, fake, c, name="Kid2")
        connect(app, fake, c, kid2, as_token="tok-kid2")
        approve_all(app, fake)
        home = c.get("/home").text
        assert "Pause everyone" in home
        c.post("/kids/pause-all", data={"csrf": csrf(c), "action": "pause"})
        assert app.state.db.kid(kid_id)["paused"] == 1 and app.state.db.kid(kid2)["paused"] == 1
        assert "Resume everyone" in c.get("/home").text
        c.post("/kids/pause-all", data={"csrf": csrf(c), "action": "resume"})
        assert app.state.db.kid(kid_id)["paused"] == 0
    finally:
        ACCOUNTS.pop("tok-kid2", None)


def test_title_details_and_week_chart(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    fake.items[11].summary = "A group plants a rooftop garden."
    d = c.get(f"/kids/{kid_id}/titles/11.json").json()
    assert d["title"] == "Cloud Garden" and d["summary"].startswith("A group") and d["visible"] is True
    assert c.get(f"/kids/{kid_id}/titles/13.json").status_code == 404  # R-rated never listed
    app.state.db.add_usage(kid_id, app.state.svc.today(), 5400)
    page = c.get(f"/kids/{kid_id}").text
    assert '<svg class="chart"' in page and "1h 30m" in page and "Today" in page


def test_tabs_render_for_each_state(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    for path in ("", "/titles", "/schedule", "/settings"):
        r = c.get(f"/kids/{kid_id}{path}")
        assert r.status_code == 200, path
    assert "Connect Kid1" in c.get(f"/kids/{kid_id}").text
    connect(app, fake, c, kid_id)
    assert "Waiting for Admin to approve" in c.get(f"/kids/{kid_id}").text
    approve_all(app, fake)
    for path in ("", "/titles", "/schedule", "/activity", "/settings"):
        assert c.get(f"/kids/{kid_id}{path}").status_code == 200, path
    activity = c.get(f"/kids/{kid_id}/activity").text
    assert "Cloud Garden" in activity and "Living Room TV" in activity


def test_schedule_any_time(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    tok = csrf(c, f"/kids/{kid_id}/schedule")
    c.post(f"/kids/{kid_id}/schedule", data={"csrf": tok, "weekday_minutes": "60", "weekend_minutes": "120",
                                             "weekday_start": "07:00", "weekday_end": "20:00",
                                             "weekend_start": "08:00", "weekend_end": "21:00", "any_time_weekend": "1"})
    kid = app.state.db.kid(kid_id)
    assert kid["weekend_start"] == kid["weekend_end"] and kid["weekday_end"] == "20:00"
    r = c.post(f"/kids/{kid_id}/schedule", data={"csrf": tok, "weekday_minutes": "9999",
                                                 "weekday_start": "07:00", "weekday_end": "20:00",
                                                 "weekend_start": "08:00", "weekend_end": "21:00"})
    assert app.state.db.kid(kid_id)["weekday_minutes"] == 60  # rejected


def test_new_titles_are_counted_until_reviewed(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    app.state.db.update_kid(kid_id, titles_seen_at="2026-01-01T00:00:00+00:00")
    fake.items[14].added_at = int(time.time())
    page = c.get(f"/kids/{kid_id}").text
    assert "1 new title" in page
    titles = c.get(f"/kids/{kid_id}/titles").text
    assert 'data-new="1"' in titles
    assert "new title" not in c.get(f"/kids/{kid_id}").text  # reviewed now


def test_home_shows_new_titles_per_child(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    app.state.db.update_kid(kid_id, titles_seen_at="2026-01-01T00:00:00+00:00")
    fake.items[12].added_at = int(time.time())
    assert '<span class="count">1 new</span>' in c.get("/home").text


def test_review_fixes(env, monkeypatch):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    db = app.state.db
    # title details: Plex error gives a clean 503
    monkeypatch.setattr(fake, "library", lambda key, fresh=False: (_ for _ in ()).throw(PlexError("down")))
    assert c.get(f"/kids/{kid_id}/titles/11.json").status_code == 503
    monkeypatch.undo()
    # chart: a day that ended a few seconds past the limit is not "over"
    db.update_kid(kid_id, weekday_minutes=60, weekend_minutes=60)
    db.add_usage(kid_id, app.state.svc.today(), 3600 + 20)
    assert 'class="bar over' not in c.get(f"/kids/{kid_id}").text
    db.add_usage(kid_id, app.state.svc.today(), 900)
    assert 'class="bar over' in c.get(f"/kids/{kid_id}").text
    # the owner looking at a child's titles doesn't clear the parent's "new" badge
    db.update_kid(kid_id, titles_seen_at="2026-01-01T00:00:00+00:00")
    fake.items[14].added_at = int(time.time())
    o, _ = login(app, fake, "owner")
    o.get(f"/kids/{kid_id}/titles")
    assert db.kid(kid_id)["titles_seen_at"].startswith("2026-01-01")
    # admin switches return to the Parents tab
    r = o.post("/admin/parents/103", data={"csrf": csrf(o, "/admin"), "enabled": "1"})
    assert r.headers["location"] == "/admin?tab=people"


def test_feature_and_access_requests(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    home = c.get("/home").text
    assert "Suggest a feature" in home and "/requests?kind=more_access#new" in home  # footer entry point
    page = c.get("/requests?kind=feature").text
    assert re.search(r'value="feature"[^>]*checked', page) and "Ask for access" in page
    tok = csrf(c, "/requests")
    c.post("/requests", data={"csrf": tok, "kind": "feature", "message": "A bedtime story mode"})
    c.post("/requests", data={"csrf": tok, "kind": "more_access", "message": "Music for Kid1 please"})
    assert c.post("/requests", data={"csrf": tok, "kind": "bogus", "message": "x"}).status_code == 303
    kinds = [r["kind"] for r in app.state.db.q("SELECT kind FROM requests ORDER BY id")]
    assert kinds == ["feature", "more_access"]
    o, _ = login(app, fake, "owner")
    admin = o.get("/admin").text
    assert "Suggest a feature" in admin and "Ask for access" in admin


def test_co_parent_can_manage_the_child(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    a, _ = login(app, fake, "sam")
    assert a.get(f"/kids/{kid_id}").status_code == 404
    o, _ = login(app, fake, "owner")
    otok = csrf(o, "/admin?tab=kids")
    assert "Choose a co-parent" in o.get("/admin?tab=kids").text
    o.post(f"/admin/kids/{kid_id}/co-parents", data={"csrf": otok, "parent": "102"})
    assert a.get(f"/kids/{kid_id}").status_code == 200
    assert "Kid1" in a.get("/home").text
    atok = csrf(a, f"/kids/{kid_id}")
    a.post(f"/kids/{kid_id}/control", data={"csrf": atok, "action": "pause"})
    assert app.state.db.kid(kid_id)["paused"] == 1
    assert "Shared with Sam" in c.get(f"/kids/{kid_id}").text
    # the original parent can't be added twice, and removal revokes access
    o.post(f"/admin/kids/{kid_id}/co-parents", data={"csrf": otok, "parent": "101"})
    assert app.state.db.one("SELECT COUNT(*) n FROM kid_parents")["n"] == 1
    o.post(f"/admin/kids/{kid_id}/co-parents", data={"csrf": otok, "parent": "102", "action": "remove"})
    assert a.get(f"/kids/{kid_id}").status_code == 404


def test_co_parent_cannot_remove_the_child(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    o, _ = login(app, fake, "owner")
    o.post(f"/admin/kids/{kid_id}/co-parents", data={"csrf": csrf(o, "/admin"), "parent": "102"})
    a, _ = login(app, fake, "sam")
    assert 'id="remove"' not in a.get(f"/kids/{kid_id}/settings").text
    r = a.post(f"/kids/{kid_id}/remove", data={"csrf": csrf(a, "/home"), "confirm": "kid1"})
    assert r.status_code == 404 and app.state.db.kid(kid_id)["status"] == "active"


def test_friendly_errors_and_manifest(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    r = c.get("/kids/999")
    assert r.status_code == 404 and "Page not found" in r.text and "<html" in r.text
    assert c.get("/kids/999/status.json").json() == {"error": "Not Found"}
    r = c.post("/kids/new", data={"name": "x"})  # no CSRF token
    assert r.status_code == 400 and "This form expired" in r.text
    m = client(app).get("/manifest.webmanifest")
    assert m.headers["content-type"].startswith("application/manifest+json") and m.json()["short_name"] == "KidsWatch"


def test_bulk_title_changes(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    tok = csrf(c, f"/kids/{kid_id}/titles")
    r = c.post(f"/kids/{kid_id}/titles/batch", data={"csrf": tok, "kind": "block", "keys": "11,12,99"},
               headers={"x-requested-with": "fetch"})
    assert r.json() == {"ok": True, "done": 2, "skipped": 1}
    assert fake.labeled(rules.ok_label(kid_id)) == [14]
    c.post(f"/kids/{kid_id}/titles/batch", data={"csrf": tok, "kind": "", "keys": "11,12"})
    assert fake.labeled(rules.ok_label(kid_id)) == [11, 12, 14]
    r = c.post(f"/kids/{kid_id}/titles/batch", data={"csrf": tok, "kind": "nope", "keys": "11"},
               headers={"x-requested-with": "fetch"})
    assert r.status_code == 400 and "error" in r.json()


def test_activity_most_watched(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    page = c.get(f"/kids/{kid_id}/activity").text
    assert "Most watched" in page and "Watched once" in page


def test_avatar_color_hours_cancel_and_admin_stats(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    tok = csrf(c, f"/kids/{kid_id}/settings")
    c.post(f"/kids/{kid_id}/settings", data={"csrf": tok, "name": "Kid1", "level_choice": "age:kids",
                                             "section_1": "1", "section_2": "1", "color": "4"})
    assert app.state.db.kid(kid_id)["color"] == 4
    assert 'avatar lg c4' in c.get(f"/kids/{kid_id}").text
    page = c.get(f"/kids/{kid_id}").text
    assert " left · " in page
    c.post("/requests", data={"csrf": tok, "kind": "feature", "message": "Dark mode toggle"})
    rid = app.state.db.one("SELECT id FROM requests WHERE kind='feature'")["id"]
    c.post(f"/requests/{rid}/cancel", data={"csrf": tok})
    assert app.state.db.one("SELECT status FROM requests WHERE id=?", (rid,))["status"] == "cancelled"
    a, _ = login(app, fake, "sam")
    assert a.post(f"/requests/{rid}/cancel", data={"csrf": csrf(a)}).status_code == 404  # not hers
    o, _ = login(app, fake, "owner")
    admin = o.get("/admin").text
    assert "Approved kids" in admin and "Watching now" in admin


def test_later_bedtime_and_no_watching_controls(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    tok = csrf(c, f"/kids/{kid_id}")
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "later:30"})
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "later:60"})
    kid = app.state.db.kid(kid_id)
    assert kid["later_minutes"] == 90 and kid["later_day"] == app.state.svc.today()
    st = c.get(f"/kids/{kid_id}/status.json").json()
    assert "later bedtime tonight" in st["hours_text"] and st["later"] == 90
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "reset_later"})
    assert app.state.db.kid(kid_id)["later_minutes"] == 0
    c.post(f"/kids/{kid_id}/schedule", data={"csrf": tok, "weekday_minutes": "0", "weekend_minutes": "0",
                                             "weekday_start": "07:00", "weekday_end": "20:00",
                                             "weekend_start": "07:00", "weekend_end": "21:00"})
    assert app.state.db.kid(kid_id)["weekday_minutes"] == 0
    assert "No watching today" in c.get(f"/kids/{kid_id}").text
    assert 'value="0" selected' in c.get(f"/kids/{kid_id}/schedule").text


def test_unread_reply_badge(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    tok = csrf(c)
    c.post("/requests", data={"csrf": tok, "kind": "title", "message": "Maple Club please"})
    c.get("/requests")  # seen
    time.sleep(1.1)  # times are stored to the second
    o, _ = login(app, fake, "owner")
    rid = app.state.db.one("SELECT id FROM requests")["id"]
    o.post(f"/admin/requests/{rid}", data={"csrf": csrf(o, "/admin"), "action": "done", "note": "Added"})
    assert "new replies" in c.get("/home").text
    time.sleep(1.1)
    c.get("/requests")
    assert "new replies" not in c.get("/home").text


def test_security_review_2_fixes(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    tok = csrf(c, f"/kids/{kid_id}")
    # unicode digits no longer crash anything
    assert c.post(f"/kids/{kid_id}/settings", data={"csrf": tok, "level_choice": "age:kids", "section_1": "1",
                                                    "color": "²"}).status_code == 303
    c.post(f"/kids/{kid_id}/schedule", data={"csrf": tok, "weekday_minutes": "²", "weekday_start": "٠٧:00",
                                             "weekday_end": "20:00", "weekend_start": "07:00", "weekend_end": "21:00"})
    assert app.state.db.kid(kid_id)["weekday_start"] == "07:00"
    r = c.post(f"/kids/{kid_id}/titles/batch", data={"csrf": tok, "kind": "block", "keys": "²"},
               headers={"x-requested-with": "fetch"})
    assert r.status_code == 400
    assert c.post("/logout", data={"csrf": "été"}).status_code == 400
    # batch: one audit row, not one per title
    before = app.state.db.one("SELECT COUNT(*) n FROM audit")["n"]
    c.post(f"/kids/{kid_id}/titles/batch", data={"csrf": tok, "kind": "block", "keys": "11,12,14"})
    assert app.state.db.one("SELECT COUNT(*) n FROM audit")["n"] - before <= 2  # batch row + sync row
    # create/cancel loops can't flood the owner's phone or dodge the cap
    sent = []
    app.state.notifier.send = lambda title, msg, path="/admin": sent.append(title)
    for _ in range(20):
        c.post("/requests", data={"csrf": tok, "kind": "feature", "message": "x"})
        rid = app.state.db.one("SELECT id FROM requests ORDER BY id DESC LIMIT 1")["id"]
        c.post(f"/requests/{rid}/cancel", data={"csrf": tok})
    assert len(sent) <= 3
    assert app.state.db.one("SELECT COUNT(*) n FROM requests WHERE kind='feature'")["n"] <= 15
    # posters only for titles a child could ever see
    assert c.get("/poster/13").status_code == 404   # R-rated
    assert c.get("/poster/17").status_code == 404   # unrated
    assert c.get("/poster/11").status_code == 200


def test_malformed_links_get_a_friendly_page(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    r = c.get("/kids/abc")
    assert r.status_code == 404 and "Page not found" in r.text
    assert c.get("/kids/abc/status.json").json() == {"error": "Not Found"}


def test_connect_qr_and_setup_stepper(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    page = c.get(f"/kids/{kid_id}").text
    assert 'class="stepper"' in page and 'aria-current="step"' in page
    r = c.get(f"/kids/{kid_id}/connect-qr.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml") and b"<svg" in r.content
    a, _ = login(app, fake, "sam")
    assert a.get(f"/kids/{kid_id}/connect-qr.svg").status_code == 404   # not her child
    connect(app, fake, c, kid_id)
    assert c.get(f"/kids/{kid_id}/connect-qr.svg").status_code == 404   # connected: no link to show


def test_special_days_form(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    tok = csrf(c, f"/kids/{kid_id}/schedule")
    base = {"csrf": tok, "weekday_minutes": "60", "weekend_minutes": "120", "weekday_start": "07:00",
            "weekday_end": "20:00", "weekend_start": "08:00", "weekend_end": "21:00"}
    c.post(f"/kids/{kid_id}/schedule", data={**base, "friday_weekend": "1", "break_until": "2099-08-31"})
    kid = app.state.db.kid(kid_id)
    assert kid["friday_weekend"] == 1 and kid["sunday_school"] == 0 and kid["break_until"] == "2099-08-31"
    assert "School break on" in c.get(f"/kids/{kid_id}/schedule").text
    c.post(f"/kids/{kid_id}/schedule", data={**base, "break_until": "not a date"})
    assert app.state.db.kid(kid_id)["break_until"] == "2099-08-31"  # rejected
    c.post(f"/kids/{kid_id}/schedule", data={**base})
    assert app.state.db.kid(kid_id)["break_until"] is None and app.state.db.kid(kid_id)["friday_weekend"] == 0


def test_week_trend_and_poster_cache(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    from datetime import date as _d, timedelta as _td
    today = _d.fromisoformat(app.state.svc.today())
    for back in range(7, 14):
        app.state.db.add_usage(kid_id, (today - _td(days=back)).isoformat(), 3600)
    app.state.db.add_usage(kid_id, today.isoformat(), 3600 * 3)
    assert "less than the week before" in c.get(f"/kids/{kid_id}/activity").text


def test_keeper_alerts_owner_once_and_on_recovery(env):
    app, fake = env
    enf = app.state.enforcer
    sent = []
    enf.on_alert = lambda title, body: sent.append(title)
    enf.last_error = "Read what is playing: could not reach the server"
    for _ in range(40):  # 40 x 20 s = more than 10 minutes of failures
        enf._failed()
    assert sent == ["KidsWatch for Plex: screen time is not being enforced"]
    enf._healthy_again()
    assert sent[-1] == "KidsWatch for Plex: screen time is back"
    enf._healthy_again()
    assert len(sent) == 2


def test_ends_text_and_prefilled_request(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    app.state.db.update_kid(kid_id, weekday_start="00:00", weekday_end="23:59", weekend_start="00:00", weekend_end="23:59")
    st = c.get(f"/kids/{kid_id}/status.json").json()
    assert st["ends_text"] == "Ends 11:59 PM" and st["summary_line"].endswith("· Ends 11:59 PM")
    app.state.db.update_kid(kid_id, weekday_start="00:00", weekday_end="00:00", weekend_start="00:00", weekend_end="00:00")
    st = c.get(f"/kids/{kid_id}/status.json").json()
    assert st["ends_text"] == "" and st["summary_line"].endswith("Any time today")   # any time: no end
    page = c.get('/requests?kind=title&kid=1&message=Could+you+add+%22Maple Club%22%3F').text
    assert "Could you add &#34;Maple Club&#34;?</textarea>" in page or 'Could you add "Maple Club"?</textarea>' in page
    assert 'data-ask-link' in c.get(f"/kids/{kid_id}/titles").text


def test_live_database_from_00bca9d_upgrades_cleanly(tmp_path):
    """The live site runs 00bca9d. Build its database, add data, then open it with today's code."""
    import sqlite3
    from pathlib import Path
    from app.db import DB
    path = tmp_path / "live.db"
    c = sqlite3.connect(path)
    c.executescript(Path(__file__).with_name("data").joinpath("schema_00bca9d.sql").read_text())
    c.execute("INSERT INTO parents(plex_id,username,title,enabled,created_at) VALUES(101,'alex','Alex',1,'2026-10-19T00:00:00+00:00')")
    c.execute("INSERT INTO kids(id,parent_id,name,status,plex_id,plex_username,plex_accepted,titles_count,created_at,updated_at) "
              "VALUES(1,1,'testkid','active',201,'kid1',1,232,'2026-10-19T20:00:00+00:00','2026-10-19T20:00:00+00:00')")
    c.execute("INSERT INTO kid_titles(kid_id,rating_key,kind,section_key,item_type,title) VALUES(1,5,'block','1','movie','x')")
    c.commit()
    c.close()
    db = DB(str(path))
    kid = db.kid(1)
    assert kid["status"] == "active" and kid["plex_username"] == "kid1" and kid["titles_count"] == 232
    for col in ("paused_until", "titles_seen_at", "color", "later_day", "later_minutes", "friday_weekend",
                "sunday_school", "break_until"):
        assert col in kid.keys()
    assert db.one("SELECT COUNT(*) n FROM kid_parents")["n"] == 0
    assert "requests_seen_at" in db.one("SELECT * FROM parents").keys()
    assert db.one("SELECT kind FROM kid_titles")["kind"] == "block"
    DB(str(path))  # opening twice is safe


def test_new_child_can_copy_a_siblings_rules(env):
    app, fake = env
    db = app.state.db
    c, _ = login(app, fake, "alex")
    assert "Start with the same rules as" not in c.get("/kids/new").text  # no siblings yet
    kid1 = add_kid(app, fake, c)
    db.update_kid(kid1, weekday_minutes=45, weekend_start="09:00", friday_weekend=1)
    db.x("INSERT INTO kid_titles(kid_id,rating_key,kind,section_key,item_type,title) VALUES(?,5,'block','1','movie','x')",
         (kid1,))
    page = c.get("/kids/new").text
    assert "Start with the same rules as" in page and f'<option value="{kid1}">Kid1</option>' in page
    r = c.post("/kids/new", data={"csrf": csrf(c), "name": "Kid2", "age_level": "little", "copy_from": str(kid1)})
    assert r.status_code == 303
    kid2_id = int(r.headers["location"].rsplit("/", 1)[1])
    k = db.kid(kid2_id)
    assert (k["weekday_minutes"], k["weekend_start"], k["friday_weekend"], k["age_level"]) == (45, "09:00", 1, "little")
    assert db.one("SELECT kind FROM kid_titles WHERE kid_id=? AND rating_key=5", (kid2_id,))["kind"] == "block"
    assert "Kid1&#39;s schedule and picks" in c.get(f"/kids/{kid2_id}").text

    # Another parent can't copy from a child that isn't theirs: the choice is ignored.
    a, _ = login(app, fake, "sam")
    r = a.post("/kids/new", data={"csrf": csrf(a), "name": "Kid3", "age_level": "kids", "copy_from": str(kid1)})
    kid3 = db.kid(int(r.headers["location"].rsplit("/", 1)[1]))
    assert kid3["weekday_minutes"] != 45
    assert not db.q("SELECT 1 FROM kid_titles WHERE kid_id=?", (kid3["id"],))


def test_titles_search_covers_genres(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid = add_kid(app, fake, c)
    page = c.get(f"/kids/{kid}/titles").text
    assert 'placeholder="Title, genre or rating"' in page and 'data-rating="pg"' in page
    assert re.search(r'data-title="cloud garden"[^>]*data-genres="animation family"', page)


def test_activity_lists_recent_changes_in_plain_words(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    tok = csrf(c, f"/kids/{kid_id}")
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "bonus:15"})
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "later:30"})
    c.post(f"/kids/{kid_id}/titles", data={"csrf": tok, "key": "11", "kind": "block"})
    page = c.get(f"/kids/{kid_id}/activity").text
    assert "Recent changes" in page
    for words in ("<strong>You</strong> added 15 minutes for the day", "<strong>You</strong> made bedtime 30 minutes later",
                  "<strong>You</strong> blocked Cloud Garden", "approved the Plex account", "The Plex account was connected",
                  "<strong>You</strong> added Kid1"):
        assert words in page, words
    assert page.index("blocked Cloud Garden") < page.index("added Kid1")  # newest first
    o, _ = login(app, fake, "owner")  # the owner sees the parent's name instead of "You"
    assert "<strong>You</strong> blocked" not in o.get(f"/kids/{kid_id}/activity").text
    assert re.search(r"<strong>[^<]+</strong> blocked Cloud Garden", o.get(f"/kids/{kid_id}/activity").text)


def test_settings_shows_who_manages_and_how_to_ask_for_a_helper(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    page = c.get(f"/kids/{kid_id}/settings").text
    assert "Who manages Kid1" in page and 'class="title">You</div>' in page
    link = re.search(r'href="(/requests\?kind=more_access[^"]+)"', page).group(1).replace("&amp;", "&")
    form = c.get(link).text
    assert "Please let ___ help manage Kid1." in form


def test_admin_log_reads_in_plain_words(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    c.post(f"/kids/{kid_id}/titles", data={"csrf": csrf(c, f"/kids/{kid_id}"), "key": "11", "kind": "block"})
    o, _ = login(app, fake, "owner")
    page = o.get("/admin?tab=activity").text
    assert "blocked Cloud Garden" in page and "Kid1 · " in page
    assert "approved a child" in page and "<strong>You</strong>" in page and "<strong>KidsWatch for Plex</strong>" in page
    assert 'class="log-detail"' in page
    assert "titles synced" not in page  # raw action codes are translated


def test_schedule_spells_out_the_next_seven_days(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    app.state.db.update_kid(kid_id, weekday_minutes=60, weekend_minutes=0, friday_weekend=1,
                            weekday_start="15:00", weekday_end="20:00")
    page = c.get(f"/kids/{kid_id}/schedule").text
    start = page.index('class="list days')
    days = page[start:page.index("</ul>", start)]
    assert days.count("<li") == 7 and "Today" in days and "Tomorrow" in days and 'class="muted day"' in days
    assert days.count('data-dow="') == 7  # the page script recomputes these rows as the form changes
    assert "Weekend rules" in days  # every 7-day span has a Friday
    assert "1 hr" in days and "3:00 PM to 8:00 PM" in days and "No watching" in days


def test_block_straight_from_the_watch_history(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    page = c.get(f"/kids/{kid_id}").text
    assert 'data-quick-block="11"' in page and 'aria-label="Block Cloud Garden for Kid1"' in page
    assert 'data-quick-block="11"' in c.get(f"/kids/{kid_id}/activity").text
    tok = csrf(c, f"/kids/{kid_id}")
    assert c.post(f"/kids/{kid_id}/titles", data={"csrf": tok, "key": "11", "kind": "block"},
                  headers={"x-requested-with": "fetch"}).json() == {"ok": True}
    page = c.get(f"/kids/{kid_id}").text
    assert 'data-quick-block="11"' not in page and ">Blocked</span>" in page


def test_weekend_rules_today_for_a_sick_day(env):
    app, fake = env
    db = app.state.db
    c, kid_id = active_kid(app, fake)
    svc = app.state.svc
    weekday = svc.now_local().date().weekday() < 5
    db.update_kid(kid_id, weekday_minutes=60, weekend_minutes=180, friday_weekend=0, sunday_school=0)
    page = c.get(f"/kids/{kid_id}").text
    assert ('value="dayoff"' in page) == weekday
    if not weekday:
        return  # on a real weekend the option is hidden; nothing more to check today
    tok = csrf(c, f"/kids/{kid_id}")
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "dayoff"})
    assert db.kid(kid_id)["break_until"] == svc.today()
    page = c.get(f"/kids/{kid_id}").text
    assert 'value="reset_dayoff"' in page and "of 3 hr" in page
    assert "gave Kid1 weekend rules today" in c.get(f"/kids/{kid_id}/activity").text
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "reset_dayoff"})
    assert db.kid(kid_id)["break_until"] is None


def test_new_parent_sees_a_three_step_welcome(env):
    app, fake = env
    c, _ = login(app, fake, "sam")
    page = c.get("/home").text
    assert "Set up your first child" in page and page.count('class="step-title"') == 3
    assert "Add your first child to choose what they can watch." in page
    add_kid(app, fake, c, "Kid3")
    assert "Set up your first child" not in c.get("/home").text


def test_copy_rules_to_another_child(env):
    app, fake = env
    db = app.state.db
    c, kid1 = active_kid(app, fake)
    kid2_id = add_kid(app, fake, c, "Kid2", "little")
    db.update_kid(kid1, weekday_minutes=45, weekday_end="19:30", sunday_school=1)
    db.x("INSERT INTO kid_titles(kid_id,rating_key,kind,section_key,item_type,title) VALUES(?,11,'block','1','movie','Cloud Garden')", (kid1,))
    db.x("INSERT INTO kid_titles(kid_id,rating_key,kind,section_key,item_type,title) VALUES(?,14,'allow','1','movie','Sunny Wheels')", (kid2_id,))
    page = c.get(f"/kids/{kid1}/settings").text
    assert "Use these rules for another child" in page and f'<option value="{kid2_id}">Kid2</option>' in page
    tok = csrf(c, f"/kids/{kid1}/settings")
    r = c.post(f"/kids/{kid1}/copy-to", data={"csrf": tok, "target": str(kid2_id)})
    assert r.status_code == 303 and r.headers["location"] == f"/kids/{kid2_id}"
    k = db.kid(kid2_id)
    assert (k["weekday_minutes"], k["weekday_end"], k["sunday_school"], k["age_level"]) == (45, "19:30", 1, "little")
    assert [(t["rating_key"], t["kind"]) for t in db.q("SELECT * FROM kid_titles WHERE kid_id=?", (kid2_id,))] == [(11, "block")]
    assert "copied Kid1&#39;s rules to Kid2" in c.get(f"/kids/{kid2_id}/activity").text
    assert c.post(f"/kids/{kid1}/copy-to", data={"csrf": tok, "target": str(kid1)}).status_code == 400
    a, _ = login(app, fake, "sam")
    kid3 = add_kid(app, fake, a, "Kid3")
    atok = csrf(a, f"/kids/{kid3}/settings")
    assert a.post(f"/kids/{kid3}/copy-to", data={"csrf": atok, "target": str(kid1)}).status_code == 404
    assert db.kid(kid1)["weekday_minutes"] == 45


def test_out_of_time_offers_fifteen_more_minutes(env):
    app, fake = env
    db, svc = app.state.db, app.state.svc
    c, kid_id = active_kid(app, fake)
    db.update_kid(kid_id, weekday_minutes=30, weekend_minutes=30, weekday_start="00:00", weekday_end="00:00",
                  weekend_start="00:00", weekend_end="00:00")
    assert re.search(r'data-limit-add\s+hidden', c.get(f"/kids/{kid_id}").text)  # time left: no button
    db.add_usage(kid_id, svc.today(), 31 * 60)
    page = c.get(f"/kids/{kid_id}").text
    assert re.search(r'data-limit-add\s*>', page) and 'value="bonus:15">' in page


def test_admin_quick_replies(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    c.post("/requests", data={"csrf": csrf(c, "/requests"), "kind": "title", "message": "Maple Club season 3 please"})
    o, _ = login(app, fake, "owner")
    page = o.get("/admin?tab=requests").text
    assert 'data-fill="Added to Plex."' in page and 'aria-label="Quick replies"' in page


def test_past_bedtime_offers_thirty_more_minutes(env):
    app, fake = env
    db, svc = app.state.db, app.state.svc
    c, kid_id = active_kid(app, fake)
    now = svc.now_local()
    if now.hour < 2:
        return  # the window below (00:15 to an hour ago) needs a couple of hours of today behind it
    end = f"{now.hour - 1:02d}:00"
    db.update_kid(kid_id, weekday_start="00:15", weekday_end=end, weekend_start="00:15", weekend_end=end)
    page = c.get(f"/kids/{kid_id}").text
    assert re.search(r'data-later-add\s*>', page) and 'value="later:30">' in page
    assert c.get(f"/kids/{kid_id}/status.json").json()["evening"] is True


def test_copying_rules_never_widens_or_unblocks(env):
    """Copying keeps the target's own mode and free-movies block (security review 2026-10-19)."""
    app, fake = env
    db = app.state.db
    c, kid1 = active_kid(app, fake)
    kid2_id = add_kid(app, fake, c, "Kid2", "teens")
    db.update_kid(kid1, mode="picked")
    db.update_kid(kid2_id, block_online=0)
    c.post(f"/kids/{kid2_id}/copy-to", data={"csrf": csrf(c, f"/kids/{kid2_id}/settings"), "target": str(kid1)})
    assert db.kid(kid1)["mode"] == "picked" and db.kid(kid1)["block_online"] == 1
    r = c.post("/kids/new", data={"csrf": csrf(c), "name": "Kid3", "age_level": "kids", "copy_from": str(kid2_id)})
    ana = db.kid(int(r.headers["location"].rsplit("/", 1)[1]))
    assert ana["mode"] == "age" and ana["block_online"] == 1


def test_day_off_buttons_log_only_real_changes(env):
    app, fake = env
    db, svc = app.state.db, app.state.svc
    c, kid_id = active_kid(app, fake)
    from datetime import date, timedelta
    later = (date.fromisoformat(svc.today()) + timedelta(days=5)).isoformat()
    db.update_kid(kid_id, break_until=later)  # a real school break is on
    tok = csrf(c, f"/kids/{kid_id}")
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "dayoff"})
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "reset_dayoff"})
    assert db.kid(kid_id)["break_until"] == later  # the break is untouched
    assert not db.q("SELECT 1 FROM audit WHERE action IN ('control_dayoff','control_reset_dayoff')")


def test_overview_links_to_what_the_child_sees(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    assert f'href="/kids/{kid_id}/titles?show=visible"' in c.get(f"/kids/{kid_id}").text
    assert '<option value="visible" selected>' in c.get(f"/kids/{kid_id}/titles?show=visible").text
    assert "Weekends only" in c.get(f"/kids/{kid_id}/schedule").text


def test_controls_offer_an_exact_undo(env):
    app, fake = env
    db = app.state.db
    c, kid_id = active_kid(app, fake)
    tok = csrf(c, f"/kids/{kid_id}")
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "bonus:15", "back": f"/kids/{kid_id}"})
    page = c.get(f"/kids/{kid_id}").text
    assert 'class="toast-undo"' in page and 'value="reset_bonus">Undo</button>' in page
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "reset_bonus"})
    assert db.kid(kid_id)["bonus_minutes"] == 0
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "bonus:15"})
    c.get(f"/kids/{kid_id}")  # read the first toast
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "bonus:15"})  # second helping: no exact undo
    assert 'class="toast-undo"' not in c.get(f"/kids/{kid_id}").text
    c.post(f"/kids/{kid_id}/control", data={"csrf": tok, "action": "pause:30"})
    assert 'value="resume">Undo</button>' in c.get(f"/kids/{kid_id}").text


def test_title_details_have_a_genre_chip_box(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    assert 'data-sheet-genres role="group" aria-label="Show more in this genre" hidden' in c.get(f"/kids/{kid_id}/titles").text
    assert c.get(f"/kids/{kid_id}/titles/11.json").json()["genres"] == "Animation, Family"


def test_chunked_big_posts_are_refused(env):
    """A chunked body has no Content-Length, so the size has to be counted as it arrives."""
    app, fake = env
    c, _ = login(app, fake, "alex")

    def chunks():
        yield b"a="
        for _ in range(10):
            yield b"x" * 10000

    r = c.post("/requests", content=chunks(), headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413
    tok = csrf(c, "/requests")
    small = c.post("/requests", content=iter([f"csrf={tok}&kind=feature&message=Dark+mode".encode()]),
                   headers={"content-type": "application/x-www-form-urlencoded"})
    assert small.status_code == 303  # small chunked forms still work


def test_one_failed_stop_does_not_spare_other_kids(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    db = app.state.db
    db.x("INSERT INTO kids(parent_id,name,status,plex_id,plex_accepted,created_at,updated_at) "
         "VALUES(101,'Sam','active',202,1,'2026-10-20T00:00:00+00:00','2026-10-20T00:00:00+00:00')")
    db.x("UPDATE kids SET paused=1")
    fake.live = [Session("s1", KID.id, "movie", "Cloud Garden", "playing", "TV"),
                 Session("s2", 202, "movie", "Paper Harbor", "playing", "iPad")]
    fake.fail_terminate = {"s1"}
    app.state.enforcer.tick(time.time())
    assert [x[1] for x in fake.calls if x[0] == "terminate"] == ["s2"]


def test_session_without_id_alerts_owner_once(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    sent = []
    app.state.enforcer.on_alert = lambda title, body: sent.append(body)
    app.state.db.update_kid(kid_id, paused=1)
    fake.live = [Session(None, KID.id, "movie", "Cloud Garden", "playing", "Old TV")]
    app.state.enforcer.tick(time.time())
    app.state.enforcer.tick(time.time() + 20)
    assert len(sent) == 1 and "Cloud Garden" in sent[0] and "Old TV" in sent[0]


def test_new_share_without_filters_is_fixed(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    fake.drop_filters = True
    r, _ = connect(app, fake, c, kid_id)
    assert r.status_code == 200
    share = fake.share_map[KID.id]
    assert share.filter_movies == f"label=pk-ok-{kid_id}" and share.filter_tv == f"label=pk-ok-{kid_id}"


def test_new_share_that_cannot_be_fixed_is_taken_back(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    fake.drop_filters, fake.fail_filters = True, True
    r, _ = connect(app, fake, c, kid_id)
    assert r.status_code == 400 and "taken back" in r.text
    assert KID.id not in fake.share_map
    kid = app.state.db.kid(kid_id)
    assert kid["status"] == "draft" and kid["plex_id"] is None
    assert not app.state.db.q("SELECT 1 FROM requests WHERE kind='add_child'")
    fake.drop_filters, fake.fail_filters = False, False  # Plex behaves again: connecting works
    r, _ = connect(app, fake, c, kid_id)
    assert r.status_code == 200 and app.state.db.kid(kid_id)["status"] == "requested"


@pytest.mark.parametrize("restart", [False, True])
def test_failed_unsafe_share_rollback_remains_tracked_until_retracted(env, restart):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    fake.drop_filters = fake.fail_filters = fake.fail_remove_share = True
    r, _ = connect(app, fake, c, kid_id)
    assert r.status_code == 400 and "Access may be unrestricted" in r.text
    assert "Automatic removal will keep retrying" in r.text
    assert "Nothing was shared" not in r.text and "taken back" not in r.text
    assert KID.id in fake.share_map and fake.share_map[KID.id].filter_movies == ""

    db, svc = app.state.db, app.state.svc
    kid = db.kid(kid_id)
    assert kid["status"] == "draft" and kid["plex_id"] == KID.id
    assert kid["plex_username"] == KID.username and kid["sync_state"] == "error"
    assert kid["sync_error"].startswith("Unsafe share:")
    if restart:
        from app.db import DB
        from app.service import Service
        svc = Service(DB(db.path), fake, svc.cfg)
        svc.sync_inline = True
    # Neither reconnecting nor label synchronization can hide the failed rollback.
    ok, text, status = svc.connect(kid_id, "tok-kid1")
    assert not ok and status is None and "Access may be unrestricted" in text
    assert svc.sync_titles(kid_id) is False
    svc.periodic()  # removal still fails, so the account reference must survive
    assert db.kid(kid_id)["plex_id"] == KID.id and db.kid(kid_id)["sync_state"] == "error"
    assert not fake.labeled(rules.ok_label(kid_id))
    assert "accept" not in fake.names() and "add_label_bulk" not in fake.names()
    assert not db.q("SELECT 1 FROM requests WHERE kind='add_child'")

    fake.fail_remove_share = False  # filter repair still fails; periodic must retract access
    svc.periodic()
    assert KID.id not in fake.share_map
    kid = db.kid(kid_id)
    assert kid["status"] == "draft" and kid["plex_id"] is None and kid["plex_username"] is None
    assert kid["sync_state"] == "ok" and kid["sync_error"] is None
    assert not fake.labeled(rules.ok_label(kid_id))
    assert not db.q("SELECT 1 FROM audit WHERE action='approved'")
    fake.drop_filters = fake.fail_filters = False
    ok, _, status = svc.connect(kid_id, "tok-kid1")
    assert ok and status == "requested"
    assert not fake.labeled(rules.ok_label(kid_id))  # still awaiting admin approval


def test_changed_share_gets_its_limits_back(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    app.state.svc.periodic()
    before = len([x for x in fake.calls if x[0] == "update_share"])
    app.state.svc.periodic()  # nothing changed: no update
    assert len([x for x in fake.calls if x[0] == "update_share"]) == before
    fake.share_map[KID.id].filter_movies = ""  # someone edited the share in Plex
    app.state.svc.periodic()
    assert fake.share_map[KID.id].filter_movies == f"label=pk-ok-{kid_id}"
    assert app.state.db.q("SELECT 1 FROM audit WHERE action='share_drift'")
    assert "put the limits back" in approve_all(app, fake).get("/admin?tab=activity").text


def test_switched_off_parent_link_stops_working(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    tok = csrf(c, f"/kids/{kid_id}")
    c.post(f"/kids/{kid_id}/connect", data={"csrf": tok})
    link = re.search(r'value="http://testserver(/link/[^"]+)"', c.get(f"/kids/{kid_id}").text).group(1)
    assert client(app).get(link).status_code == 200
    app.state.db.x("UPDATE parents SET enabled=0 WHERE plex_id=?", (101,))
    assert client(app).get(link).status_code == 410


def test_help_warns_about_sign_in_links(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    html = c.get("/help").text
    assert "never sends you a sign-in link" in html and "<strong>testserver</strong>" in html


def test_landing_page_tours_the_features(env):
    app, fake = env
    html = client(app).get("/").text
    for text in ("with you in charge", "Set it up once, then adjust from your phone", "Made for real family life",
                 "Ready in three steps", "Admin approves", "Only families that Admin shares this Plex server with",
                 "Playback Error", "\"alex\" asked to stop your playback from this server: That's all the screen time for today. See you tomorrow.",
                 "Example of the message Plex shows when time runs out. Plex adds the server owner's name."):
        assert text in html
    assert 'class="btn btn-primary btn-sm" href="/login">Sign in</a>' in html  # header sign-in on the landing page
    assert html.count('aria-hidden="true"') >= 5  # the app previews are decorative
    assert ' style="' not in html  # the CSP forbids inline styles
    assert 'href="#i-play"' not in html  # no play symbol next to the Plex name
    posters = set(re.findall(r'src="(/static/lp/[^"?]+)\?v=\d+"', html))
    assert len(posters) == 6  # original colored title cards served by the site
    for src in posters:
        assert src.endswith(".svg")
        r = client(app).get(src)
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml") and len(r.content) < 2_000


def test_webp_is_served_without_system_mime_types(env, tmp_path, monkeypatch):
    import mimetypes
    from app import main

    static = tmp_path / "static"
    static.mkdir()
    (static / "placeholder.webp").write_bytes(b"RIFF\x04\x00\x00\x00WEBP")
    monkeypatch.setattr(main, "HERE", tmp_path)
    mimetypes.init()
    mimetypes._db.types_map[True].pop(".webp", None)  # like the slim live image, which doesn't know .webp
    assert mimetypes.guess_type("a.webp")[0] is None
    try:
        app = create_app(config.load(), plex=FakePlex())
        r = client(app).get("/static/placeholder.webp")
        assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    finally:
        mimetypes.init()


def test_connect_page_has_no_header_sign_in(env):
    """A parent opens the connect link in a private window; a header 'Sign in' there would invite the wrong account."""
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    c.post(f"/kids/{kid_id}/connect", data={"csrf": csrf(c, f"/kids/{kid_id}")})
    link = re.search(r'value="http://testserver(/link/[^"]+)"', c.get(f"/kids/{kid_id}").text).group(1)
    assert 'href="/login">Sign in</a>' not in client(app).get(link).text


def test_manifest_lists_a_maskable_icon(env):
    app, fake = env
    icons = client(app).get("/manifest.webmanifest").json()["icons"]
    assert any(i.get("purpose") == "maskable" for i in icons)
    for i in icons:
        assert client(app).get(i["src"]).status_code == 200


def test_movie_requests_go_to_the_request_site(env, monkeypatch):
    app, fake = env
    c, _ = login(app, fake, "alex")
    page = c.get("/requests").text   # no request site set: movies are asked for in the form, as before
    assert 'value="title"' in page and "media-ask" not in page

    monkeypatch.setenv("PK_MEDIA_REQUEST_URL", "https://requests.example.com/")
    fake = FakePlex()
    app = create_app(config.load(), plex=fake)
    app.state.svc.sync_inline = True
    c, kid_id = active_kid(app, fake)
    page = c.get("/requests").text
    assert '<a class="btn btn-primary" href="https://requests.example.com" target="_blank" rel="noopener">' in page
    assert 'value="title"' not in page
    assert re.search(r'value="feature" data-example="[^"]*" checked', page)
    page = c.get("/requests?kind=title&message=Could+you+add+Maple Club%3F").text   # an old bookmark
    assert "Could you add Maple Club?</textarea>" not in page and re.search(r'value="feature" data-example="[^"]*" checked', page)
    tok = csrf(c, "/requests")   # a form opened before the change still sends
    assert c.post("/requests", data={"csrf": tok, "kind": "title", "message": "Maple Club please"}).status_code == 303
    assert "Add a movie or show" in c.get("/requests").text
    titles = c.get(f"/kids/{kid_id}/titles").text
    assert 'href="https://requests.example.com/search" target="_blank" rel="noopener" data-ask-link data-ask-external' in titles
    assert "/requests?kind=title" not in titles
    assert '<a href="https://requests.example.com" target="_blank" rel="noopener">requests.example.com</a>' in c.get("/help").text
    assert config._web_url("javascript:alert(1)") == "" and config._web_url(" https://x.test/ ") == "https://x.test"
