"""Request flows between parents and the owner: who can see, cancel and answer what."""
import re
from types import SimpleNamespace
from .test_app import active_kid, add_kid, connect, csrf, env, login  # noqa: F401  (env is a fixture)


def requests_of(db, parent_id):
    return db.q("SELECT * FROM requests WHERE parent_id=? ORDER BY id", (parent_id,))


def test_parents_only_see_and_cancel_their_own_requests(env):
    app, fake = env
    db = app.state.db
    c, _ = login(app, fake, "alex")
    c.post("/requests", data={"csrf": csrf(c), "kind": "feature", "message": "Alex's private idea"})
    rid = requests_of(db, 101)[0]["id"]
    s, _ = login(app, fake, "sam")
    assert "Alex's private idea" not in s.get("/requests").text
    assert s.post(f"/requests/{rid}/cancel", data={"csrf": csrf(s)}).status_code == 404
    assert db.one("SELECT status FROM requests WHERE id=?", (rid,))["status"] == "open"
    assert c.post(f"/requests/{rid}/cancel", data={"csrf": csrf(c)}).status_code == 303
    assert db.one("SELECT status FROM requests WHERE id=?", (rid,))["status"] == "cancelled"
    assert c.post(f"/requests/{rid}/cancel", data={"csrf": csrf(c)}).status_code == 404  # only open ones


def test_request_about_someone_elses_child_is_refused(env):
    app, fake = env
    db = app.state.db
    c, kid_id = active_kid(app, fake)
    s, _ = login(app, fake, "sam")
    r = s.post("/requests", data={"csrf": csrf(s), "kind": "problem", "message": "hi", "kid": str(kid_id)})
    assert r.status_code == 400
    r = s.post("/requests", data={"csrf": csrf(s), "kind": "problem", "message": "hi", "kid": "99999"})
    assert r.status_code == 400
    assert requests_of(db, 102) == []


def test_bad_requests_are_not_saved(env):
    app, fake = env
    db = app.state.db
    c, _ = login(app, fake, "alex")
    tok = csrf(c)
    for data in ({"kind": "feature", "message": "   "}, {"kind": "add_child", "message": "sneaky"},
                 {"kind": "access", "message": "sneaky"}, {"kind": "", "message": "x"}):
        r = c.post("/requests", data={"csrf": tok, **data})
        assert r.status_code == 303 and r.headers["location"] == "/requests#new", data
    assert requests_of(db, 101) == []
    c.post("/requests", data={"csrf": tok, "kind": "other", "message": "x" * 5000})
    assert len(requests_of(db, 101)[0]["message"]) == 1000


def test_new_child_request_cannot_be_cancelled_by_the_parent(env):
    app, fake = env
    db = app.state.db
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    connect(app, fake, c, kid_id)
    (req,) = requests_of(db, 101)
    assert req["kind"] == "add_child" and req["kid_id"] == kid_id
    assert c.post(f"/requests/{req['id']}/cancel", data={"csrf": csrf(c)}).status_code == 404
    assert db.one("SELECT status FROM requests WHERE id=?", (req["id"],))["status"] == "open"


def test_owner_answers_each_request_once(env):
    app, fake = env
    db = app.state.db
    c, _ = login(app, fake, "alex")
    c.post("/requests", data={"csrf": csrf(c), "kind": "title", "message": "Cloud Garden 2"})
    rid = requests_of(db, 101)[0]["id"]
    o, _ = login(app, fake, "owner")
    tok = csrf(o, "/admin")
    assert o.post(f"/admin/requests/{rid}", data={"csrf": tok, "action": "explode"}).status_code == 400
    assert o.post(f"/admin/requests/{rid}", data={"csrf": tok, "action": "done", "note": "Added!"}).status_code == 303
    row = db.one("SELECT * FROM requests WHERE id=?", (rid,))
    assert row["status"] == "done" and row["owner_note"] == "Added!" and row["resolved_at"]
    assert o.post(f"/admin/requests/{rid}", data={"csrf": tok, "action": "decline"}).status_code == 400
    assert db.one("SELECT status FROM requests WHERE id=?", (rid,))["status"] == "done"
    assert o.post("/admin/requests/99999", data={"csrf": tok, "action": "done"}).status_code == 400
    assert "Added!" in c.get("/requests").text


