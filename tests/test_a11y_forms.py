"""Server-rendered accessibility regressions, including validation redirects."""
from html.parser import HTMLParser

import pytest

from .test_app import env, login, csrf, add_kid, active_kid
from .test_quality import strict_ok


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.nodes = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))

    def named(self, name):
        return next(attrs for _, attrs in self.nodes if attrs.get('name') == name)

    def identified(self, ident):
        return next(attrs for _, attrs in self.nodes if attrs.get('id') == ident)


def assert_error(html, name, ident, message):
    assert not strict_ok(html)
    nodes = Elements(html)
    field = nodes.named(name)
    assert field['aria-invalid'] == 'true'
    assert f'{ident}-error' in field['aria-describedby'].split()
    assert nodes.identified(f'{ident}-error')['class'] == 'form-error'
    assert nodes.identified('form-errors')['tabindex'] == '-1'
    assert message in html
    # Errors are part of the form and summary, rather than an expiring notification.
    assert not any('error' in attrs.get('class', '').split() and 'toast' in attrs.get('class', '').split()
                   for _, attrs in nodes.nodes)


def test_remove_error_reopens_details_and_describes_confirmation(env):
    app, fake = env
    c, _ = login(app, fake, 'alex')
    kid = add_kid(app, fake, c)
    path = f'/kids/{kid}/settings'
    r = c.post(f'/kids/{kid}/remove', data={'csrf': csrf(c, path), 'confirm': 'wrong'})
    html = c.get(r.headers['location']).text
    assert_error(html, 'confirm', 'confirm', 'To remove, type Kid1 in the box.')
    assert any(tag == 'details' and 'danger-zone' in attrs.get('class', '') and 'open' in attrs
               for tag, attrs in Elements(html).nodes)
    assert app.state.db.kid(kid)['status'] == 'draft'


def test_add_child_error_has_field_description(env):
    app, fake = env
    c, _ = login(app, fake, 'alex')
    r = c.post('/kids/new', data={'csrf': csrf(c), 'name': '', 'age_level': 'kids'})
    html = c.get(r.headers['location']).text
    assert_error(html, 'name', 'name', "Add your child&#39;s first name")
    assert Elements(html).identified('age-choice')['aria-invalid'] == 'true'


def test_settings_library_error_describes_switches(env):
    app, fake = env
    c, _ = login(app, fake, 'alex')
    kid = add_kid(app, fake, c)
    path = f'/kids/{kid}/settings'
    r = c.post(path, data={'csrf': csrf(c, path), 'name': 'Kid1', 'level_choice': 'age:kids'})
    html = c.get(r.headers['location']).text
    assert_error(html, 'section_1', 'libraries', 'Choose at least one library.')
    assert Elements(html).named('section_2')['aria-describedby'] == 'libraries-error'


@pytest.mark.parametrize('values, field, message', [
    ({'weekday_minutes': '-1'}, 'weekday_minutes', 'Enter screen time in minutes'),
    ({'weekday_start': 'invalid'}, 'weekday_start', 'Choose both a start and an end time'),
    ({'break_until': 'tomorrow'}, 'break_until', 'school break date'),
])
def test_schedule_server_errors_are_linked(env, values, field, message):
    app, fake = env
    c, _ = login(app, fake, 'alex')
    kid = add_kid(app, fake, c)
    path = f'/kids/{kid}/schedule'
    data = {'csrf': csrf(c, path), 'weekday_minutes': '60', 'weekend_minutes': '90',
            'weekday_start': '15:00', 'weekday_end': '20:00',
            'weekend_start': '08:00', 'weekend_end': '21:00', **values}
    r = c.post(path, data=data)
    html = c.get(r.headers['location']).text
    assert_error(html, field, field, message)


def test_request_validation_describes_message(env):
    app, fake = env
    c, _ = login(app, fake, 'alex')
    r = c.post('/requests', data={'csrf': csrf(c), 'kind': 'feature', 'message': ''})
    assert_error(c.get(r.headers['location']).text, 'message', 'message', 'Choose what you need')


def test_week_chart_exposes_each_days_values(env):
    app, fake = env
    c, kid = active_kid(app, fake)
    app.state.db.add_usage(kid, app.state.svc.today(), 5 * 3600)
    for path in (f'/kids/{kid}', f'/kids/{kid}/activity'):
        html = c.get(path).text
        nodes = Elements(html)
        chart = next(attrs for tag, attrs in nodes.nodes if tag == 'svg' and attrs.get('role') == 'img')
        assert chart['aria-describedby'] == 'week-chart-values'
        assert nodes.identified('week-chart-values')['class'] == 'visually-hidden'
        assert ', over the limit</li>' in html
        # One list item for every bar, from the exact same titles as the visual tooltips.
        text_list = html.split('id="week-chart-values">', 1)[1].split('</ul>', 1)[0]
        assert text_list.count('<li>') == 7
        for title in html.split('<title>')[1:]:
            value = title.split('</title>', 1)[0]
            if ':' in value:
                assert value in text_list


def test_titles_controls_are_named_without_scripts(env):
    app, fake = env
    c, kid = active_kid(app, fake)
    html = c.get(f'/kids/{kid}/titles').text
    nodes = Elements(html)
    buttons = [attrs for tag, attrs in nodes.nodes if tag == 'button' and 'data-toggle' in attrs]
    assert buttons
    assert all(attrs['aria-label'].startswith(('Block ', 'Allow ')) for attrs in buttons)
    assert 'data-title-state>Can watch' in html
    assert 'data-title-state>Hidden' in html
    assert not any('aria-label' in attrs for tag, attrs in nodes.nodes if tag == 'button' and 'data-details' in attrs)
    assert 'Details for Cloud Garden</span>' in html


def test_home_cards_are_named_regions_with_headings(env):
    app, fake = env
    c, _ = login(app, fake, 'alex')
    kid = add_kid(app, fake, c)
    html = c.get('/home').text
    nodes = Elements(html)
    section = next(attrs for tag, attrs in nodes.nodes if tag == 'section' and 'kid-card' in attrs.get('class', ''))
    assert section['aria-labelledby'] == f'kid-{kid}'
    assert any(tag == 'h2' and attrs.get('id') == f'kid-{kid}' for tag, attrs in nodes.nodes)
    assert html.count('<span class="visually-hidden"> for Kid1</span>') >= 3
