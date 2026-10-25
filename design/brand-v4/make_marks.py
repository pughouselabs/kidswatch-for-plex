"""Build the round 4 finalists in design/brand-v4 as flat single-path marks.

The owner liked round 3 marks 7b "Okayed set" and 7c "Tube set". These are the
refined finalists, each with a simplified favicon drawn for 16 px:

    mark-F1.svg       7b refined: check-mark antennae and feet, a shield with a small heart in front
    mark-F1-alt.svg   7b refined with the check kept inside the shield
    mark-F2.svg       7c refined: vintage tube set on splayed legs, checked shield
    mark-F3.svg       blend: 7c's tube body and legs, 7b's check-mark antennae, plain shield
    favicon-*.svg     the 16 px version of each: thicker lines, fewer details
                      (the F1 favicons drop the feet, and F1's drops the heart)

Each mark is a list of steps on a 64 x 64 grid. A step either adds shapes to the
mark or cuts them out of it. Inkscape turns every stroke into an outline and runs
the add and cut steps in order, so each finished SVG is one plain path filled with
currentColor: no masks, strokes or second colors. The favicons use the same grid
but are laid out on a 16 px raster (4 units per pixel) so their edges land on
whole or half pixels.

Needs Inkscape 1.x. Run from the repository root:
    python3 design/brand-v4/make_marks.py
"""
import math
import pathlib
import re
import subprocess
import tempfile

HERE = pathlib.Path(__file__).resolve().parent


def stroke(d, w):
    return (f'<path d="{d}" fill="none" stroke="#000" stroke-width="{w}" '
            'stroke-linecap="round" stroke-linejoin="round"/>')


def fill(d):
    return f'<path d="{d}" fill="#000"/>'


def rect(x, y, w, h, r, sw):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="none" stroke="#000" stroke-width="{sw}"/>'


def shield(cx, top, w, h):
    """A shield w wide and h tall: a gently dipped top edge and a rounded point (same as rounds 2 and 3)."""
    l, r, b = cx - w / 2, cx + w / 2, top + h
    shoulder = top + h * 0.13
    side = top + h * 0.5
    return (f"M{cx} {top}C{cx + w * 0.17} {top + h * 0.09} {r - w * 0.15} {shoulder} {r} {shoulder}"
            f"V{side}C{r} {side + h * 0.3} {cx + w * 0.22} {b - h * 0.08} {cx} {b}"
            f"C{cx - w * 0.22} {b - h * 0.08} {l} {side + h * 0.3} {l} {side}"
            f"V{shoulder}C{l + w * 0.15} {shoulder} {cx - w * 0.17} {top + h * 0.09} {cx} {top}Z")


def shield_check(cx, top, w, h):
    """The round 2 check mark, scaled to sit inside a shield of the given size."""
    sx, sy = w / 31, h / 32.3
    pts = [(-8, 15.3), (-2, 21.3), (8.5, 10.8)]
    return "M" + " ".join(f"{cx + x * sx:.2f} {top + y * sy:.2f}" for x, y in pts)


def guard(cx, top, w, h, gap, check_w=None):
    """Steps that stand a shield in front of whatever was drawn before, with an optional check cut through it."""
    s = shield(cx, top, w, h)
    steps = [("cut", [fill(s), stroke(s, 2 * gap)]), ("add", [fill(s)])]
    if check_w:
        steps.append(("cut", [stroke(shield_check(cx, top, w, h), check_w)]))
    return steps


def heart(cx, cy, s):
    """A small heart s wide, centered on (cx, cy)."""
    k = s / 2
    pts = [(0, 0.85), (-0.15, 0.72), (-1, 0.15), (-1, -0.35), (-1, -0.75), (-0.7, -1), (-0.45, -1),
           (-0.2, -1), (0, -0.82), (0, -0.6), (0, -0.82), (0.2, -1), (0.45, -1), (0.7, -1), (1, -0.75),
           (1, -0.35), (1, 0.15), (0.15, 0.72), (0, 0.85)]
    f = [f"{cx + x * k:.2f} {cy + y * k:.2f}" for x, y in pts]
    return "M" + f[0] + "".join("C" + " ".join(f[i:i + 3]) for i in range(1, len(f), 3)) + "Z"


def tube(cx, cy, w, h, n=4.2, steps=120):
    """A vintage tube set outline: a rounded square-ish oval (superellipse) w wide and h tall."""
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        pts.append(f"{cx + w / 2 * math.copysign(abs(c) ** (2 / n), c):.3f} "
                   f"{cy + h / 2 * math.copysign(abs(s) ** (2 / n), s):.3f}")
    return "M" + "L".join(pts) + "Z"


# Full-size parts, 64 grid
BEZEL = 6
LINE = 4             # antennae and legs
CHECK_W = 4.6        # check cut through the shield
GAP = 3.2            # clear space around the shield
SHIELD = (32, 33, 25, 28)

