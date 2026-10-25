"""Build design/brand-v3/mark-7a.svg ... mark-7d.svg as flat single-path marks.

These are four takes on round 2 mark 7 "On guard" (a shield with a check mark in
front of an outlined screen) that make the screen read clearly as a television.
Each mark is written below as a list of steps on a 64 x 64 grid. A step either adds
shapes to the mark or cuts them out of it. Inkscape turns every stroke into an
outline and runs the add and cut steps in order, so each finished SVG is one plain
path filled with currentColor: no masks, strokes or second colors.

Needs Inkscape 1.x. Run from the repository root:
    python3 design/brand-v3/make_marks.py
"""
import math
import pathlib
import re
import subprocess
import tempfile

HERE = pathlib.Path(__file__).resolve().parent

BEZEL = 6      # television outline stroke (round 2 used 5)
ANT_W = 4      # antenna and leg stroke
CHECK_W = 4.6  # check mark cut through the shield
GAP = 3.2      # clear space around the shield where it stands in front of the set


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


def shield(cx, top, w, h):
    """A shield w wide and h tall: a gently dipped top edge and a rounded point (same as round 2)."""
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


def guard(cx, top, w, h):
    """Steps that stand a checked shield in front of whatever was drawn before."""
    s = shield(cx, top, w, h)
    return [
        ("cut", [fill(s), stroke(s, 2 * GAP)]),
        ("add", [fill(s)]),
        ("cut", [stroke(shield_check(cx, top, w, h), CHECK_W)]),
    ]


def crt(cx, cy, w, h, n=4.5, steps=96):
    """A vintage tube set outline: a rounded square-ish oval (superellipse) w wide and h tall."""
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        pts.append(f"{cx + w / 2 * math.copysign(abs(c) ** (2 / n), c):.2f} "
                   f"{cy + h / 2 * math.copysign(abs(s) ** (2 / n), s):.2f}")
    return "M" + "L".join(pts) + "Z"


# Classic set shared by 7a and 7b: about 1.6 times wider than tall, thick bezel
SET = rect(6, 19, 52, 30, 9, BEZEL)
SHIELD = (32, 33, 25, 28)

MARKS = [
    {
        "id": "7a",
        "name": "Rabbit ears",
        "about": "a wide set with a thick bezel and two round-tipped antennae, the checked shield standing in front",
        "steps": [
            ("add", [SET, stroke("M28.5 18.5 21.5 8.5M35.5 18.5 42.5 8.5"),
                     circle(21, 7.5, 3.4), circle(43, 7.5, 3.4)]),
            *guard(*SHIELD),
        ],
    },
    {
        "id": "7b",
        "name": "Okayed set",
        "about": "the same wide set on two short feet, its antennae bent into a check mark, the checked shield in front",
        "steps": [
            ("add", [SET, stroke("M21 10.5 27.5 17 42 4", 4.4),
                     stroke("M13.5 51 11.5 56.5M50.5 51 52.5 56.5")]),
            *guard(*SHIELD),
        ],
    },
    {
        "id": "7c",
        "name": "Tube set",
        "about": "a vintage tube set with gently bowed sides on two splayed legs, plain antennae, the checked shield in front",
        "steps": [
            ("add", [stroke(crt(32, 34, 53, 31), BEZEL),
                     stroke("M29 17.5 23 6.5M35 17.5 41 6.5"),
                     stroke("M14.5 49.5 11 56.5M49.5 49.5 53 56.5")]),
            *guard(*SHIELD),
        ],
    },
    {
        "id": "7d",
        "name": "Knob set",
        "about": "a classic set with the screen on the left and a control strip of two knobs on the right, the checked shield in front of the screen",
        "steps": [
            ("add", [rect(5.5, 19, 53, 30, 8, BEZEL), stroke("M45.5 20V48", BEZEL - 1.5),
                     circle(51.6, 28.5, 2.6), circle(51.6, 38.5, 2.6),
                     stroke("M24 18.5 17 8.5M31 18.5 38 8.5")]),
            *guard(25.5, 33, 25, 28),
        ],
    },
]


def build(mark, tmp):
    n = mark["id"]
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
        for mark in MARKS:
            d = build(mark, tmp)
            (HERE / f"mark-{mark['id']}.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n'
                f'  <!-- {mark["id"]}. {mark["name"]} (from round 2 mark 7): {mark["about"]} -->\n'
                f'  <path fill="currentColor" fill-rule="evenodd" d="{d}"/>\n'
                "</svg>\n")
            print(f"mark-{mark['id']}.svg  {len(d)} chars")


if __name__ == "__main__":
    main()
