"""Cross-worker compare-and-set, web revision participation, date and replay semantics."""
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import rules
from .test_mobile_api import mobile, phone, active_mobile, save, HEADERS  # noqa: F401
from .test_app import env, csrf  # noqa: F401


def test_parallel_bonus_same_revision_one_wins(mobile):
    api,_,_=phone(mobile)
    app,fake=mobile
    _,kid=active_mobile(app,fake)
    rev=app.state.db.kid(kid)['revision']
    body=dict(action='bonus',value=15,expected_day=app.state.svc.today())
    def run(_):
        c=TestClient(app,base_url='https://testserver',headers=dict(api.headers))
        return save(c,f'children/{kid}/controls',body,rev).status_code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(run,range(2)))==[200,412]
    assert app.state.db.kid(kid)['bonus_minutes']==15


def test_parallel_same_key_applies_exactly_once(mobile):
    api,_,_=phone(mobile)
    app,fake=mobile
    _,kid=active_mobile(app,fake)
    rev=app.state.db.kid(kid)['revision']
    key=str(uuid.uuid4())
    body=dict(action='bonus',value=30,expected_day=app.state.svc.today())
    def run(_):
        c=TestClient(app,base_url='https://testserver',headers=dict(api.headers))
        return save(c,f'children/{kid}/controls',body,rev,key)
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(run,range(2)))
    assert all(r.status_code==200 for r in results)
    assert sum(r.json()['meta'].get('replayed',False) for r in results)==1
    assert app.state.db.kid(kid)['bonus_minutes']==30
    assert app.state.db.one('SELECT count(*) FROM mobile_operations')[0]==1


def test_web_changes_revision_but_usage_tick_does_not(mobile):
    api,_,_=phone(mobile)
    app,fake=mobile
    web,kid=active_mobile(app,fake)
    before=app.state.db.kid(kid)['revision']
    app.state.db.add_usage(kid,app.state.svc.today(),60)
    assert app.state.db.kid(kid)['revision']==before
    web.post(f'/kids/{kid}/control',data={'csrf':csrf(web),'action':'bonus:15'})
    now=app.state.db.kid(kid)['revision']
    assert now>before
    assert save(api,f'children/{kid}/controls',{'action':'bonus','value':15,'expected_day':app.state.svc.today()},before).status_code==412
    assert app.state.db.one("SELECT revision FROM mobile_revisions WHERE kind='child' AND resource_id=?",(str(kid),))[0]==now


def test_replay_after_retention_is_expired(mobile):
    api,_,_=phone(mobile)
    app,fake=mobile
    _,kid=active_mobile(app,fake)
    rev=app.state.db.kid(kid)['revision']
    key=str(uuid.uuid4())
    body={'action':'bonus','value':15,'expected_day':app.state.svc.today()}
    assert save(api,f'children/{kid}/controls',body,rev,key).status_code==200
    app.state.db.x('UPDATE mobile_idempotency SET created_at=created_at-86401')
    r=save(api,f'children/{kid}/controls',body,rev,key)
    assert r.status_code==409 and r.json()['error']['code']=='idempotency_expired'
    assert app.state.db.kid(kid)['bonus_minutes']==15


@pytest.mark.parametrize('wall',['2026-03-08T01:45:00','2026-03-08T03:15:00','2026-11-01T01:30:00','2026-10-31T00:30:00'])
def test_timezone_pause_matches_shared_rules(mobile,wall):
    api,_,_=phone(mobile)
    app,fake=mobile
    _,kid=active_mobile(app,fake)
    instant=datetime.fromisoformat(wall).replace(tzinfo=ZoneInfo('America/Denver'))
    app.state.svc.now_local=lambda:instant
    app.state.db.update_kid(kid,weekday_start='18:00',weekday_end='01:00',weekend_start='18:00',weekend_end='01:00')
    row=app.state.db.kid(kid)
    expected=rules.pause_end('tomorrow',instant,rules.Schedule.from_row(row))
    r=save(api,f'children/{kid}/controls',dict(action='pause',value='tomorrow',expected_day=app.state.svc.today()),row['revision'])
    assert r.status_code==200,r.text
    actual=datetime.fromisoformat(app.state.db.kid(kid)['paused_until'].replace('Z','+00:00'))
    assert actual==expected


