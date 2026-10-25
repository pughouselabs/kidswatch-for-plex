# Security fixes: second independent review

Reviewed 2026-10-24 at `673a786`, the merge containing fix commit `97d98a1`,
against every numbered finding in `docs/reviews/security-fixes-review.md`.
Application code and tests were read only. Only this report was changed; no push,
merge, deployment, real Plex call, or live database access was performed.

**Verdict: NOT SAFE TO SHIP.** Findings **3 and 5 remain PARTIAL** and are the
release blockers: an older Plex sharing cache can still delete a legitimate
parent's session, and link replacement can still allow two callbacks to connect
different accounts to one child, leaving an extra Plex share behind. The High
legacy-cookie bypass is fixed. The passing test suite misses both remaining
interleavings.

## Results against the six findings

| Finding | Result | Evidence and verification |
|---|---|---|
| **1 High: legacy-cookie bypass** | **FIXED** | `app/main.py:213–223` rejects a missing, empty, or non-string session ID before the request cache shortcut, then validates epoch and server session. `tests/test_sec_review_fixes.py:43–86` exercises an old owner cookie, an existing enabled parent's cookie, logout/global sign-out replay, cookie renewal, and a mismatched epoch. These tests pass. Existing pre-session cookies deliberately require sign-in again; that is the intended migration. |
| **2 Medium: unbounded limiter buckets** | **FIXED** | `app/security.py:97–114` stores only admitted timestamps in a deque, expires from its front, and evicts one oldest key without scanning the table. The per-key count is bounded by the configured limit. Independent million-attempt and concurrent HTTP probes passed; details below. `app/main.py:482–492` retains separate address/authenticated tables and the global PIN cap. |
| **3 Medium: freshly authorized parent wrongly signed out** | **PARTIAL** | `app/main.py:574–575,590–591` publishes the callback's sharing result; `app/service.py:51–56` protects the service snapshot against older publication times. This fixes the simple reproduction, but `app/plex.py:278–290` and `_Cache.put` at `app/plex.py:109–111` can retain an older response and later republish it as new. Independently reproduced session deletion; see blocker A. |
| **4 Low: indefinitely stale positive sharing snapshot** | **FIXED** | `app/main.py:73–75,234–248` refreshes after ten minutes, retries failures after 30 seconds, and refuses a service snapshot older than 1,800 seconds with 503. `require()` does not reach its session-deletion branch when this raises. `tests/test_sec_review_fixes.py:163–185` passes for a day-old positive snapshot, preserved session, recovery, and a short outage. The 30-minute service grace is explicit in code; the Plex client's separate 120-second cache means service publication time is not necessarily the original HTTP observation time. This remains bounded, unlike the original finding. Document that grace in operator-facing guidance too. |
| **5 Low: in-flight connect callback survives replacement** | **PARTIAL** | `app/main.py:1362–1367` atomically claims one unused, unexpired token after the initial Plex wait. Replacement during `check_pin` is now rejected, and two claims of the same token cannot succeed. However, rotation at `app/main.py:472–477` neither rejects nor waits for an already claimed connection, and `Service.connect` has later network waits and stale child state (`app/service.py:255–282`). Two different tokens can still connect concurrently; see blocker B. |
| **6 Low: missing no-store on public/error HTML** | **FIXED** | `app/main.py:178–182` applies `no-store` to HTML responses as well as the protected prefixes. `tests/test_sec_review_fixes.py:270–296` passes for `/`, `/help`, generic 404, login 429/503, and retained private poster caching (`app/main.py:1257`). |

## Blocker A: an older client cache defeats the sharing snapshot ordering

**Medium / P2, remaining finding 3.** There are two caches, but only the service
cache has ordering protection. `PlexClient.shared_users()` returns cached values
for 120 seconds without exposing their observation time (`app/plex.py:278–280`).
An older in-flight HTTP response can overwrite the client cache when it finishes
(`app/plex.py:290`, `app/plex.py:109–111`). A later sign-in stamps that cached
value with its own current time (`app/main.py:574,591`). The service accepts it
as newer, and `require()` permanently deletes a parent session absent from it
(`app/main.py:257–262`).

**Deterministic reproduction:** temporary database and the repository's FakePlex
for account/PIN operations, but bound the actual `PlexClient.shared_users`,
`PlexClient._xml`, and `_Cache` implementations to it. Only `_req` was mocked to
return synthetic XML; no real credentials or HTTP were used.

