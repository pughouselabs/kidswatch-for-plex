# Product mark concepts, round 2

Eight single-color marks about the product itself: kids' TV time with a parent's OK and a daily limit. There is no dog in any of them. Each one grows out of round 1 concept 1 (outlined screen with antennae), 2 (solid shape with a screen window cut out) or 3 (outlined screen with something in front of it). The [contact sheet](sheet.png) shows every mark at 512, 64 and 16 px in orange #c2410c on warm off-white #fbf7f1 and in #ff7a33 on near-black #141414, plus the 16 px render at 4x so the pixels are easy to judge.

1. **Okayed favorite** (from concept 1, [SVG](mark-1.svg)): an outlined screen whose antennae form a check mark, with a heart on the screen.
2. **Lights out** (from concept 1, [SVG](mark-2.svg)): the same check mark antenna screen with a crescent moon on it for bedtime.
3. **Screen time** (from concept 1, [SVG](mark-3.svg)): a screen with round-tipped antennae showing a solid clock face with the hands cut out.
4. **Safe screen** (from concept 2, [SVG](mark-4.svg)): a solid shield with a screen window cut out and a check mark on the screen.
5. **Loved and okayed** (from concept 2, [SVG](mark-5.svg)): a solid heart with a screen window cut out and check mark antennae rising from its top.
6. **Bedtime set** (from concept 2, [SVG](mark-6.svg)): a round set on two short feet, like a bedside alarm clock, with a crescent moon in its screen window.
7. **On guard** (from concept 3, [SVG](mark-7.svg)): an outlined screen with a solid shield standing in front of it and a check mark cut through the shield.
8. **Time check** (from concept 3, [SVG](mark-8.svg)): an outlined screen with a solid clock face in front of its corner, the hands cut out.

All eight are original drawings built from simple shapes: no play triangle, no chevron, no letters, and no outside assets or fonts. Each SVG is a single flat path filled with `currentColor` on a transparent background, so there are no masks, strokes or second colors, and the cut-outs stay see-through on any background. At 16 px, marks 1, 2, 3 and 4 stay the clearest. The small checks on 5 and 7 start to blur at that size, so those two would need a simplified 16 px version if picked. None of these are connected to the app.

The marks are written as add and cut steps in `make_marks.py`, and Inkscape flattens them into plain paths. To rebuild everything (needs Inkscape, `rsvg-convert` and Pillow), run from the repository root:

```sh
python3 design/brand-v2/make_marks.py
python3 design/brand-v2/make_sheet.py
```

To undo this addition after merging, revert the commit that adds `design/brand-v2/`.
