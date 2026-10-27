"""Real router tests using only the project's established synthetic Plex fixture."""
import json
import re
import uuid
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from .test_app import env, login, csrf, add_kid, approve_all  # noqa: F401

HEADERS={'Accept':'application/json','X-KidsWatch-Client':'android/1'}


@pytest.fixture
def mobile(env):
    old,fake=env
    app=create_app(replace(old.state.svc.cfg,public_url='https://testserver',mobile_enabled=True),fake)
    app.state.svc.sync_inline=True
    app.state.db.set_setting('setup.completed_at','2026-10-26T18:00:00Z')
    return app,fake


def browser(mobile,username='alex'):
    app,fake=mobile
    c,_=login(app,fake,username)
    c.base_url='https://testserver'
    return c


def phone(mobile,username='alex'):
    app,_=mobile
    web=browser(mobile,username)
    r=web.post('/mobile/pairings',data={'csrf':csrf(web)},headers={'Accept':'application/json'})
    assert r.status_code==201,r.text
    api=TestClient(app,base_url='https://testserver',follow_redirects=False,headers=HEADERS)
    result=api.post('/api/mobile/v1/pairings/redeem',json={'code':r.json()['code'],'instance_id':app.state.mobile.adapter.instance_id,'device_label':'My phone'})
    assert result.status_code==201,result.text
    api.headers['Authorization']='Bearer '+result.json()['data']['access_token']
    return api,web,result.json()['data']


def active_mobile(app,fake):
    c,_=login(app,fake,'alex')
    kid=add_kid(app,fake,c,name='River')
    page=c.get(f'/kids/{kid}').text
    url=re.search(r'value="(https://testserver/link/[^"]+)"',page).group(1)
    path=url.removeprefix('https://testserver')
    other=TestClient(app,base_url='https://testserver',follow_redirects=False)
    assert other.get(path).status_code==200
    fake.next_token='tok-kid1'
    assert other.get(path+'/start').status_code==303
    assert other.get(path+'/done').status_code==200
    approve_all(app,fake)
    assert app.state.db.kid(kid)['status']=='active'
    return c,kid


def save(api,path,body,revision,key=None,method='post'):
    return api.request(method,'/api/mobile/v1/'+path,json=body,headers={'If-Match':f'"r{revision}"','Idempotency-Key':key or str(uuid.uuid4())})


def test_pair_and_all_parent_reads(mobile):
    api,web,result=phone(mobile)
    app,fake=mobile
    _,kid=active_mobile(app,fake)
    for path in ['meta','session','children',f'children/{kid}',f'children/{kid}/settings',f'children/{kid}/schedule',f'children/{kid}/titles',f'children/{kid}/activity','requests','devices','events']:
        r=api.get('/api/mobile/v1/'+path)
        assert r.status_code==200,(path,r.text)
        assert r.headers['cache-control']=='no-store'
        assert r.headers['vary']=='Authorization'
        assert 'set-cookie' not in r.headers
        assert 'owner-secret-token' not in r.text
    assert api.get(f'/api/mobile/v1/children/{kid}').headers['etag'].startswith('"r')
    assert result['session']['subject']['role']=='parent'


def test_controls_version_idempotency_and_real_total(mobile):
    api,web,_=phone(mobile)
    app,fake=mobile
    _,kid=active_mobile(app,fake)
    before=api.get(f'/api/mobile/v1/children/{kid}').json()['data']['revision']
    key=str(uuid.uuid4())
    body=dict(action='bonus',value=15,expected_day=app.state.svc.today())
    first=save(api,f'children/{kid}/controls',body,before,key)
    assert first.status_code==200,first.text
    assert first.json()['data']['resource']['bonus_minutes']==15
    replay=save(api,f'children/{kid}/controls',body,before,key)
    assert replay.status_code==200,replay.text
    assert replay.json()['meta']['replayed']
    assert app.state.db.kid(kid)['bonus_minutes']==15
    changed=save(api,f'children/{kid}/controls',body|{'value':30},before,key)
    assert changed.status_code==409
    assert save(api,f'children/{kid}/controls',body,before).status_code==412
    rev=app.state.db.kid(kid)['revision']
    assert save(api,f'children/{kid}/controls',body|{'expected_day':'2020-01-01'},rev).status_code==409


