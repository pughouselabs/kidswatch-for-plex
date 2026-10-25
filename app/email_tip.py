"""Child-account email suggestions, using only data already in the template.

Integration hook, immediately after creating ``templates`` in ``create_app``::

    __import__("app.email_tip", fromlist=["register_email_tip"]).register_email_tip(templates.env)

The current sign-in session does not retain Plex's email field, so it gets the
example address. This module never reads Plex, the database, or another service.
"""
from __future__ import annotations

import re

from jinja2 import Environment, pass_context


def _child_tag(kid_name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", kid_name.lower())[:20] or "child"


def suggest_email(parent_email: str | None, kid_name: str) -> str:
    """Build a plus address; use an editable example if the email is unknown.

    Tags contain up to 20 ASCII letters/digits from the child's name, followed
    by ``plex``. An existing plus tag is replaced rather than stacked. Invalid
    email values and addresses that would exceed the local-part limit use the
    example rather than suggesting an undeliverable address.
    """
    tag = _child_tag(kid_name)
    fallback = f"jane+{tag}plex@gmail.com"
    if not isinstance(parent_email, str):
        return fallback
    email = parent_email.strip()
    if not re.fullmatch(r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", email):
        return fallback
    local, domain = email.rsplit("@", 1)
    local = local.split("+", 1)[0]
    if not local or local.startswith(".") or local.endswith(".") or ".." in local:
        return fallback
    if any(not label or label.startswith("-") or label.endswith("-") for label in domain.split(".")):
        return fallback
    local = f"{local}+{tag}plex"
    if len(local) > 64 or len(domain) > 253 or len(local) + len(domain) + 1 > 254:
        return fallback
    return f"{local}@{domain}"


@pass_context
def email_tip(context, kid_name: str) -> dict[str, str | bool]:
    """Use only an explicitly stored email, never a username or display name."""
    user = context.get("user") or {}
    parent_email = user.get("email")
    address = suggest_email(parent_email, kid_name)
    return {"address": address, "tag": f"+{_child_tag(kid_name)}plex",
            "example": address == suggest_email(None, kid_name),
            "example_address": suggest_email("jane@gmail.com", kid_name)}


def register_email_tip(environment: Environment) -> None:
    """Register the context-aware global on this app's Jinja environment."""
    environment.globals["email_tip"] = email_tip
