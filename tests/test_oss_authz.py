"""One parent can never see or change another parent's children, and only the owner reaches admin."""
from app.plex import Session

from .fakes import KID
from .test_app import active_kid, add_kid, client, csrf, env, login  # noqa: F401  (env is a fixture)

KID_PAGES = ("", "/schedule", "/settings", "/activity", "/titles", "/status.json", "/titles/11.json",
             "/connect-qr.svg")


def test_other_parent_gets_not_found_on_every_child_page(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    s, _ = login(app, fake, "sam")
    for page in KID_PAGES:
        assert s.get(f"/kids/{kid_id}{page}").status_code == 404, page
    assert c.get(f"/kids/{kid_id}/schedule").status_code == 200  # the real parent still can
    assert "Kid1" not in s.get("/home").text


def test_other_parent_cannot_change_a_child(env):
    app, fake = env
    db = app.state.db
    c, kid_id = active_kid(app, fake)
    before = dict(db.kid(kid_id))
    s, _ = login(app, fake, "sam")
    tok = csrf(s)
    posts = {
        "/schedule": {"weekday_minutes": "1440", "weekday_start": "00:00", "weekday_end": "00:00",
                      "weekend_start": "00:00", "weekend_end": "00:00"},
        "/settings": {"level_choice": "age:teens", "section_1": "1", "name": "Hacked"},
        "/control": {"action": "bonus:60"},
        "/titles": {"key": "16", "kind": "allow"},
        "/titles/batch": {"keys": "11,14,16", "kind": "allow"},
        "/connect": {},
        "/remove": {"confirm": "Kid1"},
        "/copy-to": {"target": str(kid_id)},
    }
    for path, data in posts.items():
        assert s.post(f"/kids/{kid_id}{path}", data={"csrf": tok, **data}).status_code == 404, path
    after = dict(db.kid(kid_id))
    assert {k: v for k, v in after.items() if k != "titles_synced_at"} == \
        {k: v for k, v in before.items() if k != "titles_synced_at"}
    assert db.q("SELECT * FROM kid_titles WHERE kid_id=?", (kid_id,)) == []


def test_rules_cannot_be_copied_across_families(env):
    app, fake = env
    db = app.state.db
    c, alex_kid = active_kid(app, fake)
    c.post(f"/kids/{alex_kid}/titles", data={"csrf": csrf(c), "key": "16", "kind": "allow"})
    s, _ = login(app, fake, "sam")
    sam_kid = add_kid(app, fake, s, name="Robin")
    # Alex pushing rules onto Sam's child, and Sam pulling Alex's rules, both fail
    assert c.post(f"/kids/{alex_kid}/copy-to", data={"csrf": csrf(c), "target": str(sam_kid)}).status_code == 404
    assert s.post(f"/kids/{sam_kid}/copy-to", data={"csrf": csrf(s), "target": str(alex_kid)}).status_code == 404
    # a new child can't copy picks from someone else's child either
    r = s.post("/kids/new", data={"csrf": csrf(s), "name": "Jo", "age_level": "kids", "copy_from": str(alex_kid)})
    jo = int(r.headers["location"].rsplit("/", 1)[1])
    assert db.q("SELECT * FROM kid_titles WHERE kid_id IN (?,?)", (sam_kid, jo)) == []
    assert db.one("SELECT detail FROM audit WHERE action='kid_added' AND json_extract(detail,'$.kid')=?",
                  (jo,))["detail"].find("copied_from") == -1


def test_pause_everyone_only_touches_your_own_children(env):
    app, fake = env
    db = app.state.db
    c, alex_kid = active_kid(app, fake)
    s, _ = login(app, fake, "sam")
    sam_kid = add_kid(app, fake, s, name="Robin")
    db.update_kid(sam_kid, status="active")  # pretend it was connected and approved
    s.post("/kids/pause-all", data={"csrf": csrf(s), "action": "pause"})
    assert db.kid(sam_kid)["paused"] == 1
    assert db.kid(alex_kid)["paused"] == 0


def test_pause_everyone_skips_children_not_yet_active(env):
    app, fake = env
    db = app.state.db
    c, active = active_kid(app, fake)
    draft = add_kid(app, fake, c, name="Kid2")
    c.post("/kids/pause-all", data={"csrf": csrf(c), "action": "pause"})
    assert db.kid(active)["paused"] == 1 and db.kid(draft)["paused"] == 0


def test_parents_never_reach_admin_actions(env):
    app, fake = env
    db = app.state.db
    c, kid_id = active_kid(app, fake)
    rid = db.x("INSERT INTO requests(parent_id,kind,message,created_at) VALUES(101,'feature','x','2026-01-01')")
    tok = csrf(c)
    for path, data in ((f"/admin/requests/{rid}", {"action": "approve"}),
                       (f"/admin/kids/{kid_id}/co-parents", {"parent": "102"}),
                       ("/admin/parents/103", {"enabled": "1"}),
                       ("/admin/sign-out-all", {})):
        assert c.post(path, data={"csrf": tok, **data}).status_code == 404, path
    assert db.one("SELECT status FROM requests WHERE id=?", (rid,))["status"] == "open"
    assert db.q("SELECT * FROM kid_parents") == []
    assert db.one("SELECT enabled FROM parents WHERE plex_id=103") is None
    assert db.setting("session_epoch", "1") == "1"


def test_waiting_user_is_sent_to_the_waiting_page(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    w, r = login(app, fake, "casey")  # shared with, but not switched on yet
    assert r.headers["location"] == "/waiting"
    for path in ("/home", f"/kids/{kid_id}", f"/kids/{kid_id}/status.json", "/requests", "/admin"):
        r = w.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/waiting", path


def test_csrf_token_belongs_to_one_session(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    alex_token = csrf(c)
    o, _ = login(app, fake, "owner")
    csrf(o, "/admin")  # the owner's session has its own token
    r = o.post(f"/kids/{kid_id}/control", data={"csrf": alex_token, "action": "pause"})
    assert r.status_code == 400
    assert app.state.db.kid(kid_id)["paused"] == 0


def test_signed_out_visitor_is_sent_to_the_start(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    anon = client(app)
    for path in ("/home", f"/kids/{kid_id}", f"/kids/{kid_id}/titles", "/requests", "/admin", "/poster/11"):
        r = anon.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/", path


def test_owner_can_manage_any_child(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    o, _ = login(app, fake, "owner")
    for page in KID_PAGES[:-1]:  # the QR code only exists while a link is needed
        assert o.get(f"/kids/{kid_id}{page}").status_code == 200, page
    o.post(f"/kids/{kid_id}/control", data={"csrf": csrf(o, "/admin"), "action": "pause"})
    assert app.state.db.kid(kid_id)["paused"] == 1


def test_removed_co_parent_loses_controls_and_home_listing(env):
    app, fake = env
    db = app.state.db
    c, kid_id = active_kid(app, fake)
    s, _ = login(app, fake, "sam")  # signing in once makes Sam a known parent
    o, _ = login(app, fake, "owner")
    otok = csrf(o, "/admin")
    o.post(f"/admin/kids/{kid_id}/co-parents", data={"csrf": otok, "parent": "102"})
    assert "Kid1" in s.get("/home").text
    o.post(f"/admin/kids/{kid_id}/co-parents", data={"csrf": otok, "parent": "102", "action": "remove"})
    assert "Kid1" not in s.get("/home").text
    s.post("/kids/pause-all", data={"csrf": csrf(s), "action": "pause"})
    assert db.kid(kid_id)["paused"] == 0


def test_switched_off_parent_cannot_be_made_a_co_parent(env):
    app, fake = env
    db = app.state.db
    c, kid_id = active_kid(app, fake)
    login(app, fake, "casey")  # known, but not switched on
    o, _ = login(app, fake, "owner")
    o.post(f"/admin/kids/{kid_id}/co-parents", data={"csrf": csrf(o, "/admin"), "parent": "103"})
    assert db.q("SELECT * FROM kid_parents") == []


def test_other_users_on_the_server_are_never_stopped(env):
    """The keeper only acts on sessions that belong to a connected child."""
    app, fake = env
    db = app.state.db
    c, kid_id = active_kid(app, fake)
    db.update_kid(kid_id, paused=1)
    fake.live = [Session("p1", 101, "movie", "Grown-up film", "playing", "TV"),
                 Session("p2", None, "movie", "Unknown", "playing", "TV"),
                 Session("k1", KID.id, "movie", "Cloud Garden", "playing", "iPad")]
    app.state.enforcer.tick(1_800_000_000)
    assert [x[1] for x in fake.calls if x[0] == "terminate"] == ["k1"]
