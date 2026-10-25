"""Stale share tracking must not revoke another child's Plex access."""
from dataclasses import replace

import pytest

from app.db import DB
from app.service import Service

from .fakes import KID
from .test_app import add_kid, env, login  # noqa: F401


def test_retracted_unsafe_share_can_be_reused_by_another_child(env):
    app, fake = env
    owner, _ = login(app, fake, "owner")
    db, svc = app.state.db, app.state.svc
    old_id = add_kid(app, fake, owner, name="Old child")
    fake.drop_filters = fake.fail_filters = True
    ok, text, _ = svc.connect(old_id, "tok-kid1")
    assert not ok and "Nothing was shared" in text
    assert not fake.share_map and db.kid(old_id)["plex_id"] is None
    assert not db.q("SELECT 1 FROM tracked_shares WHERE kid_id=?", (old_id,))

    fake.drop_filters = fake.fail_filters = False
    new_id = add_kid(app, fake, owner, name="New child")
    assert svc.connect(new_id, "tok-kid1")[0]
    share = fake.share_map[KID.id]
    assert share.accepted and db.kid(new_id)["status"] == "active"
    fake.calls.clear()
    svc.remove(old_id, "test")
    assert fake.share_map[KID.id] == share
    assert db.kid(new_id)["plex_id"] == KID.id
    assert db.kid(new_id)["status"] == "active"
    assert "remove_share" not in fake.names()


@pytest.mark.parametrize("status", ["draft", "requested", "active", "declined"])
@pytest.mark.parametrize("own_account", [None, 202])
def test_stale_tracking_skips_accounts_owned_by_non_removed_children(env, status, own_account):
    app, fake = env
    owner, _ = login(app, fake, "owner")
    db, svc = app.state.db, app.state.svc
    old_id = add_kid(app, fake, owner, name="Old child")
    new_id = add_kid(app, fake, owner, name="New child")
    assert svc.connect(new_id, "tok-kid1")[0]
    share = fake.share_map[KID.id]
    db.update_kid(new_id, status=status)
    # Simulate a ledger left by an older version, optionally beside a new account.
    db.x("INSERT INTO tracked_shares(kid_id,plex_id) VALUES(?,?)", (old_id, KID.id))
    if own_account:
        db.update_kid(old_id, plex_id=own_account)
        fake.share_map[own_account] = replace(share, share_id=702, user_id=own_account)
    fake.calls.clear()

    # A fresh worker must protect ownership using the database, not local state.
    Service(DB(db.path), fake, svc.cfg).remove(old_id, "test")
    assert fake.share_map == {KID.id: share}
    assert db.kid(new_id)["status"] == status
    assert db.kid(new_id)["plex_id"] == KID.id
    assert not db.q("SELECT 1 FROM tracked_shares WHERE kid_id=?", (old_id,))
    assert db.one("SELECT 1 FROM tracked_shares WHERE kid_id=? AND plex_id=?", (new_id, KID.id))
    assert [c for c in fake.calls if c[0] == "remove_share"] == (
        [("remove_share", 702)] if own_account else [])
