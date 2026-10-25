"""Forwarded headers count only from trusted proxies, and rate limits can't be reset by a flood."""
import os

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import create_app
from app.security import ClientAddress, RateLimiter, parse_networks, rate_key

from .fakes import FakePlex


def build(tmp_path, monkeypatch, **values):
    for name in list(os.environ):
        if name.startswith("PK_"):
            monkeypatch.delenv(name)
    defaults = dict(PK_DB=str(tmp_path / "t.db"), PK_PUBLIC_URL="http://testserver", PK_START_ENFORCER="0",
                    PK_SECURE_COOKIES="0", PK_OWNER_CODE="0")
    for name, value in {**defaults, **values}.items():
        monkeypatch.setenv(name, value)
    return create_app(config.load(), plex=FakePlex())


def visitor(app, peer="203.0.113.7"):
    return TestClient(app, base_url="http://testserver", follow_redirects=False, client=(peer, 50000))


def signed_in_ips(app):
    return [r["detail"] for r in app.state.db.q("SELECT detail FROM audit WHERE action='signed_in'")]


class Req:
    def __init__(self, peer, **headers):
        self.client = type("C", (), {"host": peer})()
        self.headers = {k.replace("_", "-").lower(): v for k, v in headers.items()}


# ---------- working out the address ----------

def test_direct_visitors_cannot_choose_their_address():
    a = ClientAddress()
    req = Req("203.0.113.7", cf_connecting_ip="192.168.1.20", x_forwarded_for="192.168.1.20", cf_ipcountry="US")
    assert a.ip(req) == "203.0.113.7"
    assert a.country(req) == ""


def test_trusted_proxy_uses_the_rightmost_untrusted_hop():
    a = ClientAddress(parse_networks("172.18.0.1, 10.0.0.0/8"))
    # The visitor sent a fake first entry; an appending proxy kept it on the left.
    req = Req("172.18.0.1", x_forwarded_for="192.168.1.20, 198.51.100.4, 10.1.2.3")
    assert a.ip(req) == "198.51.100.4"
    assert a.ip(Req("172.18.0.1", x_forwarded_for="198.51.100.4")) == "198.51.100.4"
    assert a.ip(Req("172.18.0.1")) == "172.18.0.1"  # no header: the proxy itself
    assert a.ip(Req("172.18.0.1", x_forwarded_for="not-an-ip")) == "172.18.0.1"


def test_cloudflare_headers_need_the_switch_and_a_trusted_peer():
    nets = parse_networks("172.18.0.1")
    headers = dict(cf_connecting_ip="198.51.100.4", cf_ipcountry="CA", x_forwarded_for="172.18.0.9")
    off = ClientAddress(nets)
    assert off.ip(Req("172.18.0.1", **headers)) == "172.18.0.9" and off.country(Req("172.18.0.1", **headers)) == ""
    on = ClientAddress(nets, trust_cf=True)
    assert on.ip(Req("172.18.0.1", **headers)) == "198.51.100.4"
    assert on.country(Req("172.18.0.1", **headers)) == "CA"
    assert on.ip(Req("203.0.113.7", **headers)) == "203.0.113.7"  # not through the proxy: ignored
    assert on.country(Req("203.0.113.7", **headers)) == ""
    assert on.country(Req("172.18.0.1", cf_ipcountry="<b>")) == ""


def test_untrusted_forwarded_header_logs_a_setup_hint_once(caplog):
    a = ClientAddress()
    with caplog.at_level("WARNING", logger="kidswatch"):
        a.ip(Req("172.18.0.1", x_forwarded_for="198.51.100.4"))
        a.ip(Req("172.18.0.1", x_forwarded_for="198.51.100.4"))
    hints = [r.getMessage() for r in caplog.records if "PK_TRUSTED_PROXIES" in r.getMessage()]
    assert hints == ["Ignoring X-Forwarded-For from 172.18.0.1. If that is your reverse proxy, "
                     "set PK_TRUSTED_PROXIES=172.18.0.1"]


def test_ipv6_visitors_share_a_bucket_per_64():
    assert rate_key("2001:db8:1:2:aaaa::1") == rate_key("2001:db8:1:2:bbbb::9") == "2001:db8:1:2::/64"
    assert rate_key("2001:db8:1:3::1") != rate_key("2001:db8:1:2::1")
    assert rate_key("203.0.113.7") == "203.0.113.7"


@pytest.mark.parametrize("value", ["proxy.local", "10.0.0.0/33", "1.2.3"])
def test_bad_trusted_proxy_setting_stops_startup(monkeypatch, value):
    monkeypatch.setenv("PK_TRUSTED_PROXIES", value)
    with pytest.raises(ValueError, match="PK_TRUSTED_PROXIES"):
        config.load()


def test_trusted_proxies_default_to_none(monkeypatch):
    monkeypatch.delenv("PK_TRUSTED_PROXIES", raising=False)
    monkeypatch.delenv("PK_TRUST_CF_HEADERS", raising=False)
    cfg = config.load()
    assert cfg.trusted_proxies == () and cfg.trust_cf_headers is False


