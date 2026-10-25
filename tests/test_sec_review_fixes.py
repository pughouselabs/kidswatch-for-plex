"""Fixes from docs/reviews/security-fixes-review.md: old cookies, limiter growth, sharing lists, link races, caching."""
import base64
import hashlib
import hmac
import json
import re
import threading
import time

import pytest
from itsdangerous import TimestampSigner

from app.plex import PlexError
from app.security import RateLimiter

from .fakes import ALEX, OWNER, SAM, SharedUser
from .test_app import add_kid, client, csrf, env, login  # noqa: F401  (env is a fixture)


def old_cookie(fake, user: dict, epoch: str = "1") -> str:
    """A signed cookie as written before server-side sessions: an identity and an epoch, but no session ID."""
    key = hmac.new(fake.owner_token().encode(), b"plex-for-kids-session-v1", hashlib.sha256).hexdigest()
    data = {"user": user, "epoch": epoch}
    return TimestampSigner(key).sign(base64.b64encode(json.dumps(data).encode())).decode()


OWNER_USER = {"id": OWNER.id, "name": "Admin", "username": "owner", "thumb": "", "role": "owner"}
ALEX_USER = {"id": ALEX.id, "name": "Alex", "username": "alex", "thumb": "", "role": "parent"}


def with_cookie(app, value):
    c = client(app)
    c.cookies.set("pk_session", value)
    return c


def count_sessions(app) -> int:
    return app.state.db.one("SELECT COUNT(*) AS n FROM sessions")["n"]


# ---------- 1. cookies from before server-side sessions ----------

def test_old_owner_cookie_is_refused(env):
    app, fake = env
    r = with_cookie(app, old_cookie(fake, OWNER_USER)).get("/admin")
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_old_cookie_of_an_existing_parent_is_refused(env):
    app, fake = env
    login(app, fake, "alex")  # the parent row exists and is switched on, so only the cookie check can refuse
    assert app.state.db.one("SELECT enabled FROM parents WHERE plex_id=?", (ALEX.id,))["enabled"]
    r = with_cookie(app, old_cookie(fake, ALEX_USER)).get("/home")
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_old_cookie_stays_refused_after_sign_out_and_sign_everyone_out(env):
    app, fake = env
    value = old_cookie(fake, OWNER_USER)
    o, _ = login(app, fake, "owner")
    o.post("/admin/sign-out-all", data={"csrf": csrf(o, "/admin")})
    assert app.state.db.setting("session_epoch") == "2"
    assert with_cookie(app, value).get("/admin").headers["location"] == "/"
    assert with_cookie(app, old_cookie(fake, OWNER_USER, epoch="2")).get("/admin").headers["location"] == "/"
    a, _ = login(app, fake, "alex")
    a.post("/logout", data={"csrf": csrf(a)})
    assert with_cookie(app, old_cookie(fake, ALEX_USER, epoch="2")).get("/home").headers["location"] == "/"


def test_refused_old_cookie_is_not_renewed(env):
    app, fake = env
    r = with_cookie(app, old_cookie(fake, OWNER_USER)).get("/")
    assert r.status_code == 200  # the start page, not a redirect to /admin
    for header in r.headers.get_list("set-cookie"):
        value = header.split(";")[0].split("=", 1)[1]
        if value and value != "null":  # a fresh cookie may hold a form token, never the old identity
            payload = value.split(".")[0]
            assert "user" not in json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4)))


