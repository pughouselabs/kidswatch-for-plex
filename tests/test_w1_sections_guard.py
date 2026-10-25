"""Empty or damaged library policies never reach Plex as an empty share update."""
import json
import logging
from contextlib import closing

import httpx
import pytest

from app import rules, setup
from app.plex import PlexError
from app.service import NO_SECTIONS_ERROR, UNSAFE_SHARE_ERROR
from tests.fakes import KID
from tests.test_app import active_kid, client, connect, env, login
from tests.test_plex_client import client as plex_client


@pytest.fixture
def connected(env):
    app, plex = env
    parent, kid_id = active_kid(app, plex)
    app.state.db.update_kid(kid_id, sections="1")
    assert app.state.svc.sync_titles(kid_id)
    plex.calls.clear()
    return app, plex, parent, kid_id


def assert_no_empty_shares(plex):
    assert not [call for call in plex.calls if call[0] in ("update_share", "invite") and not call[2]]


@pytest.mark.parametrize("entrypoint", ["apply", "periodic"])
@pytest.mark.parametrize("policy", ['["2"]', 'invalid-json', '[]', '{}', 'null', '[1]', '[""]', '["1", null]'])
def test_empty_effective_sections_retract_instead_of_updating(connected, entrypoint, policy, caplog):
    app, plex, _, kid_id = connected
    db, svc = app.state.db, app.state.svc
    db.set_setting(setup.KID_SECTIONS, policy)
    assert svc.kid_sections(db.kid(kid_id)) == []
    if policy != '["2"]':
        assert svc.library_sections() == ()  # never expand back to environment defaults
    with caplog.at_level(logging.WARNING, logger="kidswatch.service"):
        if entrypoint == "apply":
            assert not svc.apply(kid_id, "owner")
        else:
            svc.periodic()
        svc.periodic()
        svc.periodic()
    assert_no_empty_shares(plex)
    assert KID.id not in plex.share_map
    assert not plex.labeled(rules.ok_label(kid_id))
    kid = db.kid(kid_id)
    assert kid["sync_state"] == "error" and kid["sync_error"] == NO_SECTIONS_ERROR
    assert not kid["plex_accepted"]
    assert len([r for r in caplog.records if "has no permitted libraries" in r.message]) == 1
    assert len(db.q("SELECT 1 FROM audit WHERE action='library_access_blocked'")) == 1
    assert not db.q("SELECT 1 FROM audit WHERE action='share_drift'")


def test_nonempty_narrowed_policy_still_updates_share_and_labels(connected):
    app, plex, _, kid_id = connected
    app.state.db.set_setting(setup.KID_SECTIONS, '["1"]')
    app.state.svc.periodic()
    assert plex.share_map[KID.id].sections == ["1"]
    assert plex.labeled(rules.ok_label(kid_id)) == [11, 14]
    assert app.state.db.kid(kid_id)["sync_state"] == "ok"
    assert_no_empty_shares(plex)


def test_failed_retraction_is_durable_and_retried_even_after_policy_repair(connected):
    app, plex, parent, kid_id = connected
    db, svc = app.state.db, app.state.svc
    db.set_setting(setup.KID_SECTIONS, "corrupt")
    plex.fail_remove_share = True
    assert not svc.apply(kid_id, "owner")
    assert db.kid(kid_id)["sync_error"].startswith(UNSAFE_SHARE_ERROR)
    assert "Remove this account's server access in Plex now" in db.kid(kid_id)["sync_error"]
    svc.periodic()
    assert KID.id in plex.share_map
    # The durable unsafe-share marker must still force retraction after repair or restart.
    db.set_setting(setup.KID_SECTIONS, '["1"]')
    plex.fail_remove_share = False
    svc.periodic()
    assert KID.id not in plex.share_map
    assert db.kid(kid_id)["sync_error"] == NO_SECTIONS_ERROR
    with closing(db._connect()) as conn:
        assert "reconnect" in setup.library_policy_warning(conn, svc.cfg.kid_sections)
    assert connect(app, plex, parent, kid_id)[0].status_code == 200
    assert plex.share_map[KID.id].sections == ["1"]
    assert db.kid(kid_id)["sync_state"] == "ok"
    with closing(db._connect()) as conn:
        assert setup.library_policy_warning(conn, svc.cfg.kid_sections) is None
    assert_no_empty_shares(plex)


