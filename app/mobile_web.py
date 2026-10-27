"""Cookie/CSRF-only browser pairing and phone management, no bearer crossover."""
from __future__ import annotations

import io
import secrets
import time

import segno
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from markupsafe import Markup
from starlette.concurrency import run_in_threadpool

from .mobile_protocol import MobileError, fields, strict_json
from .mobile_routes import error_response


def register_web(app,api,render):
    router=APIRouter()

    def action(request,kind,device_id=None):
        write=kind in ('issue','cancel','revoke')
        if not api.enabled:
            raise MobileError(404,'not_found')
        api.transport(request)
        with api.adapter.locked():
            api.adapter.refresh(write)
            with api.core.transaction() as conn:
                issuer=api.adapter.issuer(request,conn,write)
                if kind=='cancel':
                    conn.execute('UPDATE mobile_pairings SET revoked_at=? WHERE subject_id=? AND consumed_at IS NULL AND revoked_at IS NULL',(time.time(),issuer['subject_id']))
                elif kind=='revoke':
                    actor=dict(issuer,id='web')
                    row=api.device_row(conn,actor,device_id)
                    if row['revoked_at'] is None:
                        conn.execute('UPDATE mobile_devices SET revoked_at=?,revision=revision+1 WHERE id=?',(time.time(),device_id))
                        api.core.audit(conn,issuer['subject_id'],'phone_revoked',device=device_id)
                elif kind=='devices':
                    rows=conn.execute('SELECT * FROM mobile_devices ORDER BY created_at DESC LIMIT 100').fetchall() if issuer['issued_role']=='owner' else conn.execute('SELECT * FROM mobile_devices WHERE subject_id=? ORDER BY created_at DESC LIMIT 100',(issuer['subject_id'],)).fetchall()
                    phones=[api.core.device(r,None) for r in rows]
                elif kind=='status':
                    row=conn.execute('SELECT * FROM mobile_pairings WHERE subject_id=? ORDER BY created_at DESC LIMIT 1',(issuer['subject_id'],)).fetchone()
                    state='expired'
                    if row and row['consumed_at'] is not None:
                        state='used'
                    elif row and row['revoked_at'] is None and row['expires_at']>time.time():
                        state='open'
            if kind=='issue':
                return api.core.issue(request)
            if kind=='devices':
                return page(request,'mobile_devices.html',phones=phones)
            if kind=='status':
                return JSONResponse(dict(state=state))
            if kind=='page':
                return page(request,'mobile_pairing.html',pairing=None)

    async def csrf(request):
        if request.headers.get('origin') not in (None,api.adapter.origin) or request.headers.get('sec-fetch-site') not in (None,'same-origin','none'):
            raise MobileError(403,'csrf_failed')
        if request.headers.get('content-type','').startswith('application/json'):
            body=strict_json(await request.body())
            fields(body,())
            sent=request.headers.get('x-csrf-token','')
        else:
            data=await request.form(max_files=0,max_fields=2)
            if set(data)-{'csrf'}:
                raise MobileError(422,'invalid_fields')
            sent=str(data.get('csrf',''))
        expected=request.session.get('csrf')
        if not expected or not sent.isascii() or not secrets.compare_digest(sent,expected):
            raise MobileError(403,'csrf_failed')

    def page(request,name,**context):
        result=render(request,name,nav='phones',**context)
        result.headers['Cache-Control']='no-store'
        result.headers['Referrer-Policy']='no-referrer'
        result.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return result

    @router.get('/mobile/pairings')
    async def pairing_page(request:Request):
        try:
            return await run_in_threadpool(action,request,'page')
        except MobileError as exc:
            return error_response(exc)

    @router.post('/mobile/pairings')
    async def create_pairing(request:Request):
        try:
            await csrf(request)
            pairing=await run_in_threadpool(action,request,'issue')
            if 'application/json' in request.headers.get('accept',''):
                return JSONResponse(pairing,status_code=201)
            output=io.BytesIO()
            segno.make(pairing['qr_text'],micro=False,error='m').save(output,kind='svg',scale=4,xmldecl=False)
            qr=Markup(output.getvalue().decode().replace('<svg ', '<svg role="img" aria-labelledby="qr-caption" ',1))
            return page(request,'mobile_pairing.html',pairing=pairing,qr=qr)
        except MobileError as exc:
            return error_response(exc)

    @router.get('/mobile/pairings/status')
    async def pairing_status(request:Request):
        try:
            return await run_in_threadpool(action,request,'status')
        except MobileError as exc:
            return error_response(exc)

    @router.post('/mobile/pairings/cancel')
    async def cancel_pairing(request:Request):
        try:
            await csrf(request)
            await run_in_threadpool(action,request,'cancel')
            return Response(status_code=204)
        except MobileError as exc:
            return error_response(exc)

    @router.get('/mobile/devices')
    async def devices_page(request:Request):
        try:
            return await run_in_threadpool(action,request,'devices')
        except MobileError as exc:
            return error_response(exc)

    @router.post('/mobile/devices/{device_id}/revoke')
    async def revoke(request:Request,device_id:str):
        try:
            await csrf(request)
            await run_in_threadpool(action,request,'revoke',device_id)
            return RedirectResponse('/mobile/devices',status_code=303)
        except MobileError as exc:
            return error_response(exc)

    app.include_router(router)
