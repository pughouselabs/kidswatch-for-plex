"""Bearer-only mobile transport and domain adapter dispatch, reusable across editions."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
import uuid
from contextlib import closing
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request as StarletteRequest

from .mobile_core import MobileCore
from .mobile_protocol import (MESSAGES, MobileError, canonical_origin, fields, identifier,
                              lan_literal, stamp, strict_json, string, validate_wire)
from .security import ClientAddress, rate_key

PREFIX='/api/mobile/v1'


def envelope(data=None,error=None,replayed=False):
    meta=dict(request_id=secrets.token_urlsafe(12),server_time=stamp(),api_version=1)
    if replayed:
        meta['replayed']=True
    return {'error':error,'meta':meta} if error else {'data':data,'meta':meta}


class MobileErrorResponse(JSONResponse):
    """Identify errors authored by this protocol, independently of Content-Type.

    The shared request state survives web middleware response wrapping. The
    marker is server-only and never becomes a response header or cookie.
    """
    async def __call__(self,scope,receive,send):
        scope.setdefault('state',{})['_kidswatch_mobile_error']=True
        await super().__call__(scope,receive,send)


def error_response(exc):
    error=dict(code=exc.code,message=MESSAGES.get(exc.code,'This request could not be completed.'),fields=exc.fields,
               retryable=exc.status in (429,503) or exc.code=='operation_in_progress')
    headers={'Cache-Control':'no-store','Vary':'Authorization'}
    if exc.retry:
        headers['Retry-After']=str(min(exc.retry,3600))
    return MobileErrorResponse(envelope(error=error),status_code=exc.status,headers=headers)


class MobileBoundary:
    """Outer ASGI boundary covers body limits, framework errors, redirects, cookies and cache."""
    def __init__(self,app,session_cookie="pk_session"):
        self.app=app
        self.session_cookie=session_cookie

    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or not (scope['path'].startswith('/api/mobile') or scope['path'].startswith('/mobile/')):
            return await self.app(scope,receive,send)
        api=scope['path'].startswith('/api/mobile')
        state=scope.setdefault('state',{})
        request=StarletteRequest(scope)
        headers=request.headers
        try:
            length=headers.get('content-length')
            if length is not None and (not re.fullmatch(r'[0-9]{1,12}',length) or int(length)>32768):
                raise MobileError(413,'payload_too_large')
            if len(headers.getlist('authorization'))>1 or len(headers.getlist('content-type'))>1:
                raise MobileError(400,'invalid_json')
            if api:
                if headers.get('authorization') and self.session_cookie in request.cookies:
                    raise MobileError(401,'reconnect_required')
                if request.method not in ('GET','HEAD') and headers.get('origin'):
                    raise MobileError(403,'csrf_failed')
                # Ignore all cookies as API credentials, and never let SessionMiddleware renew one.
                scope=dict(scope,headers=[(k,v) for k,v in scope['headers'] if k.lower()!=b'cookie'])
            elif headers.get('authorization'):
                raise MobileError(401,'reconnect_required')
            size=0
            chunks=[]
            while True:
                msg=await receive()
                if msg['type']!='http.request':
                    return
                size+=len(msg.get('body',b''))
                if size>32768:
                    raise MobileError(413,'payload_too_large')
                chunks.append(msg.get('body',b''))
                if not msg.get('more_body'):
                    break
            replayed=False
            async def replay():
                nonlocal replayed
                if replayed:
                    return await receive()
                replayed=True
                return {'type':'http.request','body':b''.join(chunks),'more_body':False}
            started=False
            replacing=False
            async def filtered(message):
                nonlocal started,replacing
                if message['type']=='http.response.start':
                    status=message['status']
                    if api and (300<=status<400 or (status>=400 and not state.get('_kidswatch_mobile_error'))):
                        replacing=True
                        await error_response(MobileError(404 if status<500 else 503,'not_found' if status<500 else 'server_unavailable',retry=30 if status>=500 else None))(scope,replay,send)
                        started=True
                        return
                    if not api:
                        message=dict(message,headers=[(k,v) for k,v in message.get('headers',[]) if k.lower()!=b'content-security-policy'])
                    extra=([(b'content-security-policy',b"default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")] if not api else [])+[(b'cache-control',b'no-store'),(b'vary',b'Authorization'),(b'referrer-policy',b'no-referrer')]
                    removed=(b'cache-control',b'vary',b'referrer-policy',b'access-control-allow-origin')+( (b'set-cookie',) if api else ())
                    message=dict(message,headers=[(k,v) for k,v in message.get('headers',[]) if k.lower() not in removed]+extra)
                    started=True
                if not replacing:
                    await send(message)
            try:
                await self.app(scope,replay,filtered)
            except Exception:
                if started:
                    raise
                await error_response(MobileError(503,'server_unavailable',retry=30))(scope,replay,send)
        except MobileError as exc:
            await error_response(exc)(scope,receive,send)


class MobileAPI:
    def __init__(self,adapter,enabled=False,lan_origin=''):
        self.adapter=adapter
        self.core=MobileCore(adapter)
        self.enabled=enabled
        self.lan_origin=lan_origin
        self.address=ClientAddress(adapter.cfg.trusted_proxies,adapter.cfg.trust_cf_headers)

    def transport(self,request):
        configured=urlsplit(self.adapter.origin)
        peer=request.client.host if request.client else ''
        if configured.scheme=='https':
            secure=request.url.scheme=='https' or (self.address.is_trusted(peer) and request.headers.get('x-forwarded-proto')=='https')
            if secure and request.headers.get('host','').lower()==configured.netloc.lower():
                return
        elif (self.lan_origin==self.adapter.origin and lan_literal(configured.hostname or '')
              and request.url.scheme=='http' and request.headers.get('host','').lower()==configured.netloc.lower()
              and lan_literal(peer) and not any(h in request.headers for h in ('forwarded','x-forwarded-for','x-forwarded-proto','cf-connecting-ip'))):
            return
        raise MobileError(403,'access_removed')

    def client_headers(self,request):
        if 'application/json' not in request.headers.get('accept',''):
            raise MobileError(415,'json_required')
        if not re.fullmatch(r'android/[1-9][0-9]{0,8}',request.headers.get('x-kidswatch-client','')):
            raise MobileError(426,'upgrade_required',{'api_versions':[1],'min_client_version_code':1})

    def perform_effect(self,token,op_id):
        try:
            with self.adapter.locked():
                self.adapter.effect(self.core,token,op_id)
        except Exception:
            # An intent was committed; return its accepted response even if verification fails.
            with self.core.transaction() as conn:
                conn.execute("UPDATE mobile_operations SET state='unknown',updated_at=?,safe_error_code='server_unavailable' WHERE id=? AND state='pending'",(time.time(),op_id))

    def authenticate(self,token,write):
        self.adapter.refresh(write)
        with self.core.transaction() as conn:
            return self.core.authenticate(conn,token,write)

    def device_row(self,conn,actor,device_id):
        row=conn.execute('SELECT * FROM mobile_devices WHERE id=?',(device_id,)).fetchone()
        if not row or (actor['issued_role']!='owner' and row['subject_id']!=actor['subject_id']):
            raise MobileError(404,'not_found')
        return row

    def resource(self,conn,actor,kind,rid,prepared):
        if kind=='device':
            return self.core.device(self.device_row(conn,actor,rid),actor['id'])
        return self.adapter.resource(conn,actor,kind,rid,prepared)

    def operation(self,conn,actor,op_id,prepared):
        row=conn.execute('SELECT * FROM mobile_operations WHERE id=?',(op_id,)).fetchone()
        if not row or (actor['issued_role']!='owner' and row['subject_id']!=actor['subject_id']):
            raise MobileError(404,'not_found')
        self.resource(conn,actor,row['kind'],row['resource_id'],prepared)
        reconcile=getattr(self.adapter,'reconcile_operation',None)
        if reconcile:
            row=reconcile(conn,actor,row,prepared)
        return dict(operation_id=row['id'],resource_revision=row['result_revision'],state=row['state'],resource=None)

    def page(self,items,actor,path,query):
        limit=query.get('limit','50')
        if not re.fullmatch(r'[0-9]{1,3}',limit) or not 1<=int(limit)<=100:
            raise MobileError(422,'invalid_fields',{'limit':'Choose 1 to 100 items.'})
        limit=int(limit)
        scope=path+'?'+json.dumps({k:v for k,v in query.items() if k not in ('limit','cursor')},sort_keys=True)
        start=self.core.decode_cursor(query['cursor'],actor['subject_id'],scope) if query.get('cursor') else 0
        selected=items[start:start+limit]
        cursor=self.core.cursor(actor['subject_id'],scope,start+limit) if len(items)>start+limit else None
        return dict(items=selected,next_cursor=cursor)

    def events(self,conn,actor,query,prepared):
        limit=query.get('limit','50')
        if not re.fullmatch(r'[0-9]{1,3}',limit) or not 1<=int(limit)<=100:
            raise MobileError(422,'invalid_fields',{'limit':'Choose 1 to 100 events.'})
        conn.execute('DELETE FROM mobile_events WHERE at<?',(time.time()-7*86400,))
        reset=False
        try:
            after=self.core.decode_cursor(query['after'],actor['subject_id'],'events') if query.get('after') else 0
        except MobileError:
            # Expired/unknown cursor is a refresh hint. It contains no permissions.
            after=conn.execute('SELECT coalesce(max(id),0) FROM mobile_events').fetchone()[0]
            reset=True
        first=conn.execute('SELECT min(id) FROM mobile_events').fetchone()[0]
        if after and first and after<first-1:
            reset=True
        items=[]
        last=after
        if not reset:
            for row in conn.execute('SELECT * FROM mobile_events WHERE id>? ORDER BY id',(after,)):
                last=row['id']
                visible=actor['issued_role']=='owner'
                if not visible:
                    if row['kind'] in ('request_changed','access_changed'):
                        visible=row['subject_id']==actor['subject_id']
                    elif row['kind']=='child_changed':
                        try:
                            self.resource(conn,actor,'child',row['resource_id'],prepared)
                            visible=True
                        except MobileError as exc:
                            if exc.status!=404:
                                raise
                if visible:
                    # Recheck even owner resource visibility before returning a hint.
                    kind={'kids':'child','requests':'request','parent_invitations':'invite'}.get(row['resource_kind'],row['resource_kind'])
                    try:
                        self.resource(conn,actor,kind,row['resource_id'],prepared)
                    except MobileError as exc:
                        if exc.status==404:
                            continue
                        raise
                    items.append(dict(id=str(row['id']),at=stamp(row['at']),kind=row['kind'],resource_id=row['resource_id']))
                    if len(items)>=int(limit):
                        break
        return dict(items=items,next_cursor=self.core.cursor(actor['subject_id'],'events',last),reset=reset)

    def read(self,conn,actor,path,query,prepared):
        if path=='devices':
            rows=conn.execute('SELECT * FROM mobile_devices ORDER BY created_at DESC').fetchall()
            return [self.core.device(r,actor['id']) for r in rows if actor['issued_role']=='owner' or r['subject_id']==actor['subject_id']],'device',None
        if path.startswith('operations/'):
            return self.operation(conn,actor,path.split('/')[1],prepared),'mutation',None
        if path=='events':
            return self.events(conn,actor,query,prepared),'events',None
        return self.adapter.read(conn,actor,path,query,prepared)

    def handle(self,request,path,body):
        if not self.enabled:
            raise MobileError(404,'not_found')
        self.client_headers(request)
        self.transport(request)
        query=dict(request.query_params)
        if len(query)!=len(request.query_params.multi_items()):
            raise MobileError(422,'invalid_fields')
        if any(len(part)>128 for part in path.split('/')):
            raise MobileError(422,'invalid_fields')
        write=request.method!='GET'
        allowed_query={'limit','cursor'} if not write else set()
        if '/titles' in path:
            allowed_query|={'q','choice'} if not write else set()
        if '/activity' in path:
            allowed_query|={'from','to'}
        if path=='requests' and not write:
            allowed_query.add('status')
        if path=='events':
            allowed_query={'after','limit'}
        if path.endswith(('/settings','/schedule')):
            allowed_query=set()
        if path in ('meta','session','pairings/redeem') or path.startswith('operations/') or (path.startswith('children/') and path.count('/')<2):
            allowed_query=set()
        if set(query)-allowed_query:
            raise MobileError(422,'invalid_fields')
        auth=request.headers.get('authorization','')
        if path=='meta' and request.method=='GET':
            return JSONResponse(envelope(dict(instance_id=self.adapter.instance_id,edition=self.adapter.edition,api_versions=[1],min_client_version_code=1,pairing_enabled=True)))
        if path=='pairings/redeem' and request.method=='POST':
            if auth:
                raise MobileError(401,'reconnect_required')
            fields(body,('code','instance_id','device_label'))
            label=string(body['device_label'],'device_label',60).strip()
            if not label:
                raise MobileError(422,'invalid_fields',{'device_label':'Enter a phone name.'})
            with self.adapter.locked():
                token,device=self.core.redeem(body['code'],body['instance_id'],label,rate_key(self.address.ip(request)))
            session=self.adapter.session(device)
            return JSONResponse(envelope(dict(token_type='Bearer',access_token=token,device=self.core.device(device,device['id']),session=session)),status_code=201)
        if not re.fullmatch(r'Bearer [A-Za-z0-9_-]{43}',auth):
            raise MobileError(401,'reconnect_required')
        token=auth[7:]
        # Edition locks precede refresh, then SQLite; private adapters may use an existing reentrant file lock.
        with self.adapter.locked():
            actor=self.authenticate(token,write)
            prepared=self.adapter.prepare(path,body if write else None,actor)
            self.adapter.refresh(write)
            with self.core.transaction() as conn:
                actor=self.core.authenticate(conn,token,write)
                if not write:
                    data,kind,revision=self.read(conn,actor,path,query,prepared)
                    if isinstance(data,list):
                        for item in data:
                            if kind!='recipient':
                                validate_wire(kind,item)
                        data=self.page(data,actor,path,query)
                    elif kind in ('child','settings','schedule','session','mutation','events'):
                        validate_wire(kind,data)
                    headers={'ETag':f'"r{revision}"'} if revision is not None else {}
                    return JSONResponse(envelope(data),headers=headers)
                return self.write(conn,actor,request,path,body,prepared,token)

    def write(self,conn,actor,request,path,body,prepared,token):
        key=request.headers.get('idempotency-key','')
        try:
            if str(uuid.UUID(key))!=key.lower():
                raise ValueError()
        except ValueError:
            raise MobileError(422,'invalid_fields',{'Idempotency-Key':'Use a UUID for this save.'}) from None
        intent=hashlib.sha256((request.method+'\n'+path+'\n'+json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=True)).encode()).hexdigest()
        replay=self.core.replay(conn,actor,key,intent)
        if replay:
            resource=self.resource(conn,actor,replay['resource_kind'],replay['resource_id'],prepared)
            if replay['status']==201:
                if replay['resource_kind']=='invite':
                    resource|={'link_available':False}
                data=resource
            else:
                op=conn.execute('SELECT * FROM mobile_operations WHERE id=?',(replay['operation_id'],)).fetchone()
                data=dict(operation_id=op['id'],resource_revision=op['result_revision'],state=op['state'],resource=None)
            return JSONResponse(envelope(data,replayed=True),status_code=replay['status'])
        device_path=path.startswith('devices/') and path.count('/')==1 and request.method=='DELETE'
        if device_path:
            row=self.device_row(conn,actor,path.split('/')[1])
            kind,rid,revision='device',row['id'],row['revision']
        else:
            kind,rid,revision=self.adapter.target(conn,actor,path,prepared)
        if revision is not None:
            if not request.headers.get('if-match'):
                raise MobileError(428,'revision_required')
            if request.headers['if-match']!=f'"r{revision}"':
                raise MobileError(412,'revision_changed')
        op_id=secrets.token_urlsafe(18)
        if device_path:
            fields(body,())
            conn.execute('UPDATE mobile_devices SET revoked_at=coalesce(revoked_at,?),revision=revision+1 WHERE id=?',(time.time(),rid))
            self.core.audit(conn,actor['subject_id'],'phone_revoked',device=rid)
            revision=conn.execute('SELECT revision FROM mobile_devices WHERE id=?',(rid,)).fetchone()[0]
            status,data,effect=200,None,None
        else:
            status,data,kind,rid,revision,effect=self.adapter.mutate(conn,actor,path,body,prepared,op_id)
        now=time.time()
        state='pending' if effect else 'applied'
        conn.execute('INSERT INTO mobile_operations(id,device_id,subject_id,kind,resource_id,result_revision,state,created_at,updated_at,effect) VALUES(?,?,?,?,?,?,?,?,?,?)',
                     (op_id,actor['id'],actor['subject_id'],kind,rid,revision,state,now,now,effect))
        conn.execute('INSERT INTO mobile_idempotency(device_id,key,request_hash,created_at,expires_at,tombstone_until,status,operation_id,resource_kind,resource_id,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                     (actor['id'],key,intent,now,now+86400,now+8*86400,status,op_id,kind,rid,revision))
        # Commit intent+operation+idempotency before effects. This function runs inside
        # the caller's transaction context; background dispatch starts only after it exits.
        if data is None:
            resource=self.resource(conn,actor,kind,rid,prepared)
            data=dict(operation_id=op_id,resource_revision=revision,state=state,resource=resource)
        validate_wire(kind if status==201 else 'mutation',data)
        response=JSONResponse(envelope(data),status_code=status,headers={'ETag':f'"r{revision}"'})
        response.mobile_effect=(token,op_id) if effect else None
        return response


def register(app,api):
    router=APIRouter()

    @router.api_route(PREFIX+'/{path:path}',methods=['GET','POST','PATCH','PUT','DELETE'])
    async def dispatch(request: Request,path: str):
        try:
            # Fixed route/method surface. Unknown paths never fall through to HTML handlers.
            routes=[('GET',r'meta|session|children|children/[A-Za-z0-9_-]+(?:/(?:settings|schedule|titles|activity))?|requests|owner/(?:approvals|invite-recipients|invites)|devices|operations/[A-Za-z0-9_-]+|events'),
                    ('POST',r'pairings/redeem|children/[A-Za-z0-9_-]+/(?:controls|titles)|requests|requests/[A-Za-z0-9_-]+/(?:cancel|decision)|owner/approvals/[A-Za-z0-9_-]+/decision|owner/invites'),
                    ('PATCH',r'children/[A-Za-z0-9_-]+/settings'),('PUT',r'children/[A-Za-z0-9_-]+/schedule'),('DELETE',r'(?:devices|owner/invites)/[A-Za-z0-9_-]+')]
            if not any(request.method==method and re.fullmatch(pattern,path) for method,pattern in routes):
                raise MobileError(404,'not_found')
            raw=await request.body()
            if request.method!='GET':
                if request.headers.get('content-type','').lower() not in ('application/json','application/json; charset=utf-8'):
                    raise MobileError(415,'json_required')
                body=strict_json(raw) if raw else {}
            else:
                if raw:
                    raise MobileError(400,'invalid_json')
                body=None
            response=await run_in_threadpool(api.handle,request,path,body)
            effect=getattr(response,'mobile_effect',None)
            if effect:
                # The transaction in handle has closed. Never replay this attempt on GET/retry.
                await run_in_threadpool(api.perform_effect,*effect)
            return response
        except MobileError as exc:
            return error_response(exc)
        except Exception:
            return error_response(MobileError(503,'server_unavailable',retry=30))

    app.include_router(router)
    app.add_middleware(MobileBoundary,session_cookie=getattr(api.adapter,'session_cookie','pk_session'))
