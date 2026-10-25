# Security fixes: independent review

Reviewed 2026-10-24 at merge `2ee6ce0`, including `12dd857`, against
`docs/reviews/security-oss.md`. Scope: the merge's first-parent diff, surrounding
authorization and storage paths, tests, and the container build. No application
files changed, no push, no merge, and no live service or database touched.

**Verdict: NOT SAFE TO SHIP YET.** A real pre-upgrade cookie bypasses the new
session validation and even bypasses the previously working global sign-out
epoch. Fix finding 1 before release. Findings 2–3 also need correction to meet
the bounded-limiter requirement and avoid wrongly signing out legitimate parents.
The remaining Low issues should be fixed or explicitly accepted as outstanding;
this merge does not close the entire original audit.

## Ranked findings

### 1. High / P1 — Legacy cookies bypass all session validation, including global revocation

**Location:** `app/main.py:210–213`; misleading regression test at
`tests/test_sec_sessions.py:66`.

`sid` is `None` in a pre-upgrade cookie. On a new request,
`getattr(request.state, "checked_sid", None)` is also `None`, so `user()` returns
the cookie's identity before checking either `session_epoch` or the sessions
table. This is not an unsigned-cookie attack: existing, legitimately signed
cookies have precisely this shape, and the signing key remains unchanged.

**Reproduced:** constructed the old signed cookie using the FakePlex signing key,
with an owner identity, no `sid`, and epoch `1`; deleted every server session and
set the current epoch to `999`. `/admin` still returned **200**. After logout,
replaying the same old cookie still returned **200**. Repeated with an enabled,
shared parent having a real parent row: `/home` returned **200** in both cases.
Cookie renewal on responses lets continued use keep this access alive. In
particular, the new version breaks global revocation for these old cookies.

The existing legacy-cookie test passes because it never creates the parent row:
`require()` rejects the parent for that unrelated reason. It does not demonstrate
that `user()` rejected the cookie.

**Fix:** reject missing/invalid session IDs before the request-cache comparison,
or require a nonempty string ID for the cached-success branch. Always validate
the epoch and stored session on the first identity check. Add old-owner-cookie
and existing-parent-cookie tests, including replay after logout and after global
sign-out, and a mismatched epoch. No owner-token rotation should be needed once
the validation bug is fixed.

### 2. Medium / P2 — Key count is bounded, but each rate-limit bucket is not

**Location:** `app/security.py:96–104`; unauthenticated caller at
`app/main.py:543`.

Every attempt appends a timestamp, including rejected attempts. The limiter
scans and copies that entire list on every subsequent hit. `max_keys=5000` caps
the number of dictionary entries, not the number of timestamps or work per key.

**Reproduced:** `RateLimiter(max_keys=1)` followed by 12,000 calls to
`hit("same-key", 10, 60)` retained **12,000 timestamps in one bucket**. A visitor
repeatedly hitting `/login` after receiving 429 still exercises this path without
needing spoofed headers or many addresses. Work grows with all attempts in the
window, and cumulative work is quadratic for a burst. Actual HTTP saturation
and peak memory were not load-tested.

**Fix:** use a bounded deque/token bucket; for the current fixed limits/windows,
retain at most `limit + 1` relevant timestamps, or count only admitted hits and
reject excess attempts without growing the bucket. Preserve separate address
and authenticated-key tables and the global PIN cap. Test repeated rejected
requests for bounded storage, bounded work, and recovery after the window.

### 3. Medium / P2 — Freshly authorized parent is immediately signed out by an older negative sharing snapshot

**Location:** `app/main.py:228–255`, `app/main.py:567`, `app/main.py:597`.

The OAuth callback reads `plex.shared_users()` and admits a shared parent, but
does not update `svc.shared_ids`/`shared_at`. `require()` then checks a different,
older snapshot and permanently deletes that new session if the parent is absent.

