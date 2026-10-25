"""Build design/brand-v4/sheet.png: the round 4 finalists as they will really appear.

For each finalist (F1, F1-alt, F2, F3) the sheet shows the mark at 512 and 64 px and
its 16 px favicon (plus the favicon at 4x) in #c2410c on #fbf7f1 and #ff7a33 on
#141414, then three mockups at real size: an app-icon tile, a browser tab with the
favicon, and a website header with the wordmark.

Needs rsvg-convert (librsvg), Pillow and the Noto Sans fonts. Run from the repository root:
    python3 design/brand-v4/make_sheet.py
"""
import pathlib
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).resolve().parent
FINALISTS = [
    ("F1", "7b refined", ["Check-mark antennae and", "feet, a shield with a small", "heart in front (no second", "check)"]),
    ("F1-alt", "7b refined, check kept", ["Check-mark antennae and", "feet, the checked shield", "in front"]),
    ("F2", "7c refined", ["Vintage tube set on", "splayed legs, plain", "antennae, the checked", "shield in front"]),
    ("F3", "Blend of 7b and 7c", ["7c's tube body and legs,", "7b's check-mark antennae,", "a plain shield in front"]),
]
SCHEMES = [
    ("#c2410c on #fbf7f1", "#c2410c", "#fbf7f1"),
    ("#ff7a33 on #141414", "#ff7a33", "#141414"),
]
NAME = "KidsWatch for Plex"
ORANGE, OFF_WHITE = "#c2410c", "#fbf7f1"
PAD = 32
LABEL_W = 330
TITLE_H = 44
SIDE_W = 130
MOCK_W = 560
HEADER_H = 120
SHEET_BG = "#ece6dd"
INK = "#15171c"
FONT_DIR = pathlib.Path("/usr/share/fonts/noto")
BOLD = FONT_DIR / "NotoSans-Bold.ttf"
MEDIUM = FONT_DIR / "NotoSans-Medium.ttf"
REGULAR = FONT_DIR / "NotoSans-Regular.ttf"
PANEL_W = PAD + 512 + PAD + SIDE_W + PAD
PANEL_H = TITLE_H + 512 + 40
ROW_H = PANEL_H


class Renderer:
    def __init__(self, tmp: pathlib.Path):
        self.tmp = tmp

    def __call__(self, svg: pathlib.Path, color: str, size: int) -> Image.Image:
        colored = self.tmp / f"{svg.stem}-{color[1:]}.svg"
        colored.write_text(svg.read_text().replace("currentColor", color))
        out = self.tmp / f"{colored.stem}-{size}.png"
        subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), str(colored), "-o", str(out)], check=True)
        return Image.open(out).convert("RGBA")


def smooth_round_rect(size, radius, color, scale=4):
    """An anti-aliased rounded rectangle (Pillow's own has jagged corners)."""
    w, h = size
    big = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle((0, 0, w * scale - 1, h * scale - 1), radius=radius * scale, fill=color)
    return big.resize((w, h), Image.LANCZOS)


