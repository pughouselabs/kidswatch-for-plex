# Mark finalists, round 4

The owner liked round 3 marks 7b "Okayed set" and 7c "Tube set" (see [../brand-v3](../brand-v3/README.md)). These are the refined finalists. Each one comes with a favicon drawn for 16 px, with thicker lines and fewer details. The [contact sheet](sheet.png) shows every finalist as it will really appear: the mark at 512 and 64 px and its 16 px favicon (plus the favicon at 4x) in orange #c2410c on #fbf7f1 and #ff7a33 on #141414, an app-icon tile (off-white mark on an orange rounded square) at 180 and 60 px, browser tabs with the favicon next to "KidsWatch for Plex", and website headers with the wordmark, all at real size.

- **F1, 7b refined** ([mark](mark-F1.svg), [favicon](favicon-F1.svg)): the wide set on two short feet with its antennae bent into a check mark. The shield in front carries a small heart instead of a second check, so the mark says "okayed" once. The favicon drops the feet and the heart and keeps a plain shield.
- **F1-alt, 7b refined with the check kept** ([mark](mark-F1-alt.svg), [favicon](favicon-F1-alt.svg)): the same set with the checked shield in front, as in round 3. The favicon drops the feet and cuts a thicker check that still shows at 16 px.
- **F2, 7c refined** ([mark](mark-F2.svg), [favicon](favicon-F2.svg)): the vintage tube set with gently bowed sides, a touch narrower than 7c, on two splayed legs with plain antennae, the checked shield in front. The favicon keeps the legs and a thicker check.
- **F3, blend** ([mark](mark-F3.svg), [favicon](favicon-F3.svg)): 7c's tube body and splayed legs with 7b's check-mark antennae and a plain shield in front. The favicon keeps the legs.

All marks are original drawings built from simple shapes (a rounded screen, straight antennae and legs, a shield, a check mark and a heart): no play triangle or play-button shape, no letters, and no outside assets. Each SVG is a single flat path filled with `currentColor` on a transparent background, so it takes one color and the cut-outs stay see-through on any background. The favicons are laid out on the 16 px pixel grid so their edges stay crisp. A TV with antennae and a shield are common icon ideas on their own, so a trademark search is still worth doing before the final pick. None of these are connected to the app.

**Recommendation: F1.** The check-mark antennae are the one detail no other TV icon has, and moving the check off the shield fixes round 3's "okayed twice" problem. The heart adds warmth for parents at large sizes and simply disappears in the favicon, where the plain shield reads cleanly. F3 is a close second if the owner prefers the vintage tube look, but at 16 px F1 and F3 look almost the same, so the choice is really about the large mark.

The marks are written as add and cut steps in `make_marks.py`, and Inkscape flattens them into plain paths. To rebuild everything (needs Inkscape, `rsvg-convert`, Pillow and the Noto Sans fonts), run from the repository root:

```sh
python3 design/brand-v4/make_marks.py
python3 design/brand-v4/make_sheet.py
```

To undo this addition after merging, revert the commit that adds `design/brand-v4/`.
