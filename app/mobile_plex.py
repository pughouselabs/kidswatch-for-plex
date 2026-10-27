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

    for table, kind in [('kids','child'),('requests','request'),('parent_invitations','invite'),('mobile_devices','device')]:
        conn.execute(f"INSERT OR IGNORE INTO mobile_revisions(kind,resource_id,revision) SELECT ?,CAST(id AS TEXT),revision FROM {table}",(kind,))
        for action in ('INSERT','UPDATE'):
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS mobile_sidecar_{table}_{action} AFTER {action} ON {table}
                BEGIN INSERT INTO mobile_revisions(kind,resource_id,revision) VALUES('{kind}',CAST(NEW.id AS TEXT),NEW.revision)
                ON CONFLICT(kind,resource_id) DO UPDATE SET revision=max(revision,excluded.revision); END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS mobile_device_event AFTER UPDATE OF revoked_at ON mobile_devices
        WHEN NEW.revoked_at IS NOT OLD.revoked_at BEGIN INSERT INTO mobile_events(at,kind,resource_kind,resource_id,subject_id)
        VALUES(strftime('%s','now'),'access_changed','device',NEW.id,NEW.subject_id); END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS mobile_epoch_insert AFTER INSERT ON settings
        WHEN NEW.key='session_epoch' BEGIN
        UPDATE mobile_devices SET revoked_at=strftime('%s','now'),revision=revision+1 WHERE revoked_at IS NULL AND auth_epoch<>NEW.value;
        UPDATE mobile_pairings SET revoked_at=strftime('%s','now') WHERE auth_epoch<>NEW.value; END""")


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
        self.owner_info = self.plex.owner()

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
        self.owner_info = self.plex.owner()

    @staticmethod
    def epoch(conn):
        row = conn.execute("SELECT value FROM settings WHERE key='session_epoch'").fetchone()
        return row[0] if row else '1'

    def authorize(self, conn, subject, role, write):
        owner = self.owner_info
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
        if not conn.execute("SELECT 1 FROM settings WHERE key='setup.completed_at' AND value<>''").fetchone():
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
        if kid['parent_id'] != self.owner_info.id:
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
                    created_at=stamp(row['created_at']) if row['created_at'] else None,resolved_at=stamp(row['resolved_at']) if row['resolved_at'] else None,allowed_decisions=decisions)

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
        if path.startswith('operations/'):
            op_id=path.split('/')[1]
            with closing(self.db._connect()) as conn:
                op=conn.execute('SELECT * FROM mobile_operations WHERE id=?',(op_id,)).fetchone()
                if not op or (actor['issued_role']!='owner' and op['subject_id']!=actor['subject_id']):
                    raise MobileError(404,'not_found')
                if op['effect'] and op['state']!='applied':
                    effect=json.loads(op['effect'])
                    kid=self.child(conn,actor,effect['child_id'])
                else:
                    kid=None
            if kid is not None:
                verified=None
                try:
                    share=self.plex.shares(fresh=True).get(kid['plex_id']) if kid['plex_id'] else None
                    if effect['action']=='decline':
                        verified=share is None
                    elif kid['status']=='active':
                        verified=bool(share and self.svc.share_matches(share,self.svc.kid_sections(kid),rules.build_filters(kid['id'])))
                        if verified:
                            want=self.svc.visible_titles(kid)
                            verified=all(self.plex.items_with_label(sec,rules.ok_label(kid['id']))==want.get(sec,set()) for sec in self.svc.library_sections())
                    elif kid['status']=='draft':
                        verified=True  # Local configuration has no active media share.
                except Exception:
                    verified=None
                prepared['operation_readback']=dict(operation_id=op_id,child_id=str(kid['id']),child_revision=kid['revision'],verified=verified)
        return prepared

    def resource(self, conn, actor, kind, resource_id, prepared):
        if kind in ('child','settings','schedule'):
            kid=self.child(conn,actor,resource_id)
            if kind=='child':
                return self.child_wire(conn,actor,kid)
            if kind=='settings':
                return self.settings_wire(conn,kid,prepared)
            return self.schedule_wire(kid)
        if kind=='request':
            return self.request_wire(self.request_row(conn,actor,resource_id),actor)
        if kind=='approval':
            self.owner(actor)
            prefix, _, rid=resource_id.partition('_')
            row=self.request_row(conn,actor,rid)
            if prefix not in ('parent','child') or row['kind'] != ('access' if prefix=='parent' else 'add_child'):
                raise MobileError(404,'not_found')
            return self.approval_wire(conn,actor,row)
        if kind=='invite':
            self.owner(actor)
            row=conn.execute('SELECT * FROM parent_invitations WHERE CAST(id AS TEXT)=?',(resource_id,)).fetchone()
            if not row:
                raise MobileError(404,'not_found')
            return self.invite_wire(conn,row)
        raise MobileError(404,'not_found')

    def read(self, conn, actor, path, query, prepared):
        # Lists return the full authorized set to the shared pagination layer.
        if path=='session':
            return self.session(actor),'session',None
        if path=='children':
            items=[]
            for kid in conn.execute("SELECT * FROM kids WHERE status<>'removed' ORDER BY id"):
                try:
                    self.child(conn,actor,str(kid['id']))
                except MobileError as exc:
                    if exc.status==404:
                        continue
                    raise
                items.append(self.child_wire(conn,actor,kid))
            return items,'child',None
        parts=path.split('/')
        if len(parts) in (2,3) and parts[0]=='children':
            kid=self.child(conn,actor,parts[1])
            if len(parts)==2:
                return self.child_wire(conn,actor,kid),'child',kid['revision']
            if parts[2] in ('settings','schedule'):
                kind=parts[2]
                return self.resource(conn,actor,kind,parts[1],prepared),kind,kid['revision']
            if parts[2]=='titles':
                q=query.get('q','')
                string(q,'q',100,0)
                choice=query.get('choice','all')
                if choice not in ('all','allow','block','reset'):
                    raise MobileError(422,'invalid_fields',{'choice':'Choose a title filter.'})
                choices={str(r['rating_key']):r['kind'] for r in conn.execute('SELECT rating_key,kind FROM kid_titles WHERE kid_id=?',(kid['id'],))}
                items=[]
                for it in prepared.get('titles',[]):
                    if it.section_key not in self.libraries(conn) or it.section_key not in kid['sections'].split(',') or it.item_type not in ('movie','show'):
                        continue
                    selected=choices.get(str(it.rating_key),'reset')
                    if q.casefold() not in it.title.casefold() or (choice!='all' and selected!=choice):
                        continue
                    visible=rules.title_visible(kid['age_level'],kid['mode'],it.item_type,it.rating,None if selected=='reset' else selected)
                    can=rules.allowable(it.item_type,it.rating)
                    reason=('This title is outside the supported rating limit.' if not can else
                            ('Allowed by age level' if visible and selected=='reset' else ('Allowed' if visible else 'Blocked')))
                    items.append(dict(id=str(it.rating_key),title=it.title,year=it.year,kind=it.item_type,rating=it.rating,
                                      choice=selected,visible=visible,can_allow=can,reason=reason))
                items.sort(key=lambda it:(it['title'].casefold(),it['id']))
                return items,'title',None
            if parts[2]=='activity':
                end=query.get('to',self.svc.today())
                start=query.get('from',(date.fromisoformat(self.svc.today())-timedelta(days=6)).isoformat())
                if not rules.valid_date(start) or not rules.valid_date(end) or not 0 <= (date.fromisoformat(end)-date.fromisoformat(start)).days <=30:
                    raise MobileError(422,'invalid_fields',{'from':'Choose up to 31 household dates.'})
                items=[]
                for row in conn.execute('SELECT day,seconds FROM usage WHERE kid_id=? AND day>=? AND day<=? ORDER BY day DESC',(kid['id'],start,end)):
                    at=datetime.combine(date.fromisoformat(row['day']),datetime.min.time(),tzinfo=self.svc.tz)
                    items.append(dict(id='usage_'+str(kid['id'])+'_'+row['day'],at=stamp(at),child_id=str(kid['id']),kind='usage',
                                      summary=rules.minutes_text(row['seconds'])+' used',actor_display=None,seconds=row['seconds']))
                for row in conn.execute('SELECT * FROM stops WHERE kid_id=? ORDER BY id DESC',(kid['id'],)):
                    at=datetime.fromisoformat(row['at']).astimezone(self.svc.tz)
                    if start<=at.date().isoformat()<=end:
                        items.append(dict(id='stop_'+str(row['id']),at=stamp(at),child_id=str(kid['id']),kind='stop',summary='Stop requested',actor_display=None,seconds=None))
                # Never serialize arbitrary audit details or exception strings.
                for row in conn.execute("SELECT id,at,action FROM audit WHERE json_valid(detail) AND json_extract(detail,'$.kid')=? ORDER BY id DESC",(kid['id'],)):
                    at=datetime.fromisoformat(row['at'].replace('Z','+00:00')).astimezone(self.svc.tz)
                    if start<=at.date().isoformat()<=end and row['action'] in ('mobile_child_saved','settings_saved','schedule_saved','control_pause','control_pause_timed','control_resume','control_bonus','control_later'):
                        items.append(dict(id='change_'+str(row['id']),at=stamp(at),child_id=str(kid['id']),kind='change',summary='Viewing settings saved',actor_display=None,seconds=None))
                return sorted(items,key=lambda it:(it['at'],it['id']),reverse=True),'activity',None
        if path=='requests':
            status=query.get('status','all')
            if status not in ('all','open','closed'):
                raise MobileError(422,'invalid_fields',{'status':'Choose a request filter.'})
            rows=[]
            for row in conn.execute('SELECT * FROM requests ORDER BY id DESC'):
                if actor['issued_role']!='owner' and str(row['parent_id'])!=actor['subject_id']:
                    continue
                if (status=='open' and row['status']!='open') or (status=='closed' and row['status']=='open'):
                    continue
                try:
                    self.request_row(conn,actor,str(row['id']))
                except MobileError as exc:
                    if exc.status==404:
                        continue
                    raise
                rows.append(self.request_wire(row,actor))
            return rows,'request',None
        if path=='owner/approvals':
            self.owner(actor)
            items=[]
            for row in conn.execute("SELECT * FROM requests WHERE kind IN ('access','add_child') ORDER BY id DESC"):
                try:
                    items.append(self.approval_wire(conn,actor,row))
                except MobileError as exc:
                    if exc.status==404:
                        continue
                    raise
            return items,'approval',None
        if path=='owner/invite-recipients':
            self.owner(actor)
            return [dict(id=str(k),display_name=v.title or v.username) for k,v in sorted(prepared.get('recipients',{}).items())],'recipient',None
        if path=='owner/invites':
            self.owner(actor)
            return [self.invite_wire(conn,row) for row in conn.execute('SELECT * FROM parent_invitations ORDER BY id DESC')],'invite',None
        raise MobileError(404,'not_found')

    def target(self, conn, actor, path, prepared):
        """Return current kind/ID/revision for shared If-Match handling before intent."""
        parts=path.split('/')
        if len(parts)==3 and parts[0]=='children':
            kid=self.child(conn,actor,parts[1],True)
            if kid['connection_claim']:
                raise MobileError(409,'operation_in_progress',retry=2)
            if self.svc.unsafe_share_pending(kid) or kid['status'] not in ('draft','active'):
                raise MobileError(409,'prerequisite_required')
            if parts[2]=='controls' and kid['status']!='active':
                raise MobileError(422,'unsupported_action')
            return ('settings' if parts[2]=='settings' else ('schedule' if parts[2]=='schedule' else 'child')),parts[1],kid['revision']
        if len(parts)==3 and parts[0]=='requests':
            row=self.request_row(conn,actor,parts[1])
            return 'request',parts[1],row['revision']
        if len(parts)==4 and parts[:2]==['owner','approvals']:
            resource=self.resource(conn,actor,'approval',parts[2],prepared)
            return 'approval',parts[2],resource['revision']
        if len(parts)==3 and parts[:2]==['owner','invites']:
            resource=self.resource(conn,actor,'invite',parts[2],prepared)
            return 'invite',parts[2],resource['revision']
        if path in ('requests','owner/invites'):
            if path.startswith('owner'):
                self.owner(actor)
            return None,None,None
        raise MobileError(404,'not_found')

    def mutate(self, conn, actor, path, body, prepared, operation_id):
        kind,rid,_=self.target(conn,actor,path,prepared)
        parts=path.split('/')
        effect=None
        data=None
        if parts[0]=='children':
            kid=self.child(conn,actor,parts[1],True)
            changes={}
            if parts[2]=='settings':
                fields(body,(),('name','age_level','mode','library_ids'))
                if not body:
                    raise MobileError(422,'invalid_fields')
                for key,value in body.items():
                    if key=='name':
                        changes['name']=string(value,key,40).strip()
                        if not changes['name']:
                            raise MobileError(422,'invalid_fields',{'name':'Enter a name.'})
                    elif key=='age_level':
                        if not isinstance(value,str) or value not in rules.AGE_LEVELS:
                            raise MobileError(422,'invalid_fields',{'age_level':'Choose an age level.'})
                        changes[key]=value
                    elif key=='mode':
                        if value not in ('age','picked'):
                            raise MobileError(422,'invalid_fields',{'mode':'Choose a viewing mode.'})
                        changes[key]=value
                    else:
                        if not isinstance(value,list) or not 1<=len(value)<=100 or any(not isinstance(v,str) or v not in self.libraries(conn) for v in value) or len(set(value))!=len(value):
                            raise MobileError(422,'invalid_fields',{'library_ids':'Choose permitted libraries.'})
                        changes['sections']=','.join(value)
                effect='policy'
            elif parts[2]=='schedule':
                fields(body,('timezone','weekday','weekend','friday_weekend','sunday_school','break_until'))
                if body['timezone']!=self.cfg.tz or any(type(body[k]) is not bool for k in ('friday_weekend','sunday_school')):
                    raise MobileError(422,'invalid_fields',{'timezone':'Use the household timezone.'})
                for day in ('weekday','weekend'):
                    obj=body[day]
                    if not isinstance(obj,dict):
                        raise MobileError(422,'invalid_fields',{day:'Enter a schedule.'})
                    fields(obj,('minutes','start','end','any_time'))
                    if (obj['minutes'] is not None and (type(obj['minutes']) is not int or not 0<=obj['minutes']<=1440)) or type(obj['any_time']) is not bool or not rules.valid_hhmm(obj['start']) or not rules.valid_hhmm(obj['end']) or (obj['start']==obj['end'] and not obj['any_time']):
                        raise MobileError(422,'invalid_fields',{day:'Choose different times, or turn on Any time.'})
                    changes[day+'_minutes']=obj['minutes']
                    changes[day+'_start']='00:00' if obj['any_time'] else obj['start']
                    changes[day+'_end']='00:00' if obj['any_time'] else obj['end']
                if body['break_until'] is not None and not rules.valid_date(body['break_until']):
                    raise MobileError(422,'invalid_fields',{'break_until':'Choose a calendar date.'})
                changes.update(friday_weekend=int(body['friday_weekend']),sunday_school=int(body['sunday_school']),break_until=body['break_until'])
            elif parts[2]=='controls':
                fields(body,('action','value','expected_day'))
                if not rules.valid_date(body['expected_day']):
                    raise MobileError(422,'invalid_fields',{'expected_day':'Use the server household date.'})
                if body['expected_day']!=self.svc.today():
                    raise MobileError(409,'day_changed')
                action,value=body['action'],body['value']
                if action=='pause' and isinstance(value,str) and value in ('indefinite','30','60','tomorrow'):
                    until=rules.pause_end(value,self.svc.now_local(),rules.Schedule.from_row(kid))
                    changes=dict(paused=int(value=='indefinite'),paused_until=stamp(until) if until else None)
                elif action=='resume' and value is None:
                    changes=dict(paused=0,paused_until=None)
                elif action in ('bonus','later') and type(value) is int and value in ([15,30,60] if action=='bonus' else [30,60]):
                    if action=='later':
                        a,b=rules.window_for(rules.Schedule.from_row(kid),self.svc.now_local().date())
                        if a==b:
                            raise MobileError(422,'unsupported_action')
                    self.db.increment_daily(kid['id'],action,self.svc.today(),value,600 if action=='bonus' else 180,conn=conn)
                else:
                    raise MobileError(422,'invalid_fields',{'value':'Choose a supported control and value.'})
            elif parts[2]=='titles':
                fields(body,('ids','choice'))
                ids=body['ids']
                if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids) or body['choice'] not in ('allow','block','reset'):
                    raise MobileError(422,'invalid_fields')
                items={str(it.rating_key):it for it in prepared.get('titles',[]) if it.section_key in self.libraries(conn) and it.section_key in kid['sections'].split(',') and it.item_type in ('movie','show')}
                for value in ids:
                    identifier(value)
                    if value not in items or (body['choice']=='allow' and not rules.allowable(items[value].item_type,items[value].rating)):
                        raise MobileError(422,'invalid_fields',{'ids':'Choose supported titles from these libraries.'})
                for value in ids:
                    it=items[value]
                    conn.execute('DELETE FROM kid_titles WHERE kid_id=? AND rating_key=?',(kid['id'],it.rating_key))
                    if body['choice']!='reset':
                        conn.execute('INSERT INTO kid_titles(kid_id,rating_key,kind,section_key,item_type,title,year) VALUES(?,?,?,?,?,?,?)',
                                     (kid['id'],it.rating_key,body['choice'],it.section_key,it.item_type,it.title,it.year))
                effect='policy'
            else:
                raise MobileError(404,'not_found')
            changes['updated_at']=now_iso()
            # One shared revision even for a no-op save; title row triggers may also bump.
            conn.execute('UPDATE kids SET '+','.join(k+'=?' for k in changes)+',revision=revision+1 WHERE id=?',(*changes.values(),kid['id']))
            if effect:
                conn.execute('UPDATE kids SET connection_claim=? WHERE id=?',(operation_id,kid['id']))
            self.audit(conn,actor['subject_id'],'mobile_child_saved',kid=kid['id'])
        elif path=='requests':
            fields(body,('child_id','kind','message'))
            if body['kind'] not in self.capabilities()['request_kinds']:
                raise MobileError(422,'invalid_fields',{'kind':'Choose an advertised request kind.'})
            message=string(body['message'],'message',1000).strip()
            if not message:
                raise MobileError(422,'invalid_fields',{'message':'Enter a message.'})
            child_id=body['child_id']
            if child_id is not None:
                identifier(child_id,'child_id')
                self.child(conn,actor,child_id,True)
            recent=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(timespec='seconds')
            if conn.execute("SELECT count(*) FROM requests WHERE parent_id=? AND (status='open' OR (status='cancelled' AND resolved_at>?))",(actor['subject_id'],recent)).fetchone()[0]>=15:
                raise MobileError(429,'rate_limited',retry=3600)
            rid=str(conn.execute('INSERT INTO requests(parent_id,kid_id,kind,message,created_at) VALUES(?,?,?,?,?)',(actor['subject_id'],child_id,body['kind'],message,now_iso())).lastrowid)
            kind='request'
        elif parts[0]=='requests':
            row=self.request_row(conn,actor,rid)
            if parts[2]=='cancel':
                fields(body,())
                if str(row['parent_id'])!=actor['subject_id'] or row['kind'] in ('access','add_child') or row['status']!='open':
                    raise MobileError(409,'prerequisite_required')
                status,note='cancelled',row['owner_note']
            elif parts[2]=='decision':
                self.owner(actor)
                fields(body,('decision','note'))
                if body['decision'] not in self.request_wire(row,actor)['allowed_decisions']:
                    raise MobileError(422,'unsupported_action')
                status={'approve':'approved','decline':'declined','done':'done'}[body['decision']]
                note=string(body['note'],'note',500,0) or None
            else:
                raise MobileError(404,'not_found')
            conn.execute('UPDATE requests SET status=?,owner_note=?,resolved_at=? WHERE id=?',(status,note,now_iso(),row['id']))
        elif parts[:2]==['owner','approvals']:
            self.owner(actor)
            fields(body,('decision','note'))
            if body['decision'] not in ('approve','decline'):
                raise MobileError(422,'invalid_fields')
            note=string(body['note'],'note',500,0) or None
            approval=self.resource(conn,actor,'approval',rid,prepared)
            if approval['state']!='open' or (body['decision']=='approve' and approval['prerequisites']):
                raise MobileError(409,'prerequisite_required')
            req_id=rid.split('_',1)[1]
            row=self.request_row(conn,actor,req_id)
            if row['kind']=='access' and body['decision']=='approve':
                self.shared(conn,str(row['parent_id']),True)
                conn.execute('UPDATE parents SET enabled=1 WHERE plex_id=?',(row['parent_id'],))
            elif row['kind']=='add_child':
                kid=self.child(conn,actor,str(row['kid_id']),True)
                if kid['status']!='requested' or kid['connection_claim']:
                    raise MobileError(409,'prerequisite_required')
                changes={'status':'active' if body['decision']=='approve' else 'declined','connection_claim':operation_id}
                if body['decision']=='decline':
                    from .service import UNSAFE_SHARE_ERROR
                    changes.update(sync_state='error',sync_error=UNSAFE_SHARE_ERROR+' Approval removal pending')
                conn.execute('UPDATE kids SET '+','.join(k+'=?' for k in changes)+' WHERE id=?',(*changes.values(),kid['id']))
                effect='approve' if body['decision']=='approve' else 'decline'
            conn.execute('UPDATE requests SET status=?,owner_note=?,resolved_at=? WHERE id=?',('approved' if body['decision']=='approve' else 'declined',note,now_iso(),req_id))
        elif path=='owner/invites':
            self.owner(actor)
            fields(body,('recipient_id','expiry_days'))
            recipient=identifier(body['recipient_id'],'recipient_id')
            if type(body['expiry_days']) is not int or body['expiry_days'] not in (1,7,30):
                raise MobileError(422,'invalid_fields')
            if recipient not in {str(k) for k in prepared.get('recipients',{})}:
                raise MobileError(409,'prerequisite_required')
            try:
                Invitations._eligible(conn,int(recipient))
            except (InvitationError, ValueError):
                raise MobileError(409,'prerequisite_required') from None
            prior=conn.execute("SELECT status FROM requests WHERE parent_id=? AND kind='access' ORDER BY id DESC LIMIT 1",(recipient,)).fetchone()
            if prior and prior[0] not in ('open','cancelled'):
                raise MobileError(409,'prerequisite_required')
            token=secrets.token_urlsafe(32)
            rid=str(conn.execute('INSERT INTO parent_invitations(token_hash,owner_id,intended_plex_id,created_at,expires_at) VALUES(?,?,?,?,?)',
                                 (token_hash(token),actor['subject_id'],recipient,now_iso(),time.time()+body['expiry_days']*86400)).lastrowid)
            kind='invite'
            data=self.resource(conn,actor,kind,rid,prepared)|dict(link_available=True,join_url=self.origin+'/join/'+token)
        elif parts[:2]==['owner','invites']:
            self.owner(actor)
            fields(body,())
            row=conn.execute('SELECT * FROM parent_invitations WHERE CAST(id AS TEXT)=?',(rid,)).fetchone()
            if row['claimed_at']:
                raise MobileError(409,'prerequisite_required')
            if not row['revoked_at']:
                conn.execute('UPDATE parent_invitations SET revoked_at=? WHERE id=?',(now_iso(),rid))
        else:
            raise MobileError(404,'not_found')
        if effect:
            effect_child=parts[1] if parts[0]=='children' else str(row['kid_id'])
            effect_kid=conn.execute('SELECT revision FROM kids WHERE id=?',(effect_child,)).fetchone()
            effect=json.dumps(dict(action=effect,child_id=effect_child,child_revision=effect_kid['revision']))
        resource=data or self.resource(conn,actor,kind,rid,prepared)
        revision=resource['revision']
        self.audit(conn,actor['subject_id'],'mobile_intent_saved',operation=operation_id,kind=kind,resource=rid)
        if path in ('requests','owner/invites'):
            return 201,resource,kind,rid,revision,effect
        return (202 if effect else 200),None,kind,rid,revision,effect

    def reconcile_operation(self,conn,actor,row,prepared):
        observed=prepared.get('operation_readback')
        if not observed or row['state']=='applied' or observed['operation_id']!=row['id']:
            return row
        effect=json.loads(row['effect'])
        kid=self.child(conn,actor,observed['child_id'])
        # Compare readback against the exact committed configuration, never a newer edit.
        if kid['revision']!=observed['child_revision'] or kid['revision']!=effect['child_revision']:
            state='unknown'
        elif observed['verified'] is True:
            state='applied'
        else:
            state='unknown' if observed['verified'] is None else 'failed'
        conn.execute('UPDATE mobile_operations SET state=?,updated_at=?,safe_error_code=? WHERE id=?',
                     (state,time.time(),None if state=='applied' else 'server_unavailable',row['id']))
        conn.execute('UPDATE kids SET connection_claim=NULL WHERE id=? AND connection_claim=?',(kid['id'],row['id']))
        return conn.execute('SELECT * FROM mobile_operations WHERE id=?',(row['id'],)).fetchone()

    def effect(self, core, token, operation_id):
        """One attempt only. Interrupted/uncertain external effects are never blindly replayed."""
        self.refresh(True)
        with core.transaction() as conn:
            actor=core.authenticate(conn,token,True)
            op=conn.execute('SELECT * FROM mobile_operations WHERE id=?',(operation_id,)).fetchone()
            if not op or op['state']!='pending' or not op['effect']:
                return
            effect=json.loads(op['effect'])
            child_id=effect['child_id']
            kid=self.child(conn,actor,child_id,True)
            if kid['connection_claim']!=operation_id or kid['revision']!=effect['child_revision']:
                return
            conn.execute("UPDATE mobile_operations SET state='unknown',updated_at=? WHERE id=?",(time.time(),operation_id))
        ok=False
        try:
            if effect['action']=='decline':
                self.svc._drop_share(kid)
                with core.transaction() as conn:
                    core.authenticate(conn,token,True)
                    conn.execute("UPDATE kids SET plex_id=NULL,sync_state='ok',sync_error=NULL WHERE id=? AND connection_claim=?",(child_id,operation_id))
                ok=True
            else:
                ok=self.svc.apply(int(child_id),actor['subject_id'])
                # Recheck after remote apply before title effects, including epoch and membership.
                self.refresh(True)
                with core.transaction() as conn:
                    actor=core.authenticate(conn,token,True)
                    current=self.child(conn,actor,child_id,True)
                    if current['connection_claim']!=operation_id or current['revision']!=effect['child_revision']:
                        raise MobileError(409,'operation_in_progress')
                if ok:
                    ok=self.svc.sync_titles(int(child_id))
                if ok and current['plex_id']:
                    share=self.plex.shares(fresh=True).get(current['plex_id'])
                    ok=bool(share and self.svc.share_matches(share,self.svc.kid_sections(current),rules.build_filters(int(child_id))))
                    if ok:
                        want=self.svc.visible_titles(current) if current['status']=='active' else {}
                        ok=all(self.plex.items_with_label(s,rules.ok_label(int(child_id)))==want.get(s,set()) for s in self.svc.library_sections())
        except (Exception,):
            # Exception strings can contain backend credentials. Persist only safe outcome.
            ok=False
        finally:
            with core.transaction() as conn:
                latest=conn.execute('SELECT revision FROM kids WHERE id=?',(child_id,)).fetchone()
                if effect['action']!='decline' and (not latest or latest['revision']!=effect['child_revision']):
                    ok=False
                conn.execute('UPDATE kids SET connection_claim=NULL WHERE id=? AND connection_claim=?',(child_id,operation_id))
                conn.execute('UPDATE mobile_operations SET state=?,updated_at=?,safe_error_code=? WHERE id=?',
                             ('applied' if ok else 'failed',time.time(),None if ok else 'server_unavailable',operation_id))
