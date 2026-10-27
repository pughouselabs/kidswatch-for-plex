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
