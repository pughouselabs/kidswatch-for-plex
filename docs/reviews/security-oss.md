# Security review: open-source release

Scope: the full tree at commit `492324f` (branch `main`), reviewed as strangers will
run it: a single container behind their own HTTPS reverse proxy, reachable from the
internet. Reviewed: Plex sign-in, the owner's 6-digit code, sessions, CSRF, access
rules between parents, co-parents and the owner, input checks, outbound URLs,
headers and CSP, secrets, Docker defaults, rate limits, logging, and behavior when
the notification webhook or Tautulli are not set up.

Method: a full read of `app/*.py`, `app/static/app.js`, the templates (for output
escaping only), `Dockerfile`, `docker-compose.example.yml`, `.env.example`,
`README.md` and `SECURITY.md`. Findings marked **Confirmed** were reproduced
against the real app with the repo's `FakePlex` test double (scratch script kept
outside the repo). The test suite passes (`120 passed`). No code was changed.

## Summary

No critical or high findings. Nothing lets an outside visitor sign in, read
another family's data, or reach the Plex owner token. The main gaps are in the
supporting controls: client addresses can be forged, which defeats the rate limits
and puts false details in the owner's sign-in alert; sessions cannot be revoked one
at a time; and a few smaller defaults should fail closed.

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| M1 | Medium | Client IP comes from headers any visitor can set | Confirmed |
| M2 | Medium | Rate limiter wipes all state after 5,000 keys | Confirmed |
| M3 | Medium | Sessions last 30 days, renew on use, and survive logout | Confirmed |
| L1 | Low | Unshared parents keep access until the keeper refreshes (forever with `PK_START_ENFORCER=0`) | Confirmed |
| L2 | Low | Plex PIN codes sit in the readable session cookie | Code read |
| L3 | Low | Non-ASCII digits in the owner code cause a server error | Confirmed |
| L4 | Low | Old connect links stay valid after a new one is made | Code read |
| L5 | Low | Phished Plex sign-in gives a parent's role with no alert | Code read, documented in README |
| L6 | Low | `no-store` is missing on sign-in and error pages | Code read |
| L7 | Low | Batch title changes are CPU heavy | Code read |
| L8 | Low | Docker example has no memory or process limits; pip installs without hashes | Code read |
| I1-I4 | Info | Small hardening notes | |

---

## Medium

### M1. Client IP comes from headers any visitor can set

Evidence:

- `app/main.py:118-119`: `_ip()` returns `CF-Connecting-IP` whenever the header
  exists, whatever the source.
- `Dockerfile:14-15`: uvicorn runs with `--proxy-headers --forwarded-allow-ips "*"`.
  With `*`, uvicorn takes the **leftmost** `X-Forwarded-For` entry
  (`uvicorn/middleware/proxy_headers.py`, `get_trusted_client_address`, the
  `always_trust` branch). Proxies that append to the header, such as nginx's common
  `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`, keep whatever the
  client sent at the left. So `request.client.host` can be forged too, even behind a
  normal proxy.
- That address is the rate-limit key (`app/main.py:437-438`), it is written to the
  audit log (`app/main.py:533, 556, 580, 589`), and it goes into the owner's
  sign-in code alert together with `CF-IPCountry` (`app/main.py:552-555`).

Exploit:

1. Rate limits: a script sends `GET /login` with a new `CF-Connecting-IP` value on
   every request. The 10-per-minute limit never triggers (confirmed: 15 requests
   with rotating headers, 0 limited, while 15 from one address had 5 limited). Each
   request makes the server create a PIN at `clients.plex.tv`
   (`app/main.py:480`), so one visitor can make the server hammer plex.tv. If
   plex.tv throttles the server's client ID or IP, sign-in breaks for every family.
2. Phishing help: the owner code exists to stop a phished Plex sign-in. An attacker
   who gets the owner to approve the attacker's Plex PIN then has the server push
   "Code 123456. Requested from 192.168.1.20 (US)". The attacker chooses that
   address and country, so the alert looks like the owner's own home, and the owner
   is more likely to read the code back when the attacker asks for it.
3. Audit trail: every sign-in record can carry a fake address.