1. Start a keeper-style `svc.refresh_shared()` in a thread. Hold its HTTP response,
   which contains Sam but not Alex, behind an event barrier.
2. Sign Alex in. The second sharing read returns Alex and Sam. Alex's `/home`
   returns 200.
3. Release the old response. Service IDs correctly remain `[101, 102]`, but the
   Plex client cache now contains only `[102]`.
4. Sign Sam in immediately. That callback succeeds with 303 and publishes the
   old cached list using the new callback time. No third sharing HTTP read occurs.
5. Alex's next `/home` returns **303 `/`**, and Alex's server session count is
   **0**, although the later real sharing response authorized Alex.

The current test at `tests/test_sec_review_fixes.py:138` directly calls
`publish_shared` and therefore cannot expose this underlying-cache path.

**Required correction:** preserve observation ordering through both caches, or
make authorization reads bypass a cache that cannot provide that ordering.
Do not restamp cached data as a fresh authoritative observation. Add the above
out-of-order HTTP-response test, including the second parent's sign-in, and
require Alex's session and access to survive. A negative refresh failure must
still preserve the session and fail temporarily.

## Blocker B: token claims do not serialize a child's connection

**Remaining finding 5; Medium / P2 impact in the stronger reproduction.** The
token's `used=1` now means both consumed/revoked and currently connecting. The
rotation route always reports success (`app/main.py:1120–1128`); it cannot tell
the parent that the old callback already claimed the child. After claiming,
`Service.connect()` reads the child, waits for account/share information, then
writes and invites without a per-child claim or checking whether that state is
still current (`app/service.py:255–285,319`). The SQL claim at
`app/main.py:1363` protects a token, not the child across multiple tokens.

**Deterministic reproduction with both callbacks on one ASGI event loop:** use
one app, temporary DB, FakePlex, and `httpx.AsyncClient`/`ASGITransport` clients.
No multiple-worker configuration is needed.

1. Owner creates a draft child and starts the old link as fake child account 201.
2. Start `/link/{old}/done`. Pause its `plex.account()` call inside
   `Service.connect`, after token claim and the draft-child read but before any
   child update or Plex invitation.
3. Owner posts `/kids/{id}/connect`. It returns **303** and a new link.
4. Use the new link for fake account 999 (a non-parent account). Its callback
   returns **200**, and the child now has Plex ID **999**.
5. Release the old account lookup. The old callback also returns **200** and
   overwrites the child with Plex ID **201**. FakePlex retains **both shares
   `[201, 999]`**, with two invitations recorded.

The extra account has the same child-label filters but is no longer the account
represented by that child row. This is not merely an ambiguous success message;
the two callbacks leave inconsistent app/Plex state. The existing tests pause
only the earlier `check_pin` call (`tests/test_sec_review_fixes.py:217–245`), so
they miss this later interval.

Two additional event-barrier probes confirmed:

- Pausing immediately after claim, rotating the link, then releasing still gives
  rotation **303**, old callback **200**, child **active**, and one invitation.
- For an enabled parent's draft, disabling the parent through the real admin
  route after claim returns **303**, yet releasing the callback gives **200**,
  child **requested**, and an invitation. `Service.connect()` never rechecks
  the parent's enabled state. The parent lookup before the SQL claim is also
  outside that claim's transaction (`app/main.py:1320–1323,1362–1363`).

**Required correction:** coordinate rotation, final authorization, and connection
per child, with an explicit in-progress state and supported-process-safe claim
semantics. Either cancel the old operation before side effects or refuse/wait
for replacement and clearly report that connection is already in progress.
Prevent two callbacks from observing the same draft and attaching two accounts.
Define parent-disable behavior at that same authorization boundary. Add the
post-claim, two-account, and disable interleavings above, including assertions
on actual remaining shares and the final child identity. Another token-only
check before the next network wait is insufficient.

## Validation and regression checks

- `~/Work/plex-kids/.venv/bin/python -m pytest -q`: **309 passed in 16.94s**;
  one existing Starlette/httpx deprecation warning. No tests or app files edited.
