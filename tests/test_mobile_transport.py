"""Server-side LAN allowlist and proxy trust, without performing any network fetch."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.mobile_protocol import canonical_origin, lan_literal
from .test_mobile_api import mobile,HEADERS  # noqa: F401
from .test_app import env  # noqa: F401


def test_https_is_required_and_forwarded_headers_need_trusted_peer(mobile):
    app,_=mobile
    c=TestClient(app,base_url='http://testserver',headers=HEADERS,client=('192.0.2.10',40000))
    assert c.get('/api/mobile/v1/meta').status_code==403
    assert c.get('/api/mobile/v1/meta',headers={'X-Forwarded-Proto':'https'}).status_code==403


def test_trusted_https_proxy_and_host_binding(mobile):
    app,fake=mobile
    from app.security import parse_networks
    proxied=create_app(replace(app.state.svc.cfg,trusted_proxies=parse_networks('192.0.2.5')),fake)
    c=TestClient(proxied,base_url='http://testserver',headers=HEADERS,client=('192.0.2.5',40000))
    assert c.get('/api/mobile/v1/meta',headers={'X-Forwarded-Proto':'https'}).status_code==200
    assert c.get('/api/mobile/v1/meta',headers={'X-Forwarded-Proto':'https','Host':'evil.example'}).status_code==403


def test_narrow_lan_opt_in_direct_only(mobile):
    app,fake=mobile
    origin='http://192.168.20.10:8080'
    cfg=replace(app.state.svc.cfg,public_url=origin,mobile_lan_origin=origin)
    lan=create_app(cfg,fake)
    c=TestClient(lan,base_url=origin,headers=HEADERS,client=('192.168.20.11',40000))
    assert c.get('/api/mobile/v1/meta').status_code==200
    assert c.get('/api/mobile/v1/meta',headers={'X-Forwarded-For':'192.168.20.11'}).status_code==403
    public=TestClient(lan,base_url=origin,headers=HEADERS,client=('198.51.100.2',40000))
    assert public.get('/api/mobile/v1/meta').status_code==403
    loop=TestClient(lan,base_url=origin,headers=HEADERS,client=('127.0.0.1',40000))
    assert loop.get('/api/mobile/v1/meta').status_code==403
    no_opt=create_app(replace(cfg,mobile_lan_origin=''),fake)
    assert TestClient(no_opt,base_url=origin,headers=HEADERS,client=('192.168.20.11',40000)).get('/api/mobile/v1/meta').status_code==403


@pytest.mark.parametrize('host',['127.0.0.1','169.254.169.254','0.0.0.0','224.0.0.1','8.8.8.8','server.local','::1','fe80::1','ff02::1','::'])
def test_forbidden_http_hosts(host):
    assert not lan_literal(host)


def test_ipv6_ula_and_origin_canonicalization():
    assert lan_literal('fd12:3456::1')
    assert canonical_origin('https://EXAMPLE.com:443/')=='https://example.com'
    assert canonical_origin('https://[fd12:3456::1]:8443')=='https://[fd12:3456::1]:8443'


@pytest.mark.parametrize('method,path',[
    ('OPTIONS','/api/mobile/v1/session'),
    ('TRACE','/api/mobile/v1/session'),
    ('GET','/api/mobile/missing'),
    ('GET','/api/mobile/v1/session/'),
    ('GET','/api/mobile/v1'),
])
@pytest.mark.parametrize('fetch',[False,True])
def test_framework_errors_use_canonical_envelope_and_transport_guards(mobile,method,path,fetch):
    app,_=mobile
    headers=dict(HEADERS)
    if fetch:
        headers['X-Requested-With']='fetch'
    c=TestClient(app,base_url='https://testserver',headers=headers,follow_redirects=False)
    c.cookies.set('pk_session','not-an-api-credential')
    response=c.request(method,path)
    assert response.status_code==404,response.text
    body=response.json()
    assert body['error']=={'code':'not_found','message':'This request could not be completed.','fields':{},'retryable':False}
    assert body['meta']['api_version']==1
    assert body['meta']['request_id'] and body['meta']['server_time'].endswith('Z')
    assert response.headers['content-type'].startswith('application/json')
    assert response.headers['cache-control']=='no-store'
    assert response.headers['vary']=='Authorization'
    assert not any(h in response.headers for h in ('set-cookie','location','access-control-allow-origin'))


@pytest.mark.parametrize('fetch',[False,True])
def test_head_framework_error_has_safe_headers_and_no_body(mobile,fetch):
    app,_=mobile
    headers=dict(HEADERS)
    if fetch:
        headers['X-Requested-With']='fetch'
    response=TestClient(app,base_url='https://testserver',headers=headers,follow_redirects=False).head('/api/mobile/v1/session')
    assert response.status_code==404
    assert response.content==b''  # HTTP HEAD omits the representation body.
    assert response.headers['content-type'].startswith('application/json')
    assert response.headers['cache-control']=='no-store'
    assert response.headers['vary']=='Authorization'
    assert not any(h in response.headers for h in ('set-cookie','location','access-control-allow-origin'))


def test_canonical_domain_error_status_fields_and_retry_after_survive_boundary(mobile):
    from .test_mobile_api import phone
    from app.mobile_protocol import MobileError
    api,_,_=phone(mobile)
    app,_=mobile
    def unavailable(*_):
        raise MobileError(503,'authorization_unavailable',{'safe_field':'Try later.'},retry=17)
    app.state.mobile.adapter.refresh=unavailable
    response=api.get('/api/mobile/v1/session',headers={'X-Requested-With':'fetch'})
    assert response.status_code==503
    assert response.headers['retry-after']=='17'
    assert response.json()['error']=={'code':'authorization_unavailable','message':'The server cannot verify access right now.','fields':{'safe_field':'Try later.'},'retryable':True}
    assert response.json()['meta']['api_version']==1


def test_framework_web_error_handler_is_unchanged(mobile):
    app,_=mobile
    response=TestClient(app,base_url='https://testserver').get('/not-a-web-route',headers={'X-Requested-With':'fetch'})
    assert response.status_code==404
    assert response.json()=={'error':'Not Found'}


@pytest.mark.parametrize('status',[302,404,405,500])
def test_untrusted_json_framework_body_is_replaced_without_header_or_body_leaks(status):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from app.mobile_routes import MobileBoundary
    app=FastAPI()
    @app.get('/api/mobile/v1/probe')
    def unsafe_framework_response():
        return JSONResponse({'error':'fake_backend_credential=must-not-escape'},status_code=status,
                            headers={'Location':'https://untrusted.example','Set-Cookie':'unsafe=1',
                                     'Access-Control-Allow-Origin':'*','Retry-After':'999'})
    app.add_middleware(MobileBoundary)
    response=TestClient(app,base_url='https://testserver',follow_redirects=False).get('/api/mobile/v1/probe')
    assert response.status_code==(503 if status>=500 else 404)
    assert response.json()['error']['code']==('server_unavailable' if status>=500 else 'not_found')
    assert response.json()['meta']['api_version']==1
    assert 'must-not-escape' not in response.text
    assert not any(h in response.headers for h in ('set-cookie','location','access-control-allow-origin'))
    assert response.headers['cache-control']=='no-store'
    assert response.headers['vary']=='Authorization'
    if status>=500:
        assert response.headers['retry-after']=='30'


@pytest.mark.parametrize('status,code,retry',[(400,'invalid_json',None),(412,'revision_changed',None),(422,'invalid_fields',None),(429,'rate_limited',23)])
def test_protocol_authored_errors_keep_original_status_fields_and_retry(status,code,retry):
    from fastapi import FastAPI
    from app.mobile_routes import MobileBoundary,error_response
    from app.mobile_protocol import MobileError
    app=FastAPI()
    @app.get('/api/mobile/v1/probe')
    def domain_error():
        return error_response(MobileError(status,code,{'body':'Safe validation detail.'},retry=retry))
    app.add_middleware(MobileBoundary)
    response=TestClient(app,base_url='https://testserver').get('/api/mobile/v1/probe')
    assert response.status_code==status
    assert response.json()['error']['code']==code
    assert response.json()['error']['fields']=={'body':'Safe validation detail.'}
    assert response.json()['meta']['api_version']==1
    assert not any('kidswatch' in key.lower() for key in response.headers)
    if retry:
        assert response.headers['retry-after']==str(retry)