The README warns about this (`README.md:212-216`), but the shipped default trusts
every source, and even a proxy set up as the README describes defeats it if the
proxy appends rather than replaces.

Fix:

- Replace `--forwarded-allow-ips "*"` with a setting. uvicorn reads the
  `FORWARDED_ALLOW_IPS` environment variable, so drop the flag from `CMD`, set a
  safe default in the Dockerfile (for a proxy on the Docker host, the bridge
  gateway, for example `172.17.0.1`), and document how to set it in `.env`. uvicorn
  then picks the rightmost untrusted entry, which is correct behind appending
  proxies.
- In `_ip()`, honor `CF-Connecting-IP` and `CF-IPCountry` only when the operator
  turns them on (for example `PK_TRUST_CF_HEADERS=1`) **and** the direct peer is a
  trusted proxy. Otherwise use `request.client.host` and leave the country out of
  the alert.
- Add one server-wide limit on PIN creation (for example 60 per minute across all
  addresses) so header tricks cannot turn the app into a plex.tv flood.

### M2. Rate limiter wipes all state after 5,000 keys

Evidence: `app/main.py:439-440`: `if len(rate) > 5000: rate.clear()`.

Exploit: with M1, 5,001 requests to `/login` with different spoofed addresses
empty the whole table. Every limit resets at once: sign-in (`/login`,
`/link/{token}/start`), the batch title limit (`batch:{id}`, `app/main.py:1151`)
and the 3-per-10-minutes notification limit (`notify:{id}`, `app/main.py:1233`).
Confirmed: an address that was getting 429 got 303 again after the flood. Even
without M1, a few thousand real addresses (or IPv6 addresses from one /64) do the
same.

Fix: never clear everything. On each call, drop only expired entries (all hits
older than the window). If the table is still too big, drop the oldest keys
(for example with an `OrderedDict` used as an LRU list), and keep the per-user keys
(`batch:`, `notify:`) in a separate table that strangers cannot fill.

### M3. Sessions last 30 days, renew on use, and survive logout

Evidence:

- `app/main.py:148-149`: Starlette's signed-cookie `SessionMiddleware` with
  `max_age=30 * 24 * 3600`. Starlette writes the cookie again on every response, so
  the 30 days restart with each visit.
- `app/main.py:592-596`: logout only clears the browser's copy. No server-side
  record exists to delete.
- `app/main.py:198-203`: the only server-side check is the global `session_epoch`.
- Owner sessions are never checked again after the code step. The 6-digit code
  protects the moment of sign-in, not the next 30 days.

Exploit: someone copies a session cookie (shared family computer, a synced browser
profile, malware, a backup). After the real user presses "Sign out", the copied
cookie still works (confirmed: a replayed cookie loaded `/home` with 200 after
logout). For the owner's cookie this means full control of children's sharing and
the ability to approve new Plex accounts, for as long as it keeps being used. The
only way to cut it off is "Sign everyone out", which signs out every family.

Fix:

- Add a `sessions` table (random ID, user ID, role, created time, last seen). The
  cookie carries only the session ID. Logout deletes the row, and the Admin page can
  list and end single sessions.
- Set lifetimes by role: for example, owner sessions end after 12 hours idle or
  7 days total, parent sessions after 30 days total. A cheaper first step that
  stays backwards compatible: store `iat` (sign-in time) in the session, check it
  in `user()`, and treat sessions without it as expired.

---

## Low

### L1. Unshared parents keep access until the keeper refreshes

Evidence:

- `app/main.py:213`: `gone = svc.shared_ids is not None and u["id"] not in svc.shared_ids`.
  When `shared_ids` is `None`, the check is skipped.
- `app/service.py:30, 38-42`: `shared_ids` starts as `None` and only
  `refresh_shared()` fills it in, from `periodic()`.
- `app/enforcer.py:62-65`: `periodic()` runs only from the keeper thread, every
  300 seconds, and the user list is cached for 120 seconds (`app/plex.py:272`).
- `app/main.py:142-143`: with `PK_START_ENFORCER=0` the keeper never starts, so
  `shared_ids` stays `None` for the life of the process. `link_row()` has the same
  gap (`app/main.py:1247`).

