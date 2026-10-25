#!/usr/bin/env python3
"""Build the static edition pages using only Python's standard library.

Run from any directory. --check verifies committed output without writing it.
The chooser, redirects, headers and shared scripts are not generated.
"""
import argparse
import html
import json
from pathlib import Path
import re

SOURCE = Path(__file__).resolve().parent
SITE = SOURCE.parent / "site"
PAGES = ("index", "parents", "server-owners")
TOKEN = re.compile(r"\{\{([a-zA-Z0-9_.-]+)\}\}")
# These are platform installation/account documentation, not page components.
RICH_FACTS = {"facts.update_guide", "facts.remove_guide", "facts.account_tip_body", "step.body"}


def lookup(context, path):
    value = context
    for key in path.split("."):
        value = value[key]
    if not isinstance(value, str):
        raise ValueError(f"Expected text for {path}")
    return value


def render(name, context, fragments=None):
    fragments = fragments or {}
    template = (SOURCE / "templates" / name).read_text(encoding="utf-8")

    def replace(match):
        key = match[1]
        if key in fragments:
            return fragments[key]
        value = lookup(context, key)
        if key.startswith("current.") or key in RICH_FACTS:
            return value
        return html.escape(value, quote=True)

    output = TOKEN.sub(replace, template)
    if "{{" in output:
        raise ValueError(f"Unresolved template token in {name}")
    return output


def theme_rules(editions):
    def rule(selector, values):
        lines = []
        for key, value in values.items():
            if not re.fullmatch(r"[a-z0-9-]+", key) or re.search(r"[{};\n]", value):
                raise ValueError("Invalid theme variable")
            lines.append(f"  --{key}: {value};")
        return selector + " {\n" + "\n".join(lines) + "\n}"

    light, dark = [], []
    for edition in editions:
        selector = f'[data-edition="{edition["slug"]}"]'
        if edition["slug"] == "plex":
            # The unchanged chooser uses the original default palette.
            selector = ':root:not([data-edition]), ' + selector
        light.append(rule(selector, edition["theme"]["light"]))
        dark.append(rule(selector, edition["theme"]["dark"]))
    return "\n".join(light) + "\n@media (prefers-color-scheme: dark) {\n" + "\n".join(dark) + "\n}"


def outputs():
    editions = [json.loads(path.read_text(encoding="utf-8"))
                for path in sorted((SOURCE / "editions").glob("*.json"))]
    result = {}
    for edition in editions:
        if not re.fullmatch(r"[a-z]+", edition["slug"]):
            raise ValueError("Invalid edition path")
        for page in PAGES:
            context = dict(edition, page=edition["pages"][page], current={
                key: ' aria-current="page"' if page == target else ""
                for key, target in (("home", "index"), ("owners", "server-owners"), ("parents", "parents"))
            })
            fragments = {name: render(name.replace("_", "-") + ".html", context)
                         for name in ("header", "footer", "phone", "account_tip")}
            fragments["manual_steps"] = "".join(
                render("manual-step.html", dict(context, step=step))
                for step in edition["manual_steps"]
            )
            result[SITE / edition["slug"] / (page + ".html")] = render(page + ".html", context, fragments)
    result[SITE / "style.css"] = render("style.css", {}, {"themes": theme_rules(editions)})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if generated files are stale")
    args = parser.parse_args()
    stale = []
    for path, content in outputs().items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(str(path.relative_to(SITE.parent)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    if stale:
        parser.exit(1, "Stale generated files: " + ", ".join(stale) + "\n")
    print("Generated output is current." if args.check else "Built six edition pages and one shared stylesheet.")


if __name__ == "__main__":
    main()
