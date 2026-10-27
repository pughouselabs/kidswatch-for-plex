"""Edition-independent v1 wire primitives. No media client or credential dependencies."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')


class MobileError(Exception):
    def __init__(self, status, code, fields=None, retry=None):
        self.status, self.code, self.fields, self.retry = status, code, fields or {}, retry


MESSAGES = {
    'pairing_unavailable': 'This code is unavailable. Create a new code on the website.',
    'reconnect_required': 'Reconnect this phone to continue.',
    'access_removed': 'Access to this account was removed. Ask the owner before reconnecting.',
    'revision_changed': 'This changed on another device. Review the latest details.',
    'authorization_unavailable': 'The server cannot verify access right now.',
    'server_unavailable': 'The server cannot complete this request right now.',
    'invalid_fields': 'Check the highlighted fields.',
}


def stamp(value=None):
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, (int, float)):
        value = datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def code_normalize(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    value = value.replace(' ', '').replace('-', '').upper()
    return value if len(value) == 12 and all(c in ALPHABET for c in value) else None


def keyed(secret, domain, value):
    return hmac.new(secret, (domain + ':' + value).encode(), hashlib.sha256).hexdigest()


def canonical_origin(value):
    try:
        u = urlsplit(value)
        if (u.scheme not in ('https', 'http') or not u.hostname or u.username is not None
                or u.password is not None or u.path not in ('', '/') or u.query or u.fragment
                or any(ord(c) < 33 or ord(c) == 127 for c in value) or u.port == 0 or '%' in u.netloc or '\\' in value or any(c.isspace() for c in value)):
            raise ValueError()
        host = u.hostname.encode('idna').decode('ascii').lower()
        if ':' in host:
            host = '[' + ipaddress.IPv6Address(host).compressed + ']'
        port = u.port
        return u.scheme + '://' + host + (':' + str(port) if port and port != (443 if u.scheme == 'https' else 80) else '')
    except (ValueError, UnicodeError):
        raise ValueError('A canonical root HTTPS origin is required') from None


def lan_literal(host):
    try:
        address = ipaddress.ip_address(host)
        networks = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16') if address.version == 4 else ('fc00::/7',)
        return any(address in ipaddress.ip_network(n) for n in networks)
    except ValueError:
        return False


def strict_json(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError()
            result[k] = v
        return result
    try:
        def finite(value):
            result = float(value)
            if not math.isfinite(result):
                raise ValueError()
            return result
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_float=finite,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise MobileError(400, 'invalid_json') from None


def fields(body, required, optional=()):
    if set(body) - set(required) - set(optional) or set(required) - set(body):
        raise MobileError(422, 'invalid_fields', {'body': 'Use the documented fields.'})


def string(value, field, maximum, minimum=1):
    if not isinstance(value, str) or not minimum <= len(value) <= maximum or any(ord(c) < 32 for c in value):
        raise MobileError(422, 'invalid_fields', {field: 'Enter valid text.'})
    return value


def identifier(value, field='id'):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise MobileError(422, 'invalid_fields', {field: 'Choose a valid ID.'})
    return value


# Required output shape validators shared by the adapter and fake fixture. They
# deliberately accept additive response keys; strict write validation is separate.
SCHEMAS = {
    'meta': {'instance_id': str, 'edition': str, 'api_versions': list, 'min_client_version_code': int, 'pairing_enabled': bool},
    'session': {'subject': dict, 'device': dict, 'edition': str, 'timezone': str, 'today': str, 'capabilities': dict, 'enforcement': dict},
    'child': {'id': str, 'name': str, 'status': str, 'revision': int, 'age_level': str, 'mode': str, 'library_ids': list,
              'today': dict, 'state': str, 'paused_until': (str, type(None)), 'bonus_minutes': int, 'later_minutes': int,
              'sync': dict, 'allowed_actions': list},
    'settings': {'revision': int, 'name': str, 'age_level': str, 'mode': str, 'library_ids': list, 'library_choices': list},
    'schedule': {'revision': int, 'timezone': str, 'weekday': dict, 'weekend': dict, 'friday_weekend': bool,
                 'sunday_school': bool, 'break_until': (str, type(None))},
    'title': {'id': str, 'title': str, 'year': (int, type(None)), 'kind': str, 'rating': (str, type(None)),
              'choice': str, 'visible': bool, 'can_allow': bool, 'reason': str},
    'activity': {'id': str, 'at': str, 'child_id': str, 'kind': str, 'summary': str,
                 'actor_display': (str, type(None)), 'seconds': (int, type(None))},
    'request': {'id': str, 'revision': int, 'child_id': (str, type(None)), 'kind': str, 'message': str, 'status': str,
                'owner_note': (str, type(None)), 'created_at': (str, type(None)), 'resolved_at': (str, type(None)), 'allowed_decisions': list},
    'approval': {'id': str, 'revision': int, 'kind': str, 'subject_display': str, 'child_id': (str, type(None)),
                 'state': str, 'prerequisites': list, 'allowed_decisions': list},
    'invite': {'id': str, 'revision': int, 'intended_display': str, 'expires_at': str, 'state': str},
    'device': {'id': str, 'revision': int, 'label': str, 'subject_display': str, 'issued_role': str, 'created_at': str,
               'last_seen_at': str, 'expires_at': str, 'revoked_at': (str, type(None)), 'is_current': bool},
    'mutation': {'operation_id': str, 'resource_revision': int, 'state': str, 'resource': (dict, type(None))},
    'events': {'items': list, 'next_cursor': str, 'reset': bool},
    'errors': {'code': str, 'message': str, 'fields': dict, 'retryable': bool},
}


def validate_wire(kind, value):
    for key, types in SCHEMAS[kind].items():
        if key not in value or type(value[key]) not in (types if isinstance(types, tuple) else (types,)):
            raise ValueError(f'{kind}.{key}: invalid wire type')
    if 'revision' in value and value['revision'] < 1:
        raise ValueError('Invalid revision')
    return value
