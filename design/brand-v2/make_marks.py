"""Build design/brand-v2/mark-1.svg ... mark-8.svg as flat single-path marks.

Each mark is written below as a list of steps on a 64 x 64 grid. A step either adds
shapes to the mark or cuts them out of it. Inkscape turns every stroke into an
outline and runs the add and cut steps in order, so each finished SVG is one plain
path filled with currentColor: no masks, strokes or second colors.

Needs Inkscape 1.x. Run from the repository root:
    python3 design/brand-v2/make_marks.py
"""
import pathlib
import re
import subprocess
import tempfile

HERE = pathlib.Path(__file__).resolve().parent

FRAME_W = 5    # screen outline stroke
ANT_W = 4.5    # antenna and check stroke
GAP = 3.2      # clear space around a shape that sits in front of the screen


def stroke(d, w=ANT_W):
    return (f'<path d="{d}" fill="none" stroke="#000" stroke-width="{w}" '
            'stroke-linecap="round" stroke-linejoin="round"/>')


def fill(d):
    return f'<path d="{d}" fill="#000"/>'


def rect(x, y, w, h, r, sw=None):
    paint = f'fill="none" stroke="#000" stroke-width="{sw}"' if sw else 'fill="#000"'
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" {paint}/>'


def circle(cx, cy, r, sw=None):
    paint = f'fill="none" stroke="#000" stroke-width="{sw}"' if sw else 'fill="#000"'
    return f'<circle cx="{cx}" cy="{cy}" r="{r}" {paint}/>'


def heart(cx, top, w):
    """A heart w units wide with its top lobes at y=top (height is about 0.86 w)."""
    s = w / 52
    pts = [(32, 60), (20, 51), (6, 42), (6, 29), (6, 21), (12, 15.5), (19, 15.5),
           (25, 15.5), (29.5, 19), (32, 23), (34.5, 19), (39, 15.5), (45, 15.5),
           (52, 15.5), (58, 21), (58, 29), (58, 42), (44, 51), (32, 60)]
    p = [f"{cx + (x - 32) * s:.2f} {top + (y - 15.5) * s:.2f}" for x, y in pts]
    curves = " ".join(f"C{p[i]} {p[i + 1]} {p[i + 2]}" for i in range(1, len(p) - 1, 3))
    return f"M{p[0]} {curves}Z"


def shield(cx, top, w, h):
    """A shield w wide and h tall: a gently dipped top edge and a rounded point."""
    l, r, b = cx - w / 2, cx + w / 2, top + h
    shoulder = top + h * 0.13
    side = top + h * 0.5
    return (f"M{cx} {top}C{cx + w * 0.17} {top + h * 0.09} {r - w * 0.15} {shoulder} {r} {shoulder}"
            f"V{side}C{r} {side + h * 0.3} {cx + w * 0.22} {b - h * 0.08} {cx} {b}"
            f"C{cx - w * 0.22} {b - h * 0.08} {l} {side + h * 0.3} {l} {side}"
            f"V{shoulder}C{l + w * 0.15} {shoulder} {cx - w * 0.17} {top + h * 0.09} {cx} {top}Z")


# Shared parts for the outlined-screen family (concept 1)
SCREEN_1 = rect(7.5, 22, 49, 35.5, 11, FRAME_W)
CHECK_ANTENNAE = "M21 15 28.5 22.5 45.5 6"          # short left leg, long right leg
BALL_ANTENNAE = "M27.5 21 20.5 10.5M36.5 21 43.5 10.5"

HEART_CHECK = "M25.5 18 31.5 24 47 5"

# Shared screen for the something-in-front family (concept 3)
SCREEN_3 = rect(6.5, 6.5, 51, 39, 11, FRAME_W)

