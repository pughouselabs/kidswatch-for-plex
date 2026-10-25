"""Client addresses behind proxies, rate limits, and server-side sessions."""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import secrets
import time
from collections import OrderedDict, deque

log = logging.getLogger("kidswatch")

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_networks(value: str, name: str = "PK_TRUSTED_PROXIES") -> tuple[Network, ...]:
    """Comma-separated addresses or ranges (10.0.0.5, 172.18.0.0/16, fd00::/8). Bad entries stop startup."""
    nets = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            raise ValueError(f"{name} must list IP addresses or ranges, like 172.18.0.1 or 10.0.0.0/8") from None
    return tuple(nets)


def _addr(value: str):
    try:
        return ipaddress.ip_address(value.strip().strip("[]"))
    except ValueError:
        return None


class ClientAddress:
    """Works out the visitor's address. Forwarded headers count only when the direct peer is a trusted proxy."""

    def __init__(self, trusted: tuple[Network, ...] = (), trust_cf: bool = False):
        self.trusted, self.trust_cf = trusted, trust_cf
        self._warned = False

    def is_trusted(self, value: str) -> bool:
        a = _addr(value)
        return a is not None and any(a in n for n in self.trusted)

    def ip(self, request) -> str:
        peer = request.client.host if request.client else "?"
        if not self.is_trusted(peer):
            if not self._warned and request.headers.get("x-forwarded-for") and _addr(peer) is not None:
                self._warned = True  # once per start: a proxy is probably in front but not trusted yet
                log.warning("Ignoring X-Forwarded-For from %s. If that is your reverse proxy, "
                            "set PK_TRUSTED_PROXIES=%s", peer, peer)
            return peer
        if self.trust_cf:
            cf = _addr(request.headers.get("cf-connecting-ip", ""))
            if cf is not None:
                return str(cf)
        # Walk right to left: the first hop that isn't one of our proxies is the visitor.
        hops = [h for h in (request.headers.get("x-forwarded-for") or "").split(",") if h.strip()]
        for hop in reversed(hops):
            a = _addr(hop)
            if a is None:
                break
            if not self.is_trusted(str(a)):
                return str(a)
        return peer

    def country(self, request) -> str:
        """CF-IPCountry, only when Cloudflare headers are switched on and came through a trusted proxy."""
        if not self.trust_cf or not self.is_trusted(request.client.host if request.client else ""):
            return ""
        value = request.headers.get("cf-ipcountry", "").strip()
        return value if len(value) == 2 and value.isascii() and value.isalpha() else ""


def rate_key(ip: str) -> str:
    """One bucket per IPv4 address, and per /64 for IPv6 (one home or phone usually owns a whole /64)."""
    a = _addr(ip)
    if a is not None and a.version == 6 and not a.ipv4_mapped:
        return str(ipaddress.ip_network(f"{a}/64", strict=False))
    return ip


class RateLimiter:
    """Sliding-window hit counts per key, with a fixed size.

    Only allowed hits are stored, so a key never holds more than its limit and a refused visitor who keeps
    trying adds no work or memory. When full it forgets the least recently used key, never the whole table,
    so a flood of new addresses can't reset everyone's limits at once."""

    def __init__(self, max_keys: int = 5000):
        self.max_keys = max_keys
        self.hits: OrderedDict[str, deque[float]] = OrderedDict()

    def hit(self, key: str, limit: int, window: int) -> bool:
        """Record one hit; True means the key is over its limit (and the hit is not counted)."""
        now = time.time()
        hits = self.hits.get(key)
        if hits is None:
            hits = self.hits[key] = deque()
        else:
            self.hits.move_to_end(key)
        while hits and now - hits[0] >= window:
            hits.popleft()
        while len(hits) > max(limit, 0):  # the same key used with a smaller limit
            hits.popleft()
        while len(self.hits) > self.max_keys:
            self.hits.popitem(last=False)
        if len(hits) >= limit:
            return True
        hits.append(now)
        return False


# How long a sign-in lasts, by role: (idle seconds, total seconds). Use keeps it alive up to the total.
SESSION_LIMITS = {
    "owner": (12 * 3600, 7 * 86400),
    "parent": (14 * 86400, 30 * 86400),
    "waiting": (14 * 86400, 30 * 86400),
}
SEEN_EVERY = 60  # write last_seen at most once a minute per session


class Sessions:
    """Server-side record of each sign-in. The cookie holds a random ID; signing out deletes the row."""

    def __init__(self, db):
        self.db = db

    @staticmethod
    def _key(sid: str) -> str:
        return hashlib.sha256(sid.encode()).hexdigest()

    def create(self, user_id: int, role: str) -> str:
        now = time.time()
        total = max(t for _, t in SESSION_LIMITS.values())
        self.db.x("DELETE FROM sessions WHERE created_at<?", (now - total,))
        sid = secrets.token_urlsafe(32)
        self.db.x("INSERT INTO sessions(id,user_id,role,created_at,last_seen) VALUES(?,?,?,?,?)",
                  (self._key(sid), user_id, role, now, now))
        return sid

    def valid(self, sid, user_id: int, role: str) -> bool:
        if not isinstance(sid, str) or not sid:
            return False
        row = self.db.one("SELECT * FROM sessions WHERE id=?", (self._key(sid),))
        if not row or row["user_id"] != user_id:
            return False
        idle, total = SESSION_LIMITS.get(role, SESSION_LIMITS["owner"])
        now = time.time()
        if now - row["created_at"] > total or now - row["last_seen"] > idle:
            self.end(sid)
            return False
        if now - row["last_seen"] > SEEN_EVERY:
            self.db.x("UPDATE sessions SET last_seen=? WHERE id=?", (now, row["id"]))
        return True

    def end(self, sid) -> None:
        if isinstance(sid, str) and sid:
            self.db.x("DELETE FROM sessions WHERE id=?", (self._key(sid),))

    def end_for_observation(self, sid: str, observed: int) -> bool:
        return self.db.end_session_for_observation(self._key(sid), observed)

    def end_user(self, user_id: int) -> None:
        self.db.x("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def end_all(self) -> None:
        self.db.x("DELETE FROM sessions")


class Pending:
    """Short-lived server-side values (Plex PIN codes) kept out of the readable session cookie."""

    def __init__(self, ttl: int, max_items: int = 10000):
        self.ttl, self.max_items = ttl, max_items
        self.items: OrderedDict[str, tuple[float, dict]] = OrderedDict()

    def put(self, value: dict) -> str:
        now = time.time()
        while self.items and (now - next(iter(self.items.values()))[0] > self.ttl
                              or len(self.items) >= self.max_items):
            self.items.popitem(last=False)
        nonce = secrets.token_urlsafe(16)
        self.items[nonce] = (now, value)
        return nonce

    def pop(self, nonce) -> dict | None:
        if not isinstance(nonce, str):
            return None
        at, value = self.items.pop(nonce, (0.0, None))
        return value if value is not None and time.time() - at <= self.ttl else None
