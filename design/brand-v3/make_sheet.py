"""Build design/brand-v3/sheet.png: each TV take on mark 7 next to the round 2 original,
at 512, 64 and 16 px in both color schemes.

Needs rsvg-convert (librsvg) and Pillow. Run from the repository root:
    python3 design/brand-v3/make_sheet.py
"""
import pathlib
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).resolve().parent
ORIGINAL = HERE.parent / "brand-v2" / "mark-7.svg"
MARKS = [("7a", "Rabbit ears"), ("7b", "Okayed set"), ("7c", "Tube set"), ("7d", "Knob set")]
SCHEMES = [
    ("#c2410c on #fbf7f1", "#c2410c", "#fbf7f1"),
    ("#ff7a33 on #141414", "#ff7a33", "#141414"),
]
SIZES = [512, 64, 16]
ZOOM = 4  # extra nearest-neighbor view of the 16 px render
PAD = 32
MIN_CELL = 130
LABEL_W = 250
HEADER_H = 110
TITLE_H = 44
CAPTION_H = 40
DIVIDER = 48
SHEET_BG = "#ece6dd"
INK = "#15171c"
FONT = "/usr/share/fonts/noto/NotoSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/noto/NotoSans-Regular.ttf"


def render(svg: pathlib.Path, color: str, size: int, tmp: pathlib.Path) -> Image.Image:
    colored = tmp / f"{svg.parent.name}-{svg.stem}-{color[1:]}.svg"
    colored.write_text(svg.read_text().replace("currentColor", color))
    out = tmp / f"{colored.stem}-{size}.png"
    subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), str(colored), "-o", str(out)], check=True)
    return Image.open(out).convert("RGBA")


def main() -> None:
    big = ImageFont.truetype(FONT, 96)
    mid = ImageFont.truetype(FONT, 30)
    small = ImageFont.truetype(FONT_REG, 22)
    # (render size, scale, caption); each cell is at least MIN_CELL wide so captions fit
    cells = [(size, 1, f"{size} px") for size in SIZES] + [(16, ZOOM, f"16 px at {ZOOM}x")]
    widths = [max(size * scale, MIN_CELL) for size, scale, _ in cells]
    half_w = sum(w + PAD for w in widths)
    panel_w = PAD + half_w + DIVIDER + half_w
    panel_h = TITLE_H + 512 + CAPTION_H + PAD
    row_h = len(SCHEMES) * (panel_h + PAD)
    width = LABEL_W + panel_w + PAD
    height = HEADER_H + len(MARKS) * (row_h + PAD)
    sheet = Image.new("RGBA", (width, height), SHEET_BG)
    draw = ImageDraw.Draw(sheet)
    draw.text((PAD, 28), "KidsWatch for Plex: mark 7 \"On guard\" redrawn to read as a TV, round 3", font=mid, fill=INK)
    draw.text((PAD, 66), "Each row: the round 2 original on the left, the new take on the right, "
              "in both color schemes", font=small, fill=INK)

    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        for r, (mark_id, name) in enumerate(MARKS):
            y_row = HEADER_H + r * (row_h + PAD)
            draw.text((PAD, y_row + 150), mark_id, font=big, fill=INK)
            draw.text((PAD, y_row + 280), name, font=small, fill=INK)
            draw.text((PAD, y_row + 312), "from round 2 mark 7", font=small, fill=INK)
            sides = [(ORIGINAL, "Original 7: On guard"), (HERE / f"mark-{mark_id}.svg", f"{mark_id}: {name}")]
            for s, (scheme, fg, bg) in enumerate(SCHEMES):
                y = y_row + s * (panel_h + PAD)
                x0 = LABEL_W
                draw.rounded_rectangle((x0, y, x0 + panel_w, y + panel_h), radius=18, fill=bg)
                for h, (svg, title) in enumerate(sides):
                    x = x0 + PAD + h * (half_w + DIVIDER)
                    draw.text((x, y + 12), f"{title}   ({scheme})", font=small, fill=fg)
                    for (size, scale, caption), w in zip(cells, widths):
                        img = render(svg, fg, size, tmp)
                        if scale > 1:
                            img = img.resize((size * scale, size * scale), Image.NEAREST)
                        side = size * scale
                        top = y + TITLE_H
                        sheet.alpha_composite(img, (x + (w - side) // 2, top + (512 - side) // 2))
                        draw.text((x + w // 2, top + 512 + 6), caption, font=small, fill=fg, anchor="ma")
                        x += w + PAD
                xd = x0 + PAD + half_w + DIVIDER // 2 - PAD // 2
                draw.line((xd, y + 20, xd, y + panel_h - 20), fill=fg, width=2)
    sheet.convert("RGB").save(HERE / "sheet.png", optimize=True)


if __name__ == "__main__":
    main()
