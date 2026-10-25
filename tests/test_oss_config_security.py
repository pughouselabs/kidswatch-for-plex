"""Config defaults when optional settings are empty, and the security headers every response carries."""
import os

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import create_app

from .fakes import FakePlex
from .test_app import active_kid, client, env, login  # noqa: F401  (env is a fixture)


def clean_env(monkeypatch, **values):
    for name in list(os.environ):
        if name.startswith("PK_"):
            monkeypatch.delenv(name)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def build(tmp_path, monkeypatch, **values):
    clean_env(monkeypatch, PK_DB=str(tmp_path / "t.db"), PK_PUBLIC_URL="http://testserver",
              PK_START_ENFORCER="0", **values)
    fake = FakePlex()
    return create_app(config.load(), plex=fake), fake


# ---------- config ----------

@pytest.mark.parametrize("raw, expected", [
    ("", ""),
    ("   ", ""),
    ("https://requests.example.com/", "https://requests.example.com"),
    ("  http://10.0.0.5:5055  ", "http://10.0.0.5:5055"),
    ("javascript:alert(1)", ""),
    ("requests.example.com", ""),
    ("ftp://requests.example.com", ""),
    ("//requests.example.com", ""),
])
def test_request_site_must_be_a_web_address(monkeypatch, raw, expected):
    clean_env(monkeypatch, PK_MEDIA_REQUEST_URL=raw)
    assert config.load().media_request_url == expected


def test_list_settings_ignore_spaces_case_and_blanks(monkeypatch):
    clean_env(monkeypatch, PK_SEED_PARENTS=" Alex , SAM,, ", PK_KID_SECTIONS=" 1 , 4 ,",
              PK_PUBLIC_URL="https://kids.example.com/", PK_PLEX_URL="http://plex:32400/")
    cfg = config.load()
    assert cfg.seed_parents == ("alex", "sam")
    assert cfg.kid_sections == ("1", "4")
    assert cfg.public_url == "https://kids.example.com" and cfg.plex_url == "http://plex:32400"


def test_empty_optional_settings_still_run_safely(tmp_path, monkeypatch):
    """A .env with every optional line left blank, as in .env.example."""
    app, fake = build(tmp_path, monkeypatch, PK_SEED_PARENTS="", PK_HA_WEBHOOK_FILE="", PK_TAUTULLI_URL="",
                      PK_TAUTULLI_CONFIG="", PK_MEDIA_REQUEST_URL="", PK_SECURE_COOKIES="0")
    assert app.state.notifier.configured() is False
    c = client(app)
    assert c.get("/healthz").json()["ok"] is True
    assert c.get("/help").status_code == 200
    fake.next_token = "tok-owner"
    c.get("/login")
    r = c.get("/auth/done")
    assert r.status_code == 503 and "not set up" in r.text  # the admin code is still required
    fake.next_token = "tok-alex"
    c.get("/login")
    assert c.get("/auth/done").headers["location"] == "/waiting"  # nobody is switched on by default


