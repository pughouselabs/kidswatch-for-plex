"""Suggestions and their template integration must not need another Plex read."""
import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from app.email_tip import register_email_tip, suggest_email
from .test_app import env, login, add_kid
from .test_quality import accessibility_problems, strict_ok


@pytest.mark.parametrize("name, tag", [
    ("Rowan", "rowan"),
    ("Emma Rose", "emmarose"),
    ("Jo-Anne O'Neil 2", "joanneoneil2"),
    ("Álëx 😊", "lx"),
    ("名字", "child"),
    ("", "child"),
    ("A" * 25, "a" * 20),
    ('<script>alert("x")</script>', "scriptalertxscript"),
])
def test_child_tag_is_lowercase_ascii_and_bounded(name, tag):
    assert suggest_email("parent@example.com", name) == f"parent+{tag}plex@example.com"
    assert re.fullmatch(r"[a-z0-9]{1,20}", tag)


@pytest.mark.parametrize("email, expected", [
    (" Parent.Name@gmail.com ", "Parent.Name+rowanplex@gmail.com"),
    ("parent+old@outlook.com", "parent+rowanplex@outlook.com"),
    ("parent@hotmail.com", "parent+rowanplex@hotmail.com"),
    ("parent@proton.me", "parent+rowanplex@proton.me"),
    (None, "jane+rowanplex@gmail.com"),
    ("", "jane+rowanplex@gmail.com"),
    ("parent", "jane+rowanplex@gmail.com"),
    ("parent@@example.com", "jane+rowanplex@gmail.com"),
    ('parent\"@example.com', "jane+rowanplex@gmail.com"),
    ("parent@example.com\nBcc:elsewhere", "jane+rowanplex@gmail.com"),
    (".parent@example.com", "jane+rowanplex@gmail.com"),
    ("parent..name@example.com", "jane+rowanplex@gmail.com"),
    ("parent@-example.com", "jane+rowanplex@gmail.com"),
    ("parent@example..com", "jane+rowanplex@gmail.com"),
    ("p" * 60 + "@example.com", "jane+rowanplex@gmail.com"),
])
def test_known_email_and_fallback(email, expected):
    assert suggest_email(email, "Rowan") == expected


def test_global_reads_only_explicit_email_from_context():
    environment = Environment(autoescape=True)
    register_email_tip(environment)
    template = environment.from_string("{{ email_tip(name).address }}|{{ email_tip(name).example }}")
    assert template.render(name="Rowan", user={"email": "parent@example.com"}) == "parent+rowanplex@example.com|False"
    # A display name or Plex username that resembles an email is not evidence
    # that it is the parent's current email address.
    assert template.render(name="Rowan", user={"username": "other@example.com", "name": "other@example.com"}) == "jane+rowanplex@gmail.com|True"
    assert template.render(name="Rowan", user=None) == "jane+rowanplex@gmail.com|True"
    assert template.render(name="Rowan") == "jane+rowanplex@gmail.com|True"


def test_connect_card_with_documented_hook(env, monkeypatch):
    # Register exactly as the documented main.py hook does, keeping production
    # route ownership with the coordinator. Capture the per-app environment on
    # its next render, rather than changing global Jinja defaults.
    from starlette.templating import Jinja2Templates

    original = Jinja2Templates.TemplateResponse

    def render(templates, *args, **kwargs):
        register_email_tip(templates.env)
        return original(templates, *args, **kwargs)

    monkeypatch.setattr(Jinja2Templates, "TemplateResponse", render)
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid = add_kid(app, fake, c, name="Jo-Anne 2")
    before = list(fake.calls)
    response = c.get(f"/kids/{kid}")
    assert response.status_code == 200
    html = response.text
    assert 'value="jane+joanne2plex@gmail.com"' in html
    assert 'Add <code>+joanne2plex</code> right before the @ in your own email address.' in html
    assert 'For example, <code>jane@gmail.com</code> becomes <code>jane+joanne2plex@gmail.com</code>.' in html
    assert "Mail still lands in your inbox, so you keep control of your child's account and password." in html
    assert 'yourname' not in html
    assert "Use your own email with a tag." in html
    assert "If your email provider does not support this, use any address you check." in html
    assert 'data-copy="#email-tip-address"' in html
    assert 'data-copy-message="Email address copied" hidden' in html
    assert not strict_ok(html)
    assert not accessibility_problems(html)
    assert not re.search(r'\sstyle=|<style|<script(?![^>]*\ssrc=)', html)
    assert fake.calls == before
    assert "Use your own email with a tag." not in c.get("/kids/new").text


def test_connect_template_is_compatible_before_hook_is_added(env):
    app, fake = env
    c, _ = login(app, fake, "alex")
    kid = add_kid(app, fake, c)
    assert c.get(f"/kids/{kid}").status_code == 200


@pytest.mark.parametrize("status, has_tip", [("draft", True), ("requested", False)])
def test_tip_is_only_in_account_creation_step_and_does_not_submit_email(status, has_tip):
    environment = Environment(
        loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "app/templates"),
        autoescape=True,
    )
    register_email_tip(environment)
    html = environment.get_template("_connect.html").render(
        kid={"id": 1, "name": "Emma Rose", "status": status, "plex_accepted": False},
        user={"role": "parent", "email": "parent@example.com"},
        owner_name="Owner", link=None, csrf="test-csrf",
    )
    assert ('class="email-tip"' in html) is has_tip
    assert 'name="email"' not in html
    if has_tip:
        assert 'readonly value="parent+emmaroseplex@example.com"' in html
        assert "Example email address" not in html


def test_public_parent_tip_is_inside_what_you_need():
    html = (Path(__file__).resolve().parents[1] / "site/plex/parents.html").read_text()
    needs = html.split('id="need"', 1)[1].split("</section>", 1)[0]
    assert "Use your own email with a tag." in needs
    assert "Add <code>+&lt;kidname&gt;plex</code> right before the @ in your own email address." in needs
    assert "For example, <code>jane@gmail.com</code> becomes <code>jane+emmaplex@gmail.com</code>." in needs
    assert "yourname" not in needs
    assert "jane+emmaplex@gmail.com" in needs
    assert "If your email provider does not support this, use any address you check." in needs
    assert not strict_ok(html)
