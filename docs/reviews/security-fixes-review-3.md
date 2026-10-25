# Security fixes: third independent review

Reviewed 2026-10-24 at `2923e40` (main), the merge of fix commit `7080b0e`, against
every finding in `security-oss.md`, `security-fixes-review.md` and
`security-fixes-review-2.md`, including that report's "Coordinator follow-up".
Application code and tests were read only. Probes ran against temporary databases
and the repository's `FakePlex` test double, from a scratch directory outside the
repo. No live Plex, database or deployment was touched. Only this report changed.

**Verdict: NOT SAFE TO SHIP.** Both round-two blockers are fixed: authorization
now reads sharing lists in database order, and child connections are claimed per
child. One new blocker sits next to that fix. Removing a child while its Plex
connection is still running brings the child back. The child then has a Plex
share that the keeper does not enforce and that Remove cannot take back. One small
hang and two documentation gaps are also listed below.

## New findings

### N1. Medium / P2 (blocker): removing a child during a connection brings it back with an unmanaged share

**Location:** `app/main.py:1141-1150` (`kid_remove`) and `app/service.py:430-443`
(`remove`) do not check or take `kids.connection_claim`.
`app/service.py:364-367` (end of `_connect`) writes `status` from the child row
it read before any Plex call, with no check that the claim or status still holds.
`attach_connection` (`app/db.py:244-249`) is the last conditional write.

**Reproduced** (scratch test, both the owner's child and a parent's child): start
`/link/{token}/done` and pause inside `plex.invite`, after `attach_connection` has
succeeded. Post `/kids/{id}/remove` with the correct confirmation: **303**, and the
row is `removed`. Release the pause. The callback returns **200**. The final row
is:

- the owner's child: `status=active`, `plex_id=NULL`, `sync_state=ok`, the
  "Removal pending" error cleared, titles 11, 12 and 14 labeled, and an
  **accepted** Plex share for account 201;
- a parent's child: `status=requested`, `plex_id=NULL`, a share for 201, and a new
  open `add_child` request for the owner to approve.

What this causes:

- The parent was told "Kid1 was removed and no longer has access." That is false.
- The keeper only enforces children with `plex_id IS NOT NULL`
  (`app/enforcer.py:85-86`). This account can watch the child's labeled titles
  with no time limits, bedtime or pause.
- Pressing Remove again returns 303 but leaves the Plex share in place, because
  `_drop_share` skips a child with no `plex_id` (`app/service.py:424-428`).
  `python -m app.cleanup` uses the same path. Only removing the share by hand in
  Plex takes it away. A second Remove does clear the labels, so the leftover share
  then shows nothing.

When the pause was put inside `plex.account` instead (before the attach), Remove
won cleanly: the callback was refused, the row stayed removed, and no share was
made. The open window runs from the attach to the final update: invite,
read-back, accept and the online-sources opt-out. That is several plex.tv round
trips. Only the child's own parent, a co-parent, or the owner can trigger it, so
an outsider cannot use it. But it breaks the "Remove takes access away" promise,
and the round-two fix was meant to close this kind of interleaving: one action on
a child racing with that child's connection.

By code read, the owner's Decline (`app/service.py:394-400`) and Approve
(`app/service.py:385-392`) can race the same way. A reconnect of a `requested`
child with `plex_accepted=0` holds a claim (`app/db.py:237-239`), and the final
update writes back the old status. No access results: Decline drops the share
first, and an undone approval only removes labels. It still leaves the record
wrong, for example `requested` with no account after a decline.

**Fix:** run Remove and Decline (and Approve) behind the same per-child lock.
Either refuse with the existing 409 "connection in progress" page when
`connection_claim IS NOT NULL` (set the new status in a `BEGIN IMMEDIATE` write
that requires `connection_claim IS NULL`), or make them wait. Also make the final
`_connect` update conditional (`WHERE id=? AND connection_claim=? AND status=?`
with the status read at the start). If that update matches nothing, retract the
share that was just made, the same way an unsafe share is retracted. Add the
paused-at-invite Remove test for both kinds of child, and check the final status,
`plex_id`, the remaining fake shares and the labels.

### N2. Low / P3: `require()` spins forever if the database's sharing sequence falls behind the process

**Location:** `app/main.py:266-275`, `app/service.py:69-79`, `app/db.py:213-219`.