def test_session_with_the_wrong_epoch_is_refused(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    app.state.db.set_setting("session_epoch", "999")
    assert c.get("/home").headers["location"] == "/"


# ---------- 2. rate limit buckets stay small ----------

def test_refused_hits_do_not_grow_a_bucket():
    r = RateLimiter(max_keys=1)
    results = [r.hit("same-key", 10, 60) for _ in range(12000)]
    assert results.count(False) == 10 and all(results[10:])
    assert len(r.hits["same-key"]) == 10


def test_bucket_recovers_after_the_window(monkeypatch):
    import app.security as sec
    now = [1000.0]
    monkeypatch.setattr(sec.time, "time", lambda: now[0])
    r = RateLimiter()
    for _ in range(500):
        r.hit("k", 3, 60)
    assert r.hit("k", 3, 60) is True
    now[0] += 61
    assert r.hit("k", 3, 60) is False and len(r.hits["k"]) == 1


def test_smaller_limit_on_the_same_key_trims_the_bucket():
    r = RateLimiter()
    for _ in range(10):
        r.hit("k", 10, 60)
    assert r.hit("k", 2, 60) is True and len(r.hits["k"]) <= 2


def test_repeated_refused_sign_ins_keep_the_bucket_small(env):
    app, fake = env
    c = client(app)
    codes = [c.get("/login").status_code for _ in range(60)]
    assert codes.count(303) == 10 and codes[-1] == 429
    by_ip, _ = app.state.rate_limits
    (bucket,) = by_ip.hits.values()
    assert len(bucket) == 10 and all(isinstance(t, float) for t in bucket)  # only the allowed sign-ins are kept


# ---------- 3. a fresh sign-in isn't undone by an older sharing list ----------

def test_newly_shared_parent_stays_signed_in(env):
    app, fake = env
    svc = app.state.svc
    svc.shared_ids, svc.shared_at = {SAM.id}, time.time()  # read before Alex was shared
    c, r = login(app, fake, "alex")
    assert r.headers["location"] == "/home"
    assert c.get("/home").status_code == 200
    assert count_sessions(app) == 1 and ALEX.id in svc.shared_ids


def test_slow_older_read_does_not_replace_a_newer_list(env):
    app, fake = env
    svc = app.state.svc
    started = time.time()  # a keeper read that began before Alex signed in and finished after
    observed = app.state.db.next_shared_observation()
    c, _ = login(app, fake, "alex")
    svc.publish_shared({SAM.id: None}, started, observed=observed)
    assert c.get("/home").status_code == 200 and ALEX.id in svc.shared_ids


def test_newer_negative_list_still_signs_the_parent_out(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    app.state.svc.publish_shared({SAM.id: SharedUser(SAM.id, "sam", "Sam", "")}, time.time(),
                                 observed=app.state.db.next_shared_observation())
    assert c.get("/home").headers["location"] == "/"
    assert count_sessions(app) == 0


# ---------- 4. an old list isn't trusted forever while Plex is down ----------

def plex_down(fake):
    def down():
        raise PlexError("Plex is down")
    fake.shared_users = down


def test_stale_positive_list_refuses_when_plex_is_down(env, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "SHARED_RETRY", 0)
    app, fake = env
    c, _ = login(app, fake, "alex")
    assert c.get("/home").status_code == 200  # a good list is kept
    real = fake.shared_users
    plex_down(fake)
    app.state.svc.shared_at = time.time() - 86400
    r = c.get("/home")
    assert r.status_code == 503 and count_sessions(app) == 1  # refused, but still signed in
    fake.shared_users = real
    assert c.get("/home").status_code == 200  # Plex is back: access returns without signing in again


def test_short_outage_keeps_the_recent_list(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    assert c.get("/home").status_code == 200
    plex_down(fake)
    app.state.svc.shared_at = time.time() - 700  # due for a re-read, but still within the outage allowance
    assert c.get("/home").status_code == 200


# ---------- 5. a link replaced while its sign-in is in progress ----------

def start_link(app, fake, c, kid_id):
    link = re.search(r'value="http://testserver(/link/[^"]+)"', c.get(f"/kids/{kid_id}").text).group(1)
    kid_browser = client(app)
    fake.next_token = "tok-kid1"
    assert kid_browser.get(link + "/start").status_code == 303
    return kid_browser, link


def blocked_check_pin(fake):
    entered, release = threading.Event(), threading.Event()
    real = fake.check_pin

    def slow(pin_id, code):
        entered.set()
        assert release.wait(10)
        return real(pin_id, code)

    fake.check_pin = slow
    return entered, release


def run_done(kid_browser, link):
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", kid_browser.get(link + "/done")))
    t.start()
    return t, out


def test_link_replaced_during_sign_in_cannot_connect(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    kid_browser, link = start_link(app, fake, c, kid_id)
    entered, release = blocked_check_pin(fake)
    t, out = run_done(kid_browser, link)
    assert entered.wait(10)
    c.post(f"/kids/{kid_id}/connect", data={"csrf": csrf(c, f"/kids/{kid_id}")})  # a new link replaces it
    release.set()
    t.join(10)
    assert out["r"].status_code == 410
    kid = app.state.db.kid(kid_id)
    assert kid["status"] == "draft" and kid["plex_id"] is None
    assert not [call for call in fake.calls if call[0] == "invite"]


def test_parent_switched_off_during_sign_in_cannot_connect(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    kid_browser, link = start_link(app, fake, c, kid_id)
    entered, release = blocked_check_pin(fake)
    t, out = run_done(kid_browser, link)
    assert entered.wait(10)
    app.state.db.x("UPDATE parents SET enabled=0 WHERE plex_id=?", (ALEX.id,))
    release.set()
    t.join(10)
    assert out["r"].status_code == 410 and app.state.db.kid(kid_id)["status"] == "draft"


def test_link_used_once_cannot_connect_again(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    kid_browser, link = start_link(app, fake, c, kid_id)
    assert kid_browser.get(link + "/done").status_code == 200
    assert app.state.db.kid(kid_id)["plex_id"] == 201
    assert client(app).get(link).status_code == 410


def test_failed_connect_leaves_the_link_usable(env, monkeypatch):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    kid_browser, link = start_link(app, fake, c, kid_id)
    monkeypatch.setattr(app.state.svc, "connect", lambda *a: (False, "Plex said no.", None))
    assert kid_browser.get(link + "/done").status_code == 400
    assert client(app).get(link).status_code == 200  # the parent can try the same link again


# ---------- 6. no page is cached ----------

@pytest.mark.parametrize("path", ["/", "/help", "/missing"])
def test_public_and_error_pages_are_not_cached(env, path):
    app, fake = env
    assert client(app).get(path).headers.get("cache-control") == "no-store"


def test_sign_in_error_pages_are_not_cached(env):
    app, fake = env
    c = client(app)
    for _ in range(10):
        c.get("/login")
    r = c.get("/login")
    assert r.status_code == 429 and r.headers.get("cache-control") == "no-store"

    def down():
        raise PlexError("Plex is down")

    fake.create_pin = down
    app.state.rate_limits[0].hits.clear()
    r = client(app).get("/login")
    assert r.status_code == 503 and r.headers.get("cache-control") == "no-store"


def test_posters_keep_their_caching(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    assert c.get("/poster/11").headers["cache-control"] == "private, max-age=86400"