# ---------- in the app ----------

def test_rotating_spoofed_headers_does_not_dodge_the_sign_in_limit(tmp_path, monkeypatch):
    app = build(tmp_path, monkeypatch)
    c = visitor(app)
    codes = [c.get("/login", headers={"CF-Connecting-IP": f"10.0.0.{i}", "X-Forwarded-For": f"10.0.1.{i}"}).status_code
             for i in range(15)]
    assert codes.count(429) == 5


def test_audit_and_owner_alert_use_the_real_address(tmp_path, monkeypatch):
    hook = tmp_path / "hook"
    hook.write_text("http://ha.invalid/api/webhook/x")
    app = build(tmp_path, monkeypatch, PK_OWNER_CODE="1", PK_HA_WEBHOOK_FILE=str(hook))
    sent = []
    monkeypatch.setattr(app.state.notifier, "send", lambda title, msg, path="/admin": sent.append(msg))
    fake = app.state.plex
    c = visitor(app)
    spoof = {"CF-Connecting-IP": "192.168.1.20", "CF-IPCountry": "US", "X-Forwarded-For": "192.168.1.20"}
    fake.next_token = "tok-owner"
    c.get("/login", headers=spoof)
    assert c.get("/auth/done", headers=spoof).headers["location"] == "/verify"
    assert "Requested from 203.0.113.7." in sent[-1] and "192.168.1.20" not in sent[-1] and "(US)" not in sent[-1]


def test_behind_a_trusted_proxy_each_visitor_gets_their_own_limit(tmp_path, monkeypatch):
    app = build(tmp_path, monkeypatch, PK_TRUSTED_PROXIES="172.18.0.1", PK_SEED_PARENTS="alex")
    c = visitor(app, "172.18.0.1")
    for i in range(10):
        assert c.get("/login", headers={"X-Forwarded-For": "198.51.100.4"}).status_code == 303
    assert c.get("/login", headers={"X-Forwarded-For": "198.51.100.4"}).status_code == 429
    app.state.plex.next_token = "tok-alex"
    assert c.get("/login", headers={"X-Forwarded-For": "198.51.100.5"}).status_code == 303
    c.get("/auth/done", headers={"X-Forwarded-For": "198.51.100.5"})
    assert '"ip": "198.51.100.5"' in signed_in_ips(app)[-1]


def test_sign_in_starts_have_a_server_wide_cap(tmp_path, monkeypatch):
    app = build(tmp_path, monkeypatch, PK_TRUSTED_PROXIES="172.18.0.1")
    c = visitor(app, "172.18.0.1")
    codes = [c.get("/login", headers={"X-Forwarded-For": f"198.51.100.{i}"}).status_code for i in range(70)]
    assert codes.count(303) == 60 and codes[-1] == 429


# ---------- the limiter ----------

def test_full_limiter_keeps_limits_for_recent_keys():
    r = RateLimiter(max_keys=100)
    for _ in range(5):
        r.hit("victim", 5, 60)
    for i in range(1000):
        r.hit(f"flood-{i}", 5, 60)
        if i % 50 == 0:
            r.hits.move_to_end("victim")  # the victim keeps coming back, so it stays recently used
    assert len(r.hits) <= 100
    assert r.hit("victim", 5, 60) is True  # still limited: the table was never wiped


def test_full_limiter_drops_expired_keys_first(monkeypatch):
    import app.security as sec
    now = [1000.0]
    monkeypatch.setattr(sec.time, "time", lambda: now[0])
    r = RateLimiter(max_keys=3)
    r.hit("old", 1, 10)
    now[0] += 5
    r.hit("a", 1, 60)
    r.hit("b", 1, 60)
    now[0] += 10  # "old" has expired, "a" and "b" have not
    r.hit("c", 1, 60)
    assert list(r.hits) == ["a", "b", "c"]
    r.hit("d", 1, 60)  # nothing expired: the least recently used key goes
    assert list(r.hits) == ["b", "c", "d"]


def test_a_visitor_flood_does_not_reset_signed_in_limits(tmp_path, monkeypatch):
    from .test_app import csrf, login
    app = build(tmp_path, monkeypatch, PK_SEED_PARENTS="alex")
    sent = []
    monkeypatch.setattr(app.state.notifier, "send", lambda title, msg, path="/admin": sent.append(msg))
    c, _ = login(app, app.state.plex, "alex")
    tok = csrf(c, "/requests")
    for i in range(3):
        c.post("/requests", data={"csrf": tok, "kind": "feature", "message": f"idea {i}"})
    assert len(sent) == 3
    by_ip, _ = app.state.rate_limits
    for i in range(20000):  # far more visitor addresses than the table holds
        by_ip.hit(f"198.51.{i // 250}.{i % 250}", 10, 60)
    assert len(by_ip.hits) <= by_ip.max_keys
    c.post("/requests", data={"csrf": tok, "kind": "feature", "message": "one more"})
    assert len(sent) == 3  # the 3-per-10-minutes alert limit still holds
