"""Plex edition adapter for the portable mobile capability store."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

from . import rules
from .db import SHARED_TRUST_MAX, now_iso
from .invitations import Invitations, InvitationError, token_hash
from .mobile_core import migrate as core_migrate
from .mobile_protocol import MobileError, fields, identifier, stamp, string
from .security import SESSION_LIMITS
from .setup import effective_kid_sections


def migrate(conn):
    core_migrate(conn)
    for table in ('kids', 'requests', 'parent_invitations'):
        if 'revision' not in {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN revision INTEGER NOT NULL DEFAULT 1')
    child_fields = ('parent_id,name,status,plex_id,age_level,mode,sections,weekday_minutes,weekend_minutes,'
                    'weekday_start,weekday_end,weekend_start,weekend_end,paused,paused_until,bonus_day,bonus_minutes,'
                    'later_day,later_minutes,friday_weekend,sunday_school,break_until,block_online')
    for table, cols in [('kids', child_fields), ('requests', 'parent_id,kid_id,kind,message,status,owner_note,resolved_at'),
                        ('parent_invitations', 'intended_plex_id,expires_at,revoked_at,claimed_at,claimed_by')]:
        condition = ' OR '.join(f'NEW.{c} IS NOT OLD.{c}' for c in cols.split(','))
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS mobile_revision_{table} AFTER UPDATE OF {cols} ON {table}
            WHEN NEW.revision=OLD.revision AND ({condition}) BEGIN
            UPDATE {table} SET revision=revision+1 WHERE id=NEW.id; END''')
    for table, column in [('kid_titles', 'kid_id'), ('kid_parents', 'kid_id')]:
        for action in ('INSERT', 'UPDATE', 'DELETE'):
            ref = 'OLD' if action == 'DELETE' else 'NEW'
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS mobile_revision_{table}_{action} AFTER {action} ON {table}
                BEGIN UPDATE kids SET revision=revision+1 WHERE id={ref}.{column}; END''')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS mobile_disable AFTER UPDATE OF enabled ON parents
        WHEN NEW.enabled<>OLD.enabled BEGIN
        INSERT INTO mobile_subjects(subject_id,epoch) VALUES(CAST(NEW.plex_id AS TEXT),2)
          ON CONFLICT(subject_id) DO UPDATE SET epoch=epoch+1;
        UPDATE mobile_devices SET revoked_at=strftime('%s','now'),revision=revision+1 WHERE subject_id=CAST(NEW.plex_id AS TEXT) AND revoked_at IS NULL;
        UPDATE mobile_pairings SET revoked_at=strftime('%s','now') WHERE subject_id=CAST(NEW.plex_id AS TEXT);
        UPDATE kids SET revision=revision+1 WHERE parent_id=NEW.plex_id OR id IN (SELECT kid_id FROM kid_parents WHERE parent_id=NEW.plex_id);
        END''')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS mobile_epoch AFTER UPDATE OF value ON settings
        WHEN NEW.key='session_epoch' AND NEW.value<>OLD.value BEGIN
        UPDATE mobile_devices SET revoked_at=strftime('%s','now'),revision=revision+1 WHERE revoked_at IS NULL;
        UPDATE mobile_pairings SET revoked_at=strftime('%s','now'); END''')
    for table, kind in [('kids','child_changed'), ('requests','request_changed'), ('parent_invitations','access_changed')]:
        child = 'CAST(NEW.id AS TEXT)' if table == 'kids' else ('CAST(NEW.kid_id AS TEXT)' if table == 'requests' else 'NULL')
        subject = 'NULL' if table == 'kids' else ('CAST(NEW.parent_id AS TEXT)' if table == 'requests' else 'NULL')
        for action in ('INSERT', 'UPDATE'):
            when = 'WHEN NEW.revision<>OLD.revision' if action == 'UPDATE' else ''
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS mobile_event_{table}_{action} AFTER {action} ON {table} {when}
                BEGIN INSERT INTO mobile_events(at,kind,resource_kind,resource_id,subject_id,child_id)
                VALUES(strftime('%s','now'),'{kind}','{table}',CAST(NEW.id AS TEXT),{subject},{child}); END''')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS mobile_libraries AFTER UPDATE OF value ON settings
        WHEN NEW.key='setup.kid_sections' AND NEW.value<>OLD.value BEGIN UPDATE kids SET revision=revision+1; END''')


class PlexAdapter:
    edition = 'plex'

    def __init__(self, app, cfg, origin):
        self.app, self.cfg = app, cfg
        self.db, self.svc, self.plex = app.state.db, app.state.svc, app.state.plex
        self.secret = self.plex.owner_token().encode()
        self.origin = origin
        self.instance_id = self.db.setting('mobile_instance_id')
        if not self.instance_id:
            self.db.x("INSERT OR IGNORE INTO settings(key,value) VALUES('mobile_instance_id',?)", (secrets.token_urlsafe(24),))
            self.instance_id = self.db.setting('mobile_instance_id')

    @staticmethod
    def locked():
        from contextlib import nullcontext
        return nullcontext()

    @staticmethod
    def audit(conn, subject, action, **detail):
        conn.execute('INSERT INTO audit(at,actor,action,detail) VALUES(?,?,?,?)', (stamp(),subject,action,json.dumps(detail)))

    def refresh(self, write):
        snapshot = self.db.shared_snapshot()
        max_age = 30 if write else 600
        if snapshot['user_ids'] is None or time.time() - snapshot['observed_at'] > max_age:
            if not self.svc.refresh_shared() and (write or time.time() - snapshot['observed_at'] > SHARED_TRUST_MAX):
                raise MobileError(503, 'authorization_unavailable', retry=30)
        # Reading owner() uses the established verified runtime identity, never a caller ID.
        self.plex.owner()

    @staticmethod
    def epoch(conn):
        row = conn.execute("SELECT value FROM settings WHERE key='session_epoch'").fetchone()
        return row[0] if row else '1'

    def authorize(self, conn, subject, role, write):
        owner = self.plex.owner()
        if role == 'owner':
            if subject != str(owner.id):
                raise MobileError(403, 'access_removed')
            return owner.title or owner.username
        if role != 'parent' or subject == str(owner.id):
            raise MobileError(403, 'access_removed')
        row = conn.execute('SELECT * FROM parents WHERE CAST(plex_id AS TEXT)=?', (subject,)).fetchone()
        if not row or not row['enabled']:
            raise MobileError(403, 'access_removed')
        self.shared(conn, subject, write)
        return row['title'] or row['username']

    @staticmethod
    def shared(conn, subject, write):
        snapshot = conn.execute('SELECT * FROM sharing_state WHERE id=1').fetchone()
        if snapshot['user_ids'] is None or time.time() - snapshot['observed_at'] > (30 if write else SHARED_TRUST_MAX):
            raise MobileError(503, 'authorization_unavailable', retry=30)
        if subject not in {str(n) for n in json.loads(snapshot['user_ids'])}:
            raise MobileError(403, 'access_removed')

    def issuer(self, request, conn, write):
        session = request.session
        u = session.get('user') or {}
        if u.get('role') not in ('parent', 'owner') or not isinstance(session.get('sid'), str):
            raise MobileError(401, 'reconnect_required')
        digest = hashlib.sha256(session['sid'].encode()).hexdigest()
        row = conn.execute('SELECT * FROM sessions WHERE id=?', (digest,)).fetchone()
        pair = dict(subject_id=str(u.get('id')), issued_role=u['role'], issuer_session_hash=digest, auth_epoch=session.get('epoch'))
        if not self.valid_issuer(conn, pair):
            raise MobileError(401, 'reconnect_required')
        if not conn.execute("SELECT 1 FROM settings WHERE key='setup.completed_at'").fetchone():
            # Existing upgraded installs are already configured; new installs must finish setup.
            if not self.db.setting('client_id') or not conn.execute('SELECT 1 FROM parents LIMIT 1').fetchone():
                raise MobileError(409, 'prerequisite_required')
        pair['display_name'] = self.authorize(conn, pair['subject_id'], pair['issued_role'], write)
        return pair

    def valid_issuer(self, conn, pair):
        row = conn.execute('SELECT * FROM sessions WHERE id=?', (pair['issuer_session_hash'],)).fetchone()
        if not row or pair['issued_role'] not in SESSION_LIMITS:
            return False
        idle, total = SESSION_LIMITS[pair['issued_role']]
        return (str(row['user_id']) == pair['subject_id'] and row['role'] == pair['issued_role'] and pair['auth_epoch'] == self.epoch(conn)
                and time.time() - row['last_seen'] < idle and time.time() - row['created_at'] < total)

    def child(self, conn, actor, child_id, write=False):
        kid = conn.execute('SELECT * FROM kids WHERE CAST(id AS TEXT)=?', (child_id,)).fetchone()
        if not kid or kid['status'] == 'removed':
            raise MobileError(404, 'not_found')
        if actor['issued_role'] != 'owner' and str(kid['parent_id']) != actor['subject_id'] and not conn.execute(
                'SELECT 1 FROM kid_parents WHERE kid_id=? AND CAST(parent_id AS TEXT)=?', (kid['id'], actor['subject_id'])).fetchone():
            raise MobileError(404, 'not_found')
        if kid['parent_id'] != self.plex.owner().id:
            parent = conn.execute('SELECT enabled FROM parents WHERE plex_id=?', (kid['parent_id'],)).fetchone()
            if not parent or not parent[0]:
                raise MobileError(404, 'not_found')
            try:
                self.shared(conn, str(kid['parent_id']), write)
            except MobileError as exc:
                if exc.status == 503:
                    raise
                raise MobileError(404, 'not_found') from None
        return kid

    def libraries(self, conn):
        return effective_kid_sections(conn, self.cfg.kid_sections)

    def session(self, actor):
        idle = SESSION_LIMITS[actor['issued_role']][0]
        return dict(subject=dict(id=actor['subject_id'], display_name=actor['display_name'], role=actor['issued_role']),
                    device=dict(id=actor['id'],label=actor['label'],expires_at=stamp(actor['expires_at']),
                                idle_expires_at=stamp(min(actor['last_seen_at'] + idle, actor['expires_at']))),
                    edition='plex', timezone=self.cfg.tz, today=self.svc.today(), capabilities=self.capabilities(),
                    enforcement=dict(mode='server_managed', notice='Saved controls do not guarantee playback stopped.'))

    def capabilities(self):
        from .main import REQUEST_KINDS
        return dict(controls=['pause','resume','bonus','later'], editable_settings=['name','age_level','mode','library_ids'],
                    bonus_choices=[15,30,60], later_choices=[30,60], bonus_cap=600, later_cap=180,
                    request_kinds=list(REQUEST_KINDS),request_message_max=1000,request_cancel=True,
                    invite_expiry_days=[1,7,30],invite_recipient_mode='accepted_share',notifications='polling',
                    web_only=['add_child','owner_setup','child_account_linking'])

    def child_wire(self, conn, actor, kid):
        now = self.svc.now_local()
        schedule = rules.Schedule.from_row(kid)
        used = conn.execute('SELECT seconds FROM usage WHERE kid_id=? AND day=?', (kid['id'], self.svc.today())).fetchone()
        used = used[0] if used else 0
        reason = rules.block_reason(schedule, now, used)
        healthy = self.app.state.enforcer.healthy()
        state = {'hours':'outside_hours','limit':'limit_reached','paused':'paused'}.get(reason, 'available')
        if not healthy or kid['sync_state'] != 'ok':
            state = 'needs_attention'
        elif self.app.state.enforcer.live.get(kid['id']) and reason is None:
            state = 'watching'
        allowed = ['pause','resume','bonus','later'] if kid['status'] == 'active' and not self.svc.unsafe_share_pending(kid) else []
        return dict(id=str(kid['id']),name=kid['name'],status=kid['status'] if kid['status'] in ('draft','requested','active','declined') else 'needs_attention',
                    revision=kid['revision'],age_level=kid['age_level'],mode=kid['mode'],library_ids=[s for s in kid['sections'].split(',') if s in self.libraries(conn)],
                    today=dict(date=self.svc.today(),checked_at=stamp(self.app.state.enforcer.last_ok) if self.app.state.enforcer.last_ok is not None else None,
                               used_seconds=used,limit_seconds=rules.limit_seconds(schedule,now.date()),remaining_seconds=rules.remaining_seconds(schedule,now.date(),used)),
                    state=state,paused_until=stamp(kid['paused_until']) if kid['paused_until'] else None,
                    bonus_minutes=kid['bonus_minutes'] if kid['bonus_day']==self.svc.today() else 0,
                    later_minutes=kid['later_minutes'] if kid['later_day']==self.svc.today() else 0,
                    sync=dict(state='failed' if kid['sync_state']=='error' else ('pending' if self.svc.sync_pending(kid['id']) else ('applied' if kid['titles_synced_at'] else 'unknown')),
                              checked_at=stamp(kid['titles_synced_at']) if kid['titles_synced_at'] else None),allowed_actions=allowed)

    def settings_wire(self, conn, kid, prepared):
        choices = [dict(id=s,title=prepared.get('libraries',{}).get(s,'Library')) for s in self.libraries(conn)]
        return dict(revision=kid['revision'],name=kid['name'],age_level=kid['age_level'],mode=kid['mode'],
                    library_ids=[s for s in kid['sections'].split(',') if s in self.libraries(conn)],library_choices=choices)

    def schedule_wire(self, kid):
        days = {}
        for day in ('weekday','weekend'):
            a,b = kid[day+'_start'],kid[day+'_end']
            days[day] = dict(minutes=kid[day+'_minutes'],start=a,end=b,any_time=a==b)
        return dict(revision=kid['revision'],timezone=self.cfg.tz,**days,friday_weekend=bool(kid['friday_weekend']),
                    sunday_school=bool(kid['sunday_school']),break_until=kid['break_until'])

    @staticmethod
    def request_wire(row, actor):
        decisions = ['approve','decline','done'] if actor['issued_role']=='owner' and row['status']=='open' and row['kind'] not in ('access','add_child') else []
        return dict(id=str(row['id']),revision=row['revision'],child_id=str(row['kid_id']) if row['kid_id'] else None,
                    kind=row['kind'],message=row['message'],status=row['status'],owner_note=row['owner_note'],
                    created_at=stamp(row['created_at']),resolved_at=stamp(row['resolved_at']) if row['resolved_at'] else None,allowed_decisions=decisions)

    def request_row(self, conn, actor, resource_id):
        row = conn.execute('SELECT * FROM requests WHERE CAST(id AS TEXT)=?', (resource_id,)).fetchone()
        if not row or (actor['issued_role']!='owner' and str(row['parent_id'])!=actor['subject_id']):
            raise MobileError(404, 'not_found')
        if row['kid_id']:
            self.child(conn, actor, str(row['kid_id']))
        return row

    @staticmethod
    def owner(actor):
        if actor['issued_role'] != 'owner':
            raise MobileError(404,'not_found')

    def invite_wire(self, conn, row):
        parent = conn.execute('SELECT title,username FROM parents WHERE plex_id=?', (row['intended_plex_id'],)).fetchone()
        state = 'claimed' if row['claimed_at'] else ('revoked' if row['revoked_at'] else ('expired' if row['expires_at']<=time.time() else 'open'))
        return dict(id=str(row['id']),revision=row['revision'],intended_display=(parent['title'] or parent['username']) if parent else 'Parent',expires_at=stamp(row['expires_at']),state=state)

    def approval_wire(self, conn, actor, row):
        parent = conn.execute('SELECT title,username FROM parents WHERE plex_id=?',(row['parent_id'],)).fetchone()
        prereq = []
        if row['kind']=='add_child':
            kid=self.child(conn,actor,str(row['kid_id']))
            name=kid['name']
            if not kid['plex_id'] or not kid['plex_accepted'] or self.svc.unsafe_share_pending(kid):
                prereq=['Finish the child connection on the website.']
        else:
            name=(parent['title'] or parent['username']) if parent else 'Parent'
            snap=conn.execute('SELECT * FROM sharing_state WHERE id=1').fetchone()
            if str(row['parent_id']) not in {str(n) for n in json.loads(snap['user_ids'] or '[]')}:
                prereq=['Accept the Plex library invitation first.']
        return dict(id=('parent_' if row['kind']=='access' else 'child_')+str(row['id']),revision=row['revision'],
                    kind='parent_access' if row['kind']=='access' else 'child_access',subject_display=name,
                    child_id=str(row['kid_id']) if row['kid_id'] else None,state=row['status'] if row['status'] in ('approved','declined') else 'open',
                    prerequisites=prereq,allowed_decisions=['approve','decline'] if row['status']=='open' else [])

    def prepare(self, path, body, actor):
        """Only remote reads. Authorize again after every wait in the commit transaction."""
        prepared = {}
        if path.endswith('/settings'):
            prepared['libraries'] = self.plex.section_titles()
        if '/titles' in path:
            child_id=path.split('/')[1]
            with closing(self.db._connect()) as conn:
                kid=self.child(conn,actor,child_id, bool(body))
                sections=[s for s in kid['sections'].split(',') if s in self.libraries(conn)]
            prepared['titles']=[it for s in sections for it in self.plex.library(s)]
        if path in ('owner/invites','owner/invite-recipients'):
            self.owner(actor)
            prepared['recipients']=Invitations(self.db,self.svc).recipients()
        return prepared
