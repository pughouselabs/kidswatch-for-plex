"""Liveness never stands in for a fresh Plex check or a running keeper."""
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import load
from app.main import create_app
from app.plex import PlexClient


@pytest.fixture
def probe(tmp_path, monkeypatch):
    prefs = tmp_path / "Preferences.xml"
    prefs.write_text('<Preferences PlexOnlineToken="fake-install-token" MachineIdentifier="fake-machine"/>')
    monkeypatch.setenv("PK_DB", str(tmp_path / "install.db"))
    monkeypatch.setenv("PK_PLEX_PREFS", str(prefs))
    monkeypatch.setenv("PK_PLEX_URL", "http://plex.invalid:32400")
    monkeypatch.setenv("PK_KID_SECTIONS", "1,2")
    monkeypatch.setenv("PK_START_ENFORCER", "1")
    monkeypatch.setenv("PK_POLL_SECONDS", "20")
    replies, requests = [], []

    def handler(request):
        requests.append(request)
        assert request.url.path == "/library/sections"
        assert request.headers["X-Plex-Token"] == "fake-install-token"
        assert "fake-install-token" not in str(request.url)
        reply = replies.pop(0) if replies else httpx.Response(200, json={"MediaContainer": {"Directory": [
            {"key": "1", "type": "movie"}, {"key": "2", "type": "show"}]}})
        if isinstance(reply, Exception):
            raise reply
        return reply

    http = httpx.Client(transport=httpx.MockTransport(handler))
    plex = PlexClient(load(), "install-test", http=http)
    app = create_app(load(), plex=plex)
    keeper = app.state.enforcer
    # Represent a running keeper without starting a background worker in deterministic probes.
    keeper._thread = threading.current_thread()
    keeper.last_ok = time.time()
    monkeypatch.setattr(keeper, "start", lambda: None)
    with TestClient(app) as client:
        yield app, client, replies, requests
    http.close()


def test_readiness_checks_plex_even_with_no_children(probe):
    app, client, replies, requests = probe
    assert not app.state.db.q("SELECT * FROM kids")
    replies.append(httpx.Response(401, text="private upstream error"))
    assert client.get("/healthz").status_code == 200
    assert not requests  # Docker liveness deliberately does not contact Plex.
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["plex"] == "unavailable"
    assert response.headers["cache-control"] == "no-store"
    assert "private upstream error" not in response.text
    assert "fake-install-token" not in response.text
    assert "plex.invalid" not in response.text


def test_readiness_uses_fresh_authenticated_requests(probe):
    app, client, replies, requests = probe
    assert client.get("/readyz").json() == {"ok": True, "plex": "ok", "libraries": "ok", "keeper": "ok"}
    replies.append(httpx.ConnectError("private token and server address"))
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["plex"] == "unavailable"
    assert "private token" not in response.text
    assert len(requests) == 2
    assert requests[-1].extensions["timeout"]["read"] == 4


@pytest.mark.parametrize("directory", [[], [{"key": "1", "type": "movie"}],
                                      [{"key": "1", "type": "artist"}, {"key": "2", "type": "show"}]])
def test_readiness_rejects_missing_or_non_video_libraries(probe, directory):
    app, client, replies, requests = probe
    replies.append(httpx.Response(200, json={"MediaContainer": {"Directory": directory}}))
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["plex"] == "ok"
    assert response.json()["libraries"] == "invalid"


@pytest.mark.parametrize("state", ["starting", "stale", "error", "stopped"])
def test_readiness_rejects_unhealthy_keeper(probe, state):
    app, client, replies, requests = probe
    keeper = app.state.enforcer
    if state == "starting":
        keeper.last_ok = None
    elif state == "stale":
        keeper.last_ok = time.time() - 1000
    elif state == "error":
        keeper.last_error = "private error details"
    else:
        keeper._thread = threading.Thread()
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["keeper"] == ("error" if state == "error" else "starting-or-stale")
    assert "private error details" not in response.text


def test_disabled_keeper_stays_live_but_not_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("PK_DB", str(tmp_path / "disabled.db"))
    monkeypatch.setenv("PK_START_ENFORCER", "0")
    from .fakes import FakePlex

    plex = FakePlex()
    plex.readiness_sections = lambda: {"1": "movie", "2": "show"}
    with TestClient(create_app(load(), plex=plex)) as client:
        assert client.get("/healthz").json() == {"ok": True, "keeper": "disabled"}
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json()["keeper"] == "disabled"


def test_malformed_plex_response_is_not_ready(probe):
    app, client, replies, requests = probe
    replies.append(httpx.Response(200, text="not JSON"))
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["plex"] == "unavailable"
