"""No cookie fallback, current authority, scope isolation, safe outage/error boundaries."""
import uuid
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.plex import PlexError
from .fakes import ALEX, SAM, OWNER
from .test_app import csrf, login
from .test_mobile_api import mobile, phone, browser, active_mobile, save, HEADERS  # noqa: F401
from .test_app import env  # noqa: F401


def test_cookie_only_mixed_and_browser_origin_auth(mobile):
    api,web,_=phone(mobile)
    app,_=mobile
    assert web.get('/api/mobile/v1/session',headers=HEADERS).status_code==401
    mixed=TestClient(app,base_url='https://testserver',headers=dict(api.headers))
    mixed.cookies.set('pk_session',web.cookies.get('pk_session'))
    r=mixed.get('/api/mobile/v1/session')
    assert r.status_code==401 and 'set-cookie' not in r.headers
    assert api.post('/api/mobile/v1/requests',json={},headers={'Origin':'https://testserver'}).status_code==403
    assert api.get('/mobile/devices').status_code==401
    assert api.options('/api/mobile/v1/session',headers={'Origin':'https://evil.example','Access-Control-Request-Method':'GET'}).headers.get('access-control-allow-origin') is None


def test_web_csrf_origin_and_fetch_metadata(mobile):
    web=browser(mobile)
    token=csrf(web)
    for headers in ({},{'Origin':'https://evil.example'},{'Sec-Fetch-Site':'cross-site'}):
        data={} if not headers else {'csrf':token}
        r=web.post('/mobile/pairings',data=data,headers=headers)
        assert r.status_code==403,r.text
        assert r.json()['error']['code']=='csrf_failed'
    assert web.get('/mobile/pairings').status_code==200
    assert not mobile[0].state.db.one('SELECT 1 FROM mobile_pairings')


def test_web_pairing_accessible_qr_and_no_stored_raw_code(mobile):
    web=browser(mobile)
    r=web.post('/mobile/pairings',data={'csrf':csrf(web)})
    assert r.status_code==201,r.text
    assert '<svg role="img" aria-labelledby="qr-caption"' in r.text
    assert 'Connection code' in r.text and 'Server address' in r.text
    assert r.headers['referrer-policy']=='no-referrer'
    assert r.headers['cache-control']=='no-store'
    assert 'third-party' not in r.text
    assert web.get('/mobile/pairings').text.count('class="phone-code"')==0
    assert web.post('/mobile/pairings/cancel',data={'csrf':csrf(web)}).status_code==204


def test_device_scope_parent_owner_and_revocation(mobile):
    a,web,data=phone(mobile)
    b,_,other=phone(mobile,'sam')
    owner,_,_=phone(mobile,'owner')
    assert len(a.get('/api/mobile/v1/devices').json()['data']['items'])==1
    assert len(owner.get('/api/mobile/v1/devices').json()['data']['items'])==3
    assert save(b,'devices/'+data['device']['id'],{},1,method='delete').status_code==404
    assert save(owner,'devices/'+data['device']['id'],{},1,method='delete').status_code==200
    assert a.get('/api/mobile/v1/session').status_code==401
    assert b.get('/api/mobile/v1/session').status_code==200
    assert 'My phone' in web.get('/mobile/devices').text


def test_disabled_parent_and_signout_all_revoke_phones(mobile):
    a,_,_=phone(mobile)
    b,_,_=phone(mobile,'sam')
    ownerweb=browser(mobile,'owner')
    assert ownerweb.post('/admin/parents/101',data={'csrf':csrf(ownerweb,'/admin'),'enabled':'0'}).status_code==303
    assert a.get('/api/mobile/v1/session').status_code in (401,403)
    assert b.get('/api/mobile/v1/session').status_code==200
    assert ownerweb.post('/admin/sign-out-all',data={'csrf':csrf(ownerweb,'/admin')}).status_code==303
    assert b.get('/api/mobile/v1/session').status_code==401


def test_unshared_parent_outage_and_role_change(mobile):
    api,_,_=phone(mobile)
    app,fake=mobile
    fake.shared_users=lambda:{SAM.id:None}
    app.state.db.x('UPDATE sharing_state SET observed_at=0')
    assert api.get('/api/mobile/v1/session').status_code==403
    fake.shared_users=lambda:(_ for _ in ()).throw(PlexError('pretend-private-key=DO_NOT_LEAK'))
    app.state.db.x('UPDATE sharing_state SET observed_at=0')
    r=api.get('/api/mobile/v1/session')
    assert r.status_code==503 and 'DO_NOT_LEAK' not in r.text
    fake.shared_users=lambda:{ALEX.id:None,SAM.id:None}
    fake.owner=lambda:ALEX
    assert api.get('/api/mobile/v1/session').status_code==403


def test_coparent_child_scope_immediate_and_request_isolation(mobile):
    a,_,_=phone(mobile)
    b,_,_=phone(mobile,'sam')
    app,fake=mobile
    _,kid=active_mobile(app,fake)
    assert b.get(f'/api/mobile/v1/children/{kid}').status_code==404
    app.state.db.x('INSERT INTO kid_parents(kid_id,parent_id) VALUES(?,102)',(kid,))
    assert b.get(f'/api/mobile/v1/children/{kid}').status_code==200
    r=a.post('/api/mobile/v1/requests',json={'child_id':str(kid),'kind':'other','message':'Private submission'},headers={'Idempotency-Key':str(uuid.uuid4())})
    assert r.status_code==201
    assert not b.get('/api/mobile/v1/requests').json()['data']['items']
    events=b.get('/api/mobile/v1/events').json()['data']['items']
    assert all(e['kind']!='request_changed' for e in events)
    app.state.db.x('DELETE FROM kid_parents WHERE kid_id=? AND parent_id=102',(kid,))
    assert b.get(f'/api/mobile/v1/children/{kid}').status_code==404


