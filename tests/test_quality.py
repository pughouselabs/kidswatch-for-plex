"""Every page must be valid HTML (strict parser) and must not use inline styles or scripts (strict CSP)."""
import re

import html5lib

from .test_app import active_kid, add_kid, approve_all, client, connect, env, login  # noqa: F401


def strict_ok(html: str) -> list[str]:
    parser = html5lib.HTMLParser(strict=False)
    parser.parse(html)
    return [f"{e[0]} {e[1]} {e[2]}" for e in parser.errors]


def pages_for(app, fake):
    c, kid_id = active_kid(app, fake)
    draft = add_kid(app, fake, c, name="Kid2")
    o, _ = login(app, fake, "owner")
    paths = [(c, "/home"), (c, "/requests"), (c, "/help"), (c, "/kids/new"), (c, "/kids/999")]
    for k in (kid_id, draft):
        paths += [(c, f"/kids/{k}{p}") for p in ("", "/titles", "/schedule", "/activity", "/settings")]
    paths += [(o, f"/admin?tab={t}") for t in ("requests", "kids", "people", "activity")]
    paths += [(client(app), "/"), (client(app), "/link/not-a-real-token-123456789")]
    return paths


def test_every_page_is_valid_html(env):
    app, fake = env
    problems = {}
    for cl, path in pages_for(app, fake):
        r = cl.get(path)
        if "text/html" not in r.headers.get("content-type", ""):
            continue
        errors = strict_ok(r.text)
        if errors:
            problems[path] = errors[:5]
    assert not problems, problems


def test_no_inline_styles_or_scripts(env):
    app, fake = env
    for cl, path in pages_for(app, fake):
        html = cl.get(path).text
        assert not re.search(r"\sstyle=", html), path
        assert not re.search(r"<script(?![^>]*\ssrc=)", html), path
        assert "<style" not in html, path


def accessibility_problems(html: str) -> list[str]:
    """Every button and link needs a name, every field a label, every image an alt attribute."""
    doc = html5lib.parse(html, namespaceHTMLElements=False)
    parents = {child: parent for parent in doc.iter() for child in parent}
    labelled = {el.get("for") for el in doc.iter("label") if el.get("for")}

    def text(el) -> str:
        return "".join(el.itertext()).strip()

    def inside_label(el) -> bool:
        while el in parents:
            el = parents[el]
            if el.tag == "label":
                return True
        return False

    problems = []
    for el in doc.iter():
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag in ("a", "button") and not (text(el) or el.get("aria-label")):
            problems.append(f"{tag} without a name: {el.attrib}")
        elif tag in ("input", "select", "textarea") and el.get("type") not in ("hidden", "submit"):
            if not (el.get("aria-label") or el.get("id") in labelled or inside_label(el)):
                problems.append(f"{tag} without a label: {el.attrib}")
        elif tag == "img" and el.get("alt") is None:
            problems.append(f"img without alt: {el.get('src')}")
    return problems


def test_every_control_has_an_accessible_name(env):
    app, fake = env
    problems = {}
    for cl, path in pages_for(app, fake):
        r = cl.get(path)
        if "text/html" in r.headers.get("content-type", ""):
            found = accessibility_problems(r.text)
            if found:
                problems[path] = found[:4]
    assert not problems, problems
