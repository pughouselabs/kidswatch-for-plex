"""Parent handoff, recovery and accessible state regressions from the flow review."""
import re
import time

import pytest

from .test_a11y_forms import Elements
from .test_app import active_kid, add_kid, approve_all, client, connect, csrf, env, login


def schedule_data(c, path, **values):
    return dict(csrf=csrf(c, path), weekday_minutes='75', weekend_minutes='90',
                weekday_start='15:00', weekday_end='20:00', weekend_start='08:00',
                weekend_end='21:00', **values)


@pytest.mark.parametrize('day', ['weekday', 'weekend'])
def test_equal_watch_hours_reject_and_preserve_form(env, day):
    app, fake = env
    c, _ = login(app, fake, 'alex')
    kid = add_kid(app, fake, c)
    path = f'/kids/{kid}/schedule'
    before = dict(app.state.db.kid(kid))
    data = schedule_data(c, path)
    data.update({f'{day}_start': '08:00', f'{day}_end': '08:00', 'friday_weekend': '1'})
    r = c.post(path, data=data)
    assert r.status_code == 303
    assert dict(app.state.db.kid(kid)) == before
    html = c.get(r.headers['location']).text
    nodes = Elements(html)
    assert 'Choose different times, or turn on Any time.' in html
    assert nodes.named(f'{day}_start')['value'] == '08:00'
    assert nodes.named(f'{day}_end')['value'] == '08:00'
    assert nodes.named(f'{day}_start')['aria-invalid'] == 'true'
    assert 'watch-hours-hint' in nodes.named(f'{day}_start')['aria-describedby'].split()
    assert 'checked' not in nodes.named(f'any_time_{day}')
    assert 'checked' in nodes.named('friday_weekend')
    assert nodes.named('weekday_minutes')  # Both daily allowances remain in the form.
    assert '<option value="75" selected>' in html


@pytest.mark.parametrize('any_time', [False, True])
def test_explicit_any_time_and_overnight_remain_supported(env, any_time):
    app, fake = env
    c, _ = login(app, fake, 'alex')
    kid = add_kid(app, fake, c)
    path = f'/kids/{kid}/schedule'
    data = schedule_data(c, path)
    data.update(weekday_start='21:00', weekday_end='08:00')
    if any_time:
        data.update(weekday_start='08:00', any_time_weekday='1')
    assert c.post(path, data=data).status_code == 303
    row = app.state.db.kid(kid)
    assert (row['weekday_start'], row['weekday_end']) == (('00:00', '00:00') if any_time else ('21:00', '08:00'))
    assert 'Schedule saved.' in c.get(path).text


def test_schedule_group_and_control_names(env):
    app, fake = env
    c, _ = login(app, fake, 'alex')
    kid = add_kid(app, fake, c)
    html = c.get(f'/kids/{kid}/schedule').text
    nodes = Elements(html)
    assert len([a for tag, a in nodes.nodes if tag == 'fieldset' and a.get('class') == 'sched-row']) == 2
    for day in ['weekday', 'weekend']:
        for suffix, word in [('start', 'start'), ('end', 'end')]:
            control = nodes.named(f'{day}_{suffix}')
            assert control['aria-label'] == f'{day.capitalize()} {word} time'
            assert control['aria-describedby'] == 'watch-hours-hint'
        assert nodes.named(f'any_time_{day}')['aria-label'] == f'Any time on {day}s'


@pytest.mark.parametrize('decision, expected', [('approve', 'active'), ('decline', 'declined')])
def test_connected_child_waits_independently_and_status_tracks_decision(env, decision, expected):
    app, fake = env
    c, _ = login(app, fake, 'alex')
    kid = add_kid(app, fake, c)
    connect(app, fake, c, kid)
    app.state.db.update_kid(kid, plex_accepted=1)
    path = f'/kids/{kid}'
    html = c.get(path).text
    assert 'id="connect"' not in html
    assert f'data-wait-url="{path}/status.json"' in html
    assert 'Check approval' in html
    o, _ = login(app, fake, 'owner')
    req = app.state.db.one("SELECT id FROM requests WHERE kid_id=? AND kind='add_child'", (kid,))
    assert o.post(f"/admin/requests/{req['id']}", data={'csrf': csrf(o, '/admin'), 'action': decision}).status_code == 303
    assert c.get(path + '/status.json').json()['status'] == expected
    if decision == 'decline':
        assert 'request was declined' in c.get(path).text
    other, _ = login(app, fake, 'sam')
    assert other.get(path + '/status.json').status_code == 404


def test_coparent_request_links_to_assignment_and_back_without_granting(env):
    app, fake = env
    c, kid = active_kid(app, fake)
    login(app, fake, 'sam')
    c.post('/requests', data={'csrf': csrf(c), 'kind': 'more_access', 'kid': kid,
                              'message': 'Please let Sam help manage Kid1.'})
    req = app.state.db.one("SELECT id FROM requests WHERE kind='more_access'")['id']
    o, _ = login(app, fake, 'owner')
    assert f'href="/admin?tab=kids#kid-parents-{kid}"' in o.get('/admin').text
    assert 'Grant access first, then mark this request done.' in o.get('/admin').text
    html = o.get('/admin?tab=kids').text
    assert f'id="kid-parents-{kid}"' in html
    assert f'href="/admin?tab=requests#request-{req}"' in html
    o.post(f'/admin/requests/{req}', data={'csrf': csrf(o, '/admin'), 'action': 'done'})
    assert not app.state.db.q('SELECT * FROM kid_parents WHERE kid_id=?', (kid,))