Exploit: the owner stops sharing Plex with a household (for example after a
falling out) but forgets to switch them off in KidsWatch. With the keeper off,
that parent can still change their children's settings, see watch history, and
create connect links indefinitely (confirmed). With the keeper on, the gap is about
the first 5 to 7 minutes after each restart or change.

Fix: fail closed. In `require()` and `link_row()`, if `shared_ids` is `None`, call
`svc.refresh_shared()` (cheap thanks to the cache) and refuse parent access if it
is still unknown. Call `refresh_shared()` once at startup whatever
`PK_START_ENFORCER` says.

### L2. Plex PIN codes sit in the readable session cookie

Evidence: `app/main.py:483` stores `{"id": pin_id, "code": code}` and
`app/main.py:1268` stores the child's `link_pin` the same way. Starlette session
cookies are signed, not encrypted: anyone who can read the cookie can decode it.

Exploit: while the PIN is still valid on plex.tv, a copy of the cookie taken after
the parent approves in Plex but before `/auth/done` runs (or after a sign-in left
unfinished) is enough to call `clients.plex.tv/api/v2/pins/{id}?code=...` directly
and receive the parent's **full Plex account token**, or the child's. That is far
more than KidsWatch access. Reading the cookie needs local access (HttpOnly and
Secure are set), so this is Low, but the app otherwise promises parent tokens are
never kept.

Fix: keep `{pin_id, code, at}` in a server-side dict keyed by a random nonce,
with a time limit (the pattern `pending_codes` already uses), and put only the
nonce in the session.

### L3. Non-ASCII digits in the owner code cause a server error