def test_settings_schedule_titles_mutations(mobile):
    api,web,_=phone(mobile)
    app,fake=mobile
    _,kid=active_mobile(app,fake)
    def rev():
        return app.state.db.kid(kid)['revision']
    r=save(api,f'children/{kid}/settings',{'name':'River','mode':'picked'},rev(),method='patch')
    assert r.status_code==202,r.text
    op=r.json()['data']['operation_id']
    assert api.get('/api/mobile/v1/operations/'+op).json()['data']['state']=='applied'
    schedule=api.get(f'/api/mobile/v1/children/{kid}/schedule').json()['data']
    schedule.pop('revision')
    schedule['weekday']={'minutes':None,'start':'07:00','end':'07:00','any_time':True}
    r=save(api,f'children/{kid}/schedule',schedule,rev(),method='put')
    assert r.status_code==200,r.text
    assert app.state.db.kid(kid)['weekday_start']=='00:00'
    r=save(api,f'children/{kid}/titles',dict(ids=['11'],choice='allow'),rev())
    assert r.status_code==202,r.text
    assert app.state.db.one('SELECT kind FROM kid_titles WHERE kid_id=? AND rating_key=11',(kid,))[0]=='allow'
    assert save(api,f'children/{kid}/titles',dict(ids=['14','13'],choice='allow'),rev()).status_code==422
    assert app.state.db.one('SELECT kind FROM kid_titles WHERE kid_id=? AND rating_key=14',(kid,)) is None


def test_requests_owner_invites_and_decisions(mobile):
    api,_,_=phone(mobile)
    owner,_,_=phone(mobile,'owner')
    created=api.post('/api/mobile/v1/requests',json={'child_id':None,'kind':'other','message':'A fictional request'},headers={'Idempotency-Key':str(uuid.uuid4())})
    assert created.status_code==201,created.text
    request=created.json()['data']
    r=save(owner,'requests/'+request['id']+'/decision',{'decision':'done','note':'Reviewed'},request['revision'])
    assert r.status_code==200,r.text
    assert api.get('/api/mobile/v1/requests').json()['data']['items'][0]['status']=='done'
    recipients=owner.get('/api/mobile/v1/owner/invite-recipients')
    assert recipients.status_code==200,recipients.text
    assert any(r['id']=='103' for r in recipients.json()['data']['items'])
    key=str(uuid.uuid4())
    r=owner.post('/api/mobile/v1/owner/invites',json={'recipient_id':'103','expiry_days':7},headers={'Idempotency-Key':key})
    assert r.status_code==201,r.text
    invite=r.json()['data']
    assert invite['join_url'].startswith('https://testserver/join/')
    replay=owner.post('/api/mobile/v1/owner/invites',json={'recipient_id':'103','expiry_days':7},headers={'Idempotency-Key':key})
    assert replay.status_code==201,replay.text
    assert not replay.json()['data']['link_available'] and 'join_url' not in replay.text
    assert save(owner,'owner/invites/'+invite['id'],{},invite['revision'],method='delete').status_code==200
    assert api.get('/api/mobile/v1/owner/approvals').status_code==404


@pytest.mark.parametrize('method,path,kwargs,status',[('get','session',{},401),('get','not-found',{},404),('post','pairings/redeem',{'content':'{broken','headers':{'Content-Type':'application/json'}},400),('post','pairings/redeem',{'content':'{}','headers':{'Content-Type':'text/plain'}},415)])
def test_api_errors_are_json_no_store(mobile,method,path,kwargs,status):
    app,_=mobile
    c=TestClient(app,base_url='https://testserver',headers=HEADERS)
    r=getattr(c,method)('/api/mobile/v1/'+path,**kwargs)
    assert r.status_code==status,r.text
    assert 'error' in r.json() and r.headers['cache-control']=='no-store'
    assert 'set-cookie' not in r.headers
