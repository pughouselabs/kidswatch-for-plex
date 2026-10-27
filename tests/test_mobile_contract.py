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
