from fastapi.testclient import TestClient

from tests.mobile_dev_server import create_fixture
from .test_mobile_api import mobile  # noqa: F401
from .test_app import env  # noqa: F401


def test_mobile_fixture_is_fake_only_and_production_has_no_dev_routes(mobile):
    production,_=mobile
    assert not any(getattr(route,'path','').startswith('/dev/') for route in production.routes)
    fixture=create_fixture()
    with TestClient(fixture,base_url='https://localhost:8792',follow_redirects=True) as client:
        page=client.get('/dev/login?u=alex')
        assert page.status_code==200 and 'Connect a phone' in page.text
        assert fixture.state.db.kid(1)['name']=='River'
    fixture.state.fixture_folder.cleanup()