def test_revocation_during_remote_prepare_wins_at_commit(mobile):
    api,_,data=phone(mobile)
    app,fake=mobile
    _,kid=active_mobile(app,fake)
    revision=app.state.db.kid(kid)['revision']
    def libraries():
        app.state.db.x('UPDATE mobile_devices SET revoked_at=1 WHERE id=?',(data['device']['id'],))
        return {'1':'Movies','2':'Shows'}
    fake.section_titles=libraries
    r=save(api,f'children/{kid}/settings',{'name':'Must not save'},revision,method='patch')
    assert r.status_code==401,r.text
    assert app.state.db.kid(kid)['name']=='River'


def test_feature_off_preserves_web(mobile):
    app,fake=mobile
    off=create_app(replace(app.state.svc.cfg,mobile_enabled=False),fake)
    c=TestClient(off,base_url='https://testserver',headers=HEADERS)
    assert c.get('/api/mobile/v1/meta').status_code==404
    assert c.get('/').status_code==200
    assert 'Connect a phone' not in c.get('/help').text


@pytest.mark.parametrize('payload',[{'kind':[],'child_id':None,'message':'x'},{'kind':'other','child_id':None,'message':False},{'kind':'other','child_id':123,'message':'x'}])
def test_strict_write_types(mobile,payload):
    api,_,_=phone(mobile)
    r=api.post('/api/mobile/v1/requests',json=payload,headers={'Idempotency-Key':str(uuid.uuid4())})
    assert r.status_code==422,r.text


def test_payload_limit_including_chunked_and_json_errors(mobile):
    api,_,_=phone(mobile)
    for content in ('x'*32769, iter([b'x'*20000,b'y'*20000])):
        r=api.post('/api/mobile/v1/requests',content=content,headers={'Content-Type':'application/json'})
        assert r.status_code==413 and r.json()['error']['code']=='payload_too_large'
        assert r.headers['cache-control']=='no-store'
    for raw in ('{"message":1e999}','{"kind":"other","kind":"title"}'):
        assert api.post('/api/mobile/v1/requests',content=raw,headers={'Content-Type':'application/json'}).status_code==400
    assert api.get('/api/mobile/v1/children?extra=1').status_code==422
    assert api.get('/api/mobile/v1/children?limit=101').status_code==422


def test_setup_completion_is_explicit_even_with_parent_row(mobile):
    web=browser(mobile)
    app,_=mobile
    app.state.db.x("DELETE FROM settings WHERE key='setup.completed_at'")
    assert app.state.db.one('SELECT 1 FROM parents WHERE enabled=1')
    r=web.post('/mobile/pairings',data={'csrf':csrf(web)},headers={'Accept':'application/json'})
    assert r.status_code==409 and r.json()['error']['code']=='prerequisite_required'
    app.state.db.set_setting('setup.completed_at','2026-10-26T18:00:00Z')
    assert web.post('/mobile/pairings',data={'csrf':csrf(web)},headers={'Accept':'application/json'}).status_code==201


def test_pairing_session_revocation_and_generic_errors(mobile):
    app,_=mobile
    web=browser(mobile)
    pairing=web.post('/mobile/pairings',data={'csrf':csrf(web)},headers={'Accept':'application/json'}).json()
    # Browser logout retires outstanding codes, while already paired phones remain separate.
    web.post('/logout',data={'csrf':csrf(web)})
    c=TestClient(app,base_url='https://testserver',headers=HEADERS)
    body=dict(code=pairing['code'],instance_id=app.state.mobile.adapter.instance_id,device_label='Phone')
    used=c.post('/api/mobile/v1/pairings/redeem',json=body)
    wrong=c.post('/api/mobile/v1/pairings/redeem',json=body|{'code':'000000000000'})
    other=c.post('/api/mobile/v1/pairings/redeem',json=body|{'instance_id':'other-instance'})
    assert [r.status_code for r in (used,wrong,other)]==[400,400,400]
    assert used.json()['error']==wrong.json()['error']==other.json()['error']


def test_cookie_logout_does_not_revoke_existing_phone(mobile):
    api,web,_=phone(mobile)
    assert web.post('/logout',data={'csrf':csrf(web)}).status_code==303
    assert api.get('/api/mobile/v1/session').status_code==200


def test_waiting_identity_cannot_issue_and_owner_role_change_requires_repair(mobile):
    app,fake=mobile
    waiting=browser(mobile,'casey')
    r=waiting.post('/mobile/pairings',data={'csrf':csrf(waiting,'/waiting')},headers={'Accept':'application/json'})
    assert r.status_code==401
    owner,_,_=phone(mobile,'owner')
    fake.owner=lambda:ALEX
    r=owner.get('/api/mobile/v1/session')
    assert r.status_code==403 and r.json()['error']['code']=='access_removed'
