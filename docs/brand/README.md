# KidsWatch for Plex brand mark

The mark is F2 from the [round 4 finalists](../../design/brand-v4/README.md): a vintage tube TV on two splayed legs with a checked shield in front.

| File | Use |
| --- | --- |
| [kidswatch-mark.svg](kidswatch-mark.svg) | The full mark. Use it at 24 px and larger: site header, README, print. |
| [kidswatch-favicon.svg](kidswatch-favicon.svg) | The same mark redrawn for 16 px, with thicker lines. Use it below 24 px, such as browser tabs. |
| [kidswatch-app-icon.svg](kidswatch-app-icon.svg) | The app icon: the off-white mark (#fbf7f1) on an orange rounded square. |

## Rules

- **One color.** Use brand orange #c2410c on light backgrounds, the lighter orange #ff7a33 on dark ones, or off-white #fbf7f1 on orange. Never use two colors, gradients, outlines or shadows. The cut-outs (screen, shield, check) stay see-through so the background shows through them. To recolor, change the `fill` value in the SVG.
- **Minimum size.** 24 px for the full mark and 16 px for the favicon version. Leave clear space of at least one eighth of the mark's width around it.
- **Do not add a play triangle** or any play-button shape, and do not add letters to the mark. Do not stretch, rotate or redraw it.

## Rebuilding the icons

`build_icons.py` makes every icon file from the two SVGs above: `app/static/icon.svg` (app icon), `app/static/icon-favicon.svg`, the app PNGs (192, 512, maskable 512 and the 180 px Apple touch icon), and `site/icon.svg` and `site/favicon.svg`. It needs `rsvg-convert`. Run it from the repository root:

```sh
python3 docs/brand/build_icons.py
```

After changing an icon, raise the `?v=` number on the links to it in `app/templates/base.html`, `app/main.py` (the web app manifest) and `site/*.html` so browsers fetch the new file.