- `sudo -n docker build -t kidswatch-security-review:673a786 .`: **passed**.
  Dependency installation used the build cache; this is not a fresh dependency
  audit. The Dockerfile still specifies one uvicorn process and
  `--no-proxy-headers`. No container was started or deployed.
- **Example settings/startup:** parsed all active `.env.example` values, changed
  only the DB path to a temporary file, and entered the app lifespan with
  FakePlex. `/healthz` returned 200 with the default keeper enabled. Real Plex
  credentials, deployment paths, and network reachability were not exercised.
- **Owner sign-in with webhook off:** default `owner_code=True` and empty webhook
  gave **503**, with **zero sessions**. An otherwise equivalent test app with
  explicit `owner_code=False` gave **303 `/admin`**, followed by `/admin` **200**.
  The default requirement to configure code delivery remains deliberate, as
  stated in `.env.example`; this is not a newly introduced lockout. No real
  notification receiver or disabled-but-configured HA automation was exercised.
- **Existing DB migration:** extracted the pre-security schema from
  `2ee6ce0^1:app/db.py`, populated synthetic settings, parent, child and link rows,
  then initialized the current `DB` twice. All populated rows stayed identical;
  the sessions table had `id,user_id,role,created_at,last_seen`. This round adds
  only `DB.changes` (`app/db.py:174–177`), not a schema migration.
- **Limiter load:** 1,000,000 same-key attempts took approximately **0.174s**,
  admitted exactly **10**, and retained exactly **10 timestamps**. After 20,000
  distinct keys, the table held **5,000 keys / 5,000 timestamps**. Existing tests
  confirm window recovery and smaller-limit trimming.
- **Concurrent HTTP limiter load:** 1,000 in-process requests across ten direct
  IPs on one ASGI event loop produced **60 redirects / 940 HTTP 429s**, exactly
  **60 created PINs**, ten address buckets each at most ten entries, and 60
  global PIN timestamps; approximately **0.473s**. This checks application
  concurrency, not real network saturation or memory under a sustained attack.
  Limits remain process-local; multiple uvicorn workers were not tested.
- **Parent access/recovery:** ordinary new-share login, a genuinely newer
  negative list, stale-positive 503 with session preservation, and recovery all
  pass the suite. Blocker A is the additional false-sign-out case.
- **Link failure/retry:** the normal failed-connect rearm and same-token
  one-time-use tests pass. Their success does not cover multiple tokens for one
  child, as blocker B demonstrates.

## Other outstanding items carried by the first review

These original-audit items were recorded separately from the six numbered
findings and remain unchanged; this review does not claim the whole original
audit is closed.

| Item | Status | Current evidence |
|---|---|---|
| L5: new-address parent alert and per-session termination UI | **NOT FIXED** | Parent sign-in still only records the audit at `app/main.py:608`; the requested alert and individual-session control were not added. |
| L7: costly title batches | **NOT FIXED** | Up to 1,000 calls at `app/main.py:1223–1233`; each still scans libraries and uses separate writes at `app/service.py:355–369`. |
| L8: resource caps and hash-locked dependencies | **NOT FIXED** | `docker-compose.example.yml:38–44` retains filesystem/capability controls but no memory/process caps; `Dockerfile:5` installs requirements without hash enforcement. |

Only this report is committed. Undo it by reverting its documentation commit.
The review image can be removed with
`sudo -n docker image rm kidswatch-security-review:673a786`. Temporary databases
were automatically removed. Shared backlog writes were left to the coordinator
because this dispatch permits changes only to this report.

**NOT SAFE TO SHIP — blockers: #3, stale client-cache results republished as new
delete valid parent sessions; #5, post-claim link rotation permits overlapping
child connections, identity overwrite, and an extra Plex share.**

## Coordinator follow-up

Implemented 2026-10-24 on the SF3 worktree, after reproducing all six initial
regression cases against the reviewed code. This section supplements the
original review; its earlier findings and verdict remain intact above.

### Findings 3 and 5

- **Finding 3:** Authorization reads bypass the Plex client's unversioned users
  cache. Before every OAuth, keeper, or background authorization read, SQLite
  allocates a persistent, increasing observation sequence. Publishing uses an
  atomic compare-and-set on that sequence and returns the winning snapshot.
  The original observation start time controls freshness; accessing a cache
  never makes it fresh. The sequence and snapshot are shared by all workers
  and survive restarts without relying on monotonic-clock values across boots.
  Session deletion also checks the observation inside its write transaction.
  If another worker published a newer snapshot meanwhile, the request checks
  that snapshot before deciding whether to sign the parent out.
