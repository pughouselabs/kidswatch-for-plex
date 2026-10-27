"""Disposable fake adapter exercises the portable transaction/security boundary."""
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.db import DB
from app.mobile_core import MobileCore, migrate
from app.mobile_protocol import MobileError, canonical_origin, strict_json


class Adapter:
    secret = b'fictional-test-only-owner-secret'
    origin = 'https://server.example'
    instance_id = 'instance_demo'
    edition = 'plex'

    def __init__(self, db):
        self.db = db
        from contextlib import closing
        with closing(db._connect()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            migrate(conn)
        self.allowed = True
        self.down = False

    locked = staticmethod(nullcontext)

    def refresh(self, write):
        if self.down:
            raise MobileError(503, 'authorization_unavailable')

    def authorize(self, conn, subject, role, write):
        if not self.allowed:
            raise MobileError(403, 'access_removed')
        return 'Alex'

    epoch = staticmethod(lambda conn: '1')
    valid_issuer = staticmethod(lambda conn, row: True)

    def issuer(self, request, conn, write):
        return dict(subject_id='101', issued_role='parent',issuer_session_hash='fake-session-hash',auth_epoch='1',display_name='Alex')

    @staticmethod
    def audit(conn, subject, action, **detail):
        conn.execute('INSERT INTO audit(at,actor,action,detail) VALUES(?,?,?,?)', ('2026-10-26T18:00:00Z',subject,action,json.dumps(detail)))


@pytest.fixture
def core(tmp_path):
    return MobileCore(Adapter(DB(str(tmp_path/'mobile.db'))))


def test_atomic_two_worker_redemption(core):
    pairing = core.issue(None)
    def redeem(_):
        other=MobileCore(Adapter(DB(core.db.path)))
        try:
            return other.redeem(pairing['code'],core.adapter.instance_id,'My phone','192.0.2.1')[0]
        except MobileError as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(redeem,range(2)))
    assert results.count('pairing_unavailable') == 1
    assert core.db.one('SELECT count(*) FROM mobile_devices')[0] == 1
    assert core.db.one("SELECT count(*) FROM audit WHERE action='phone_connected'")[0] == 1
    stored=str([dict(r) for r in core.db.q('SELECT * FROM mobile_devices')])+str([dict(r) for r in core.db.q('SELECT * FROM mobile_pairings')])
    assert pairing['code'] not in stored
    assert all(token not in stored for token in results if token!='pairing_unavailable')


def test_expiry_revocation_replay_and_outage(core):
    pairing=core.issue(None)
    core.adapter.down=True
    with pytest.raises(MobileError) as error:
        core.redeem(pairing['code'],core.adapter.instance_id,'Phone','192.0.2.1')
    assert error.value.status == 503
    assert core.db.one('SELECT consumed_at FROM mobile_pairings')[0] is None
    core.adapter.down=False
    core.db.x('UPDATE mobile_pairings SET expires_at=0')
    with pytest.raises(MobileError) as error:
        core.redeem(pairing['code'],core.adapter.instance_id,'Phone','192.0.2.1')
    assert error.value.code=='pairing_unavailable'


def test_limits_survive_restart_and_count_invalid(core):
    for _ in range(5):
        with pytest.raises(MobileError) as error:
            MobileCore(Adapter(DB(core.db.path))).redeem('bad',core.adapter.instance_id,'Phone','192.0.2.1')
        assert error.value.status==400
    with pytest.raises(MobileError) as error:
        core.redeem('bad',core.adapter.instance_id,'Phone','192.0.2.1')
    assert error.value.status==429


@pytest.mark.parametrize('raw',[b'{"x":1e999}',b'{"x":[{"y":-1e999}]}',b'{"x":NaN}',b'{"x":1,"x":2}',b'[]'])
def test_json_rejects_nonfinite_duplicates_and_nonobject(raw):
    with pytest.raises(MobileError):
        strict_json(raw)


@pytest.mark.parametrize('url',['https://server.example:0','https://server.example\n','https://user@server.example','https://server.example/path','https://server.example/#x'])
def test_origin_rejects_ambiguous_authority(url):
    with pytest.raises(ValueError):
        canonical_origin(url)


def test_code_expires_while_waiting_for_sqlite_writer(core, monkeypatch):
    import sqlite3
    import threading
    from contextlib import contextmanager
    import app.mobile_core as module

    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(module, 'time', SimpleNamespace(time=lambda: clock.now))
    pairing = core.issue(None)
    assert core.db.one('SELECT expires_at FROM mobile_pairings')[0] == 1120
    refreshed, continue_refresh, waiting_for_transaction = (threading.Event() for _ in range(3))
    def refresh(write):
        refreshed.set()
        assert continue_refresh.wait(5)
    core.adapter.refresh = refresh
    @contextmanager
    def locked():
        if continue_refresh.is_set():
            waiting_for_transaction.set()
        yield
    core.adapter.locked = locked
    def redeem():
        try:
            core.redeem(pairing['code'],core.adapter.instance_id,'Phone','192.0.2.7')
            return 'accepted'
        except MobileError as exc:
            return exc.code
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(redeem)
        assert refreshed.wait(5)  # rate accounting finished before the competing writer
        with sqlite3.connect(core.db.path) as writer:
            writer.execute('BEGIN IMMEDIATE')
            continue_refresh.set()
            assert waiting_for_transaction.wait(5)
            clock.now = 1121.0
            writer.commit()
        assert future.result(timeout=5) == 'pairing_unavailable'
    assert core.db.one('SELECT count(*) FROM mobile_devices')[0] == 0
    assert core.db.one("SELECT count(*) FROM audit WHERE action='phone_connected'")[0] == 0
    assert core.db.one('SELECT consumed_at FROM mobile_pairings')[0] is None


def test_code_normalization_rejects_unicode_before_casefold():
    from app.mobile_protocol import code_normalize
    assert code_normalize('ABCD EFGH JKMS')=='ABCDEFGHJKMS'
    assert code_normalize('abcd-efgh-jkms')=='ABCDEFGHJKMS'
    assert code_normalize('ABCDEFGHJKMſ') is None
