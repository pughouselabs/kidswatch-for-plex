# Onboarding Wave 1 independent review

Reviewed October 24, 2026 at `1f5364e` (merge of `kw-i1-integration`),
compared with `b1086b4`, against `docs/design/onboarding.md`.
`python -m pytest -q`: **504 passed**, one existing Starlette/httpx warning.
I did not rerun the browser checks. The 390px and 1280px results come from the
integration notes.

## Blocker

**B1. Narrowing the library policy can send Plex an empty library list.**
`Service.apply` (`app/service.py:153`) and the periodic drift check
(`app/service.py:287`) used to skip the share update when a child had no
permitted sections (`if sections and ...`). That guard was removed. Now a child
whose libraries all fall outside the saved policy gets
`update_share(share_id, [], filters)`, which sends `librarySectionIds: []`.
`effective_kid_sections` deliberately returns `()` for a corrupt
`setup.kid_sections`, so a bad saved value does the same to **every** shared
child on the next periodic run.

Nobody has checked what Plex does with an empty list. The comment above
`update_share` (`app/plex.py:306`) records a live check only for nonempty lists.
Plex's sharing UI treats "All libraries" as its own mode, and the fake in
`tests/fakes.py` simply assumes empty means none. If Plex reads `[]` as
"all libraries", the label filters still restrict movies and TV. Music, photo
and other libraries have no label filter, though, so a child could see the
household's photos. The integration test
`test_narrowed_policy_cleans_existing_labels_and_empty_child_library_share`
only proves the fake's assumption.

Fix: restore the nonempty guard in both places, so old sections stay shared
with their labels removed and nothing shows. Or pause/retract the share and mark
the child as needing attention when `kid_sections(kid)` is empty. Also add a
test that a corrupt saved policy never calls `update_share` with `[]`. Only
switch to sending an empty list after a live check on a disposable share shows
Plex treats it as no libraries.

## Security checklist

| Area | Result | Evidence |
| --- | --- | --- |
| Invite tokens | Pass | `secrets.token_urlsafe(32)`, only a SHA-256 is stored, returned once (`app/invitations.py:96-108`). |
| Single use, expiry, revoke | Pass | The claim rechecks `claimed_at IS NULL`, `revoked_at IS NULL` and `expires_at` under `BEGIN IMMEDIATE`, then sets `claimed_at`. Revoke posts with CSRF and the owner check. |
| Identity bound | Pass | `intended_plex_id` is compared both before and inside the transaction. Owner and child accounts are refused. |
| Scanner-safe GET | Pass | `GET /join/{token}` only previews, with `no-store` and `no-referrer`. Claiming needs POST, CSRF and sign-in. A previous owner decision is never overridden (`reviewed`). |
| `safe_return` | Pass | Full-match `/join/` plus a 43-character token, checked both when stored and when used. No scheme, host or `//` can get through. The return path lives in the server-side PIN store, not the cookie. |
| Legacy approval POST | Pass | `POST /admin/requests/{id}` redirects `kind='access'` before any decision. The `enabled=1` branch was removed. Admin cards link to the detail page. |
| CSRF and owner code | Pass | Setup, invites and decisions all check CSRF and need an owner session. An owner session exists only after `/verify` when `PK_OWNER_CODE` is set. The approval and setup routes recheck the session at write time. |
| Trusted activation | Pass | `activate_child_on_connect` reads trust, parent enabled/accepted and `POLICY_VERSION` inside one `BEGIN IMMEDIATE`, under the held connection claim. It audits `child_connection_policy` with `policy_version`. `finish_setup` refuses a trust change while any claim is held. |
| Upgrade default | Pass | `setup.migrate` writes trust `0` when settings or application rows exist and `1` on a fresh DB. Missing or invalid settings fall back to strict. Children already requested get a strict receipt and stay pending. |
| Library override | Pass, apart from B1 | No direct `cfg.kid_sections` reads remain outside `Service.library_sections`. |
| Secrets in logs/URLs | Pass | The Docker CMD runs `--no-access-log`. Notification failures audit no receiver text. The test notification stores only a fingerprint. Invite tokens appear only in the deliberate capability URL. |
| Waiting-page upgrade | Pass | The role becomes `parent` only after a fresh `read_shared`, then a check of `parents.enabled` and accepted share inside a write transaction that rechecks the session row and epoch. A client cannot supply any of that. |

## Should fix (not blocking)

1. **A failed trusted activation leaves an orphan.** If `activate_child_on_connect`
   raises (parent disabled mid-connection, readback failure), the child stays
   `requested` with a restricted share but has no `add_child` request
   (`app/main.py:1371-1380`). The owner sees it only as a count in the inbox and
   cannot approve or decline it from a request card. It fails closed, but it is a
   dead end. Either create the strict request or retract the share in that case.
2. **The trust setting can only change through full setup.** You have to go
   through the libraries, a new test notification and the receipt checkbox. The
   spec also asks for a control in Admin settings.
3. **Library changes don't apply right away.** Narrowing the libraries in
   `finish_setup` waits for the next periodic drift check before shares and labels
   change. Call `apply`/`queue_sync` for affected children, or tell the owner
   "changes apply within a few minutes".
4. **The approval pages use their own template environment.**
   `approval_routes._render` builds a separate Jinja environment, so those pages
   skip the shared `render` context (flashes are always empty, `unread=0`) and
   don't get the email-tip registration. It works today, but it will drift.

## Correctness and UX

The flows match spec section 3, the "Owner setup and invitations" wireframes and
the Wave 1 scope. Copy is plain and uses the spec's trust sentence word for word.
Trusted draft pages drop the "owner approves" step, and the inbox labels earlier
pending children separately. The email tip appears only on the draft step, uses
an example when the parent's email is unknown, and the Copy button stays hidden
until JavaScript runs. The integration notes report a clean 390px run, with no
horizontal overflow and no axe findings. I spot-checked the templates and found
no fixed widths in the new markup.

VERDICT: FIX FIRST — B1: an empty-section `update_share` (from narrowed or corrupt library policy) sends Plex an unverified empty library list; restore the guard or retract the share.

## Coordinator follow-up

- Fixed B1: `Service.apply` and drift repair retract shares when no permitted
  sections remain, keep an actionable child error, and log the policy problem
  once. Failed retractions use the existing durable unsafe-share retry path.
  Corrupt saved policy still returns no libraries, never installation defaults.
- Admin and setup now warn about unusable library choices and explain repair and
  reconnection. `/readyz` reports invalid libraries without exposing family data.
- With coordinator approval, the Plex adapter also refuses invite/update requests
  when section-key mapping yields no library IDs. Transport tests cover this
  additional path; valid nonempty IDs retain their existing payloads.
- Fixed non-blocking item 3 with explicit copy: library access and titles update
  within a few minutes. Deferred items 1, 2 and 4: trusted-activation recovery,
  an Admin trust control and shared approval rendering need separate changes
  outside this guard/readiness task and the concurrent UX work.
- Validation: 32 new regression cases in `tests/test_w1_sections_guard.py`;
  coordinator-approved correction of the old empty-share assertion;
  `python -m pytest -q`: **536 passed**, one existing Starlette/httpx warning.
  No live Plex changes were made. Revert this follow-up commit to undo the code;
  any retracted accounts must be reconnected after permitted libraries are fixed.
