"""Settings, read once from environment variables."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .security import Network, parse_networks

DEFAULT_KID_SECTIONS = ("1", "2")


@dataclass(frozen=True)
class Config:
    public_url: str
    plex_prefs: str
    plex_url: str
    tautulli_url: str
    tautulli_config: str
    db_path: str
    tz: str
    ha_webhook_file: str
    poll_seconds: int
    seed_parents: tuple[str, ...]
    kid_sections: tuple[str, ...]
    secure_cookies: bool
    owner_name: str
    start_enforcer: bool = True
    owner_code: bool = True
    media_request_url: str = ""
    trusted_proxies: tuple[Network, ...] = ()
    trust_cf_headers: bool = False
    mobile_enabled: bool = False
    mobile_lan_origin: str = ""
    app_name: str = ""
    footer_text: str = ""
    landing_art_dir: str = ""


def load() -> Config:
    env = os.environ.get
    return Config(
        public_url=env("PK_PUBLIC_URL", "http://localhost:8080").rstrip("/"),
        plex_prefs=env("PK_PLEX_PREFS", "/plex-prefs/Preferences.xml"),
        plex_url=env("PK_PLEX_URL", "http://plex-host:32400").rstrip("/"),
        tautulli_url=env("PK_TAUTULLI_URL", "").rstrip("/"),
        tautulli_config=env("PK_TAUTULLI_CONFIG", ""),
        db_path=env("PK_DB", "/data/plexkids.db"),
        tz=env("PK_TZ", "UTC"),
        ha_webhook_file=env("PK_HA_WEBHOOK_FILE", ""),
        poll_seconds=_poll_seconds(env("PK_POLL_SECONDS", "20")),
        seed_parents=tuple(u.strip().lower() for u in env("PK_SEED_PARENTS", "").split(",") if u.strip()),
        # Local library keys a child may be given. Check these on your Plex server.
        # An empty value falls back to the default instead of giving children no libraries.
        kid_sections=tuple(k.strip() for k in env("PK_KID_SECTIONS", "").split(",") if k.strip())
        or DEFAULT_KID_SECTIONS,
        secure_cookies=_switch("PK_SECURE_COOKIES", True),
        owner_name=env("PK_OWNER_NAME", "Admin"),
        start_enforcer=_switch("PK_START_ENFORCER", True),
        # Admin sign-in also needs a one-time code pushed to the owner's phone.
        owner_code=_switch("PK_OWNER_CODE", True),
        # Your optional request site. Empty: parents ask through Requests instead.
        media_request_url=_web_url(env("PK_MEDIA_REQUEST_URL", "")),
        # Reverse proxies whose X-Forwarded-For is believed. Empty: use the direct connection's address.
        trusted_proxies=parse_networks(env("PK_TRUSTED_PROXIES", "")),
        trust_cf_headers=_switch("PK_TRUST_CF_HEADERS", False),
        mobile_enabled=_switch("PK_MOBILE_ENABLED", False),
        mobile_lan_origin=env("PK_MOBILE_LAN_ORIGIN", ""),
        app_name=env("PK_APP_NAME", "").strip()[:80],
        footer_text=env("PK_FOOTER_TEXT", "").strip()[:240],
        landing_art_dir=env("PK_LANDING_ART_DIR", "").strip(),
    )


def _switch(name: str, default: bool) -> bool:
    """1 or 0 (true/false, yes/no and on/off work too). Empty keeps the default, so a blank line never
    turns a security control off. Anything else stops startup."""
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be 1 (on) or 0 (off)")


def _poll_seconds(value: str) -> int:
    message = "PK_POLL_SECONDS must be a positive whole number of seconds (1 or greater)"
    try:
        if not re.fullmatch(r"[0-9]+", value.strip()) or int(value) < 1:
            raise ValueError(message)
        return int(value)
    except ValueError:
        raise ValueError(message) from None


def _web_url(url: str) -> str:
    url = url.strip().rstrip("/")
    return url if url.startswith(("https://", "http://")) else ""