def test_built_in_request_form_offers_titles_without_a_request_site(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    assert 'value="title"' in c.get("/requests").text


@pytest.mark.parametrize("name, field", [("PK_OWNER_CODE", "owner_code"), ("PK_SECURE_COOKIES", "secure_cookies")])
def test_blank_security_switch_keeps_its_secure_default(monkeypatch, name, field):
    clean_env(monkeypatch, **{name: ""})
    assert getattr(config.load(), field) is True


def test_blank_kid_sections_uses_the_default(monkeypatch):
    clean_env(monkeypatch, PK_KID_SECTIONS="")
    assert config.load().kid_sections == ("1", "2")


# ---------- headers ----------

SECURITY_HEADERS = {
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
}


def assert_secure(r):
    for name, value in SECURITY_HEADERS.items():
        assert r.headers.get(name) == value, (r.request.url, name)
    csp = r.headers["content-security-policy"]
    for part in ("default-src 'self'", "script-src 'self'", "style-src 'self'", "frame-ancestors 'none'",
                 "base-uri 'none'", "form-action 'self'"):
        assert part in csp, part
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp and "*" not in csp.replace("*.plex.tv", "")


def test_every_kind_of_response_carries_the_security_headers(env):
    app, fake = env
    anon = client(app)
    responses = [
        anon.get("/"),                                        # page
        anon.get("/healthz"),                                 # JSON
        anon.get("/manifest.webmanifest"),
        anon.get("/static/app.js"),                           # static file
        anon.get("/static/nope.js"),                          # static 404
        anon.get("/no-such-page"),                            # friendly 404
        anon.get("/kids/abc"),                                # malformed link
        anon.get("/home"),                                    # redirect
        anon.get("/link/" + "x" * 30),                        # expired link
        anon.post("/logout", data={"csrf": "nope"}),          # CSRF failure
    ]
    for r in responses:
        assert_secure(r)


def test_too_large_post_with_content_length_carries_headers(env):
    app, fake = env
    r = client(app).post("/logout", content=b"x" * (64 * 1024 + 1))
    assert r.status_code == 413
    assert_secure(r)


def test_chunked_too_large_post_still_carries_headers(env):
    app, fake = env

    def body():
        for _ in range(20):
            yield b"x" * 8192

    r = client(app).post("/logout", content=body())
    assert r.status_code == 413
    assert_secure(r)


def test_private_pages_are_never_cached(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    for path in ("/home", f"/kids/{kid_id}", f"/kids/{kid_id}/status.json", "/requests"):
        assert c.get(path).headers.get("cache-control") == "no-store", path
    o, _ = login(app, fake, "owner")
    assert o.get("/admin").headers.get("cache-control") == "no-store"
    assert client(app).get("/link/" + "x" * 30).headers.get("cache-control") == "no-store"
    assert c.get("/poster/11").headers["cache-control"] == "private, max-age=86400"
    assert client(app).get("/").headers.get("cache-control") == "no-store"  # every page carries a form token


def test_api_docs_are_switched_off(env):
    app, fake = env
    anon = client(app)
    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert anon.get(path).status_code == 404, path


def test_secure_cookie_mode_sets_hsts_and_cookie_flags(tmp_path, monkeypatch):
    app, fake = build(tmp_path, monkeypatch, PK_OWNER_CODE="0")
    c = TestClient(app, base_url="https://testserver", follow_redirects=False)
    r = c.get("/help")  # renders a CSRF token, so the session cookie is set
    assert r.headers["strict-transport-security"] == "max-age=31536000"
    cookie = r.headers["set-cookie"].lower()
    assert cookie.startswith("pk_session=")
    assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie


def test_local_preview_mode_sends_no_hsts(env):
    app, fake = env
    r = client(app).get("/help")
    assert "strict-transport-security" not in r.headers
    assert "secure" not in r.headers["set-cookie"].lower()


def test_tampered_session_cookie_is_ignored(env):
    app, fake = env
    c, kid_id = active_kid(app, fake)
    value = c.cookies.get("pk_session")
    forged = client(app)
    forged.cookies.set("pk_session", value[:-4] + ("AAAA" if not value.endswith("AAAA") else "BBBB"))
    r = forged.get("/home")
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_session_key_changes_with_the_owner_token(tmp_path, monkeypatch, env):
    """A cookie signed for one server can't be replayed against another."""
    app, fake = env
    c, _ = login(app, fake, "alex")
    other_fake = FakePlex()
    other_fake.owner_token = lambda: "a-different-owner-token"
    clean_env(monkeypatch, PK_DB=str(tmp_path / "other.db"), PK_PUBLIC_URL="http://testserver",
              PK_START_ENFORCER="0", PK_SECURE_COOKIES="0", PK_SEED_PARENTS="alex")
    other = create_app(config.load(), plex=other_fake)
    replay = client(other)
    replay.cookies.set("pk_session", c.cookies.get("pk_session"))
    assert replay.get("/home").headers["location"] == "/"