Evidence: `app/main.py:575`: `re.sub(r"\D", "", code)` keeps any Unicode digit
(Python's `\d` matches Arabic-Indic digits and others), then
`secrets.compare_digest` raises `TypeError` on non-ASCII strings.

Exploit: posting `code=١٢٣٤٥٦` to `/verify` returns a 500 (confirmed: unhandled
`TypeError`). It does not get anyone in and does not use up a try, so the impact is
an error page and noisy logs. Mobile keyboards in some languages type these digits.

Fix: `re.sub(r"[^0-9]", "", code)` (or pass `flags=re.ASCII`), then compare. The
existing `is_num()` helper shows the same care elsewhere.

### L4. Old connect links stay valid after a new one is made

Evidence: `new_link()` (`app/main.py:426-430`) inserts a token without retiring
older ones. Tokens are marked used only after a successful connect
(`app/main.py:1290`). `/link/{token}` needs no sign-in, by design (it opens on the
child's device), and shows the child's and parent's names (`app/main.py:1257-1258`).

Exploit: a parent shows a QR code, decides it was seen by the wrong person, and
presses "New connect link". The old link still works for the rest of its 30
minutes. Anyone with it can attach **their own** Plex account as that child. For a
child added by the owner, that account becomes `active` right away
(`app/service.py:303-304`) and gets an invite showing that child's titles. Other
parents' children still need the owner's approval, and the share only ever shows
labeled, rating-capped titles, so impact is limited.

Fix: when making a new link, run
`UPDATE link_tokens SET used=1 WHERE kid_id=? AND used=0` first. Optionally shorten
`LINK_TTL` to 15 minutes and delete expired rows now and then.

### L5. Phished Plex sign-in gives a parent's role with no alert

Evidence: `app/main.py:475-536`. Plex's PIN flow lets an attacker start sign-in in
their own browser and get a victim to approve the attacker's PIN. The 3-minute
`PIN_MAX_AGE` (`app/main.py:66, 491`) narrows the window, and the owner has the
6-digit code, but parents have nothing extra. The README states this
(`README.md:217-218`).

Exploit: a parent who approves an attacker's Plex link gives the attacker that
parent's KidsWatch role: unpause children, raise their age level, allow titles up
to PG-13 or TV-14, and read their watch history and devices.

Fix (to reduce the harm, since the flow cannot be fully fixed): notify the owner
(and show on the parent's home page) when a parent signs in from an address not
seen before, with an "end that session" link once M3's sessions table exists.

### L6. `no-store` is missing on sign-in and error pages

Evidence: `app/main.py:170-171` adds `Cache-Control: no-store` only for paths
starting with `/kids`, `/admin`, `/home`, `/link` and `/requests`. `/verify`,
`/waiting`, `/auth/done`, `/login` and error pages rendered by `message()` are left
out, and every rendered page carries a CSRF token.

Exploit: on a shared computer, the browser cache or a misconfigured caching proxy
can keep these pages. The risk is small because they hold little private data.

Fix: set `no-store` for every `text/html` response (and keep the existing
`private, max-age` on posters).

### L7. Batch title changes are CPU heavy

Evidence: `app/main.py:1148-1158` accepts up to 1,000 keys and calls
`svc.set_title` once for each. `app/service.py:345-346` scans the whole cached
library to find each key, and every call opens new SQLite connections. With the
limit of 20 batches per minute (`app/main.py:1151`, see also M2), one parent can
start tens of millions of comparisons per minute on a large library, which slows
pages and the keeper for everyone.

Fix: build a `rating_key -> item` map once per request (or per library in the
cache) and pass it in. Write the changes in one transaction.

### L8. Docker example has no memory or process limits; pip installs without hashes

Evidence: `docker-compose.example.yml` sets `read_only`, `cap_drop: ALL` and
`no-new-privileges` (good) but has no `mem_limit` or `pids_limit`. The `BodyLimit`
comment (`app/main.py:86-87`) already assumes a memory limit. `Dockerfile:5` runs
`pip install -r requirements.txt` with exact versions but no hashes.

Fix: add `mem_limit: 512m` and `pids_limit: 200` (or the `deploy.resources`
form) to the example. Generate hashes (`pip-compile --generate-hashes` or
`uv pip compile --generate-hashes`) and install with `--require-hashes`. Run a
dependency audit (for example `pip-audit`) in CI.

---

## Info

- **I1. Session key comes from the owner token** (`app/main.py:138`). This is an
  acceptable trade-off and is documented (`README.md:202-205`): anyone with the
  owner token can already do far more than forge a session. Note that the key is
  set once at startup, so a token change needs a restart. Consider adding an
  optional `PK_SESSION_SECRET_FILE` for operators who want to rotate sessions
  without touching Plex.
- **I2. Owner code travels inside the webhook body** (`app/main.py:553`,
  `app/notify.py:32`). If the receiver URL is plain HTTP across the internet, the
  code is readable on the way. The README already says to use HTTPS. Optionally,
  warn in the logs at startup when the webhook URL is not `https://` and does not
  point at a private address.
- **I3. CSP allows images from `https://*.plex.tv`** (`app/main.py:162`), but no
  template loads a Plex image (posters are proxied through `/poster/`). The rule
  can be removed to make the policy tighter.
- **I4. The school-break date check accepts impossible dates** such as
  `2026-13-45` (`app/main.py:947`). It is only compared as text, so there is no
  security impact; `date.fromisoformat()` would give a clearer error.

---

## What is done well

Sign-in and sessions

- Parent and child Plex tokens are used once and never stored (`app/main.py:505`,
  `app/main.py:1288`, `app/service.py:237`).
- The Plex sign-in return address comes from `PK_PUBLIC_URL`, not the request's
  Host header, so sign-in links cannot be pointed at another site
  (`app/main.py:484, 1269`).
- The owner's second step fails closed: with no notification receiver, admin
  sign-in is refused rather than skipped (`app/main.py:542-544`). Codes come from
  `secrets.randbelow`, last 5 minutes, allow 5 tries, work once, and are tied to the
  browser that started sign-in (`app/main.py:541-590`).
- The session is cleared and rebuilt at sign-in, the cookie is HttpOnly, Secure by
  default and SameSite=Lax, and "Sign everyone out" gives instant global revocation
  (`app/main.py:1391-1398`).
- New parents start in "waiting" unless the owner seeds them, must be people the
  server is actually shared with, and are re-checked on every request
  (`app/main.py:205-219, 515-525`). Children's accounts cannot sign in as parents
  (`app/main.py:507`).

Requests and access rules

- Every POST goes through one `form()` helper that checks the CSRF token with
  `compare_digest` (`app/main.py:227-233`). Every POST route was checked and uses it.
- Access to a child is checked in one place, `kid_for()`, which allows only the
  owner, the parent who added the child, or a co-parent the owner added
  (`app/main.py:264-274`). Co-parents cannot remove a child (`app/main.py:1059`).
  Copy sources and targets, request child IDs and request cancels are all checked
  against the signed-in parent. Admin routes require the owner. Refusals answer 404,
  so they do not reveal which IDs exist.
- Redirect targets are fixed or come from an allow-list (`back`,
  `app/main.py:965-966`), so there is no open redirect.
- Request bodies are capped at 64 KB, including chunked uploads
  (`app/main.py:83-115`). Forms allow no files and at most 100 fields.
- SQL is parameterized everywhere. The only built-up column names come from code
  constants, never from form fields (`app/db.py:195-200`, `COPY_FIELDS`).
- Names, messages and notes are trimmed to fixed lengths. Open requests,
  children per parent and notification alerts are capped.

Plex sharing safety

- A child's share starts empty and shows only titles carrying that child's label.
  The app reads each new share back, puts the limits back if Plex dropped them, and
  removes the share if it cannot. A failed removal is stored and retried after
  restarts (`app/service.py:67-87, 197-212, 267-287`).
- Linking refuses accounts that already have a share (so it cannot be used to put
  limits on, or take over, an adult friend's share), the owner's account, and
  parents' accounts (`app/service.py:250-265`).
- Ratings use an allow-list. Unrated, R, NC-17 and TV-MA titles can never be shown
  or allowed (`app/rules.py:57-75`, `app/service.py:349-350`).

Output, headers and outbound calls

- Jinja autoescaping is on. The `ratings` filter escapes before marking output safe
  (`app/main.py:1441-1444`). `app.js` writes text with `textContent`; the only
  `innerHTML` use copies the page's own button markup (`app/static/app.js:405-408`).
- The CSP allows no inline script or style and blocks framing, `<base>` changes and
  posting forms elsewhere. `nosniff`, `X-Frame-Options: DENY`, HSTS and a
  restrictive `Permissions-Policy` are set. `Referrer-Policy: same-origin` keeps
  connect-link tokens out of Referer headers sent to Plex (`app/main.py:161-169`).
- No user input picks a server-side URL, so there is no SSRF path. Plex, Tautulli
  and webhook addresses come only from operator settings. IDs are turned into
  integers before going into Plex URLs. `PK_MEDIA_REQUEST_URL` must start with
  `http://` or `https://`, which blocks `javascript:` links (`app/config.py:53-55`).
  Plex HTTP calls do not follow redirects (`app/plex.py:122`).
- API docs and the OpenAPI schema are turned off (`app/main.py:147`).

Secrets and logs

- The owner token is read in place from a read-only mount and sent only in request
  headers. Errors are rebuilt `from None`, so URLs and keys never reach logs or
  pages (`app/plex.py:177-185`). The notifier never logs the webhook URL
  (`app/notify.py:38`). uvicorn's access log is off, so connect-link tokens in URLs
  are not logged (`Dockerfile:15`).
- `.dockerignore` keeps `.env`, `data/`, `secrets/` and database files out of the
  image. `.env.example` holds no secrets.

Container

- Runs as UID 1000, read-only root filesystem, all capabilities dropped,
  `no-new-privileges`, published on `127.0.0.1` only, base image pinned by digest,
  exact dependency versions, read-only credential mounts with
  `create_host_path: false` (`Dockerfile`, `docker-compose.example.yml`).

Missing integrations

- No webhook: owner sign-in is blocked with a clear message (fail closed), and
  other alerts are skipped quietly without logging any message content beyond the
  title (`app/notify.py:28-31`).
- No Tautulli: the history panels show "Watch history is not available right now";
  nothing breaks and no key is needed (`app/plex.py:152-157, 512-529`).
- No request site: the built-in Requests form is used.
- Plex settings file unreadable at startup: the app refuses to start rather than
  running without a session key (`app/main.py:138`, `app/plex.py:129-135`).

## Suggested order of work

1. M1 and M2 together (proxy trust setting, CF header opt-in, limiter that never
   clears everything, global PIN-creation limit). Small change, removes the
   cheapest remote abuse.
2. L3 and L4 (one line each).
3. M3 (sessions table and role lifetimes), then L5 builds on it.
4. L1, L2, L6, L7, L8 as hardening.
