# Onboarding Wave 1 integration verification

Verified October 24, 2026 on branch `kw-i1-integration`.
Preintegration application revision: `b1086b4`.

## Integrated behavior

Merged `kw-o1-setup`, `kw-o2-invites`,
`kw-o3-approvals`, and `kw-email-tip` in that order.
All requested integration hooks are connected; none remain unwired.

- Setup migrations run before client ID creation. Fresh databases persist trust
  on; existing client IDs or application rows persist trust off. Restarts preserve
  the owner's choice.
- Child activation keeps its connection claim, verifies the restricted share,
  and reads the trust setting and policy version inside the same authorization
  transaction. Trusted activation queues titles and sends one owner notification
  without creating a child approval request. Strict mode retains that request.
- Parent requests use authenticated, versioned detail decisions. The legacy POST
  only redirects for access requests. Waiting sessions upgrade after approval.
- Invitation returns are stored with the server-side PIN and accepted only by
  `safe_return`. Parent notifications link to authenticated request details.
- Saved library policy overrides environment defaults in main pages, readiness,
  sharing, title selection, and title synchronization. Narrowing policy also
  removes old labels and updates shares when a child has no remaining libraries.
  Invalid saved policy cannot create a new share.
- The child-name email tip appears only in the draft regular-account step.
  Trusted draft pages no longer promise a separate owner approval.

## Validation

`python -m pytest -q`: **504 passed**, one existing Starlette/httpx deprecation
warning. The new `tests/test_onboarding_integration.py` covers the integrated
login, invitations, approvals, settings, transaction policy, and connection paths.
Existing approval tests retain fault injection immediately before activation.

Started `python -m tests.dev_server` with a fresh temporary fake database for
both **390px** and **1280px** Chromium runs. `tests/browser_onboarding.cjs`
completed **16 page checks per width**, including notification receipt, invite
creation/copy, sign-in/claim, approval inbox/detail, waiting-page upgrade, and
child connection. All 32 checks passed with no horizontal overflow and zero
axe findings under WCAG 2 A/AA, 2.1 A/AA, and 2.2 AA tags. Inspected screenshots
of setup, invitations, approval details, and the child email-tip step.

The preview server simulates Plex sign-in and private notification delivery on
loopback. It never uses a real account, receiver credential, or production DB.
The browser script uses separately installed `playwright-core` and `axe-core`
from `NODE_PATH`; start a fresh preview server for each `WIDTH=390` or `WIDTH=1280`
run. Screenshots and JSON results are written under `BROWSER_ARTIFACT_DIR`, or the
system temporary directory by default.

## Limits and undo

No live installation was deployed. Real Plex client playback stops and real
notification receipt still require the [live checklist](../live-checklist.md).
Axe is an automated check, not a complete accessibility certification.

Return the application to `b1086b4` or its previous image to undo this integration.
The migrations are additive; restore the matching stopped-app SQLite backup for
an exact data rollback. Reverting code or turning trust off does not retract
shares already granted: use Admin pause/removal and verify Plex access removal
when undoing an authorization decision.
