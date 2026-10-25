# Pug mascot concepts

- **1. Front-row friend** ([SVG](pug-1.svg), [512px preview](pug-1.png)): A round, three-quarter-view pug watches an orange TV beside it, with a broad face and friendly smile.
- **2. Cozy lookout** ([SVG](pug-2.svg), [512px preview](pug-2.png)): A rear-view pug with a prominent curled tail and a peek of its cheek watches a teal TV above it.
- **3. Little screen scout** ([SVG](pug-3.svg), [512px preview](pug-3.png)): A tall, rounded-square pug sits in profile facing a low plum TV, creating a compact side-by-side silhouette.

All three are original vector drawings made from simple shapes, with four flat colors, transparent backgrounds, and no external assets or fonts. The check-mark TV antenna echoes the existing app icon. The SVGs have a 512 by 512 viewBox and can be displayed at 128px or 512px; the PNG previews are 512 by 512 with transparency. These are standalone selection sketches and are not connected to the app.

To regenerate a preview with librsvg, run from the repository root:

```sh
rsvg-convert -w 512 -h 512 design/mascot/pug-1.svg -o design/mascot/pug-1.png
rsvg-convert -w 512 -h 512 design/mascot/pug-2.svg -o design/mascot/pug-2.png
rsvg-convert -w 512 -h 512 design/mascot/pug-3.svg -o design/mascot/pug-3.png
```

To undo this addition after merging, revert the commit that adds `design/mascot/`.
