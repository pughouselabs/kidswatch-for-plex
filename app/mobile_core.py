"""Portable mobile capability store. SQLite transactions never span network calls.

Adapter: db._connect()->SQLite row connection; secret:bytes; origin,instance_id,
edition:str; refresh(write:bool); authorize(conn,subject_id,issued_role,write);
auth_epoch(conn)->str; issuer(request,conn,write)->subject_id/issued_role/issuer_session_hash/auth_epoch/
display_name; valid_issuer(conn,pairing)->bool. All authorization hooks raise
MobileError, use current ordered observations, and run again at intent commit.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from contextlib import closing, contextmanager, nullcontext

from .mobile_protocol import ALPHABET, MobileError, code_normalize, keyed, stamp
from .security import SESSION_LIMITS


def migrate(conn):
    statements = [
        '''CREATE TABLE IF NOT EXISTS mobile_subjects(subject_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL DEFAULT 1)''',
        '''CREATE TABLE IF NOT EXISTS mobile_pairings(id TEXT PRIMARY KEY, code_hmac TEXT NOT NULL UNIQUE, subject_id TEXT NOT NULL,
          issued_role TEXT NOT NULL CHECK(issued_role IN ('parent','owner')), issuer_session_hash TEXT NOT NULL, auth_epoch TEXT NOT NULL, subject_epoch INTEGER NOT NULL,
          origin TEXT NOT NULL, instance_id TEXT NOT NULL, expires_at REAL NOT NULL, consumed_at REAL, revoked_at REAL, created_at REAL, device_id TEXT)''',
        '''CREATE TABLE IF NOT EXISTS mobile_devices(id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
          subject_id TEXT NOT NULL, issued_role TEXT NOT NULL CHECK(issued_role IN ('parent','owner')), auth_epoch TEXT NOT NULL, subject_epoch INTEGER NOT NULL,
          origin TEXT NOT NULL, instance_id TEXT NOT NULL, label TEXT NOT NULL, display_name TEXT NOT NULL,
          created_at REAL NOT NULL, last_seen_at REAL NOT NULL, expires_at REAL NOT NULL, revoked_at REAL, revision INTEGER NOT NULL DEFAULT 1)''',
        '''CREATE TABLE IF NOT EXISTS mobile_rate_buckets(scope TEXT, key_hash TEXT, window_start REAL, hits INTEGER CHECK(hits>=0), PRIMARY KEY(scope,key_hash,window_start))''',
        '''CREATE TABLE IF NOT EXISTS mobile_revisions(kind TEXT, resource_id TEXT, revision INTEGER CHECK(revision>=1), PRIMARY KEY(kind,resource_id))''',
        '''CREATE TABLE IF NOT EXISTS mobile_operations(id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES mobile_devices(id), subject_id TEXT NOT NULL,
          kind TEXT NOT NULL, resource_id TEXT, result_revision INTEGER NOT NULL, state TEXT NOT NULL CHECK(state IN ('applied','pending','failed','unknown')), created_at REAL NOT NULL,
          effect TEXT, updated_at REAL, safe_error_code TEXT)''',
        '''CREATE TABLE IF NOT EXISTS mobile_idempotency(device_id TEXT NOT NULL, key TEXT NOT NULL,
          request_hash TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL, tombstone_until REAL, status INTEGER, operation_id TEXT, resource_kind TEXT,
          resource_id TEXT, revision INTEGER, PRIMARY KEY(device_id,key))''',
        '''CREATE TABLE IF NOT EXISTS mobile_events(id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
          kind TEXT NOT NULL, resource_kind TEXT NOT NULL, resource_id TEXT NOT NULL, subject_id TEXT, child_id TEXT)''',
    ]
    for sql in statements:
        conn.execute(sql)
    conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('mobile_schema_version','1')")
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS mobile_open_subject ON mobile_pairings(subject_id) WHERE consumed_at IS NULL AND revoked_at IS NULL')
    conn.execute('CREATE INDEX IF NOT EXISTS mobile_device_subject ON mobile_devices(subject_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS mobile_device_expiry ON mobile_devices(expires_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS mobile_events_at ON mobile_events(at,subject_id)')


class MobileCore:
    def __init__(self, adapter):
        self.adapter, self.db = adapter, adapter.db
        self.pair_key = hmac.new(adapter.secret, ('kidswatch/mobile/pairing/v1:' + adapter.instance_id).encode(), hashlib.sha256).digest()
        self.rate_key = hmac.new(adapter.secret, ('kidswatch/mobile/rate/v1:' + adapter.instance_id).encode(), hashlib.sha256).digest()
        self.cursor_key = hmac.new(adapter.secret, ('kidswatch/mobile/cursor/v1:' + adapter.instance_id).encode(), hashlib.sha256).digest()

    @contextmanager
    def transaction(self):
        with self.adapter.locked(), closing(self.db._connect()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            yield conn

    @staticmethod
    def subject_epoch(conn, subject_id):
        row = conn.execute('SELECT epoch FROM mobile_subjects WHERE subject_id=?', (subject_id,)).fetchone()
        return row[0] if row else 1

    def rate(self, limits):
        """Exact sliding windows using bounded per-attempt sub-buckets, never live eviction."""
        denied = False
        with self.transaction() as conn:
            now = time.time()
            conn.execute('DELETE FROM mobile_rate_buckets WHERE window_start<=?', (now - 3600,))
            for name, limit, window in limits:
                scope = name.split(':')[0]
                bucket = keyed(self.rate_key, 'bucket', name)
                count = conn.execute('SELECT coalesce(sum(hits),0) FROM mobile_rate_buckets WHERE scope=? AND key_hash=? AND window_start>?',
                                     (scope, bucket, now-window)).fetchone()[0]
                if count >= limit:
                    denied = True
                    continue
                if scope != 'redeem-global' and conn.execute('SELECT count(*) FROM mobile_rate_buckets').fetchone()[0] >= 10000:
                    denied = True
                    continue
                conn.execute('INSERT INTO mobile_rate_buckets VALUES(?,?,?,1) ON CONFLICT(scope,key_hash,window_start) DO UPDATE SET hits=hits+1', (scope,bucket,now))
        if denied:
            raise MobileError(429, 'rate_limited', retry=max(window for _, _, window in limits))

    def issue(self, request):
        self.adapter.refresh(True)
        with self.transaction() as conn:
            issuer = self.adapter.issuer(request, conn, True)
        self.rate([('issue-subject_id:' + issuer['subject_id'], 3, 600), ('issue-session:' + issuer['issuer_session_hash'], 10, 3600)])
        code = ''.join(secrets.choice(ALPHABET) for _ in range(12))
        expires_at = time.time() + 120
        with self.transaction() as conn:
            issuer = self.adapter.issuer(request, conn, True)
            conn.execute('DELETE FROM mobile_pairings WHERE expires_at<=?', (time.time(),))
            conn.execute('UPDATE mobile_pairings SET revoked_at=? WHERE subject_id=? AND consumed_at IS NULL AND revoked_at IS NULL', (time.time(),issuer['subject_id']))
            if conn.execute('SELECT count(*) FROM mobile_pairings WHERE consumed_at IS NULL AND revoked_at IS NULL').fetchone()[0] >= 5:
                raise MobileError(429, 'rate_limited', retry=120)
            conn.execute('INSERT INTO mobile_pairings(id,code_hmac,subject_id,issued_role,issuer_session_hash,auth_epoch,subject_epoch,origin,instance_id,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (secrets.token_urlsafe(18), keyed(self.pair_key, 'code', code), issuer['subject_id'], issuer['issued_role'], issuer['issuer_session_hash'],
                          issuer['auth_epoch'], self.subject_epoch(conn, issuer['subject_id']), self.adapter.origin,
                          self.adapter.instance_id, expires_at, time.time()))
        return dict(code=code, expires_at=stamp(expires_at), qr_text=self.adapter.origin + '/#kwpair=1&code=' + code + '&instance=' + self.adapter.instance_id)

    def redeem(self, code, instance, label, client_bucket):
        # Valid/invalid attempts always count BEFORE any code lookup or remote check.
        self.rate([('redeem-client:' + client_bucket, 5, 300), ('redeem-global', 60, 60)])
        normalized = code_normalize(code)
        if normalized is None or instance != self.adapter.instance_id:
            raise MobileError(400, 'pairing_unavailable')
        digest = keyed(self.pair_key, 'code', normalized)
        self.adapter.refresh(True)
        token, device_id = secrets.token_urlsafe(32), secrets.token_urlsafe(18)
        with self.transaction() as conn:
            # Both edition and SQLite locks may have waited past the code expiry.
            now = time.time()
            row = conn.execute('SELECT * FROM mobile_pairings WHERE code_hmac=?', (digest,)).fetchone()
            if (not row or row['consumed_at'] is not None or row['revoked_at'] is not None or row['expires_at'] <= now or row['auth_epoch'] != self.adapter.epoch(conn)
                    or row['subject_epoch'] != self.subject_epoch(conn, row['subject_id'])
                    or row['origin'] != self.adapter.origin or row['instance_id'] != instance
                    or not self.adapter.valid_issuer(conn, row)):
                raise MobileError(400, 'pairing_unavailable')
            try:
                display_name = self.adapter.authorize(conn, row['subject_id'], row['issued_role'], True)
            except MobileError as exc:
                if exc.status == 503:
                    raise
                raise MobileError(400, 'pairing_unavailable') from None
            now = time.time()  # recheck immediately at conditional consume
            if conn.execute('UPDATE mobile_pairings SET consumed_at=?,device_id=? WHERE code_hmac=? AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at>?', (now,device_id,digest,now)).rowcount != 1:
                raise MobileError(400, 'pairing_unavailable')
            idle, total = SESSION_LIMITS[row['issued_role']]
            conn.execute('INSERT INTO mobile_devices(id,token_hash,subject_id,issued_role,auth_epoch,subject_epoch,origin,instance_id,label,display_name,created_at,last_seen_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (device_id, hashlib.sha256(token.encode()).hexdigest(), row['subject_id'], row['issued_role'], row['auth_epoch'], row['subject_epoch'], self.adapter.origin, instance, label, display_name, now, now, now + total))
            self.audit(conn, row['subject_id'], 'phone_connected', device=device_id)
            device = conn.execute('SELECT * FROM mobile_devices WHERE id=?', (device_id,)).fetchone()
        return token, dict(device)

    def authenticate(self, conn, token, write=False, touch=True):
        now = time.time()
        conn.execute('DELETE FROM mobile_pairings WHERE expires_at<=?',(now,))
        conn.execute('DELETE FROM mobile_events WHERE at<?',(now-7*86400,))
        conn.execute('DELETE FROM mobile_idempotency WHERE tombstone_until<?',(now,))
        conn.execute("DELETE FROM mobile_operations WHERE state IN ('applied','failed') AND created_at<? AND id NOT IN (SELECT operation_id FROM mobile_idempotency)",(now-8*86400,))
        if not isinstance(token, str) or len(token) != 43:
            raise MobileError(401, 'reconnect_required')
        row = conn.execute('SELECT * FROM mobile_devices WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if (not row or row['revoked_at'] is not None or now >= row['expires_at'] or now - row['last_seen_at'] >= SESSION_LIMITS[row['issued_role']][0]
                or row['auth_epoch'] != self.adapter.epoch(conn) or row['subject_epoch'] != self.subject_epoch(conn, row['subject_id'])
                or row['origin'] != self.adapter.origin or row['instance_id'] != self.adapter.instance_id):
            raise MobileError(401, 'reconnect_required')
        display = self.adapter.authorize(conn, row['subject_id'], row['issued_role'], write)
        if touch and now - row['last_seen_at'] >= 60:
            conn.execute('UPDATE mobile_devices SET last_seen_at=? WHERE id=?', (now, row['id']))
            row = conn.execute('SELECT * FROM mobile_devices WHERE id=?', (row['id'],)).fetchone()
        return dict(row) | {'display_name': display}

    def audit(self, conn, subject_id, action, **detail):
        # Only deliberate safe IDs; callers never pass codes/tokens/request bodies.
        self.adapter.audit(conn, subject_id, action, **detail)

    def device(self, row, current):
        return dict(id=row['id'], revision=row['revision'], label=row['label'], subject_display=row['display_name'],
                    issued_role=row['issued_role'], created_at=stamp(row['created_at']), last_seen_at=stamp(row['last_seen_at']),
                    expires_at=stamp(row['expires_at']), revoked_at=stamp(row['revoked_at']) if row['revoked_at'] is not None else None,
                    is_current=row['id'] == current)

    def cursor(self, subject_id, scope, offset):
        raw = json.dumps([subject_id, scope, offset, int(time.time())], separators=(',', ':')).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip('=') + '.' + hmac.new(self.cursor_key, raw, hashlib.sha256).hexdigest()

    def decode_cursor(self, cursor, subject_id, scope):
        try:
            if len(cursor) > 2048:
                raise ValueError()
            value, signature = cursor.split('.')
            raw = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
            if not hmac.compare_digest(signature, hmac.new(self.cursor_key, raw, hashlib.sha256).hexdigest()):
                raise ValueError()
            owner, filter_value, offset, at = json.loads(raw)
            if owner != subject_id or filter_value != scope or time.time() - at > 900 or type(offset) is not int or offset < 0:
                raise ValueError()
            return offset
        except (ValueError, TypeError, UnicodeError):
            raise MobileError(422, 'invalid_fields', {'cursor': 'Refresh this list.'}) from None

    def replay(self, conn, actor, key, intent):
        now = time.time()
        conn.execute('DELETE FROM mobile_idempotency WHERE created_at<?', (now - 8 * 86400,))
        row = conn.execute('SELECT * FROM mobile_idempotency WHERE device_id=? AND key=?', (actor['id'], key)).fetchone()
        if not row:
            return None
        if now - row['created_at'] > 86400:
            raise MobileError(409, 'idempotency_expired')
        if row['request_hash'] != intent:
            raise MobileError(409, 'idempotency_conflict')
        if row['status'] is None:
            raise MobileError(409, 'operation_in_progress', retry=2)
        return row
