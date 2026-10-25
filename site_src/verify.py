#!/usr/bin/env python3
"""Check output parity, or serve the Pages routes and security headers for browser QA."""
import argparse
from functools import partial
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class Structure(HTMLParser):
    """Compare every element and component attribute outside platform documentation."""
    def __init__(self):
        super().__init__()
        self.events = []
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.depth:
            if tag not in VOID:
                self.depth += 1
            return
        # These three documentation bodies vary because installation and accounts differ.
        if "data-platform-doc" in attrs:
            self.events.append((tag, "platform-documentation", attrs["data-platform-doc"]))
            self.depth = 1
            return
        structural = tuple(sorted((key, value) for key, value in attrs.items()
                                  if key in {"id", "class", "role", "hidden", "disabled", "type"}
                                  or key.startswith("data-") and key not in {"data-edition", "data-share-title", "data-share-url"}))
        self.events.append(("start", tag, structural))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1
            return
        if tag not in VOID:
            self.events.append(("end", tag))


def check():
    subprocess.run([sys.executable, str(ROOT / "site_src/build.py"), "--check"], check=True)
    for page in ("index", "parents", "server-owners"):
        structures = []
        for edition in ("plex", "jellyfin"):
            parser = Structure()
            parser.feed((ROOT / f"site/{edition}/{page}.html").read_text())
            structures.append(parser.events)
        if structures[0] != structures[1]:
            for index, (left, right) in enumerate(zip(*structures)):
                if left != right:
                    raise AssertionError(f"{page} element {index}: {left} != {right}")
            raise AssertionError(f"{page}: unequal element counts")
        print(f"{page}: identical shared structure ({len(structures[0])} element events)")
    print("Intentional content differences: theme, names/metadata/links, release status, platform facts;")
    print("documentation body differences: installation, lifecycle commands, email tip versus Quick Connect.")
    print("Jellyfin stop message uses toast styling on the same TV component.")


class PagesHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        for line in (ROOT / "site/_headers").read_text().splitlines():
            if line.startswith("  ") and ":" in line:
                name, value = line.strip().split(":", 1)
                self.send_header(name, value.strip())
        super().end_headers()

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if not Path(path).suffix and (ROOT / "site" / (path.lstrip("/") + ".html")).is_file():
            self.path = path + ".html" + ("?" + query if query else "")
        super().do_GET()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=int, metavar="PORT")
    args = parser.parse_args()
    if args.serve:
        ThreadingHTTPServer(("127.0.0.1", args.serve), partial(PagesHandler, directory=str(ROOT / "site"))).serve_forever()
    else:
        check()