def app_tile(render, mark, size=180):
    """An app-icon tile: orange rounded square, off-white mark."""
    tile = smooth_round_rect((size, size), round(size * 0.225), ORANGE)
    glyph = render(mark, OFF_WHITE, round(size * 0.68))
    tile.alpha_composite(glyph, ((size - glyph.width) // 2, (size - glyph.height) // 2))
    return tile


def browser_tab(render, favicon, dark, width=MOCK_W):
    """A tab strip at real size: an active tab with the 16 px favicon and the page title."""
    strip_bg, tab_bg, text, dim = (("#202124", "#35363a", "#e8eaed", "#9aa0a6") if dark
                                   else ("#dfe1e5", "#ffffff", "#202124", "#5f6368"))
    strip = Image.new("RGBA", (width, 42), strip_bg)
    tab = smooth_round_rect((250, 40), 8, tab_bg)
    strip.alpha_composite(tab, (8, 6))
    strip.alpha_composite(Image.new("RGBA", (250 - 16, 8), tab_bg), (16, 34))
    strip.alpha_composite(render(favicon, "#ff7a33" if dark else ORANGE, 16), (22, 18))
    d = ImageDraw.Draw(strip)
    d.text((46, 26), NAME, font=ImageFont.truetype(str(REGULAR), 12), fill=text, anchor="lm")
    d.text((238, 26), "×", font=ImageFont.truetype(str(REGULAR), 15), fill=dim, anchor="mm")
    d.text((276, 26), "+", font=ImageFont.truetype(str(REGULAR), 18), fill=dim, anchor="mm")
    return strip


def site_header(render, mark, dark, width=MOCK_W):
    """A website header at real size: 40 px mark, the wordmark, and a few nav links."""
    bg, fg, text, dim = (("#141414", "#ff7a33", "#f5efe6", "#b8b0a6") if dark
                         else ("#fbf7f1", ORANGE, INK, "#5b5750"))
    bar = Image.new("RGBA", (width, 68), bg)
    bar.alpha_composite(render(mark, fg, 40), (18, 14))
    d = ImageDraw.Draw(bar)
    d.text((66, 34), NAME, font=ImageFont.truetype(str(BOLD), 20), fill=text, anchor="lm")
    nav = ImageFont.truetype(str(MEDIUM), 13)
    x = width - 18
    for link in reversed(["Parents", "Server owners", "Get started"]):
        x -= int(d.textlength(link, font=nav))
        d.text((x, 34), link, font=nav, fill=fg if link == "Get started" else dim, anchor="lm")
        x -= 20
    return bar


def main() -> None:
    big = ImageFont.truetype(str(BOLD), 84)
    mid = ImageFont.truetype(str(BOLD), 30)
    sub = ImageFont.truetype(str(BOLD), 22)
    small = ImageFont.truetype(str(REGULAR), 20)
    caption = ImageFont.truetype(str(REGULAR), 18)
    width = LABEL_W + 2 * (PANEL_W + PAD) + MOCK_W + PAD
    height = HEADER_H + len(FINALISTS) * (ROW_H + PAD)
    sheet = Image.new("RGBA", (width, height), SHEET_BG)
    draw = ImageDraw.Draw(sheet)
    draw.text((PAD, 28), f"{NAME}: mark finalists, round 4", font=mid, fill=INK)
    draw.text((PAD, 70), "Each row: the mark at 512 and 64 px with its 16 px favicon in both color schemes, "
              "then an app-icon tile, browser tabs and website headers, all at real size", font=small, fill=INK)

    with tempfile.TemporaryDirectory() as t:
        render = Renderer(pathlib.Path(t))
        for r, (mark_id, name, about) in enumerate(FINALISTS):
            mark, favicon = HERE / f"mark-{mark_id}.svg", HERE / f"favicon-{mark_id}.svg"
            y = HEADER_H + r * (ROW_H + PAD)
            draw.text((PAD, y + 150), mark_id, font=big, fill=INK)
            draw.text((PAD, y + 270), name, font=sub, fill=INK)
            for i, line in enumerate(about):
                draw.text((PAD, y + 306 + i * 28), line, font=small, fill=INK)

            for s, (scheme, fg, bg) in enumerate(SCHEMES):
                x0 = LABEL_W + s * (PANEL_W + PAD)
                draw.rounded_rectangle((x0, y, x0 + PANEL_W, y + PANEL_H), radius=18, fill=bg)
                draw.text((x0 + PAD, y + 12), f"{mark_id}   ({scheme})", font=small, fill=fg)
                top = y + TITLE_H
                sheet.alpha_composite(render(mark, fg, 512), (x0 + PAD, top))
                draw.text((x0 + PAD + 256, top + 518), "512 px", font=caption, fill=fg, anchor="ma")
                cx = x0 + PAD + 512 + PAD + SIDE_W // 2
                fav = render(favicon, fg, 16)
                cells = [(render(mark, fg, 64), "64 px"), (fav, "favicon 16 px"),
                         (fav.resize((64, 64), Image.NEAREST), "favicon at 4x")]
                cy = top + 60
                for img, text in cells:
                    sheet.alpha_composite(img, (cx - img.width // 2, cy))
                    draw.text((cx, cy + img.height + 8), text, font=caption, fill=fg, anchor="ma")
                    cy += img.height + 80

            mx = LABEL_W + 2 * (PANEL_W + PAD)
            draw.text((mx, y + 12), "App icon, 180 px", font=small, fill=INK)
            sheet.alpha_composite(app_tile(render, mark), (mx, y + TITLE_H))
            sheet.alpha_composite(app_tile(render, mark, 60), (mx + 210, y + TITLE_H + 120))
            draw.text((mx + 284, y + TITLE_H + 150), "60 px", font=caption, fill=INK, anchor="lm")
            ty = y + TITLE_H + 180 + 22
            draw.text((mx, ty), "Browser tab, real size", font=small, fill=INK)
            for i, dark in enumerate((False, True)):
                sheet.alpha_composite(browser_tab(render, favicon, dark), (mx, ty + 34 + i * 50))
            hy = ty + 34 + 100 + 18
            draw.text((mx, hy), "Website header, real size", font=small, fill=INK)
            for i, dark in enumerate((False, True)):
                sheet.alpha_composite(site_header(render, mark, dark), (mx, hy + 34 + i * 76))
    sheet.convert("RGB").save(HERE / "sheet.png", optimize=True)


if __name__ == "__main__":
    main()