MARKS = [
    {
        "name": "Okayed favorite",
        "parent": 1,
        "about": "an outlined screen whose antennae form a check mark, with a heart on the screen",
        "steps": [
            ("add", [SCREEN_1, stroke(CHECK_ANTENNAE), fill(heart(32, 30.8, 23))]),
        ],
    },
    {
        "name": "Lights out",
        "parent": 1,
        "about": "an outlined screen with check mark antennae and a crescent moon on the screen for bedtime",
        "steps": [
            ("add", [SCREEN_1, stroke(CHECK_ANTENNAE), circle(31, 40.5, 10.5)]),
            ("cut", [circle(37.5, 35, 8.6)]),
        ],
    },
    {
        "name": "Screen time",
        "parent": 1,
        "about": "a round-tipped antenna screen showing a solid clock face with the hands cut out",
        "steps": [
            ("add", [SCREEN_1, stroke(BALL_ANTENNAE), circle(19.5, 9.5, 3.6), circle(44.5, 9.5, 3.6),
                     circle(32, 39.8, 11)]),
            ("cut", [stroke("M32 33.2V39.8L36.8 43.2", 3.2)]),
        ],
    },
    {
        "name": "Safe screen",
        "parent": 2,
        "about": "a solid shield with a screen window cut out and a check mark on the screen",
        "steps": [
            ("add", [fill(shield(32, 3, 50, 58))]),
            ("cut", [rect(14.5, 15.5, 35, 24.5, 6.5)]),
            ("add", [stroke("M23.5 28 29.5 34 40.5 23")]),
        ],
    },
    {
        "name": "Loved and okayed",
        "parent": 2,
        "about": "a solid heart with a screen window cut out, and check mark antennae rising from its top",
        "steps": [
            ("add", [fill(heart(32, 19, 54))]),
            ("cut", [rect(18.5, 33, 27, 15.5, 5), stroke(HEART_CHECK, ANT_W + 2 * GAP)]),
            ("add", [stroke(HEART_CHECK)]),
        ],
    },
    {
        "name": "Bedtime set",
        "parent": 2,
        "about": "a solid round set on two short feet, like a bedside alarm clock, with a screen window cut out and a crescent moon on the screen",
        "steps": [
            ("add", [circle(32, 35, 23.5), stroke("M19.5 52 16 58M44.5 52 48 58"),
                     stroke("M25.5 13.5 20 5.5M38.5 13.5 44 5.5")]),
            ("cut", [rect(15, 23, 34, 25, 7.5)]),
            ("add", [circle(30.5, 35.5, 8.6)]),
            ("cut", [circle(35.8, 31.2, 7)]),
        ],
    },
    {
        "name": "On guard",
        "parent": 3,
        "about": "an outlined screen with a solid shield standing in front of it, a check mark cut through the shield",
        "steps": [
            ("add", [SCREEN_3]),
            ("cut", [fill(shield(32, 29.7, 31, 32.3)), stroke(shield(32, 29.7, 31, 32.3), 2 * GAP)]),
            ("add", [fill(shield(32, 29.7, 31, 32.3))]),
            ("cut", [stroke("M24 45 30 51 40.5 40.5", 4.6)]),
        ],
    },
    {
        "name": "Time check",
        "parent": 3,
        "about": "an outlined screen with a solid clock face in front of its corner, the hands cut out",
        "steps": [
            ("add", [SCREEN_3]),
            ("cut", [circle(43, 44, 15.5 + GAP)]),
            ("add", [circle(43, 44, 15.5)]),
            ("cut", [stroke("M43 35.5V44L49 48", 4), circle(43, 44, 2.9)]),
        ],
    },
]


def build(mark, n, tmp):
    parts, actions = [], ["select-all", "object-stroke-to-path"]
    for i, (op, elems) in enumerate(mark["steps"]):
        ids = [f"s{i}e{j}" for j in range(len(elems))]
        for el, eid in zip(elems, ids):
            parts.append(el.replace(" ", f' id="{eid}" ', 1))
        if len(ids) > 1:
            actions += ["select-clear", "select-by-id:" + ",".join(ids), "path-union"]
        if i:
            actions += ["select-clear", f"select-by-id:s0e0,{ids[0]}",
                        "path-union" if op == "add" else "path-difference"]
    src = tmp / f"src-{n}.svg"
    out = tmp / f"out-{n}.svg"
    src.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">'
                   + "".join(parts) + "</svg>")
    actions += ["export-plain-svg", f"export-filename:{out}", "export-do"]
    subprocess.run(["inkscape", str(src), "--actions=" + ";".join(actions)],
                   check=True, capture_output=True)
    paths = re.findall(r"<path\b[^>]*>", out.read_text(), re.S)
    if len(paths) != 1 or "transform" in paths[0]:
        raise SystemExit(f"mark {n}: expected one flat path, got {len(paths)}")
    d = re.search(r'\bd="([^"]+)"', paths[0]).group(1)
    d = re.sub(r"(\d+\.\d{2})\d+", r"\1", re.sub(r"\s+", " ", d)).strip()
    return d


def main():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        for n, mark in enumerate(MARKS, start=1):
            d = build(mark, n, tmp)
            (HERE / f"mark-{n}.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n'
                f'  <!-- {n}. {mark["name"]} (from concept {mark["parent"]}): {mark["about"]} -->\n'
                f'  <path fill="currentColor" fill-rule="evenodd" d="{d}"/>\n'
                "</svg>\n")
            print(f"mark-{n}.svg  {len(d)} chars")


if __name__ == "__main__":
    main()
