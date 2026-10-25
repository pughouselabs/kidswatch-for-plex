# Brand mark concepts

Six single-color marks for the app icon and a small mascot. Each SVG uses `currentColor` on a transparent background, so it takes the color of the text around it. The [contact sheet](sheet.png) shows every mark at 512, 64 and 16 px in dark green on warm off-white and in off-white on near-black (plus the 16 px render at 4x so the pixels are easy to judge).

1. **Antenna ears** ([SVG](mark-1.svg)): an outlined screen is the pug's face, and its two antennae flop over at the tips like pug ears.
2. **Window** ([SVG](mark-2.svg)): a solid pug head with folded ears, with a screen cut out where the face sits.
3. **Watch party** ([SVG](mark-3.svg)): the back of a pug's head, ears folded, sitting in front of an outlined screen.
4. **Approved** ([SVG](mark-4.svg)): a bold pug head with the eyes and a check mark muzzle cut out as negative space.
5. **Block** ([SVG](mark-5.svg)): a solid TV on a little stand with folded pug ears, and the whole face cut out as negative space.
6. **One line** ([SVG](mark-6.svg)): one stroke draws the screen, and its bottom edge rises into the pug's muzzle.

All six are original drawings built from simple shapes, with no play triangle and no outside assets or fonts. Marks 2 to 5 use an SVG mask to cut the ear folds and face details, so the cut-outs stay transparent on any background. The mask colors inside them are mask values, not a second color. None of these are connected to the app.

To rebuild the sheet (needs `rsvg-convert` and Pillow), run from the repository root:

```sh
python3 design/brand/make_sheet.py
```

To undo this addition after merging, revert the commit that adds `design/brand/`.
