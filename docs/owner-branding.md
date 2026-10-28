# Optional owner branding

All settings are optional. Leaving them empty preserves the existing pages and
manifest. Restart the app after changing settings or artwork.

| Setting | Default | Behavior |
| --- | --- | --- |
| `PK_APP_NAME` | Empty | Replaces the shared header brand, default page title, installation title, and manifest `name` and `short_name`. Trimmed to 80 characters. Pages with their own title keep that title. |
| `PK_FOOTER_TEXT` | Empty | Replaces the first footer line. Trimmed to 240 characters. The Plex trademark disclaimer always remains. |
| `PK_LANDING_ART_DIR` | Empty | Directory containing `art.json` and optional poster files for the signed-out landing page and its example history thumbnails. |

Text is trimmed and HTML-escaped in pages. HTML markup is displayed as text.
Manifest names are JSON strings. Empty or whitespace-only text keeps the default.
These settings do not change the mobile API, app icons, or product copy.

## Artwork mapping

Set `PK_LANDING_ART_DIR` to an existing directory readable by the app. In a container,
mount that directory read-only and use its container path in the setting. It does
not need write access. Do not put private files in this directory: mapped artwork
is public to signed-out visitors.

Create `art.json` in that directory, with any of these six slot keys:
`maple-club`, `cloud-garden`, `paper-harbor`, `mountain-puzzle`, `pebble-trail`,
and `winter-picnic`. For example:

```json
{
  "maple-club": {"filename": "sample-show.webp", "title": "Sample Show"},
  "pebble-trail": {"filename": "sample-movie.png", "title": "Sample Movie"}
}
```

Each entry requires `filename` and a nonempty plain-text `title`. Titles are trimmed
to 120 characters and replace the corresponding example history title when that
slot appears there. The six poster tiles are decorative and retain empty alt text.
Unspecified slots keep their bundled artwork and titles. Unknown slot keys are ignored.

Only `.webp`, `.png`, and `.jpg` files are accepted, at most 10 MiB each. SVG is not
accepted. Filenames must be a single component, start with an ASCII letter or digit,
and contain only ASCII letters, digits, underscores, periods, or hyphens (at most
200 characters). Subdirectories, absolute paths, traversal, and symlink files are
rejected. `art.json` must be a regular file, not a symlink, and at most 16 KiB.

The app reads artwork into memory at startup, with a maximum of six images. It
serves only mapped slots under `/owner-art/<slot>`, read-only, with the matching
image content type, `nosniff`, and a one-hour public cache lifetime. Directory
listing and arbitrary file access are unavailable. Same-origin artwork works with
the existing content security policy. Invalid or missing directories, mappings,
or files produce a warning and use bundled art without stopping the app. A bad
slot does not prevent other valid slots from loading. Source file changes require
a restart; existing browser caches may take up to one hour to refresh.

## Undo

Clear these three environment settings and restart the app to restore the bundled
branding and art. No database or mobile configuration changes are made.
