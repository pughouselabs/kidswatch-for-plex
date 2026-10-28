"""Owner branding stays optional, escaped, and confined to mapped raster files."""
from dataclasses import replace
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader, select_autoescape
import pytest

from app import config
from app.branding import OwnerArt, SLOTS, bundled_art
from app.email_tip import register_email_tip
from app.main import create_app
from .fakes import FakePlex

FIXTURES = Path(__file__).parent / "fixtures" / "branding"
DISCLAIMER = ("Plex is a trademark of Plex, Inc. KidsWatch for Plex is an independent project "
              "and is not affiliated with or endorsed by Plex, Inc.")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("PK_"):
            monkeypatch.delenv(key)
    return replace(config.load(), db_path=str(tmp_path / "test.db"), start_enforcer=False,
                   secure_cookies=False)


def browser(cfg):
    return TestClient(create_app(cfg, plex=FakePlex()), follow_redirects=False)


def mapping(tmp_path, entries):
    (tmp_path / "art.json").write_text(json.dumps(entries))
    return str(tmp_path)


def test_unset_templates_match_original_snapshots():
    env = Environment(loader=FileSystemLoader("app/templates"), autoescape=select_autoescape(["html", "xml"]))
    register_email_tip(env)
    for name in ("base", "landing"):
        html = env.get_template(f"{name}.html").render(user=None, flashes=[], owner_name="Admin", mobile_enabled=False)
        assert html == (FIXTURES / f"{name}.html").read_text()


def test_unset_landing_route_matches_original_snapshot(cfg):
    with browser(cfg) as c:
        assert c.get("/").text == (FIXTURES / "landing.html").read_text()
        manifest = c.get("/manifest.webmanifest").json()
        assert manifest["name"] == "KidsWatch for Plex"
        assert manifest["short_name"] == "KidsWatch"


def test_env_limits_and_whitespace(cfg, monkeypatch):
    monkeypatch.setenv("PK_APP_NAME", "  " + "n" * 100)
    monkeypatch.setenv("PK_FOOTER_TEXT", "f" * 300)
    monkeypatch.setenv("PK_LANDING_ART_DIR", " /art ")
    loaded = config.load()
    assert loaded.app_name == "n" * 80
    assert loaded.footer_text == "f" * 240
    assert loaded.landing_art_dir == "/art"
    monkeypatch.setenv("PK_APP_NAME", "   ")
    monkeypatch.setenv("PK_FOOTER_TEXT", "   ")
    assert config.load().app_name == config.load().footer_text == ""


def test_branding_escaped_in_every_base_location_and_manifest(cfg):
    value = '<script>"&</script>'
    with browser(replace(cfg, app_name=value, footer_text=value)) as c:
        html = c.get("/").text
        assert value not in html
        assert html.count('&lt;script&gt;&#34;&amp;&lt;/script&gt;') == 4
        assert DISCLAIMER in html
        manifest = c.get("/manifest.webmanifest").json()
        assert manifest["name"] == manifest["short_name"] == value
        assert '<script>' not in c.get("/help").text
        assert DISCLAIMER in c.get("/help").text
        assert DISCLAIMER in c.get("/does-not-exist").text


def test_direct_config_limits(cfg):
    with browser(replace(cfg, app_name="n" * 100, footer_text="f" * 300)) as c:
        html = c.get("/").text
        assert "n" * 80 in html and "n" * 81 not in html
        assert "f" * 240 in html and "f" * 241 not in html
        assert c.get("/manifest.webmanifest").json()["name"] == "n" * 80
        assert DISCLAIMER in html


@pytest.mark.parametrize("suffix,content_type", [("png", "image/png"), ("jpg", "image/jpeg"), ("webp", "image/webp")])
def test_art_routes_and_history(cfg, tmp_path, suffix, content_type):
    filename = f"poster.{suffix}"
    (tmp_path / filename).write_bytes(b"poster bytes")
    directory = mapping(tmp_path, {slot: {"filename": filename, "title": '<b>Sample & title</b>'} for slot in SLOTS})
    with browser(replace(cfg, landing_art_dir=directory)) as c:
        html = c.get("/").text
        for slot in SLOTS:
            assert f'/owner-art/{slot}' in html
            assert bundled_art(slot) not in html
        assert html.count('/owner-art/maple-club') == 2
        assert html.count('/owner-art/pebble-trail') == 2
        assert html.count('&lt;b&gt;Sample &amp; title&lt;/b&gt;') == 2
        r = c.get("/owner-art/maple-club")
        assert r.status_code == 200 and r.content == b"poster bytes"
        assert r.headers["content-type"] == content_type
        assert r.headers["cache-control"] == "public, max-age=3600"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "img-src 'self'" in r.headers["content-security-policy"]
        for path in ("/owner-art/", "/owner-art/art.json", f"/owner-art/{filename}", "/owner-art/%2e%2e%2fart.json"):
            assert c.get(path).status_code == 404
        assert c.post('/owner-art/maple-club').status_code == 405
        # Replacing a source file after startup cannot change bytes already validated and loaded.
        (tmp_path / filename).unlink()
        assert c.get('/owner-art/maple-club').content == b"poster bytes"