def test_parent_and_child_approvals_use_real_prerequisites(mobile):
    from .test_app import login,add_kid
    api,_,_=phone(mobile,'owner')
    app,fake=mobile
    waiting,_=login(app,fake,'casey')
    assert waiting.post('/waiting',data={'csrf':csrf(waiting,'/waiting'),'message':'Please review'}).status_code==303
    approvals=api.get('/api/mobile/v1/owner/approvals').json()['data']['items']
    parent=next(x for x in approvals if x['kind']=='parent_access')
    r=save(api,'owner/approvals/'+parent['id']+'/decision',{'decision':'approve','note':''},parent['revision'])
    assert r.status_code==200,r.text
    assert app.state.db.one('SELECT enabled FROM parents WHERE plex_id=103')[0]==1
    # Actual strict-mode connection produces an add_child request. No fake approval rows.
    c,_=login(app,fake,'alex')
    kid=add_kid(app,fake,c,'River')
    import re
    url=re.search(r'value="https://testserver(/link/[^\"]+)"',c.get(f'/kids/{kid}').text).group(1)
    private=TestClient(app,base_url='https://testserver',follow_redirects=False)
    fake.next_token='tok-kid1'
    private.get(url+'/start')
    assert private.get(url+'/done').status_code==200
    child=next(x for x in api.get('/api/mobile/v1/owner/approvals').json()['data']['items'] if x['kind']=='child_access')
    r=save(api,'owner/approvals/'+child['id']+'/decision',{'decision':'approve','note':'Reviewed'},child['revision'])
    assert r.status_code==202,r.text
    assert app.state.db.kid(kid)['status']=='active'
    assert api.get('/api/mobile/v1/operations/'+r.json()['data']['operation_id']).json()['data']['state']=='applied'


def test_uncertain_effect_readback_never_replays_write(mobile):
    api,_,_=phone(mobile)
    app,fake=mobile
    _,kid=active_mobile(app,fake)
    rev=app.state.db.kid(kid)['revision']
    # Simulate death after durable intent: dispatch itself cannot complete.
    original=app.state.mobile.adapter.effect
    app.state.mobile.adapter.effect=lambda *_: (_ for _ in ()).throw(RuntimeError('lost worker'))
    r=save(api,f'children/{kid}/settings',{'name':'River Two'},rev,method='patch')
    assert r.status_code==202,r.text
    op=r.json()['data']['operation_id']
    assert app.state.db.kid(kid)['name']=='River Two'
    writes_before=[c for c in fake.calls if c[0] in ('update_share','add_label_bulk','remove_label_bulk')]
    read=api.get('/api/mobile/v1/operations/'+op)
    assert read.status_code==200 and read.json()['data']['state']=='applied'
    assert [c for c in fake.calls if c[0] in ('update_share','add_label_bulk','remove_label_bulk')]==writes_before
    assert app.state.db.kid(kid)['connection_claim'] is None
    app.state.mobile.adapter.effect=original


def test_failed_effect_and_private_backend_error_not_fabricated(mobile):
    api,_,_=phone(mobile)
    app,fake=mobile
    _,kid=active_mobile(app,fake)
    fake.fail_labels=True
    r=save(api,f'children/{kid}/titles',dict(ids=['11'],choice='block'),app.state.db.kid(kid)['revision'])
    assert r.status_code==202
    op=api.get('/api/mobile/v1/operations/'+r.json()['data']['operation_id'])
    assert op.status_code==200 and op.json()['data']['state'] in ('failed','unknown')
    assert 'Plex answered' not in op.text


def test_capped_bonus_returns_actual_result(mobile):
    api,_,_=phone(mobile)
    app,fake=mobile
    _,kid=active_mobile(app,fake)
    app.state.db.update_kid(kid,bonus_day=app.state.svc.today(),bonus_minutes=590)
    r=save(api,f'children/{kid}/controls',{'action':'bonus','value':60,'expected_day':app.state.svc.today()},app.state.db.kid(kid)['revision'])
    assert r.status_code==200 and r.json()['data']['resource']['bonus_minutes']==600
