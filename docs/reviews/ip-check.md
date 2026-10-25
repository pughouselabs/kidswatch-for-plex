# Originality and trademark check

Review date: 2026-10-24. Base commit: `492324f` (main). Scope: every bundled asset
(icons, fonts, placeholder art, images) and every public use of the Plex name,
checked against Plex's published trademark guidelines.

**Not legal advice.** This is a plain reading of public guidance by a project
contributor, not a lawyer's opinion. If Plex or anyone else raises a concern, or
before spending money on the name, ask a trademark attorney.

## Source used

Plex, "Trademarks and Guidelines",
<https://www.plex.tv/about/privacy-legal/plex-trademarks-and-guidelines/>.
Fetched 2026-10-24. The page reports `dateModified` 2024-01-25 (first published
2017-10-31). Quotes below are from that copy. Plex can change these guidelines at
any time, and its license says users must follow them "as amended from time to
time", so recheck before each major release.

Rules from that page that matter here:

- **Naming. You may:** use "for Plex" after your app's name, "provided that the
  name of your application is unique", and say your app "Works with Plex".
  **You may not:** use Plex in the name itself, use "for Plex" after a
  descriptive name ("Remote Control, Client, Player etc. are descriptive terms"),
  **"Register a domain containing Plex (or misspellings of Plex)"**, or "Apply for
  a trademark with a name including Plex".
- **Icons. You may not:** "Use or make derivative use of Plex trademark icons.
  Examples of derivative iconography would include: a play symbol in profile; a
  play symbol, or part of one, with black, dark grey or orange coloring."
- **Look and feel. You may not:** copy "the Plex look and feel ... (including the
  use of our distinct color combinations, graphic designs or typography)", or show
  Plex marks "more prominent than the names and trademarks identifying your
  business, product or service".
- **Attribution.** "If you use any Plex trademarks, you must provide attribution
  of Plex ownership", either "Plex, the Plex Play logo and Plex Media Server are
  trademarks of Plex and used under a license" (naming the marks you use) or
  "* used under license from Plex".
- **Listed marks:** PLEX, PLEX PASS, PLEX MEDIA SERVER, PLEX.TV and the Plex Play
  logo ("&gt;" in stylized format), among others, owned by Plex, Inc.
- **Wording (from the written-works section):** use Plex "as an adjective or noun,
  never as verb, or in the plural or possessive form".

## Short answer

"KidsWatch for Plex" plus "Not affiliated with or endorsed by Plex Inc." is the
pattern Plex itself invites ("XYZ for Plex"), and it is a reasonable nominative
use **once three things are fixed**: the app icon uses an orange play symbol,
which is exactly the derivative icon Plex names; the project domain
`kidswatchplex.com` contains "Plex", which the guidelines forbid; and the
disclaimer is missing the trademark attribution line Plex requires (and is
missing from the app itself). The name "KidsWatch" is borderline on Plex's
"unique, not descriptive" test, and an older parental-control product used the
same name, so a quick trademark search is worth doing before launch.

## Required fixes (before going public)

| # | Finding | Where | Fix | File owner |
|---|---------|-------|-----|------------|
| R1 | The logo is an orange tile with a white TV holding an **orange play triangle** (`#d0480f` on an `#ea631d` to `#c8430d` gradient). Plex lists "a play symbol, or part of one, with ... orange coloring" as forbidden derivative iconography. | `app/static/icon.svg`, `site/icon.svg`, `app/static/icon-192.png`, `icon-512.png`, `icon-maskable-512.png`, `apple-touch-icon.png` | Remove the play triangle from the mark. Keep the TV with the check-mark antennae (the original idea) and put something non-play inside the screen (a heart, a smile, a clock, or a blank screen). Ideally also move the tile color off orange (see N1). Regenerate all PNGs from the new SVG. | design / landing agents |
| R2 | "Sign in with Plex" buttons show a **play icon** on an orange button right next to the word Plex. That pairs a play symbol, orange, and the Plex name in one spot. | `app/templates/landing.html` lines 14 and 203 | Use a different icon (`i-user`, `i-link`, or none) on those two buttons. The play icon is fine on "Resume" buttons, which are not near the Plex name. | templates agent |
| R3 | Project domain **`kidswatchplex.com`** contains "Plex". WHOIS shows it was registered 2026-10-24 at Cloudflare and has no DNS yet. Plex says you may not "Register a domain containing Plex (or misspellings of Plex)". | `README.md` line 3, `site/index.html` lines 9 and 15 (canonical and og:url) | Pick a domain without "plex" (for example a `kidswatch` name on another TLD, or a `pughouselabs` subpath) and point the README and site at it. Do not launch on `kidswatchplex.com`. Whether to keep it parked or let it lapse is the owner's call, but holding it is itself what the rule names. | README / site agents, owner for the registration |
| R4 | The disclaimer says "not affiliated", but Plex also **requires an ownership attribution** whenever its marks are used. It is missing everywhere. | `README.md` line 10, `site/index.html` line 73 | Replace with: "Plex, Plex Pass and Plex Media Server are trademarks of Plex, Inc. and are used under license. KidsWatch for Plex is an independent project and is not affiliated with or endorsed by Plex, Inc." | README / site agents |
| R5 | The running app (footer and public landing page) has **no disclaimer at all**. Families who sign in never see that it is not a Plex product, and the landing page has a big "Sign in with Plex" button. | `app/templates/base.html` line 48, `app/templates/landing.html` footer | Add the R4 text in small print to the shared footer (it covers every page, including landing). | templates agent |

## Nice-to-haves (lower risk, still worth doing)

