"""Fake-only Android handoff fixture is checked against the server wire models."""
import json
from pathlib import Path

from app.mobile_protocol import SCHEMAS, validate_wire


def test_frozen_wire_fixture():
    data = json.loads((Path(__file__).parent / 'fixtures/mobile-v1.json').read_text())
    assert data.keys() == SCHEMAS.keys()
    for kind, envelope in data.items():
        assert envelope['meta']['api_version'] == 1
        validate_wire(kind, envelope['error' if kind == 'errors' else 'data'])


def test_nested_validator_and_nullable_legacy_request_date():
    import copy
    import pytest
    fixture=json.loads((Path(__file__).parent/'fixtures/mobile-v1.json').read_text())
    legacy=copy.deepcopy(fixture['request']['data'])
    legacy['created_at']=None
    validate_wire('request',legacy)
    bad=copy.deepcopy(fixture['child']['data'])
    bad['today']['used_seconds']=True
    with pytest.raises(ValueError):
        validate_wire('child',bad)
    bad=copy.deepcopy(fixture['session']['data'])
    del bad['capabilities']['request_cancel']
    with pytest.raises(ValueError):
        validate_wire('session',bad)
    for level in ('little','kids','teens'):
        child=copy.deepcopy(fixture['child']['data'])
        child['age_level']=level
        validate_wire('child',child)
