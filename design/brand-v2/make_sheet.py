"""Build design/brand-v2/sheet.png: every round 2 mark at 512, 64 and 16 px in both color schemes.

Needs rsvg-convert (librsvg) and Pillow. Run from the repository root:
    python3 design/brand-v2/make_sheet.py
"""
import pathlib
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).resolve().parent
NAMES = ["Okayed favorite", "Lights out", "Screen time", "Safe screen",
         "Loved and okayed", "Bedtime set", "On guard", "Time check"]
PARENTS = [1, 1, 1, 2, 2, 2, 3, 3]
SCHEMES = [
    ("Orange #c2410c on warm off-white #fbf7f1", "#c2410c", "#fbf7f1"),
    ("Orange #ff7a33 on near-black #141414", "#ff7a33", "#141414"),
]
SIZES = [512, 64, 16]
ZOOM = 4  # extra nearest-neighbor view of the 16 px render
PAD = 32
MIN_CELL = 130
LABEL_W = 250
HEADER_H = 150
CAPTION_H = 40
SHEET_BG = "#ece6dd"
INK = "#15171c"
FONT = "/usr/share/fonts/noto/NotoSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/noto/NotoSans-Regular.ttf"


def render(svg: pathlib.Path, color: str, size: int, tmp: pathlib.Path) -> Image.Image:
    colored = tmp / f"{svg.stem}-{color[1:]}.svg"
    colored.write_text(svg.read_text().replace("currentColor", color))
    out = tmp / f"{svg.stem}-{color[1:]}-{size}.png"
    subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), str(colored), "-o", str(out)], check=True)
    return Image.open(out).convert("RGBA")


def main() -> None:
    big = ImageFont.truetype(FONT, 96)
    mid = ImageFont.truetype(FONT, 30)
    small = ImageFont.truetype(FONT_REG, 22)
    # (render size, scale, caption); each cell is at least MIN_CELL wide so captions fit
    cells = [(size, 1, f"{size} px") for size in SIZES] + [(16, ZOOM, f"16 px at {ZOOM}x")]
    widths = [max(size * scale, MIN_CELL) for size, scale, _ in cells]
    panel_w = PAD + sum(w + PAD for w in widths)
    row_h = PAD + 512 + CAPTION_H + PAD
    width = LABEL_W + len(SCHEMES) * (panel_w + PAD)
    height = HEADER_H + len(NAMES) * (row_h + PAD)
    sheet = Image.new("RGBA", (width, height), SHEET_BG)
    draw = ImageDraw.Draw(sheet)
    draw.text((PAD, 28), "KidsWatch for Plex: product mark concepts, round 2", font=mid, fill=INK)
    for i, (title, _, _) in enumerate(SCHEMES):
        x = LABEL_W + i * (panel_w + PAD)
        draw.text((x, 92), title, font=mid, fill=INK)

    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        for n, name in enumerate(NAMES, start=1):
            svg = HERE / f"mark-{n}.svg"
            y = HEADER_H + (n - 1) * (row_h + PAD)
            draw.text((PAD, y + 150), str(n), font=big, fill=INK)
            draw.text((PAD, y + 280), name, font=small, fill=INK)
            draw.text((PAD, y + 312), f"from concept {PARENTS[n - 1]}", font=small, fill=INK)
            for i, (_, fg, bg) in enumerate(SCHEMES):
                x0 = LABEL_W + i * (panel_w + PAD)
                draw.rounded_rectangle((x0, y, x0 + panel_w, y + row_h), radius=18, fill=bg)
                x = x0 + PAD
                for (size, scale, caption), w in zip(cells, widths):
                    img = render(svg, fg, size, tmp)
                    if scale > 1:
                        img = img.resize((size * scale, size * scale), Image.NEAREST)
                    side = size * scale
                    sheet.alpha_composite(img, (x + (w - side) // 2, y + PAD + (512 - side) // 2))
                    draw.text((x + w // 2, y + PAD + 512 + 6), caption, font=small, fill=fg, anchor="ma")
                    x += w + PAD
    sheet.convert("RGB").save(HERE / "sheet.png", optimize=True)


if __name__ == "__main__":
    main()