@pytest.mark.parametrize('picked', [False, True])
def test_title_actions_name_title_child_and_reset_mode_on_first_render(env, picked):
    app, fake = env
    c, kid = active_kid(app, fake)
    if picked:
        app.state.db.update_kid(kid, mode='picked')
    nodes = Elements(c.get(f'/kids/{kid}/titles').text)
    toggles = [a for tag, a in nodes.nodes if 'data-toggle' in a]
    resets = [a for tag, a in nodes.nodes if 'data-reset' in a]
    assert toggles and len(toggles) == len(resets)
    for a in toggles:
        assert re.fullmatch(r'(Block|Allow) .+ for Kid1', a['aria-label'])
    for a in resets:
        assert a['aria-label'].startswith('Your pick · Undo for ')
        assert a['aria-label'].endswith('hidden by default' if picked else "Kid1's age level")


def test_canceled_request_never_appears_as_owner_reply(env):
    app, fake = env
    c, _ = login(app, fake, 'alex')
    c.post('/requests', data={'csrf': csrf(c), 'kind': 'other', 'message': 'Help with my family TV.'})
    req = app.state.db.one("SELECT id FROM requests WHERE kind='other'")['id']
    c.post(f'/requests/{req}/cancel', data={'csrf': csrf(c)})
    html = c.get('/home').text
    assert 'Help with my family TV.' not in html
    assert 'Admin replied' not in html
    assert 'Help with my family TV.' in c.get('/requests').text


@pytest.mark.parametrize('status, wording', [('approved', 'approved your request'), ('done', 'completed your request')])
def test_answered_request_without_note_has_explicit_state(env, status, wording):
    app, fake = env
    c, _ = login(app, fake, 'alex')
    app.state.db.x("INSERT INTO requests(parent_id,kind,message,status,created_at,resolved_at) VALUES(101,'more_access','My request',?,?,?)",
                   (status, '2099-01-01T00:00:00+00:00', '2099-01-01T00:00:00+00:00'))
    assert wording in c.get('/home').text
    assert 'Admin replied' not in c.get('/home').text


def test_expired_request_form_returns_to_task_and_never_replays(env):
    app, fake = env
    c, _ = login(app, fake, 'alex')
    old_csrf = csrf(c, '/requests')
    # A completed sign-in renews the session, leaving an already-open form stale.
    c.get('/login')
    fake.next_token = 'tok-alex'
    c.get('/auth/done')
    r = c.post('/requests', data={'csrf': old_csrf, 'kind': 'other', 'message': 'Do not replay this.'},
               headers={'referer': 'https://outside.example/steal'})
    assert r.status_code == 400
    assert 'This form expired' in r.text
    assert 'href="/requests?restore=1#new"' in r.text
    assert 'outside.example' not in r.text
    assert not app.state.db.q('SELECT * FROM requests')
    assert 'data-draft-sent' not in c.get('/requests').text
    assert "script-src 'self'" in r.headers['content-security-policy']
    assert "'unsafe-inline'" not in r.headers['content-security-policy']


def test_draft_clear_marker_only_follows_success_and_is_consumed_once(env):
    app, fake = env
    c, _ = login(app, fake, 'alex')
    assert 'data-request-draft="101"' in c.get('/requests').text
    c.post('/requests', data={'csrf': csrf(c), 'kind': 'other', 'message': ''})
    assert 'data-draft-sent' not in c.get('/requests').text
    marker = 'a' * 32
    r = c.post('/requests', data={'csrf': csrf(c), 'kind': 'other', 'message': 'Please help.', 'draft_id': marker})
    assert 'data-draft-sent' not in c.get('/requests').text  # A different tab cannot consume the signal.
    assert 'data-draft-sent' not in c.get('/requests?sent=' + 'b' * 32).text
    assert f'data-draft-sent="{marker}"' in c.get(r.headers['location']).text
    assert 'data-draft-sent' not in c.get(r.headers['location']).text


def test_used_expired_and_unknown_link_show_same_neutral_instructions(env):
    app, fake = env
    c, _ = login(app, fake, 'alex')
    kid = add_kid(app, fake, c, name='Private child name')
    _, used_path = connect(app, fake, c, kid)
    expired = 'expired-test-link-token-0001'
    app.state.db.x('INSERT INTO link_tokens(token,kid_id,parent_id,expires_at) VALUES(?,?,101,?)',
                   (expired, kid, time.time() - 60))
    private = client(app)
    for path in [used_path, f'/link/{expired}', '/link/unknown-test-link-token-0001', used_path + '/done']:
        r = private.get(path)
        assert r.status_code == 410
        assert 'This link is no longer available' in r.text
        assert 'If you already finished signing in' in r.text
        assert 'If you still need to connect' in r.text
        assert 'Private child name' not in r.text
        assert 'active' not in r.text
