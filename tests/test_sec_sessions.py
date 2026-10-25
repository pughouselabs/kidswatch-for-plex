"""Server-side sessions, fail-closed sharing checks, connect links, the owner code and on/off settings."""
import base64
import hashlib
import hmac
import json
import re
import time

import pytest
from itsdangerous import TimestampSigner

from app import config
from app.main import create_app
from app.plex import PlexError
from app.security import SESSION_LIMITS

from .fakes import ALEX, SAM, FakePlex
from .test_app import add_kid, client, csrf, env, login  # noqa: F401  (env is a fixture)


def back_date(app, column, seconds):
    app.state.db.x(f"UPDATE sessions SET {column}={column}-?", (seconds,))


def cookie_data(c) -> dict:
    payload = c.cookies.get("pk_session").split(".")[0]
    return json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4)))


# ---------- sessions ----------

def test_sign_out_ends_copies_of_the_cookie(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    copied = client(app)
    copied.cookies.set("pk_session", c.cookies.get("pk_session"))
    assert copied.get("/home").status_code == 200
    c.post("/logout", data={"csrf": csrf(c)})
    r = copied.get("/home")
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert app.state.db.one("SELECT COUNT(*) AS n FROM sessions")["n"] == 0


def test_signing_in_again_replaces_the_earlier_session(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    copied = client(app)
    copied.cookies.set("pk_session", c.cookies.get("pk_session"))
    fake.next_token = "tok-alex"
    c.get("/login")
    assert c.get("/auth/done").headers["location"] == "/home"
    assert c.get("/home").status_code == 200
    assert copied.get("/home").headers["location"] == "/"
    assert app.state.db.one("SELECT COUNT(*) AS n FROM sessions")["n"] == 1


def test_cookie_holds_a_session_id_not_a_stored_row(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    sid = cookie_data(c)["sid"]
    row = app.state.db.one("SELECT * FROM sessions")
    assert row["id"] == hashlib.sha256(sid.encode()).hexdigest() and row["user_id"] == ALEX.id
    assert row["role"] == "parent"


def test_cookies_from_before_server_side_sessions_sign_in_again(env):
    app, fake = env
    key = hmac.new(fake.owner_token().encode(), b"plex-for-kids-session-v1", hashlib.sha256).hexdigest()
    old = {"user": {"id": ALEX.id, "name": "Alex", "username": "alex", "thumb": "", "role": "parent"}, "epoch": "1"}
    value = TimestampSigner(key).sign(base64.b64encode(json.dumps(old).encode())).decode()
    c = client(app)
    c.cookies.set("pk_session", value)
    assert c.get("/home").headers["location"] == "/"


def test_owner_sessions_end_after_twelve_idle_hours(env):
    app, fake = env
    o, _ = login(app, fake, "owner")
    back_date(app, "last_seen", 11 * 3600)
    assert o.get("/admin").status_code == 200
    back_date(app, "last_seen", 12 * 3600 + 60)
    assert o.get("/admin").headers["location"] == "/"


def test_parent_sessions_survive_a_quiet_day(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    back_date(app, "last_seen", 2 * 86400)
    assert c.get("/home").status_code == 200


def test_use_renews_a_session_only_up_to_its_total_lifetime(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    back_date(app, "last_seen", 600)
    before = app.state.db.one("SELECT last_seen FROM sessions")["last_seen"]
    assert c.get("/home").status_code == 200
    assert app.state.db.one("SELECT last_seen FROM sessions")["last_seen"] > before  # sliding renewal
    back_date(app, "created_at", SESSION_LIMITS["parent"][1] + 60)
    assert c.get("/home").headers["location"] == "/"  # the total cap wins, however recent the visit


def test_sign_everyone_out_deletes_every_session(env):
    app, fake = env
    a, _ = login(app, fake, "alex")
    o, _ = login(app, fake, "owner")
    o.post("/admin/sign-out-all", data={"csrf": csrf(o, "/admin")})
    assert app.state.db.one("SELECT COUNT(*) AS n FROM sessions")["n"] == 0
    assert a.get("/home").headers["location"] == "/"


def test_switching_a_parent_off_ends_their_sessions(env):
    app, fake = env
    a, _ = login(app, fake, "alex")
    o, _ = login(app, fake, "owner")
    o.post(f"/admin/parents/{ALEX.id}", data={"csrf": csrf(o, "/admin"), "enabled": "0"})
    assert not app.state.db.one("SELECT 1 FROM sessions WHERE user_id=?", (ALEX.id,))
    assert o.get("/admin").status_code == 200


def test_plex_pin_codes_stay_out_of_the_cookie(env):
    app, fake = env
    c = client(app)
    c.get("/login")
    data = cookie_data(c)
    assert "code" not in json.dumps(data) and isinstance(data["pin"], str)
    assert len(app.state.plex.pin_token) == 1


# ---------- sharing checks fail closed ----------

def unshare(fake, *accounts):
    fake.shared_users = lambda: {a.id: None for a in accounts}


def test_unshared_parent_is_refused_even_without_the_keeper(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    app.state.svc.shared_ids = None  # the keeper is off in tests, and nothing has been read since a restart
    unshare(fake, SAM)
    assert c.get("/home").headers["location"] == "/"


def test_unknown_sharing_list_refuses_without_signing_out(env):
    app, fake = env
    c, _ = login(app, fake, "alex")

    def down():
        raise PlexError("Plex is down")

    fake.shared_users = down
    app.state.svc.shared_ids = None  # nothing read since a restart
    r = c.get("/home")
    assert r.status_code == 503 and "Plex isn&#39;t answering" in r.text
    assert app.state.db.one("SELECT COUNT(*) AS n FROM sessions")["n"] == 1


def test_stale_sharing_list_is_read_again(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    assert c.get("/home").status_code == 200
    unshare(fake, SAM)
    assert c.get("/home").status_code == 200  # within the keeper's refresh time
    app.state.svc.shared_at = time.time() - 700
    assert c.get("/home").headers["location"] == "/"


def test_connect_link_stops_when_the_parent_is_unshared(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    link = re.search(r'value="http://testserver(/link/[^"]+)"', c.get(f"/kids/{kid_id}").text).group(1)
    app.state.svc.shared_ids = None
    unshare(fake, SAM)
    assert client(app).get(link).status_code == 410


# ---------- connect links ----------

def test_new_connect_link_retires_the_old_one(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid_id = add_kid(app, fake, c)
    page = c.get(f"/kids/{kid_id}").text
    old = re.search(r'value="http://testserver(/link/[^"]+)"', page).group(1)
    assert client(app).get(old).status_code == 200
    c.post(f"/kids/{kid_id}/connect", data={"csrf": csrf(c, f"/kids/{kid_id}")})
    new = re.search(r'value="http://testserver(/link/[^"]+)"', c.get(f"/kids/{kid_id}").text).group(1)
    assert new != old
    assert client(app).get(old).status_code == 410
    assert client(app).get(old + "/start").headers["location"] == old
    assert client(app).get(new).status_code == 200


# ---------- owner code ----------

@pytest.fixture
def owner_code_app(tmp_path, monkeypatch):
    monkeypatch.setenv("PK_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("PK_PUBLIC_URL", "http://testserver")
    monkeypatch.setenv("PK_SECURE_COOKIES", "0")
    monkeypatch.setenv("PK_START_ENFORCER", "0")
    monkeypatch.setenv("PK_OWNER_CODE", "1")
    hook = tmp_path / "hook"
    hook.write_text("http://ha.invalid/api/webhook/x")
    monkeypatch.setenv("PK_HA_WEBHOOK_FILE", str(hook))
    fake = FakePlex()
    app = create_app(config.load(), plex=fake)
    sent = []
    monkeypatch.setattr(app.state.notifier, "send", lambda title, msg, path="/admin": sent.append(msg))
    return app, fake, sent


def test_non_ascii_digits_are_a_wrong_code_not_an_error(owner_code_app):
    app, fake, sent = owner_code_app
    c, r = login(app, fake, "owner")
    assert r.headers["location"] == "/verify"
    code = re.search(r"Code (\d{6})", sent[-1]).group(1)
    arabic = "".join(chr(0x0660 + int(d)) for d in code)  # the same digits typed on an Arabic keyboard
    r = c.post("/verify", data={"csrf": csrf(c, "/verify"), "code": arabic})
    assert r.status_code == 303 and r.headers["location"] == "/verify"
    assert c.get("/admin").status_code == 303
    r = c.post("/verify", data={"csrf": csrf(c, "/verify"), "code": code[:3] + " " + code[3:]})
    assert r.headers["location"] == "/admin" and c.get("/admin").status_code == 200
    assert app.state.db.one("SELECT role FROM sessions")["role"] == "owner"


def test_code_page_is_never_cached(owner_code_app):
    app, fake, sent = owner_code_app
    c, _ = login(app, fake, "owner")
    assert c.get("/verify").headers["cache-control"] == "no-store"


# ---------- on/off settings ----------

@pytest.mark.parametrize("raw, expected", [("1", True), ("0", False), (" TRUE ", True), ("off", False),
                                           ("yes", True), ("no", False), ("", True), ("  ", True)])
def test_switches_read_common_spellings_and_keep_defaults_when_blank(monkeypatch, raw, expected):
    for name in ("PK_SECURE_COOKIES", "PK_OWNER_CODE", "PK_START_ENFORCER"):
        monkeypatch.setenv(name, raw)
    cfg = config.load()
    assert cfg.secure_cookies is cfg.owner_code is cfg.start_enforcer is expected


@pytest.mark.parametrize("name", ["PK_SECURE_COOKIES", "PK_OWNER_CODE", "PK_START_ENFORCER", "PK_TRUST_CF_HEADERS"])
def test_unreadable_switch_stops_startup(monkeypatch, name):
    monkeypatch.setenv(name, "maybe")
    with pytest.raises(ValueError, match=f"{name} must be 1"):
        config.load()


def test_blank_cloudflare_switch_stays_off(monkeypatch):
    monkeypatch.setenv("PK_TRUST_CF_HEADERS", "")
    assert config.load().trust_cf_headers is False


@pytest.mark.parametrize("raw", [" , ", ",,", "   "])
def test_kid_sections_with_only_separators_use_the_default(monkeypatch, raw):
    monkeypatch.setenv("PK_KID_SECTIONS", raw)
    assert config.load().kid_sections == ("1", "2")
