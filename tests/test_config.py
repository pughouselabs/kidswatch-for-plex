"""Deployment defaults keep optional services off and security controls on."""
import os

from app.config import load


def test_standalone_defaults_keep_security_enabled(monkeypatch):
    for name in list(os.environ):
        if name.startswith("PK_"):
            monkeypatch.delenv(name)
    cfg = load()
    assert cfg.owner_code is True
    assert cfg.secure_cookies is True
    assert cfg.start_enforcer is True
    assert cfg.ha_webhook_file == ""
    assert cfg.tautulli_url == "" and cfg.tautulli_config == ""
    assert cfg.media_request_url == ""
    assert cfg.seed_parents == ()
    assert cfg.owner_name == "Admin" and cfg.tz == "UTC"
