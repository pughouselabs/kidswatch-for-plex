# Shared edition site verification

Task UNI, based on public main `9484ef8`. Work is local only; nothing was pushed
or deployed.

The three corresponding Plex/Jellyfin pages now have identical shared structure.
The structural comparison recorded 1,056 element events for each overview, 788
for each parent guide and 762 for each owner guide. Only the contents of the
platform install guide, lifecycle instructions and child-account tip are excluded
from that comparison. Their containers, disclosures, cards and interactions are
still shared.

Intentional differences are orange versus violet theme variables; platform/product
names, icons, links and original metadata; Plex account/share/label/Pass facts versus
Jellyfin local-user/Quick Connect facts; exact non-affiliation and analytics footer
lines; released Plex instructions versus preview-only Jellyfin planning; platform
limitations and device observations; and Plex's actual error dialog versus
Jellyfin's observed small-toast message. There are no separate edition feature,
phone, schedule, activity, install-card, navigation or footer layouts.

Verification performed:

- Full test suite: **552 passed, 1 skipped**. The existing Starlette/httpx
  deprecation warning remains. No test assertions were changed.
- Focused email-tip suite after the final content changes: **29 passed**. The Plex
  plus-address tip remains intact; Jellyfin has no child email requirement.
- Chromium: **28 variants and 216 interaction assertions passed**, covering all
  seven pages at 1280 and 390 pixels, light and dark. No horizontal overflow,
  including expanded disclosures; no console, JavaScript or CSP errors; exact
  edition brand colors; working title flips, picked-only toggle, age tabs,
  grayscale blocked tiles with opaque white text, phone pause/undo/add-time,
  owner approval/keyboard tabs, prompt copy and share copy; no cookies.
- Screenshots include each initial viewport and full page, stored outside the
  shipped site at `/tmp/kidswatch-unified-qa`. Representative desktop and mobile,
  light and dark screenshots were visually inspected.
- All original canonical URLs and OG values are retained, as are all original
  element IDs, with old Jellyfin fragment targets preserved as shared aliases.
- Chooser HTML, chooser-specific CSS, `_redirects` and `_headers` match the base
  commit byte for byte. Production CSP is applied by the local QA server.
- `python3 site_src/build.py --check` and `python3 site_src/verify.py` pass.
  The builder adds no timestamps or external dependencies. Duplicate edition
  scripts/styles have been removed; edition pages load one stylesheet.
- `git diff --check` passes. The launch gate returned **GO** after the
  implementation commit: secrets, private-data rules, owner rules, copy and
  clean Git state all passed. No pushes or deployments were performed.

Reproduce using the commands in `site_src/README.md`. Revert the commit titled
`Unify edition sites with shared templates` to restore the old pages and assets.
No server state or deployment needs rollback.