@pytest.mark.parametrize("filename", ["../outside.png", "/outside.png", "nested/poster.png", "..\\poster.png", "poster.svg", "poster.html", "poster.jpeg", "missing.png", "dir.png"])
def test_rejected_art_falls_back(cfg, tmp_path, filename, caplog):
    (tmp_path / 'dir.png').mkdir()
    directory = mapping(tmp_path, {'maple-club': {'filename': filename, 'title': 'Example'}})
    with browser(replace(cfg, landing_art_dir=directory)) as c:
        assert c.get('/').text == (FIXTURES / 'landing.html').read_text()
        r = c.get('/owner-art/maple-club')
        assert r.status_code == 307 and r.headers['location'] == bundled_art('maple-club')
    assert 'using bundled art' in caplog.text


def test_symlink_art_and_mapping_rejected(tmp_path, caplog):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'poster.png').write_bytes(b'private')
    root = tmp_path / 'art'
    root.mkdir()
    (root / 'poster.png').symlink_to(outside / 'poster.png')
    directory = mapping(root, {'maple-club': {'filename': 'poster.png', 'title': 'Example'}})
    assert not OwnerArt(directory).images
    (outside / 'art.json').write_text('{}')
    (root / 'art.json').unlink()
    (root / 'art.json').symlink_to(outside / 'art.json')
    assert not OwnerArt(directory).images
    assert caplog.text.count('using bundled art') == 2


@pytest.mark.parametrize('content', ['{', '[]', 'null', '{"maple-club": null}', '{"maple-club": {"filename": 1, "title": "x"}}', '{"maple-club": {"filename": "p.png", "title": ""}}'])
def test_invalid_mapping_falls_back(tmp_path, content, caplog):
    (tmp_path / 'art.json').write_text(content)
    assert not OwnerArt(str(tmp_path)).images
    assert 'using bundled art' in caplog.text


def test_missing_directory_and_mapping(cfg, tmp_path, caplog):
    for directory in (tmp_path / 'missing', tmp_path):
        with browser(replace(cfg, landing_art_dir=str(directory))) as c:
            assert c.get('/').text == (FIXTURES / 'landing.html').read_text()
    assert caplog.text.count('using bundled art') == 2


def test_independent_slots_and_title_limit(tmp_path, caplog):
    (tmp_path / 'poster.png').write_bytes(b'poster')
    directory = mapping(tmp_path, {
        'maple-club': {'filename': 'poster.png', 'title': 't' * 200},
        'pebble-trail': {'filename': 'missing.png', 'title': 'Missing'},
        'unknown': {'filename': 'poster.png', 'title': 'Ignored'},
    })
    art = OwnerArt(directory)
    assert art.title('maple-club', '') == 't' * 120
    assert art.url('pebble-trail') == bundled_art('pebble-trail')
    assert art.title('pebble-trail', 'Original') == 'Original'
    assert set(art.images) == {'maple-club'}
    assert 'using bundled art' in caplog.text


def test_nonregular_and_oversize_files_fall_back(tmp_path, monkeypatch, caplog):
    from app import branding

    monkeypatch.setattr(branding, 'MAX_IMAGE_BYTES', 8)
    (tmp_path / 'large.png').write_bytes(b'x' * 9)
    (tmp_path / 'empty.png').write_bytes(b'')
    os.mkfifo(tmp_path / 'pipe.png')
    directory = mapping(tmp_path, {
        'maple-club': {'filename': 'large.png', 'title': 'Large'},
        'pebble-trail': {'filename': 'empty.png', 'title': 'Empty'},
        'paper-harbor': {'filename': 'pipe.png', 'title': 'Pipe'},
    })
    assert not OwnerArt(directory).images
    assert caplog.text.count('using bundled art') == 3
    (tmp_path / 'art.json').write_bytes(b'x' * (16 * 1024 + 1))
    assert not OwnerArt(directory).images


def test_symlink_inside_directory_rejected(tmp_path, caplog):
    (tmp_path / 'actual.png').write_bytes(b'image')
    (tmp_path / 'alias.png').symlink_to(tmp_path / 'actual.png')
    directory = mapping(tmp_path, {'maple-club': {'filename': 'alias.png', 'title': 'Example'}})
    assert not OwnerArt(directory).images
    assert 'using bundled art' in caplog.text