`sharing_snapshot()` keeps the in-memory list when the database's `observation` is
lower. `end_session_for_observation` then compares against that in-memory number,
always returns False, and the loop retries with no limit. `require()` runs on the
event loop in `async` routes, so one parent request stalls the whole app.
**Reproduced:** set the service's in-memory observation above the database's
observation, with the parent missing from the in-memory list. `/home` had not
returned after 3 seconds and was still spinning. The only real trigger is
replacing or restoring the SQLite file while the app is running. The stuck-claim
instructions tell operators to stop every worker first, so this is Low. Fix: bound
the loop (one retry, then 503), or treat a database observation lower than the
in-memory one as "unknown" and return 503.

### N3. Low / P3: operator recovery steps exist only in a review report

The "Coordinator follow-up" in `security-fixes-review-2.md` explains how to clear
a stuck `connection_claim` after a crash, and states the 30-minute sharing-outage
grace. Neither appears in `README.md` or `SECURITY.md`. Round two asked for the
grace to be in operator-facing guidance. The gap matters more now: a stuck claim
makes both "New connect link" and "switch this parent off" return 409 for that
parent's family, with no documented way out. Switching off a parent is a safety
control (the owner can still remove the Plex share by hand). Fix: add a short
"Connection stuck / in progress" and "Plex outage" section to the README's
troubleshooting text.

## Round-two blockers and the coordinator's claims

| Item | Result | Evidence |
|---|---|---|
| **A: stale client cache republished as fresh** | **FIXED** | `PlexClient.shared_users` no longer caches (`app/plex.py:276-291`). Every authorization read takes a database sequence number *before* the HTTP call (`app/service.py:53-57`, `app/db.py:198-202`). `publish_shared` is a compare-and-set on that number and returns the winning list (`app/db.py:204-208`, `app/service.py:59-67`). The OAuth callback authorizes from the winner (`app/main.py:586,601`). Probe: an older observation published later with a fresh wall time lost, and Alex stayed signed in. The round-two out-of-order HTTP reproduction, now `tests/test_sec_observation_claims.py:76-114`, passes in-process and through a second `Service`/`DB` instance. |
| **A: session deletion vs. a newer list from another worker** | **FIXED** | `end_session_for_observation` re-reads the sequence inside `BEGIN IMMEDIATE` before deleting (`app/db.py:213-219`). `require()` retries against the newer list (`app/main.py:266-275`). Test at `tests/test_sec_observation_claims.py:117-134` passes. See N2 for the unbounded retry. |
| **B: two links connecting two accounts to one child** | **FIXED** | `claim_connection` checks, in one `BEGIN IMMEDIATE`, that the token is unused and unexpired and is the newest for that child, that the child can still be connected, and that the parent is enabled and in a sharing list younger than 1,800 s (`app/db.py:138-142,224-242`). Rotation refuses while a claim is held (`app/db.py:262-271`, `app/main.py:485-489`, HTTP 409). `attach_connection` checks the claim and authorization again after the network waits (`app/db.py:244-249`, `app/service.py:323-330`). Probe: two browsers finishing the same token at once gave `[200, 410]` and one share. The two-account and rotation interleavings pass (`tests/test_sec_observation_claims.py:137-184,224-252`). |
| **B: parent switched off during a connect** | **FIXED** | `set_parent_enabled` refuses while that parent's child holds a claim (`app/db.py:273-281`), and the admin route returns 409 before ending sessions (`app/main.py:1502-1503`). Tests pause at `connect`, `account` and `invite` (`tests/test_sec_observation_claims.py:186-221`). When disabling wins first, the claim fails on `enabled=1`. Accepted residual: the owner removing the parent's *Plex share* (not the KidsWatch switch) after the attach does not stop the invite. That is an external change outside the lock, and the parent's own sessions still end on their next request. |
| **B: canceled request** | **FIXED** | The claim is released in the worker thread's `finally` (`app/service.py:288-291`). The test at `tests/test_sec_observation_claims.py:255-299` passes. |
| **Multi-process claims and schema upgrade** | **FIXED** | Migration runs under `BEGIN IMMEDIATE` (`app/db.py:151`). The three-process claim test (`tests/test_sec_observation_claims.py:27-55`) passes. |

## Round-one findings (security-fixes-review.md)