def test_declined_access_request_keeps_the_parent_switched_off(env):
    app, fake = env
    db = app.state.db
    w, _ = login(app, fake, "casey")
    w.post("/waiting", data={"csrf": csrf(w, "/waiting"), "message": "please"})
    (req,) = requests_of(db, 103)
    o, _ = login(app, fake, "owner")
    page = o.get(f"/admin/requests/{req['id']}").text
    version = re.search(r'name="version" value="([^"]+)"', page).group(1)
    o.post(f"/admin/requests/{req['id']}/decision", data={"csrf": csrf(o, "/admin"), "action": "decline", "version": version})
    assert db.one("SELECT enabled FROM parents WHERE plex_id=103")["enabled"] == 0
    _, r = login(app, fake, "casey")
    assert r.headers["location"] == "/waiting"


def test_waiting_page_sends_one_open_access_request(env):
    app, fake = env
    db = app.state.db
    sent = []
    app.state.notifier = SimpleNamespace(send=lambda title, msg, path="/admin": sent.append(title))
    w, _ = login(app, fake, "casey")
    tok = csrf(w, "/waiting")
    for _ in range(5):
        w.post("/waiting", data={"csrf": tok, "message": "please"})
    assert len(requests_of(db, 103)) == 1 and len(sent) == 1
    # a parent who can already use the site has no waiting page to post to
    c, _ = login(app, fake, "alex")
    assert c.post("/waiting", data={"csrf": csrf(c), "message": "x"}).headers["location"] == "/waiting"
    assert requests_of(db, 101) == []


def test_removing_a_child_closes_its_open_requests(env):
    app, fake = env
    db = app.state.db
    c, kid_id = active_kid(app, fake)
    c.post("/requests", data={"csrf": csrf(c), "kind": "problem", "message": "Kid1 can't see a show",
                              "kid": str(kid_id)})
    rid = db.one("SELECT id FROM requests WHERE kind='problem'")["id"]
    r = c.post(f"/kids/{kid_id}/remove", data={"csrf": csrf(c), "confirm": "kid1"})
    assert r.status_code == 303 and r.headers["location"] == "/home"
    assert db.kid(kid_id)["status"] == "removed"
    row = db.one("SELECT status, resolved_at FROM requests WHERE id=?", (rid,))
    assert row["status"] == "declined" and row["resolved_at"]


def test_remove_needs_the_childs_name(env):
    app, fake = env
    db = app.state.db
    c, kid_id = active_kid(app, fake)
    for confirm in ("", "kid", "Kid12"):
        c.post(f"/kids/{kid_id}/remove", data={"csrf": csrf(c), "confirm": confirm})
        assert db.kid(kid_id)["status"] == "active", confirm
    assert not [x for x in fake.calls if x[0] == "remove_share"]


def test_approving_a_new_child_request_twice_does_nothing_more(env):
    app, fake = env
    db = app.state.db
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    connect(app, fake, c, kid_id)
    (req,) = requests_of(db, 101)
    o, _ = login(app, fake, "owner")
    tok = csrf(o, "/admin")
    o.post(f"/admin/requests/{req['id']}", data={"csrf": tok, "action": "approve"})
    assert db.kid(kid_id)["status"] == "active"
    approvals = db.one("SELECT COUNT(*) n FROM audit WHERE action='approved'")["n"]
    assert o.post(f"/admin/requests/{req['id']}", data={"csrf": tok, "action": "decline"}).status_code == 400
    assert db.kid(kid_id)["status"] == "active"
    assert db.one("SELECT COUNT(*) n FROM audit WHERE action='approved'")["n"] == approvals
