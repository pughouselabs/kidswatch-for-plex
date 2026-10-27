"""Startup compatibility and portable database constraints on synthetic databases."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from app.db import DB, SCHEMA
from app.mobile_plex import migrate


def test_additive_migration_twice_and_two_workers(tmp_path):
    path=str(tmp_path/'upgrade.db')
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO settings VALUES('client_id','fictional-existing')")
        conn.execute("INSERT INTO parents(plex_id,username,enabled,created_at) VALUES(101,'alex',1,'2026-10-26T18:00:00Z')")
        conn.execute("INSERT INTO kids(parent_id,name,status,created_at,updated_at) VALUES(101,'River','draft','2026-10-26T18:00:00Z','2026-10-26T18:00:00Z')")
    def start(_):
        return DB(path)
    # First establish WAL (SQLite PRAGMA mode changes themselves cannot run together).
    DB(path)
    with ThreadPoolExecutor(2) as pool:
        dbs=list(pool.map(start,range(2)))
    db=dbs[0]
    with db._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        migrate(conn)
        migrate(conn)
    assert db.one('SELECT name,status FROM kids')[0]=='River'
    assert db.setting('mobile_schema_version')=='1'
    assert db.one("SELECT revision FROM mobile_revisions WHERE kind='child' AND resource_id='1'")[0]==1
    assert db.one('PRAGMA integrity_check')[0]=='ok'
    tables={r[0] for r in db.q("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'mobile_pairings','mobile_devices','mobile_rate_buckets','mobile_revisions','mobile_operations','mobile_idempotency','mobile_events'}<=tables