**Reproduced:** set a current service snapshot containing Sam but not Alex, then
let the FakePlex sharing response contain both (representing a new share or
re-share). Alex's sign-in returned **303 /home**; the immediately following
`/home` returned **303 /** and Alex's session count became **0**. With the keeper
off, repeated sign-ins fail until the service snapshot ages past ten minutes.
With the keeper on, its next successful refresh can end the problem sooner.
This could also happen with the former keeper cache; the new on-demand cache
extends the behavior to keeper-off operation and makes the deletion explicit.

**Fix:** publish the successful callback's sharing snapshot to the service cache
with its observation time, and/or refresh a negative membership result before
invalidating a session. An inability to refresh should return a temporary 503
without deleting the session. Add a re-share/new-share regression test that
requires `/home` to succeed immediately after successful authentication.

### 4. Low / P3 — L1 still fails open on an indefinitely stale positive sharing snapshot

**Location:** `app/main.py:231–242`, `app/service.py:40–45`.

An initial unknown sharing list correctly causes 503, but a failed refresh keeps
any old positive list and returns it for authorization regardless of age.

**Reproduced:** after signing in a parent, set the positive service snapshot's
timestamp to one day ago and make `shared_users()` raise `PlexError`. `/home`
still returned **200**. If the owner removes the Plex share and this app cannot
refresh afterward, the parent keeps access to settings and connect links for
the remaining session lifetime. This is a remaining fail-closed gap, not a new
Plex-outage regression.

**Fix:** return unknown once a snapshot exceeds a defined authorization age and
refresh fails; issue 503 while preserving the session. If an outage grace period
is desired, bound and document it explicitly. Test both stale-positive failure
and recovery, not only a cache that has never been populated.

### 5. Low / P3 — L4 revocation does not stop an old connect callback already in progress

**Location:** `app/main.py:467`, `app/main.py:1339–1356`.

The callback validates the link before waiting for Plex, then connects the
account without checking that the token is still live. Replacing the link while
that network call is pending marks the old token used but cannot prevent the
pending request from attaching an account.

**Reproduced:** with an owner-created draft child, start the old link's callback
and block FakePlex `check_pin()` using a threading event. In the owner's browser,
request a new connect link and verify the old row is `used=1`. Release the blocked
check: the old callback returns **200** and the child's account becomes
**active** with Plex ID 201. This is an inherited check/use race left open by the
new revocation promise; ordinary sequential use of a replaced link is refused.

**Fix:** serialize link rotation and final connection authorization per child,
and atomically claim/check the unexpired unused token before side effects.
Revalidate parent authorization at that boundary as well. A second check alone
narrows the race but is insufficient if rotation can occur after it; use shared
locking/transactional claim semantics appropriate to supported worker counts.
Add the deterministic in-flight replacement test, with expected cancellation or
an explicit already-claimed result rather than successful silent revocation.

### 6. Low / P3 — L6 remains incomplete for login failures and public HTML/error pages

**Location:** `app/main.py:177–179`, `app/main.py:543–548`.

The prefix list now covers `/verify`, `/waiting`, and `/auth`, but not `/login`,
`/help`, `/`, or arbitrary error paths. Login throttling and Plex failures render
HTML, and rendered pages carry session/CSRF information.

**Reproduced:** `/`, `/help`, and `/missing` had no `Cache-Control` header;
`/verify` had `no-store`. `/login` error responses also miss the prefix test by
inspection.

**Fix:** apply `no-store` to HTML responses, including rendered errors and login
failures. Preserve the intentional poster caching policy. Extend the header
tests to 429/503 login pages and a generic 404.

## Audit coverage and remaining original Low items

| Item | Result |
|---|---|
| M1: trusted forwarded headers | Pass for the stated trust model: only configured direct peers can supply forwarded identity; XFF walks right to left; CF identity/country additionally require opt-in. Container inspection confirms `--no-proxy-headers`. |
| M2: limiter reset and IPv6 | Global clearing removed; LRU key eviction and separate authenticated limits work; native IPv6 addresses share a /64 bucket; server-wide PIN starts capped at 60/minute. Per-key storage is still unbounded (finding 2). |
| M3: newly issued sessions | Random IDs hashed in SQLite; logout deletes the session; switching off a parent deletes all that user's sessions; global sign-out deletes all sessions. Owner 12-hour idle/7-day total and parent 14-day idle/30-day total limits work for new cookies. Old-cookie migration fails (finding 1). |
| L1: sharing recheck | Initial unknown state refuses access without deleting the session; keeper-off stale snapshots are refreshed. Findings 3–4 remain. |
| L2: readable Plex PINs | Fixed for new login/link flows: cookie carries only an opaque nonce; pending PIN data is process-local, capped, and expires. Restart loses unfinished sign-ins, requiring retry. |
| L3: Unicode owner code | Fixed: ASCII-only extraction treats Arabic-Indic digits as a wrong attempt instead of raising 500. |
| L4: old connect links | Sequential replacement works; in-flight race remains (finding 5). |
| L5: parent sign-in alerts | Not implemented. `app/main.py:599` still only audits parent sign-in; no new-address alert or per-session termination UI. A phished approved PIN can still grant parent access. Add the original proposed address-change alert and session-ending control. |
| L6: caching | Partial; see finding 6. |
| L7: expensive batches | Not implemented. `app/main.py:1220–1222` still loops over up to 1,000 titles; `app/service.py:348–349` scans libraries per title and uses separate writes. A large batch can still consume disproportionate CPU/DB work. Build a lookup once and transact batch changes. |
| L8: container/dependency hardening | Not implemented. `docker-compose.example.yml:38–44` has no memory/process caps; `Dockerfile:5` installs without hashes. Resource exhaustion remains uncontained by this example, and exact versions alone do not verify downloaded artifact hashes. Add reviewed resource limits, hash-locked installation, and dependency auditing. |

CF opt-in assumes the trusted proxy actually sanitizes Cloudflare headers and
that its origin cannot be reached by untrusted clients supplying those headers.
The application cannot establish that external deployment condition. With
`PK_TRUSTED_PROXIES` empty behind a proxy, visitors intentionally share one
10/minute sign-in bucket; the updated README explains configuring it. No live
reverse proxy was changed or tested.

## Validation and operational checks

- Required command: `~/Work/plex-kids/.venv/bin/python -m pytest -q` —
  **276 passed in 19.19s**, one Starlette/httpx deprecation warning. The passing
  suite does not negate the independently reproduced findings above.
- `sudo -n docker build -t kidswatch-security-review:2ee6ce0 .` — **passed**.
  Image config inspection confirmed the expected CMD with proxy handling disabled
  in uvicorn. Dependencies were cached; this was not a fresh dependency audit.
- Existing-database migration: extracted `SCHEMA` from `2ee6ce0^1:app/db.py`,
  created a temporary database containing settings, a parent, a child and a link,
  then initialized the new `DB` twice. All existing rows remained identical and
  the new sessions table had the expected five columns. No migration failure
  found. This used synthetic data, not the owner's database.
- Parsed every value in `.env.example`, loaded `Config`, and constructed the app
  with a temporary DB and FakePlex; **passed**. No invalid-switch/default startup
  regression found. Real startup still needs the documented writable DB path,
  readable Plex credentials, and usable deployment addresses. No live keeper,
  Plex sign-in, or HA notification delivery was exercised.
- Missing webhook with `owner_code=True`: owner sign-in returns **503** and no
  owner session. With explicit `owner_code=False`: **303 /admin**. The default
  example deliberately requires the second step and leaves the webhook unset;
  inability to sign in before delivery is configured is existing documented
  fail-closed behavior, not a new regression. A configured but disabled HA
  automation still cannot deliver a code; no fallback was added.
- Independent probes ran inline against temporary databases/FakePlex, without
  editing tests or app code. A deterministic event barrier demonstrated the
  connect callback race. No broad stress test or real multi-worker deployment
  test was run.

Only this report is committed. Revert its commit to undo the documentation
change. The temporary Docker tag can be removed with
`sudo -n docker image rm kidswatch-security-review:2ee6ce0`; no container was
started or deployed.
