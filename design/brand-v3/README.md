# Mark 7 "On guard" as a TV, round 3

The owner picked round 2 mark 7 "On guard" (a shield with a check mark in front of an outlined screen, see [../brand-v2/mark-7.svg](../brand-v2/mark-7.svg)) and asked for the screen to read more clearly as a television. These four takes keep the checked shield standing in front and redraw the screen as a TV: wider than tall (about 1.6 to 1), a thicker bezel (6 units instead of 5 on the 64 grid), and antennae on top. The [contact sheet](sheet.png) shows each one next to the original 7 at 512, 64 and 16 px (plus the 16 px render at 4x) in orange #c2410c on #fbf7f1 and #ff7a33 on #141414.

- **7a Rabbit ears** ([SVG](mark-7a.svg)): a wide set with a thick bezel and two round-tipped antennae, the checked shield in front.
- **7b Okayed set** ([SVG](mark-7b.svg)): the same wide set on two short feet, its antennae bent into a check mark, the checked shield in front.
- **7c Tube set** ([SVG](mark-7c.svg)): a vintage tube set with gently bowed, squared-oval sides on two splayed legs, plain antennae, the checked shield in front.
- **7d Knob set** ([SVG](mark-7d.svg)): a classic set with the screen on the left and a control strip of two knobs on the right, the checked shield in front of the screen.

All four are original drawings built from simple shapes: no play triangle or play-button shape, no chevron, no letters, and no outside assets or fonts. Each SVG is a single flat path filled with `currentColor` on a transparent background, so the cut-outs stay see-through on any background. At 16 px every take reads as a TV with antennae, which the original did not; the check inside the shield blurs to a notch at that size on all of them (as it did on the original), so a 16 px favicon may want a simplified shield. 7a is the strongest: the cleanest silhouette, and the round antenna tips still show at 16 px. 7b says "okayed" twice (antennae and shield), and 7d's knob strip turns to a smudge below 32 px. None of these are connected to the app.

The marks are written as add and cut steps in `make_marks.py`, and Inkscape flattens them into plain paths. To rebuild everything (needs Inkscape, `rsvg-convert` and Pillow), run from the repository root:

```sh
python3 design/brand-v3/make_marks.py
python3 design/brand-v3/make_sheet.py
```

To undo this addition after merging, revert the commit that adds `design/brand-v3/`.