| # | Finding | Fix |
|---|---------|-----|
| N1 | Brand color is orange (`--brand: #c2410c`, dark mode `#ff7a33` on near-black `#12100e`). Plex's own look is dark grey with gold/amber, and its icon rule names "black, dark grey or orange". Not a copy, but the dark theme lands close to the combination Plex calls out. | Consider a brand hue that is clearly not Plex (green, teal, or blue would also separate it from the orange logo problem in R1). If orange stays, at least fix R1 and R2. |
| N2 | On the holding page, "for Plex" in the `<h1>` is the same size as "KidsWatch" and is the only colored part, so the eye lands on "Plex" first. Plex says its marks may not be "more prominent than" your product name. | Make "for Plex" smaller or a muted color, or color "KidsWatch" instead (`site/style.css` line 26). |
| N3 | "KidsWatch" is two plain words ("kids" + "watch"). Plex says descriptive names do not qualify for the "for Plex" suffix. It reads as a coined compound, so it probably passes, but it is not strongly distinctive. A Windows parental-control product called **KidsWatch** existed around 2007 to 2009 (sold at Target; last listed build 2009), plus unrelated kids-smartwatch apps ("Kids Watch 2", "KidiWatch"). No live registration turned up in one web search; USPTO was not checked. | Run a USPTO search for "KidsWatch" in software classes (9, 42) before launch. Never try to register "KidsWatch for Plex" (Plex forbids it); register only "KidsWatch" if the owner ever wants to. |
| N4 | Plex's guidance says not to use "Plex" in the possessive. Copy uses "Plex's own free movies and Live TV" and similar: README (5 places), `help.html` (1), `_kid_header.html` (1), `kid_settings.html` (2). | Reword, for example "the free movies and Live TV from Plex". Low risk; that rule sits in the written-works section. |
| N5 | Disclaimer says "Plex Inc." | Plex writes "Plex, Inc." Use the comma form in R4. |
| N6 | Repo slug `pughouselabs/kidswatch-for-plex`. A GitHub path is not a domain, and many community projects use "-for-plex" repo names, so this looks fine. | Keep. If GitHub Pages is used, serve it from a custom domain without "plex" rather than relying on the repo name in the URL. |
| N7 | Plex's API and Terms of Service (revised 2025-10-30 per search results) are a separate topic from trademarks and were not reviewed here. | Worth a separate read before launch, since the app drives Plex sharing and session APIs. |

## Asset-by-asset check

| Asset | Origin | Redistribution | Notes |
|-------|--------|----------------|-------|
| `app/static/icon.svg`, `site/icon.svg` | Original, hand-written SVG (the comment explains the concept: TV with check-mark antennae plus a play button) | Covered by the project MIT license | Original, but see R1 (play symbol in orange). |
| `icon-192.png`, `icon-512.png`, `icon-maskable-512.png`, `apple-touch-icon.png` | Raster exports of the same icon (checked visually) | MIT | Same R1 issue; regenerate after the fix. |
| `app/static/fonts/figtree-latin.woff2` | Figtree by Erik Kennedy, variable weight 300 to 900, latin subset. Font name table: "Copyright 2022 The Figtree Project Authors", Version 2.002, license URL openfontlicense.org | SIL OFL 1.1 allows bundling and redistribution with software if the copyright and license travel with it | `fonts/OFL-Figtree.txt` is present and ships in the Docker image (Dockerfile copies `app/`). Subsetting makes a "Modified Version", which is only restricted if the font declares a Reserved Font Name. The license file declares none, so the subset may keep the Figtree name. OK. |
| `app/static/lp/*.svg` (6 files: Cloud Garden, Maple Club, Mountain Puzzle, Paper Harbor, Pebble Trail, Winter Picnic) | Original placeholder posters: one flat color plus the made-up title in generic `sans-serif` text | MIT | No real film art or real titles. Keep it that way: never commit real posters from a Plex library (studio key art is copyrighted). |
| `app/static/poster-missing.svg` | Original simple film-frame outline | MIT | OK. |
| `app/templates/_icons.html` (35 UI symbols) | Hand-written 24x24 stroke paths with this project's own coordinates; no license header, not copied from Lucide, Heroicons, Feather or Tabler (paths differ) | MIT | Simple geometric shapes. The `i-play` symbol is a generic triangle; fine on its own, see R2 for where it is used. |
| Inline caret SVG in `app/static/style.css` | A one-line chevron path (`m6 9 6 6 6-6`), a trivial shape | MIT | OK. |
| `app/static/app.js`, `tools/check_layout.mjs` | Project code, no third-party snippets or headers found | MIT | OK. |
| Screenshots (`docs/screenshots/*.png`, checked `child-titles.png`) | Taken from the dev server with the fake library above | MIT | Show only the made-up titles and the fake user "Alex". They do show the current logo, so retake them after R1. |
| Python dependencies (`requirements.txt`) | Installed from PyPI into the Docker image, not vendored in the repo | All permissive (MIT, BSD, Apache 2.0, PSF style) | Their license files ship inside `site-packages` in the image. Nothing to add to the repo. |
| External loads | None. No Google Fonts, CDNs or remote scripts; the holding site's CSP blocks all of them | n/a | OK. |

The app also identifies itself to Plex honestly: `app/plex.py` sends
`X-Plex-Product: KidsWatch for Plex`, not an official Plex client name. Links to
`plex.tv/sign-up`, `app.plex.tv` and `support.plex.tv` are plain text links,
which the guidelines allow.

## Suggested order

1. R1 and R2 together (new logo without a play symbol, new icon on the sign-in buttons), then regenerate PNGs and screenshots.
2. R3 (new domain) before anything links to the old one.
3. R4 and R5 (attribution plus disclaimer in README, holding site and app footer).
4. N1 to N5 as time allows; N3 (USPTO search) before any paid launch work on the name.
