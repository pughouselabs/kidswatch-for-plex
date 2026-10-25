"""Build the app and site icons from the two brand SVGs in this folder.

Run from the repository root (needs rsvg-convert):

    python3 docs/brand/build_icons.py
"""
import pathlib
import re
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
BRAND = ROOT / "docs" / "brand"
ORANGE, OFF_WHITE = "#c2410c", "#fbf7f1"


def path_data(name):
    return re.search(r' d="([^"]+)"', (BRAND / name).read_text()).group(1)


MARK = path_data("kidswatch-mark.svg")
FAVICON = path_data("kidswatch-favicon.svg")


def plain(d, comment):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n  <!-- {comment} -->\n'
            f'  <path fill="{ORANGE}" fill-rule="evenodd" d="{d}"/>\n</svg>\n')


def tile(d, comment, scale, rx):
    """Off-white mark centered on an orange square; rx=0 gives a full-bleed square."""
    shift = round(32 - 32 * scale, 3)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n  <!-- {comment} -->\n'
            f'  <rect width="64" height="64" rx="{rx}" fill="{ORANGE}"/>\n'
            f'  <path transform="translate({shift} {shift}) scale({scale})" fill="{OFF_WHITE}" fill-rule="evenodd" d="{d}"/>\n'
            '</svg>\n')


def png(svg_text, out, size, tmp):
    src = pathlib.Path(tmp) / f"{out.stem}.svg"
    src.write_text(svg_text)
    subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), str(src), "-o", str(out)], check=True)


# App icon: rounded tile (corner radius 22.5% of the side, mark at 68%), as on the brand-v4 contact sheet.
app_icon = tile(MARK, "KidsWatch for Plex app icon: the F2 mark in off-white on an orange rounded square", 0.68, 14.4)
# Maskable: full-bleed square, mark kept well inside the central 80% safe circle.
maskable = tile(MARK, "KidsWatch for Plex maskable icon", 0.7, 0)
# Apple touch icon: iOS rounds the corners itself, so the square is full-bleed.
touch = tile(MARK, "KidsWatch for Plex apple touch icon", 0.68, 0)

static = ROOT / "app" / "static"
(static / "icon.svg").write_text(app_icon)
(static / "icon-favicon.svg").write_text(plain(FAVICON, "KidsWatch for Plex favicon: the F2 mark drawn for 16 px"))
(BRAND / "kidswatch-app-icon.svg").write_text(app_icon)
(ROOT / "site" / "icon.svg").write_text(plain(MARK, "KidsWatch for Plex: the F2 mark"))
(ROOT / "site" / "favicon.svg").write_text(plain(FAVICON, "KidsWatch for Plex favicon: the F2 mark drawn for 16 px"))

with tempfile.TemporaryDirectory() as tmp:
    png(app_icon, static / "icon-192.png", 192, tmp)
    png(app_icon, static / "icon-512.png", 512, tmp)
    png(maskable, static / "icon-maskable-512.png", 512, tmp)
    png(touch, static / "apple-touch-icon.png", 180, tmp)
