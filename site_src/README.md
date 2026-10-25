# Edition site sources

Edit this directory, then run `python3 site_src/build.py` from the repository root.
Commit both the sources and generated output. Cloudflare Pages still serves `site/`
directly; no hosting changes or build dependency are required.

`build.py` uses Python's standard library. It generates the three pages for each
edition and `site/style.css`, with stable ordering, UTF-8, LF line endings and no
timestamps. Run `python3 site_src/build.py --check` to detect stale output without
writing files. The command also works from any other current directory.

`templates/` is the only page/component/style template set. Header, footer and phone
preview are shared components. Both editions use `site/titles-demo.js`,
`site/copy.js` and `site/previews.js`. The templates contain the current Plex design;
edition files must not introduce separate feature layouts or scripts.

`editions/plex.json` and `editions/jellyfin.json` contain theme variables, names,
metadata, links and platform facts. Plain text is HTML-escaped by the builder.
Three documented instruction bodies retain platform-specific facts:

- `manual_steps`: Plex's existing nine-step guide, or Jellyfin's preview planning
  steps. Jellyfin has no public download, image or install script.
- `update_guide` and `remove_guide`: actual Plex commands, or preview-specific
  backup/restore notes, inside shared lifecycle headings.
- The `account_tip_*` facts: Plex's plus-address email tip, or Jellyfin's Quick
  Connect guidance, inside one shared account-tip template.

These three bodies have shared containers marked `data-platform-doc`. Install
steps also use a single shared step template, with platform-specific titles and
documentation content. They are
trusted repository documentation, not user input. Their formatting can differ
because the underlying steps differ. Keep scripts, styles, event handlers and new
page layout components out of these facts. Only the step bodies, update/remove
documentation and first account-tip paragraph accept rich formatting such as
paragraphs, links and code. All other content uses escaped text.

The Jellyfin TV preview deliberately uses small-toast styling with the observed
message; Plex retains its Playback Error dialog. The element tree is shared.
Both editions display the same warning and device-results containers, with their
own precise platform facts. No preview interacts with a real server or persists
data. The edition chooser, its styles, production headers and redirects remain
hand-maintained and unchanged by the generator.

Run the structural and browser checks:

```sh
python3 site_src/verify.py
python3 site_src/verify.py --serve 8789
# In another terminal:
node site_src/browser-check.mjs
```

The local server mirrors extensionless Pages routes and applies `site/_headers`,
including the production CSP. Browser verification uses system Chromium and the
installed Playwright package. `SITE_ORIGIN` changes its default localhost address;
`SITE_SCREENSHOTS` changes its screenshot output directory.

`verify.py` compares every element and structural component attribute between
corresponding pages, excluding only the contents of the three documented platform
instruction bodies. Names, URLs and metadata text may differ; component structure,
classes, IDs, interaction hooks and control types must match. Existing edition
fragment IDs remain available as aliases in the shared templates.

Undo the change by reverting the unification commit. That restores the old pages,
styles and scripts together; no runtime service, deployment or data migration is
involved.