def test_new_share_readback_with_empty_sections_retracts(connected):
    app, plex, _, kid_id = connected
    assert not app.state.svc.check_new_share(KID.id, [], rules.build_filters(kid_id))
    assert KID.id not in plex.share_map
    assert_no_empty_shares(plex)


def test_empty_new_share_readback_failure_does_not_report_success(connected, monkeypatch):
    app, plex, _, kid_id = connected

    def unavailable(*args, **kwargs):
        raise PlexError("Plex unavailable")

    monkeypatch.setattr(plex, "shares", unavailable)
    with pytest.raises(PlexError):
        app.state.svc.check_new_share(KID.id, [], rules.build_filters(kid_id))
    assert_no_empty_shares(plex)


@pytest.mark.parametrize("policy", ['["2"]', 'corrupt'])
def test_owner_sees_library_warning_before_keeper_runs(connected, policy):
    app, plex, _, kid_id = connected
    db = app.state.db
    db.set_setting(setup.KID_SECTIONS, policy)
    db.set_setting("setup.completed_at", "2026-10-24T00:00:00+00:00")
    owner, _ = login(app, plex, "owner")
    for path in ("/admin", "/setup/libraries", "/setup/ready"):
        response = owner.get(path)
        assert response.status_code == 200
        assert "Library access needs attention" in response.text
        assert 'role="alert"' in response.text
        assert "Review" in response.text
    ready = client(app).get("/readyz")
    assert ready.status_code == 503 and ready.json()["libraries"] == "invalid"
    assert db.kid(kid_id)["name"] not in json.dumps(ready.json())
    assert "Library access needs attention" not in client(app).get("/setup").text


def test_missing_saved_policy_uses_only_configured_defaults(env):
    app, _ = env
    with closing(app.state.db._connect()) as conn:
        assert setup.effective_kid_sections(conn, ("2",)) == ("2",)
        assert setup.library_policy_warning(conn, ("2",)) is None


def test_setup_explains_when_library_changes_apply(connected):
    app, plex, _, _ = connected
    owner, _ = login(app, plex, "owner")
    assert "update within a few minutes" in owner.get("/setup/libraries").text


@pytest.mark.parametrize("operation", ["invite", "update_share"])
@pytest.mark.parametrize("keys,mapping", [([], {"1": 101}), (["deleted"], {"1": 101}), (["1"], {})])
def test_real_adapter_never_sends_empty_mapped_library_ids(tmp_path, operation, keys, mapping):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"  # discovering libraries is safe; sharing must never reach transport
        sections = "".join(f'<Section key="{key}" id="{value}"/>' for key, value in mapping.items())
        return httpx.Response(200, text=f"<MediaContainer><Server>{sections}</Server></MediaContainer>")

    plex = plex_client(tmp_path, handler)
    target = "child@example.com" if operation == "invite" else 44
    with pytest.raises(PlexError, match="No permitted libraries"):
        getattr(plex, operation)(target, keys, rules.build_filters(1))
    assert len(requests) == 1


@pytest.mark.parametrize("operation", ["invite", "update_share"])
def test_real_adapter_preserves_nonempty_mapped_library_ids(tmp_path, operation):
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, text='<MediaContainer><Server><Section key="1" id="101"/></Server></MediaContainer>')
        return httpx.Response(201, json={})

    plex = plex_client(tmp_path, handler)
    target = "child@example.com" if operation == "invite" else 44
    getattr(plex, operation)(target, ["1"], rules.build_filters(1))
    assert requests[-1].method == "POST"
    body = json.loads(requests[-1].content)
    assert (body["shared_server"]["library_section_ids"] if operation == "invite"
            else body["librarySectionIds"]) == [101]
