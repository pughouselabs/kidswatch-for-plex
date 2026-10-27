"""Disposable, synthetic-only HTTPS mobile fixture. Never import from production.

Example: python -m tests.mobile_dev_server --origin https://localhost:8792
    --cert <debug-CA-issued-cert> --key <debug-only-key>
The certificate must be trusted by the test browser/Android debug build. This
module never weakens certificate or transport checks in the production app.
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from fastapi import Request
from fastapi.responses import RedirectResponse

from app.config import Config
from app.main import create_app
from app.mobile_protocol import canonical_origin
from .fakes import ACCOUNTS, FakePlex


def create_fixture(origin='https://localhost:8792'):
    origin=canonical_origin(origin)
    if not origin.startswith('https://'):
        raise ValueError('The fake fixture requires HTTPS')
    folder=tempfile.TemporaryDirectory(prefix='kidswatch-mobile-fake-')
    cfg=Config(public_url=origin,plex_prefs='',plex_url='http://fake.invalid',tautulli_url='',tautulli_config='',
               db_path=str(Path(folder.name)/'fixture.db'),tz='America/Denver',ha_webhook_file='',poll_seconds=20,
               seed_parents=('alex','sam'),kid_sections=('1','2'),secure_cookies=True,owner_name='Owner',
               start_enforcer=False,owner_code=False,mobile_enabled=True)
    fake=FakePlex()
    fake.auth_url=lambda code,forward_url:forward_url
    app=create_app(cfg,fake)
    app.state.fixture_folder=folder  # retain until server exits; no production database is opened
    db=app.state.db
    at='2026-10-26T18:00:00Z'
    db.set_setting('setup.completed_at',at)
    db.set_setting('setup.trusted_parents','0')
    for who in ('alex','sam','casey'):
        account=ACCOUNTS['tok-'+who]
        db.x('INSERT INTO parents(plex_id,username,title,enabled,created_at) VALUES(?,?,?,?,?)',
             (account.id,account.username,account.title,int(who!='casey'),at))
    kid=db.x("INSERT INTO kids(parent_id,name,status,age_level,sections,weekday_minutes,weekend_minutes,created_at,updated_at) VALUES(101,'River','draft','kids','1,2',60,120,?,?)",(at,at))
    app.state.svc.sync_inline=True
    app.state.svc.read_shared()
    assert app.state.svc.connect(kid,'tok-kid1')[0]
    app.state.svc.approve(kid,'owner')
    db.add_usage(kid,app.state.svc.today(),1500)
    db.x('INSERT INTO requests(parent_id,kind,message,created_at) VALUES(101,?,?,?)',('other','A fictional request for review.',at))

    @app.get('/dev/login')
    def fixture_login(request:Request,u:str='alex'):
        if u not in ('alex','sam','owner','casey'):
            return RedirectResponse('/dev/login?u=alex',status_code=303)
        account=ACCOUNTS['tok-'+u]
        role='owner' if u=='owner' else ('waiting' if u=='casey' else 'parent')
        request.session.clear()
        request.session['user']=dict(id=account.id,name=account.title,username=account.username,thumb='',role=role)
        request.session['sid']=app.state.sessions.create(account.id,role)
        request.session['epoch']=db.setting('session_epoch','1')
        return RedirectResponse('/mobile/pairings' if role!='waiting' else '/waiting',status_code=303)

    return app


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin',default='https://localhost:8792')
    parser.add_argument('--cert',required=True)
    parser.add_argument('--key',required=True)
    parser.add_argument('--port',type=int,default=8792)
    args=parser.parse_args()
    import uvicorn
    uvicorn.run(create_fixture(args.origin),host='127.0.0.1',port=args.port,
                ssl_certfile=args.cert,ssl_keyfile=args.key,access_log=False)


if __name__=='__main__':
    main()