| # | Result | Evidence |
|---|---|---|
| 1 High: legacy cookies bypass validation | **FIXED** | `app/main.py:213-216` rejects a missing or non-string `sid` before the per-request shortcut. Epoch and server session are checked at `app/main.py:219`. Tests in `tests/test_sec_review_fixes.py:43-86` pass. |
| 2 Medium: limiter buckets grow without limit | **FIXED** | Only admitted hits are stored, and the deque is trimmed to the limit (`app/security.py:97-114`). |
| 3 Medium: new parent signed out by an older list | **FIXED** | See blocker A above. |
| 4 Low: stale positive list trusted forever | **FIXED** | Refresh after 600 s, 503 after 1,800 s with the session kept (`app/main.py:234-245,266-270`). The claim uses the same limit (`app/db.py:142`). Operator documentation is still missing (N3). |
| 5 Low: in-flight connect survives link replacement | **FIXED** for rotation and parent switch-off. **Not fixed** for child Remove (N1). |
| 6 Low: no `no-store` on public and error HTML | **FIXED** | `app/main.py:178-181`. Probe with `.env.example` settings: `/` returned `no-store` and HSTS. |

## Original audit (security-oss.md)

| Item | Result | Evidence |
|---|---|---|
| M1 forged client address | **FIXED** | Forwarded headers only from `PK_TRUSTED_PROXIES` peers, walked right to left. CF headers need opt-in plus a trusted peer (`app/security.py:37-76`). Uvicorn runs with `--no-proxy-headers` (`Dockerfile:16-17`). |
| M2 limiter wipe | **FIXED** | LRU eviction of one key at a time, a separate authenticated table, IPv6 /64 buckets, and a global cap of 60 PIN starts per minute (`app/security.py:78-114`, `app/main.py:498-504`). |
| M3 sessions | **FIXED** | Server-side hashed session IDs, logout deletes them, limits by role (`app/security.py:117-171`). Probe with two app instances on one database: switching a parent off, a negative sharing list, and "Sign everyone out" on worker 2 each took effect on worker 1's next request. |
| L1 unshared parents keep access | **FIXED** | Fails closed with 503 when the list is unknown or too old, re-reads on demand without the keeper. |
| L2 PIN codes in the cookie | **FIXED** | Opaque nonce in the cookie, values held server-side with a time limit (`app/security.py:174-194`, `app/main.py:138`). |
| L3 Unicode digits | **FIXED** | `re.sub(r"[^0-9]", ...)` at `app/main.py:660`. |
| L4 old connect links | **FIXED** | Rotation retires old tokens and refuses during a claim (`app/db.py:262-271`). |
| L5 phished parent sign-in | **NOT FIXED, documented** (`README.md:388`) | See the judgment below. |
| L6 caching | **FIXED** | As round-one #6. |
| L7 heavy title batches | **NOT FIXED** | `app/main.py:1228-1247` still calls `set_title` up to 1,000 times, and each call scans the library (`app/service.py:403-412`). |
| L8 container limits, hashed installs | **NOT FIXED** | No `mem_limit`/`pids_limit` in `docker-compose.example.yml`. `Dockerfile:5` installs without `--require-hashes`. No dependency audit in CI. |
| I1 session key from owner token | Accepted, documented. |
| I2 owner code in webhook body | Accepted, README says to use HTTPS. |
| I3 CSP allows `*.plex.tv` images | Not changed (`app/main.py:169`). Harmless. |
| I4 impossible dates | **FIXED** (`app/rules.py:146-151`). |

## Judgment on the deferred Low items

- **L5: acceptable for a first public release.** The weakness comes from Plex's
  PIN flow and cannot be fully fixed in the app. It is stated in the README. Its
  impact is now bounded by server-side sessions, logout, a 3-minute PIN age and
  the owner's switch-off. The new-address alert should be the first follow-up.
- **L7: acceptable.** Only a signed-in, enabled parent can trigger it. It is
  capped at 1,000 keys and 20 batches a minute, and it slows the server without
  exposing data. Track it as a performance fix.
- **L8: acceptable, with a recommendation.** It is hardening, not a hole. Adding
  `mem_limit` and `pids_limit` to the example Compose file is a two-line change
  worth making before launch, because strangers copy that file as-is. Hash-locked
  installs and `pip-audit` in CI can follow.

None of the three blocks release on its own.

## Validation

- Full suite with the project virtualenv, `python -m pytest -q`: **324 passed**
  in 19.05 s, with the existing Starlette/httpx deprecation warning.
- **Stale sharing snapshots:** an old observation published late with a fresh
  wall time is rejected, and a negative list published from a second worker is
  followed by worker 1 (signs the parent out). A fresh sign-in then recovers.
  Passed.
- **Concurrent `link_done` on one token:** `[200, 410]`, one share, child bound to
  account 201. Passed.
- **Parent switch-off during a connect:** covered by the suite at three pause
  points, plus an extra probe for unsharing after the attach (accepted residual,
  above).