BOX = rect(6, 19, 52, 30, 9, BEZEL)                       # 7b's wide set
CHECK_ANT = stroke("M21.5 11 28 17.5 41.5 4", 4.4)        # 7b's antennae bent into a check
FEET = stroke("M14 51 12 56.5M50 51 52 56.5", LINE)       # 7b's short feet
TUBE = stroke(tube(32, 34, 51, 31), BEZEL)                # 7c's tube body, a touch narrower
V_ANT = stroke("M28.5 18 23 7M35.5 18 41 7", LINE)        # 7c's plain antennae
LEGS = stroke("M15.5 49 12 56M48.5 49 52 56", LINE)       # 7c's splayed legs

# Favicon parts, 64 grid laid out on a 16 px raster (4 units = 1 px)
F_BEZEL = 8          # 2 px
F_LINE = 6           # 1.5 px
F_GAP = 4            # 1 px
F_SHIELD = (32, 28, 32, 36)                               # 8 x 9 px, rows 7 to 15
F_BOX = rect(4, 20, 56, 24, 8, F_BEZEL)                   # outer edge on the pixel grid: 0..64 x 16..48
F_TUBE = stroke(tube(32, 32, 56, 24, n=3.6), F_BEZEL)
F_CHECK_ANT = stroke("M19 9 26 16 40 2.5", F_LINE)
F_V_ANT = stroke("M28 16 23.5 4M36 16 40.5 4", F_LINE)
F_LEGS = stroke("M12 47 9.5 56.5M52 47 54.5 56.5", F_LINE)

MARKS = [
    {
        "id": "F1",
        "about": "7b refined: a wide set on two short feet, its antennae bent into a check mark, a shield with a small heart standing in front",
        "steps": [("add", [BOX, CHECK_ANT, FEET]), *guard(*SHIELD, GAP), ("cut", [fill(heart(32, 46.8, 12.5))])],
        "favicon": [("add", [F_BOX, F_CHECK_ANT]), *guard(*F_SHIELD, F_GAP)],
    },
    {
        "id": "F1-alt",
        "about": "7b refined with the check kept: check-mark antennae, feet, and the checked shield in front",
        "steps": [("add", [BOX, CHECK_ANT, FEET]), *guard(*SHIELD, GAP, CHECK_W)],
        "favicon": [("add", [F_BOX, F_CHECK_ANT]), *guard(*F_SHIELD, F_GAP, 5.5)],
    },
    {
        "id": "F2",
        "about": "7c refined: a vintage tube set on two splayed legs with plain antennae, the checked shield in front",
        "steps": [("add", [TUBE, V_ANT, LEGS]), *guard(*SHIELD, GAP, CHECK_W)],
        "favicon": [("add", [F_TUBE, F_V_ANT, F_LEGS]), *guard(*F_SHIELD, F_GAP, 5.5)],
    },
    {
        "id": "F3",
        "about": "blend: 7c's tube body and splayed legs with 7b's check-mark antennae, a plain shield in front",
        "steps": [("add", [TUBE, CHECK_ANT, LEGS]), *guard(*SHIELD, GAP)],
        "favicon": [("add", [F_TUBE, F_CHECK_ANT, F_LEGS]), *guard(*F_SHIELD, F_GAP)],
    },
]


def build(name, steps, tmp):
    parts, actions = [], ["select-all", "object-stroke-to-path"]
    for i, (op, elems) in enumerate(steps):
        ids = [f"s{i}e{j}" for j in range(len(elems))]
        for el, eid in zip(elems, ids):
            parts.append(el.replace(" ", f' id="{eid}" ', 1))
        if len(ids) > 1:
            actions += ["select-clear", "select-by-id:" + ",".join(ids), "path-union"]
        if i:
            actions += ["select-clear", f"select-by-id:s0e0,{ids[0]}",
                        "path-union" if op == "add" else "path-difference"]
    src = tmp / f"src-{name}.svg"
    out = tmp / f"out-{name}.svg"
    src.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">'
                   + "".join(parts) + "</svg>")
    actions += ["export-plain-svg", f"export-filename:{out}", "export-do"]
    subprocess.run(["inkscape", str(src), "--actions=" + ";".join(actions)],
                   check=True, capture_output=True)
    paths = re.findall(r"<path\b[^>]*>", out.read_text(), re.S)
    if len(paths) != 1 or "transform" in paths[0]:
        raise SystemExit(f"{name}: expected one flat path, got {len(paths)}")
    d = re.search(r'\bd="([^"]+)"', paths[0]).group(1)
    return re.sub(r"(\d+\.\d{2})\d+", r"\1", re.sub(r"\s+", " ", d)).strip()


def write(path, comment, d):
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n'
                    f"  <!-- {comment} -->\n"
                    f'  <path fill="currentColor" fill-rule="evenodd" d="{d}"/>\n'
                    "</svg>\n")
    print(f"{path.name}  {len(d)} chars")


def main():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        for mark in MARKS:
            n = mark["id"]
            write(HERE / f"mark-{n}.svg", f"{n}. {mark['about']}", build(n, mark["steps"], tmp))
            write(HERE / f"favicon-{n}.svg", f"{n} favicon, drawn for 16 px: thicker lines, fewer details",
                  build(f"{n}-favicon", mark["favicon"], tmp))


if __name__ == "__main__":
    main()
