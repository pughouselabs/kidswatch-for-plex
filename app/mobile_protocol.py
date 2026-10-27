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
    if not isinstance(value, str) or not value.isascii() or len(value) > 40:
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
    if not isinstance(value, str) or not minimum <= len(value) <= maximum or any(ord(c) < 32 and not (field in ('message','note') and c in '\r\n\t') for c in value):
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
    'request': {'submitter_display': (str, type(None)), 'can_cancel': bool, 'id': str, 'revision': int, 'child_id': (str, type(None)), 'kind': str, 'message': str, 'status': str,
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


_shape_wire = validate_wire


def validate_wire(kind, value):
    """Validate nested v1 DTOs, including bool/int distinctions and nullable dates."""
    _shape_wire(kind, value)
    def obj(data, schema):
        if type(data) is not dict:
            raise ValueError('Expected object')
        for key, types in schema.items():
            allowed=types if isinstance(types,tuple) else (types,)
            if key not in data or type(data[key]) not in allowed:
                raise ValueError('Invalid nested field: '+key)
    def enum(data, key, choices):
        if data[key] not in choices:
            raise ValueError('Invalid enum: '+key)
    def ident(value):
        if type(value) is not str or not ID.fullmatch(value):
            raise ValueError('Invalid opaque ID')
    def ids(values,nonempty=False):
        if type(values) is not list or len(values)>100 or (nonempty and not values):
            raise ValueError('Invalid ID list')
        for item in values:
            ident(item)
        if len(set(values))!=len(values):
            raise ValueError('Duplicate IDs')
    def timestamp(value,nullable=False):
        if nullable and value is None:
            return
        if type(value) is not str or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ',value):
            raise ValueError('Invalid UTC timestamp')
        datetime.fromisoformat(value.replace('Z','+00:00'))
    def day(value,nullable=False):
        from datetime import date
        if nullable and value is None:
            return
        if type(value) is not str or not re.fullmatch(r'\d{4}-\d\d-\d\d',value):
            raise ValueError('Invalid date')
        date.fromisoformat(value)
    def nonnegative(value,nullable=False):
        if nullable and value is None:
            return
        if type(value) is not int or value<0:
            raise ValueError('Invalid nonnegative integer')
    if 'id' in value:
        ident(value['id'])
    if kind=='meta':
        ident(value['instance_id'])
        enum(value,'edition',('plex','jellyfin'))
        if value['api_versions']!=[1] or value['min_client_version_code']<1:
            raise ValueError('Invalid API version')
    elif kind=='session':
        obj(value['subject'],{'id':str,'display_name':str,'role':str})
        ident(value['subject']['id'])
        enum(value['subject'],'role',('parent','owner'))
        obj(value['device'],{'id':str,'label':str,'expires_at':str,'idle_expires_at':str})
        ident(value['device']['id'])
        timestamp(value['device']['expires_at'])
        timestamp(value['device']['idle_expires_at'])
        day(value['today'])
        enum(value,'edition',('plex','jellyfin'))
        from zoneinfo import ZoneInfo
        ZoneInfo(value['timezone'])
        cap=value['capabilities']
        obj(cap,{'controls':list,'editable_settings':list,'bonus_choices':list,'later_choices':list,'bonus_cap':int,'later_cap':int,
                 'request_kinds':list,'request_message_max':int,'request_cancel':bool,'invite_expiry_days':list,'invite_recipient_mode':str,'notifications':str,'web_only':list})
        if any(type(x) is not str for name in ('controls','editable_settings','request_kinds','web_only') for x in cap[name]):
            raise ValueError('Invalid capability list')
        if any(x not in ('name','age_level','mode','library_ids') for x in cap['editable_settings']):
            raise ValueError('Invalid settings capability')
        for name in ('bonus_choices','later_choices','invite_expiry_days'):
            if any(type(x) is not int or x<1 for x in cap[name]):
                raise ValueError('Invalid numeric capability')
        enum(cap,'invite_recipient_mode',('accepted_share','eligible_adult'))
        enum(cap,'notifications',('polling',))
        obj(value['enforcement'],{'mode':str,'notice':str})
        enum(value['enforcement'],'mode',('companion','server_managed'))
    elif kind in ('child','settings'):
        enum(value,'age_level',('little','kids','teens'))
        enum(value,'mode',('age','picked'))
        ids(value['library_ids'],kind=='settings')
        if not 1<=len(value['name'])<=40:
            raise ValueError('Invalid child name')
        if kind=='settings':
            for library in value['library_choices']:
                obj(library,{'id':str,'title':str})
                ident(library['id'])
            if not set(value['library_ids'])<={it['id'] for it in value['library_choices']}:
                raise ValueError('Unadvertised library')
        else:
            enum(value,'status',('draft','requested','active','declined','removed','needs_attention'))
            enum(value,'state',('watching','available','paused','outside_hours','limit_reached','needs_attention'))
            timestamp(value['paused_until'],True)
            obj(value['today'],{'date':str,'checked_at':(str,type(None)),'used_seconds':int,'limit_seconds':(int,type(None)),'remaining_seconds':(int,type(None))})
            day(value['today']['date'])
            timestamp(value['today']['checked_at'],True)
            for key in ('used_seconds','limit_seconds','remaining_seconds'):
                nonnegative(value['today'][key],key!='used_seconds')
            obj(value['sync'],{'state':str,'checked_at':(str,type(None))})
            enum(value['sync'],'state',('applied','pending','failed','unknown'))
            timestamp(value['sync']['checked_at'],True)
            nonnegative(value['bonus_minutes'])
            nonnegative(value['later_minutes'])
            if any(type(action) is not str for action in value['allowed_actions']):
                raise ValueError('Invalid actions')
    elif kind=='schedule':
        from zoneinfo import ZoneInfo
        ZoneInfo(value['timezone'])
        day(value['break_until'],True)
        for name in ('weekday','weekend'):
            period=value[name]
            obj(period,{'minutes':(int,type(None)),'start':str,'end':str,'any_time':bool})
            nonnegative(period['minutes'],True)
            if period['minutes'] is not None and period['minutes']>1440:
                raise ValueError('Invalid daily minutes')
            for key in ('start','end'):
                if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',period[key]):
                    raise ValueError('Invalid local time')
            if period['any_time'] != (period['start']==period['end']):
                raise ValueError('Ambiguous watch hours')
    elif kind=='title':
        enum(value,'kind',('movie','show'))
        enum(value,'choice',('allow','block','reset'))
    elif kind=='activity':
        ident(value['child_id'])
        timestamp(value['at'])
        enum(value,'kind',('usage','stop','change'))
        nonnegative(value['seconds'],True)
    elif kind=='request':
        if value['child_id'] is not None:
            ident(value['child_id'])
        enum(value,'status',('open','approved','declined','done','answered','cancelled'))
        timestamp(value['created_at'],True)
        timestamp(value['resolved_at'],True)
        if any(type(x) is not str for x in value['allowed_decisions']):
            raise ValueError('Invalid decisions')
    elif kind=='approval':
        if value['child_id'] is not None:
            ident(value['child_id'])
        enum(value,'kind',('parent_access','child_access'))
        enum(value,'state',('open','approved','declined'))
        if any(type(x) is not str for x in value['prerequisites']) or any(x not in ('approve','decline') for x in value['allowed_decisions']):
            raise ValueError('Invalid approval actions')
    elif kind=='invite':
        enum(value,'state',('open','claimed','revoked','expired'))
        timestamp(value['expires_at'])
        if 'link_available' in value and type(value['link_available']) is not bool:
            raise ValueError('Invalid link state')
    elif kind=='device':
        enum(value,'issued_role',('parent','owner'))
        if not 1<=len(value['label'])<=60:
            raise ValueError('Invalid device label')
        for key in ('created_at','last_seen_at','expires_at','revoked_at'):
            timestamp(value[key],key=='revoked_at')
    elif kind=='mutation':
        ident(value['operation_id'])
        if type(value['resource_revision']) is not int or value['resource_revision']<1:
            raise ValueError('Invalid resource revision')
        enum(value,'state',('applied','pending','failed','unknown'))
        resource=value['resource']
        if resource is not None:
            resource_kind=next((k for key,k in [('today','child'),('library_choices','settings'),('weekday','schedule'),('message','request'),('prerequisites','approval'),('intended_display','invite'),('issued_role','device')] if key in resource),None)
            if resource_kind is None:
                raise ValueError('Invalid mutation resource')
            validate_wire(resource_kind,resource)
    elif kind=='events':
        for event in value['items']:
            obj(event,{'id':str,'at':str,'kind':str,'resource_id':str})
            ident(event['id'])
            ident(event['resource_id'])
            timestamp(event['at'])
            enum(event,'kind',('request_changed','approval_changed','child_changed','access_changed'))
    return value