- **Finding 5:** A nullable `kids.connection_claim` records an in-progress
  operation for the whole child. `BEGIN IMMEDIATE` serializes token consumption,
  the current-link check, child eligibility, and parent authorization in one
  transaction. Parent authorization checks both the enabled flag and the latest
  trusted sharing snapshot. Attaching the account checks authorization again
  after the account/share network waits, and conditionally reserves the account
  before invitation. Competing callbacks cannot claim the child or overwrite
  an attached account. Direct service callers use the same child claim.
- **Rotation and parent disabling:** Both use that same SQLite locking boundary.
  If a connection already claimed the child, these actions return HTTP 409 with
  an explicit explanation to wait for sign-in to finish and retry. The parent
  remains enabled when disabling is refused. If disabling or rotation wins
  first, the callback cannot claim the child. Claims remain held for the actual
  Plex worker thread, including when its HTTP request is canceled.
- **Failures:** Normal failures release the claim and rearm only the latest link
  for retry. An account already reserved before an uncertain Plex failure stays
  attached, so retrying cannot invite a different account and orphan a share.
  The existing unsafe-share retraction and failed-retraction tracking remain
  covered by the full suite.

### Verification and operator guidance

`tests/test_sec_observation_claims.py` adds 15 cases: the exact out-of-order HTTP
response followed by Sam's sign-in, the same race through a second service/DB
instance, publication immediately before session deletion, two accounts and
rotation on one ASGI event loop, post-claim rotation, disabling during three
later waits, invalid/expired/replaced/unauthorized link claims, three independent
processes contending for one child while upgrading an old schema, and request
cancellation while the Plex thread continues. The initial six reproduction
cases failed before the fix; the final cases assert final account identity,
actual remaining fake Plex shares, session preservation, and claim release.
Full validation: `~/Work/plex-kids/.venv/bin/python -m pytest -q` passed with
**324 tests**, with the existing Starlette/httpx deprecation warning.
`git diff --check` also passed. All Plex calls and database mutations in these
checks used synthetic local fixtures; no live Plex or deployment was touched.

Operator guidance for sharing outages: refresh after ten minutes, retry failed
reads after 30 seconds, and trust the last successful observation for at most
30 minutes from the start of that read. Beyond that grace, parent requests get
HTTP 503 and keep their server sessions so access can recover without sign-in.
New parent sign-ins still require a successful Plex read.

Process crashes fail closed: a persisted in-progress claim is deliberately not
expired by a timer, because a slow surviving worker could otherwise create a
second share. To recover a stuck claim, stop every app worker, back up SQLite,
check that child's recorded account against the actual Plex shares and retract
any unexpected access, clear only that child's `connection_claim`, and restart.
Then generate a new link if the account still needs connecting. Do not clear a
claim while any worker may still be running that connection.

### Remaining Low items accepted for this follow-up

- **L5 deferred:** New-address alerts and individual-session controls require
  product/UI work outside this dispatch's allowed files. Existing server-side
  expiry, logout, owner global sign-out, and parent disabling remain available.
  The coordinator should track the missing alert and session UI separately.
- **L7 deferred:** The existing 1,000-title batch cap and authenticated limiter
  remain, but repeated library scans/writes can still be costly. A proper fix
  should prefetch the library once and apply validated changes in a transaction;
  mixing it into the authorization race repair would expand the behavioral
  scope. This is accepted as a known performance limitation for this follow-up.
- **L8 deferred:** Container resource caps and hash-locked dependencies require
  Docker/requirements changes outside the allowed files. Existing container
  filesystem/capability controls remain. Deployment owners must size resource
  limits for their library; a separate packaging change should produce and
  validate a dependency lock. No claim is made that L8 is fixed.

Rollback: revert this follow-up's code commit and restart all app workers.
The schema changes only add the claim column and sharing-state table; the old
code ignores them, so rollback does not require dropping data. Back up the live
database before any later deployment. The original worktree commit preserves
the pre-change code, and shared backlog updates remain with the coordinator
because this dispatch permits edits only to app code, security tests, and this
append-only report.
