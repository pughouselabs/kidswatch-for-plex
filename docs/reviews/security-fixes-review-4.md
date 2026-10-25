# Security fixes review, round four

Scope: commit `769df71` ("Prevent child removal from racing Plex connections"),
which answers N1, N2 and N3 from `security-fixes-review-3.md` (see its
"Coordinator follow-up"). Application code and tests were read only. Probes ran
from a scratch directory outside the repo, against temporary databases and the
`FakePlex` test double. No live Plex, database or deployment was touched. Only
this report changed.

**Verdict: SAFE TO SHIP.** N1, N2 and N3 are fixed. Remove, Approve and Decline
now share the child's connection lock, so none of them can run during a connect.
The final connect write only lands if nothing changed underneath it. One new Low
issue came in with the account tracking (R4-1 below). It can take a Plex share
away from the wrong child, but it never gives anyone access, so it does not block
release. Fix it soon.

## Round-three findings

| # | Result | Evidence |
|---|---|---|
| **N1 remove vs connect race** | **FIXED** | `_changing_child` (`app/service.py:403`) takes `kids.connection_claim` with one conditional `UPDATE ... WHERE connection_claim IS NULL` (`app/db.py:261`). Remove, Approve and Decline run inside it and hold it through their Plex calls. A loser gets `ConnectionInProgress`, which the routes turn into HTTP 409 before changing the child or the request (`app/main.py:1154,1457`). `finish_connection` (`app/db.py:266`) needs the same claim, the starting status and the attached account. If it matches nothing, the share is taken back, or flagged `Unsafe share:` for a retry if Plex fails. Labels are never queued in that case. `attach_connection` writes `tracked_shares` in the same transaction as the attach, so Remove, cleanup and `periodic()` can find the account even when `plex_id` is empty. |
| **N2 unbounded `require()` retry** | **FIXED** | `for _ in range(2)` with `else: raise HTTPException(503)` (`app/main.py:266-278`). The session is kept. The restored-sequence test returns promptly. |
| **N3 operator guidance** | **FIXED** | The README now has "Connection stuck / in progress" and "Plex outage" sections. The numbers match the code: refresh after 600 s (`SHARED_MAX_AGE`), retry after 30 s (`SHARED_RETRY`), 1,800 s of grace measured from the start of the read (`publish_shared(ids, started, ...)`), and 503 with the session kept. The steps for clearing a stuck claim are correct. Stop every worker first. |

## N1 interleavings tried

All of these passed except the last, which is R4-1.

- **Remove during attach, same process and HTTP** (the review-3 repro, paused at
  `invite`, plus `account`, accept and opt-out, for both the owner's child and a
  parent's child): the new suite covers these. Remove returns 409, the connect
  finishes cleanly, and a second Remove takes away the share and the labels.
- **Remove and Decline from a second worker** (a separate `Service`/`DB`, connect
  paused inside `invite`): both raise `ConnectionInProgress`. The connect
  finishes, and Remove from worker 2 then clears the share.
- **Remove after the share is accepted** (paused in `block_online_sources`):
  409, as above (suite).
- **Remove wins first**: the connect claim is refused and nothing is invited
  (suite).
- **Crash during connect** (the process dies after `invite`, and again after
  accept, with no `finally`): the claim stays stuck, `plex_id` and
  `tracked_shares` are recorded, and the share exists. Remove raises 409.
  `periodic()` leaves it alone. `cleanup --apply` prints FAILED and exits 1 (it
  doesn't fail silently). After the README's manual claim clear, Remove takes away
  the share and empties `tracked_shares`.
- **Crash during Remove** (after the share drop, before the status write):
  after the manual clear, Remove runs again and ends `removed` with no labels.
- **Lost final write** (the claim or status changed out of band): the share is
  taken back right away, or by Remove, `cleanup` or `periodic()` from a fresh
  `Service` (suite, four recovery modes).
- **Retraction leaves a stale account record (R4-1)**: fails, see below.

## New finding

### R4-1. Low / P3 (not a blocker): removing one child can take away another child's share

**Location:** `app/service.py:349-353` (the `not safe` branch of `_connect`) and
`app/service.py:454` (`_drop_share`).

When Plex drops the filters on a new share and the app takes the share back
successfully, the code clears `kids.plex_id` but leaves the `tracked_shares`
row. `_drop_share` later removes a share for every account in that table, even
one that another child is now using.

**Reproduced:** draft child A connects account 201. Plex drops the filters, the
fix fails, and the share is taken back ("Nothing was shared"). A now has
`plex_id=NULL` and still has the `tracked_shares` row (A, 201). The parent creates
child B and connects account 201, which works. B is `active` with an accepted
share. The parent then removes the old draft A, and **B's share is deleted**. B
still shows `active` with `plex_id=201` and no share. `periodic()` doesn't repair
it, because the drift check skips missing shares. A fresh Remove or connect
attempt that adds a second account to the same child has the same effect.

Impact: nobody gains access. The child loses access, and the parent sees the
child as connected. The trigger is ordinary: a failed first try, then a new child
record with the same account, then deleting the old record. This is a regression
in this commit. Before it, removing A with no `plex_id` did nothing in Plex.

**Fix:** delete the `tracked_shares` row once the retraction in the `not safe`
branch succeeds. Also, in `_drop_share`, skip any tracked account (other than
the child's own `plex_id`) that a different child that isn't `removed` currently
has as its `plex_id`, and delete that row. This is safe: `_connect` refuses an
account that still has a share, so B can only own 201 after A's share is already
gone. Add the repro above as a test.

## Smaller notes (no action needed for release)

- `periodic()`'s unsafe-share retry does not take the child claim. If it runs at
  the same moment as a Remove whose label clearing fails, its later write can
  reset `sync_state` to `ok` and hide the "Titles: Removal pending" retry.
  Children with an unsafe-share error have no labels, so this can't expose any
  titles.
- `periodic()` can set `plex_accepted=1` during a reconnect, and
  `finish_connection` then writes back the value it read earlier. The next run of
  `periodic()` corrects it.

## Earlier fixes: regression check

There are no regressions. The full suite includes every earlier round's tests,
and they all pass. The touched code paths are unchanged for claims, rotation,
switching a parent off, ordered sharing snapshots, sessions and the limiter.
`claim_kid_change` and `claim_connection` use the same column, so the 409
behavior for rotation and parent switch-off still holds. The `cleanup` query now
also finds children that only appear in `tracked_shares`. A child stuck on a claim
is reported as FAILED.

## Validation

- `~/Work/plex-kids/.venv/bin/python -m pytest -q`: **340 passed** in about 21 s,
  with only the existing Starlette/httpx deprecation warning.
- Scratch probes (outside the repo, since deleted): 5 of 6 interleavings
  passed, and R4-1 reproduced.

## Verdict

N1, N2 and N3 are fixed, and the round-two and round-one fixes still hold. R4-1
is a Low fail-closed availability bug that this commit introduced. Fix it in the
next change, but it does not stop a first public release. L5, L7 and L8 stay as
accepted known limitations, as in round three.

Undo: revert the commit that added this report.

VERDICT: SAFE TO SHIP (no blockers; R4-1 Low, a stale `tracked_shares` row lets removing one child delete another child's share, is a recommended follow-up)
