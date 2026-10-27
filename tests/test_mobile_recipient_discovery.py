"""Invitation discovery must advertise the same latest-decision prerequisite as POST."""
import uuid

import pytest

from app.plex import SharedUser
from .test_app import env  # noqa: F401
from .test_mobile_api import mobile, phone, browser  # noqa: F401


@pytest.fixture(autouse=True)
def enabled_parent_fixtures(mobile):
    # Configured seed users become enabled rows through the genuine fake login flow.
    browser(mobile,'alex')
    browser(mobile,'sam')
    assert mobile[0].state.db.one('SELECT count(*) FROM parents WHERE enabled=1')[0]==2


@pytest.mark.parametrize('history,eligible',[
    ([],True),
    (['declined'],False),
    (['approved'],False),
    (['done'],False),
    (['unknown_closed'],False),
    (['declined','open'],True),
    (['declined','cancelled'],True),
    (['open','declined'],False),
    (['cancelled','approved'],False),
])
def test_recipient_discovery_and_invite_post_agree_on_latest_access_decision(mobile,history,eligible):
    owner,_,_=phone(mobile,'owner')
    app,_=mobile
    db=app.state.db
    for index,status in enumerate(history):
        # Reverse timestamp order proves that latest means highest request ID,
        # exactly as the existing invitation mutation guard defines it.
        db.x("INSERT INTO requests(parent_id,kind,message,status,created_at) VALUES(103,'access',?,?,?)",
             ('Fictional access request',status,f'2026-10-{26-index:02d}T18:00:00Z'))
    response=owner.get('/api/mobile/v1/owner/invite-recipients')
    assert response.status_code==200
    data=response.json()
    assert data['data']=={'items':[{'id':'103','display_name':'Casey'}] if eligible else [],'next_cursor':None}
    assert data['meta']['api_version']==1 and data['meta']['request_id']
    assert response.headers['cache-control']=='no-store'
    assert response.headers['vary']=='Authorization'
    assert 'set-cookie' not in response.headers
    assert db.one('SELECT count(*) FROM parent_invitations')[0]==0
    result=owner.post('/api/mobile/v1/owner/invites',json={'recipient_id':'103','expiry_days':7},
                      headers={'Idempotency-Key':str(uuid.uuid4())})
    if eligible:
        assert result.status_code==201
        assert db.one('SELECT count(*) FROM parent_invitations')[0]==1
    else:
        assert result.status_code==409
        assert result.json()['error']['code']=='prerequisite_required'
        assert db.one('SELECT count(*) FROM parent_invitations')[0]==0
        assert db.one('SELECT count(*) FROM mobile_operations')[0]==0
    if history:
        assert db.one("SELECT status FROM requests WHERE parent_id=103 AND kind='access' ORDER BY id DESC LIMIT 1")[0]==history[-1]


def test_unrelated_request_decision_does_not_hide_eligible_recipient(mobile):
    owner,_,_=phone(mobile,'owner')
    app,_=mobile
    app.state.db.x("INSERT INTO requests(parent_id,kind,message,status,created_at) VALUES(103,'other','A fictional request','declined','2026-10-26T18:00:00Z')")
    response=owner.get('/api/mobile/v1/owner/invite-recipients')
    assert response.status_code==200
    assert response.json()['data']['items']==[{'id':'103','display_name':'Casey'}]
    result=owner.post('/api/mobile/v1/owner/invites',json={'recipient_id':'103','expiry_days':7},
                      headers={'Idempotency-Key':str(uuid.uuid4())})
    assert result.status_code==201


def test_filtered_recipients_keep_pagination_and_owner_scope(mobile):
    owner,_,_=phone(mobile,'owner')
    parent,_,_=phone(mobile)
    app,fake=mobile
    users=fake.shared_users()
    users[104]=SharedUser(104,'taylor','Taylor','')
    users[105]=SharedUser(105,'morgan','Morgan','')
    fake.shared_users=lambda:dict(users)
    app.state.db.x("INSERT INTO requests(parent_id,kind,message,status,created_at) VALUES(103,'access','A fictional request','declined','2026-10-26T18:00:00Z')")
    first=owner.get('/api/mobile/v1/owner/invite-recipients?limit=1')
    assert first.status_code==200
    assert first.json()['data']['items']==[{'id':'104','display_name':'Taylor'}]
    cursor=first.json()['data']['next_cursor']
    assert cursor
    second=owner.get('/api/mobile/v1/owner/invite-recipients',params={'limit':'1','cursor':cursor})
    assert second.status_code==200
    assert second.json()['data']=={'items':[{'id':'105','display_name':'Morgan'}],'next_cursor':None}
    denied=parent.get('/api/mobile/v1/owner/invite-recipients',params={'limit':'1','cursor':cursor})
    assert denied.status_code==404
    assert 'Casey' not in first.text+second.text+denied.text
    assert 'Morgan' not in denied.text and 'Taylor' not in denied.text
    assert app.state.db.one('SELECT count(*) FROM parent_invitations')[0]==0