- **Session revocation across workers:** two `create_app` instances on one
  database file. Switch-off, negative list and "Sign everyone out" all took effect
  across instances. Passed.
- **Startup with `.env.example`:** every active value loaded, with only `PK_DB`
  pointed at a temporary file. With FakePlex: `/healthz` 200, keeper on, secure
  cookies on, no trusted proxies. Owner sign-in with no webhook returned **503**
  and created no session (fail closed, as documented). The real `app_factory()`
  refused to start with "Plex settings file is not readable" because the example
  path does not exist, which is the intended behavior.
- **Existing database upgrade:** built databases from the pre-security schema
  (`2ee6ce0^1`) and the schema just before this fix (`7080b0e^`). Added settings,
  parent, child and link rows, then opened them twice with the current `DB`. All
  rows were preserved, `connection_claim` was added, and `sharing_state` was
  seeded. Claims and publishing worked. A claim on an upgraded database fails
  closed until the first sharing read. The old code reopened the upgraded file
  and `integrity_check` was `ok`, so the documented rollback works.
- **Remove during a connect:** fails as described in N1 (paused at `invite`). It
  is safe when paused at `account`.

## Verdict

Fix N1 before release: refuse or serialize Remove (and Decline/Approve) while a
child's connection claim is held, and make the final connection update
conditional. Then add the test. N2 and N3 are small and should go in the same
change. L5, L7 and L8 are acceptable as documented known limitations for a first
public release.

Undo: revert the commit that added this report. Probes and temporary databases
lived outside the repository and have been deleted.

VERDICT: NOT SAFE TO SHIP. Blocker: N1, removing a child while its Plex connection is in progress brings the child back as active or requested with no Plex account on record, labeled titles and an accepted Plex share that the keeper does not enforce and that Remove and cleanup cannot take back.

## Coordinator follow-up

SF4 addresses N1, N2, and N3 without changing the original review above.

- **N1:** Remove, Approve, and Decline acquire the same atomic per-child SQLite
  claim as Connect and hold it across their Plex calls. Losing operations return
  HTTP 409 before changing child or request state. Independent workers use the
  same database claim. The final connection write requires the original claim,
  original status, and attached Plex account, so it cannot revive a tombstone.
  A losing final write retracts the share and never queues title labels or
  reports a successful connection.
- **N1 recovery:** Attachment writes a `tracked_shares` account record in the
  same transaction before any invite. Remove and cleanup consult that record
  even when `kids.plex_id` is empty. Failed retractions retain the account record
  and an unsafe-share error for periodic retry; cleanup also discovers removed
  children directly from the account records. Records are deleted only after a
  successful Plex removal or a fresh read confirming no share. Existing child
  rows still use their saved `plex_id`, so no data backfill is needed.
- **N2:** Parent authorization retries a changed sharing observation once, then
  returns HTTP 503 with the session preserved. A restored database sequence
  cannot create an endless request loop.
- **N3:** README troubleshooting now documents the 30-minute sharing-outage
  grace, refresh/retry intervals, retained sessions, and crash recovery for a
  stuck claim. Recovery requires stopping every worker, backing up SQLite,
  checking actual Plex shares against both account records, revoking unexpected
  access, and clearing only the affected child's claim before restarting.

Regression coverage in `tests/test_sec_remove_connect.py` includes the review's
paused-at-invite HTTP reproduction for owner and parent children, plus pauses
at account lookup, invite acceptance, and online-source opt-out. It checks 409
responses, final status and account, accepted shares, labels, request state,
and successful removal after the connection completes. Separate Service/DB
objects verify reconnect versus Approve/Decline, Remove winning before Connect,
and recovery after a lost final write through direct Remove, cleanup after
restart, and periodic retry. The restored-sequence test fails promptly instead
of hanging. The initial 13 regression cases all failed on the original code;
the final expanded set has 16 passing cases.

Validation: the project virtualenv's full `python -m pytest -q` suite passes
**340 tests**, with only the existing Starlette/httpx deprecation warning.
`git diff --check` passes. Tests use temporary databases and FakePlex; no live
service, database, or Plex account was changed. Shared backlog updates remain
with the coordinator because this dispatch's file allowlist excludes them.

Undo: revert this follow-up's commit and restart all workers. The additive
`tracked_shares` table can remain in place because older code ignores it. Before
rolling back, finish any pending share retractions with the fixed cleanup code
or remove the recorded shares directly in Plex; older cleanup cannot discover
ledger-only accounts. Back up SQLite before deployment or rollback.
