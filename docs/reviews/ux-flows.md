# Parent, co-parent, and owner flow review

Reviewed October 24, 2026 at commit `492324f`. Application code was read-only. The goal was to help a first-time parent get a child watching, understand the limits, and recover from mistakes.

The strongest parts already feel specific to families: the short setup checklist, examples that carry into requests, explicit age ratings, pause durations, and the explanation of what removing a child does. Keep those. The highest-value work is to make state changes and handoffs reliable, then reduce the visual weight of secondary information.

## Method and limits

Used `tests.dev_server` with its fake Plex data in isolated loopback instances on ports 8878 and 8880. Runtime-only fixture setup changed the public URL and supplied a fictional second child or Casey as the fake sign-in result. No application, test, configuration, or other repository file was changed. All browser artifacts are under `/tmp/kw-ui-h/`.

Captured and inspected headless Chromium screenshots at a 1280 by 900 desktop viewport and a 390 by 900 narrow viewport. Narrow captures use the desktop browser at phone width, not physical iOS/Android hardware; a scrollbar can reduce content width to 375 pixels. One early connection-success capture is 780 pixels wide. Full-page captures include sticky controls at their viewport positions, so a floating save button in the middle of a long image does not by itself prove an overlap bug.

Used DOM inspection, Chromium's accessibility tree, keyboard Tab/Escape, and source inspection alongside screenshots. The accessibility trees are `07-schedule-accessibility.json` and `10-titles-accessibility.json`. These checks are not a full WCAG audit or a screen-reader listening test. No page-wide horizontal overflow appeared in the captured narrow flows. Real Plex sign-up, OAuth screens, invitation email, device playback termination, external movie-request services, and the owner's phone verification code were not exercised. The fake sign-in callback was completed locally without visiting Plex. Owner UI access used the documented development login bypass. The fixture supplies sample watch history even for a new child; that is not an application finding.

Required validation: `~/Work/plex-kids/.venv/bin/python -m pytest -q` passed **120 tests**, with one existing Starlette/httpx deprecation warning. CSP remained `script-src 'self'; style-src 'self'`; this review proposes no inline code or styles.

Before: `/tmp/kw-ui-h/01-before-first-parent-desktop.png`. After: `/tmp/kw-ui-h/53-after-first-parent-desktop.png` and `/tmp/kw-ui-h/54-after-first-parent-phone.png`. The desktop before/after files are byte-identical because the application was not edited. Intermediate images show before/after interaction states, not implemented fixes.

## Walkthrough

| Step | Flow and result | Health | Screenshot evidence under `/tmp/kw-ui-h/` |
| --- | --- | --- | --- |
| 1 | New parent signs in, asks for site access; owner approves; parent signs in again and reaches setup. | Clear, but the waiting page has no direct sign-in-again action. | `45-new-parent-access.png`, `46-access-requested.png`, `47-owner-access-inbox.png`, `48-parent-access-approved.png` |
| 2 | Empty My kids page to Add a child; enter Riley and keep Kids age level. | Good main action and specific ratings. | `01-before-first-parent-desktop.png`, `02-add-child.png`, `03-connect-phone.png` |
| 3 | Copy/open connection link; complete fake child sign-in; return to overview. Also try an account already attached to a child and reuse the successful link. | Clear success and duplicate-account error; used-link recovery is misleading. | `04-link-account-phone.png` (Kid2 link recapture), `05-account-connected.png`, `06-waiting-approval.png`, `32-connect-existing-draft.png`, `33-account-error.png`, `34-expired-link.png` |
| 4 | Choose School year preset, adjust daily minutes, save, test overnight hours and equal start/end times. | Presets and preview help; equal times remove watch-hour restrictions silently. | `07-schedule-before-phone.png`, `08-overnight-saved.png`, `09-schedule-saved.png`, `27-equal-times-anytime.png` |
| 5 | Block Cloud Garden, reset that choice, open details, Escape out, search for a missing title, use the prefilled request. Confirm a batch block; repeat it and restore the mixed choices with Undo. | Strong search-to-request handoff; action naming, focus, and batch recovery need work. | `10-titles-phone.png`, `11-title-blocked.png`, `12-title-details.png`, `13-title-empty.png`, `14-request-prefilled.png`, `28-poster-keyboard-focus.png`, `29-bulk-confirm.png`, `30-bulk-undo.png`, `60-bulk-undone.png` |
| 6 | Send a title request and a co-parent request; owner reads the queue and approves Riley. Parent tab remains on waiting state. | Approval succeeds, but the visible parent state can stay stale. | `15-request-sent.png`, `16-coparent-request.png`, `17-owner-inbox-phone.png`, `18-parent-before-approval.png`, `19-owner-approved.png`, `21-parent-still-waiting.png` |
| 7 | Owner navigates to Kids, adds Alex to Riley; Alex opens settings and pauses/resumes Riley. Owner also adds/removes a co-parent in the second fixture. | Permissions and explanatory copy are good; requests and actual assignment are disconnected. Co-parent cannot see Remove child. | `20-owner-kids-phone.png`, `22-coparent-added.png`, `23-coparent-settings.png`, `25-timed-pause.png`, `49-coparent-removed.png` |
| 8 | Review active overview and activity; pause for 30 minutes, resume, pause everyone, resume everyone; add 15 minutes and undo it. | Clear states and individual undo. Playback-stop timing was not tested against Plex. | `24-active-overview-phone.png`, `25-timed-pause.png`, `26-pause-everyone.png`, `31-activity-phone.png`, `58-extra-time-added.png`, `59-extra-time-undone.png` |
| 9 | Owner declines a movie request, replies to and completes co-parent request; parent sees replies, sends and cancels another request. Also connect Rowan and decline that child request. | Status history works; cancellation is wrongly shown as an admin reply on home. | `38-request-declined.png`, `39-parent-replies.png`, `40-request-canceled.png`, `42-child-removed.png`, `55-child-declined-owner.png`, `56-child-declined-parent.png` |
| 10 | Change to Only titles I allow and save; verify title list. Open Copy rules confirmation and cancel, then repeat and confirm. Try wrong removal name, then remove with the correct name. | Save and removal work; copy has an explicit replacement warning. Copy execution also succeeds. | `41-remove-validation.png`, `42-child-removed.png`, `43-copy-confirm.png`, `51-settings-saved.png`, `52-picked-titles.png`, `57-rules-copied.png` |
| 11 | Owner disables and re-enables Sam, inspects security log, signs everyone out. | Actions work; feedback and correction paths are sparse. | `35-owner-parents.png`, `36-parent-disabled.png`, `37-owner-security.png`, `50-signed-out.png` |
| 12 | Read setup and shared-device guidance in Help. | Specific, task-based answers. | `61-help-phone.png` |
| 13 | Enter a request, renew the same fake parent session in another request, submit the stale form. | Safe rejection, poor recovery. | `44-expired-form-error.png` |

## Top 10 fixes, ranked by value for a busy parent

Severity: **High** risks an unintended rule or blocks a core handoff; **Medium** creates substantial ambiguity, recovery work, or an accessibility barrier; **Low** is localized polish. Each proposed change is scoped to one sitting. Locations refer to the reviewed commit.

### 1. Require an explicit choice before removing watch-hour limits

**High.** With Any time off, set both weekday fields to `08:00` and save. The saved page turns Any time on and the seven-day preview says Any time. A common editing mistake now permits watching at any hour, although the daily minute allowance still applies. Overnight `21:00` to `08:00` is intentionally accepted and is not the same issue.

**Location:** `app/templates/kid_schedule.html:44`, `app/main.py:935`, `app/rules.py:161`.

**Evidence:** [/tmp/kw-ui-h/27-equal-times-anytime.png](/tmp/kw-ui-h/27-equal-times-anytime.png); saved checkbox state was `true`.

**Small fix:** Reject equal times when the corresponding Any time checkbox was not submitted. Preserve the form and show an inline message: “Choose different times, or turn on Any time.” Keep the existing stored representation for compatibility. Verify unchecked equality rejects, checked equality succeeds, and overnight windows still work.

### 2. Update the child approval screen when approval arrives

**High.** Riley's owner approval succeeds and the admin count changes from one waiting child to zero, while the already-open parent page still says Waiting for approval. The polling hook lives on `_connect`, which is absent once the account and invitation are connected, so this state has no status watcher.

**Location:** `app/templates/kid_overview.html:17`, `app/templates/_connect.html:2`, `app/static/app.js:182`.

**Evidence:** [/tmp/kw-ui-h/19-owner-approved.png](/tmp/kw-ui-h/19-owner-approved.png) and [/tmp/kw-ui-h/21-parent-still-waiting.png](/tmp/kw-ui-h/21-parent-still-waiting.png); the waiting page had zero `[data-wait-url]` elements.

**Small fix:** Put the same status watcher on the requested-state container independent of `_connect`. Add a “Check approval” link as a manual fallback. Verify a separate parent tab changes to active without a manual reload when the owner approves; also handle declined state.

### 3. Connect a co-parent request to the actual assignment controls

**Medium.** The request says “Please let Alex help manage Riley,” but its main action is Mark done. Assignment requires switching to Kids, finding Riley, selecting Alex, pressing Add, returning to Requests, and marking the request done. Mark done alone only resolves the request; it does not grant access. The inbox offers no link to the child.

**Location:** `app/templates/admin.html:34`, `app/templates/admin.html:45`, `app/templates/admin.html:88`, `app/main.py:1365`.

**Evidence:** [/tmp/kw-ui-h/17-owner-inbox-phone.png](/tmp/kw-ui-h/17-owner-inbox-phone.png), [/tmp/kw-ui-h/20-owner-kids-phone.png](/tmp/kw-ui-h/20-owner-kids-phone.png), [/tmp/kw-ui-h/22-coparent-added.png](/tmp/kw-ui-h/22-coparent-added.png).

**Small fix:** For access requests with a child, add “Manage Riley's parents” linking to a stable child row anchor, with “Grant access first, then mark this request done.” Add a return link to the request. Do not guess accounts from free-text names.

### 4. Name title actions before the first interaction

**Medium.** The initial accessibility tree contains repeated buttons named only Block or Allow. `paint()` adds the title to the label only after a choice changes; initial render never calls it for every tile. Undo also lacks the title. A screen-reader button list does not tell a parent which movie they will change.

**Location:** `app/templates/kid_titles.html:65`, `app/templates/kid_titles.html:67`, `app/static/app.js:228`.

**Evidence:** [/tmp/kw-ui-h/10-titles-phone.png](/tmp/kw-ui-h/10-titles-phone.png), `/tmp/kw-ui-h/10-titles-accessibility.json`.

**Small fix:** Render “Block Cloud Garden for Riley” and “Reset Cloud Garden to Riley's age level” labels in Jinja, then keep those names current in `paint()`. Verify all actions have useful names immediately on load, including picked-only mode.

### 5. Make keyboard focus visible inside poster tiles

**Medium.** Tab modality plus focus on the first poster reports `:focus-visible`, but the screenshot shows only a faint line at its lower edge. The global outside shadow is clipped by the tile's overflow. Its translucent color also has weak contrast against pale surfaces.

**Location:** `app/static/style.css:34`, `app/static/style.css:98`, `app/static/style.css:344`, `app/static/style.css:346`.

**Evidence:** [/tmp/kw-ui-h/28-poster-keyboard-focus.png](/tmp/kw-ui-h/28-poster-keyboard-focus.png); computed shadow was `rgba(194, 65, 12, 0.3) 0px 0px 0px 3px`.

**Small fix:** Add an inset, opaque two-color focus ring for posters and a clearly contrasting outline for ordinary controls. Verify keyboard movement through three adjacent posters in both themes. Do not remove outline unless the replacement is visible.

### 6. Stop treating canceled requests as admin replies

**Medium.** After Sam canceled “Please help me set up the family TV,” My kids displayed “Admin replied” above that message even though the owner had done nothing. The home query includes every non-open status, and the template calls every non-declined result a reply. This creates false notifications and buries the next useful action.

**Location:** `app/main.py:641`, `app/templates/home.html:18`.

**Evidence:** [/tmp/kw-ui-h/40-request-canceled.png](/tmp/kw-ui-h/40-request-canceled.png) and [/tmp/kw-ui-h/42-child-removed.png](/tmp/kw-ui-h/42-child-removed.png).

**Small fix:** Exclude canceled requests from home replies, matching unread-count behavior. Use explicit wording for approved access when there is no written reply. Verify a parent's cancellation never creates an admin-reply notice.

### 7. Keep batch undo available long enough to review the result

**Medium.** Batch blocking 14 titles gives a useful confirmation and Undo, but Undo disappears after eight seconds. Restoring the prior mixture of age defaults, explicit allows, and explicit blocks is not equivalent to resetting everything to the age level. A parent interrupted by a child can lose the only simple restoration path.

**Location:** `app/static/app.js:120`, `app/static/app.js:363`, `app/static/app.js:373`.

**Evidence:** [/tmp/kw-ui-h/29-bulk-confirm.png](/tmp/kw-ui-h/29-bulk-confirm.png), [/tmp/kw-ui-h/30-bulk-undo.png](/tmp/kw-ui-h/30-bulk-undo.png). Immediate Undo restored the seeded mix of blocked Cloud Garden, allowed Mountain Puzzle, and age-based choices (`60-bulk-undone.png`); the eight-second lifetime is confirmed in source.

**Small fix:** Keep batch undo until dismissed or until the next title mutation, and do not time it out while focused. Explain that navigation ends this undo opportunity. Verify a mixed selection returns to its exact previous choices.

### 8. Give expired forms a recovery path back to the task

**Medium.** A stale request form safely rejects submission, but the error screen says Something went wrong and offers Go to the start page. The message says to go back and refresh, which does not match its button. The entered request is not displayed on the error screen; browser Back may recover it, but that is left to the parent to discover.

**Location:** `app/main.py:225`, `app/main.py:192`, `app/templates/message.html:11`.

**Evidence:** [/tmp/kw-ui-h/44-expired-form-error.png](/tmp/kw-ui-h/44-expired-form-error.png).

**Small fix:** Give session-expiry errors their own title and a safe same-origin return action. On the Requests page, retain an unsent draft in tab-scoped storage until successful submission, and offer to restore it after refresh. Keep CSRF checks intact; never silently replay the rejected POST.

### 9. Distinguish a used connection link from an expired one

**Medium.** Reopening the link that successfully connected Riley says This link has expired and instructs the parent to make a new link. That suggests setup failed even though it succeeded, and the connected child's overview no longer needs a connection link.

**Location:** `app/main.py:1239`, `app/main.py:1255`, `app/templates/_connect.html:18`.

**Evidence:** [/tmp/kw-ui-h/05-account-connected.png](/tmp/kw-ui-h/05-account-connected.png), [/tmp/kw-ui-h/34-expired-link.png](/tmp/kw-ui-h/34-expired-link.png). This was a consumed link, not a 30-minute timeout test.

**Small fix:** Use a neutral “This link is no longer available” state with separate instructions for someone who already finished and someone who still needs to connect. Only show child-specific connection status after normal parent authorization. Add “Refresh connection instructions” to the parent overview; never relax token checks.

### 10. Include weekdays/weekends in schedule control names

**Medium.** Both time rows expose From, Until, and Any time without the day category in their control names. Visual day labels are separate divs. In a form-control navigation mode, parents must infer which row they are editing.

**Location:** `app/templates/kid_schedule.html:42`, `app/templates/kid_schedule.html:44`, `app/templates/kid_schedule.html:46`.

**Evidence:** [/tmp/kw-ui-h/07-schedule-before-phone.png](/tmp/kw-ui-h/07-schedule-before-phone.png), `/tmp/kw-ui-h/07-schedule-accessibility.json`.

**Small fix:** Use fieldset/legend for each day category and include “Weekday start time,” “Weekend end time,” and “Any time on weekends” in accessible names. Associate the watch-hours hint with those controls. Verify names through the accessibility tree and a screen reader.

## Additional findings

| ID / severity | Friction and evidence | Location | One-sitting fix |
| --- | --- | --- | --- |
| H11 / Medium | Watch hours never name the time zone. The preview ran in UTC, while the browser/user may be elsewhere. Screenshot: `/tmp/kw-ui-h/09-schedule-saved.png`. | `app/templates/kid_schedule.html:38`; `app/config.py:37`; `app/main.py:812` | Render “All times use [configured time zone]” beside watch hours and the preview. Preserve the configured scheduling behavior. |
| H12 / Medium | Decline commits immediately, accepts a blank reply, and offers no reopen action. A mistaken click needs manual follow-up; a legitimate decline can give the parent no reason. Screenshots: `/tmp/kw-ui-h/38-request-declined.png`, `/tmp/kw-ui-h/39-parent-replies.png`, `/tmp/kw-ui-h/55-child-declined-owner.png`, `/tmp/kw-ui-h/56-child-declined-parent.png`. | `app/templates/admin.html:47`; `app/main.py:1350`; `app/templates/admin.html:61` | Add a concise confirmation naming the request for child/site-access declines; for ordinary requests add Reopen in Recently answered. Keep the optional reply but encourage a reason on decline. Do not equate reopening a request with automatically restoring Plex access. |
| H13 / Low | At 390 pixels, four large admin statistic cards push the first request down to about y=535 and its Approve button to about y=780. Screenshot: `/tmp/kw-ui-h/47-owner-access-inbox.png`. | `app/templates/admin.html:11`; `app/static/style.css:455` | Replace the phone statistic cards with one compact summary line, prioritizing waiting approvals and open requests. Preserve large touch targets on actual actions. |
| H14 / Low | After removing a co-parent, the toast says only Co-parent removed; after disabling a parent it says Saved. Neither identifies whose access changed. Screenshots: `/tmp/kw-ui-h/49-coparent-removed.png`, `/tmp/kw-ui-h/36-parent-disabled.png`. | `app/main.py:1381`; `app/main.py:1416`; `app/templates/admin.html:83` | Name the person and child in the feedback and include a targeted Undo for reversible access toggles. Exclude already-assigned co-parents from the Add selector. |
| H15 / Low | The access waiting page tells Casey to sign in after approval but provides only the sign-out icon, no sign-in-again button. Screenshot: `/tmp/kw-ui-h/46-access-requested.png`. | `app/templates/waiting.html:10` | Add “Check access” using a server-side check that upgrades only an already-approved user, or an explicit “Sign in again” link. Do not grant access based on client state. |

## Evidence highlights

Owner approval completed, but the parent still sees waiting:

![Parent still waiting after owner approval](/tmp/kw-ui-h/21-parent-still-waiting.png)

A completed connection link misleadingly asks the parent to start over:

![Used link reported as expired](/tmp/kw-ui-h/34-expired-link.png)

The error recovery action abandons the current request:

![Expired request form](/tmp/kw-ui-h/44-expired-form-error.png)

## Full screenshot inventory

All 61 files below are in `/tmp/kw-ui-h/` and were opened during the review. They are local evidence, not repository assets or durable hosted links.

- [01-before-first-parent-desktop.png](/tmp/kw-ui-h/01-before-first-parent-desktop.png)
- [02-add-child.png](/tmp/kw-ui-h/02-add-child.png)
- [03-connect-phone.png](/tmp/kw-ui-h/03-connect-phone.png)
- [04-link-account-phone.png](/tmp/kw-ui-h/04-link-account-phone.png)
- [05-account-connected.png](/tmp/kw-ui-h/05-account-connected.png)
- [06-waiting-approval.png](/tmp/kw-ui-h/06-waiting-approval.png)
- [07-schedule-before-phone.png](/tmp/kw-ui-h/07-schedule-before-phone.png)
- [08-overnight-saved.png](/tmp/kw-ui-h/08-overnight-saved.png)
- [09-schedule-saved.png](/tmp/kw-ui-h/09-schedule-saved.png)
- [10-titles-phone.png](/tmp/kw-ui-h/10-titles-phone.png)
- [11-title-blocked.png](/tmp/kw-ui-h/11-title-blocked.png)
- [12-title-details.png](/tmp/kw-ui-h/12-title-details.png)
- [13-title-empty.png](/tmp/kw-ui-h/13-title-empty.png)
- [14-request-prefilled.png](/tmp/kw-ui-h/14-request-prefilled.png)
- [15-request-sent.png](/tmp/kw-ui-h/15-request-sent.png)
- [16-coparent-request.png](/tmp/kw-ui-h/16-coparent-request.png)
- [17-owner-inbox-phone.png](/tmp/kw-ui-h/17-owner-inbox-phone.png)
- [18-parent-before-approval.png](/tmp/kw-ui-h/18-parent-before-approval.png)
- [19-owner-approved.png](/tmp/kw-ui-h/19-owner-approved.png)
- [20-owner-kids-phone.png](/tmp/kw-ui-h/20-owner-kids-phone.png)
- [21-parent-still-waiting.png](/tmp/kw-ui-h/21-parent-still-waiting.png)
- [22-coparent-added.png](/tmp/kw-ui-h/22-coparent-added.png)
- [23-coparent-settings.png](/tmp/kw-ui-h/23-coparent-settings.png)
- [24-active-overview-phone.png](/tmp/kw-ui-h/24-active-overview-phone.png)
- [25-timed-pause.png](/tmp/kw-ui-h/25-timed-pause.png)
- [26-pause-everyone.png](/tmp/kw-ui-h/26-pause-everyone.png)
- [27-equal-times-anytime.png](/tmp/kw-ui-h/27-equal-times-anytime.png)
- [28-poster-keyboard-focus.png](/tmp/kw-ui-h/28-poster-keyboard-focus.png)
- [29-bulk-confirm.png](/tmp/kw-ui-h/29-bulk-confirm.png)
- [30-bulk-undo.png](/tmp/kw-ui-h/30-bulk-undo.png)
- [31-activity-phone.png](/tmp/kw-ui-h/31-activity-phone.png)
- [32-connect-existing-draft.png](/tmp/kw-ui-h/32-connect-existing-draft.png)
- [33-account-error.png](/tmp/kw-ui-h/33-account-error.png)
- [34-expired-link.png](/tmp/kw-ui-h/34-expired-link.png)
- [35-owner-parents.png](/tmp/kw-ui-h/35-owner-parents.png)
- [36-parent-disabled.png](/tmp/kw-ui-h/36-parent-disabled.png)
- [37-owner-security.png](/tmp/kw-ui-h/37-owner-security.png)
- [38-request-declined.png](/tmp/kw-ui-h/38-request-declined.png)
- [39-parent-replies.png](/tmp/kw-ui-h/39-parent-replies.png)
- [40-request-canceled.png](/tmp/kw-ui-h/40-request-canceled.png)
- [41-remove-validation.png](/tmp/kw-ui-h/41-remove-validation.png)
- [42-child-removed.png](/tmp/kw-ui-h/42-child-removed.png)
- [43-copy-confirm.png](/tmp/kw-ui-h/43-copy-confirm.png)
- [44-expired-form-error.png](/tmp/kw-ui-h/44-expired-form-error.png)
- [45-new-parent-access.png](/tmp/kw-ui-h/45-new-parent-access.png)
- [46-access-requested.png](/tmp/kw-ui-h/46-access-requested.png)
- [47-owner-access-inbox.png](/tmp/kw-ui-h/47-owner-access-inbox.png)
- [48-parent-access-approved.png](/tmp/kw-ui-h/48-parent-access-approved.png)
- [49-coparent-removed.png](/tmp/kw-ui-h/49-coparent-removed.png)
- [50-signed-out.png](/tmp/kw-ui-h/50-signed-out.png)
- [51-settings-saved.png](/tmp/kw-ui-h/51-settings-saved.png)
- [52-picked-titles.png](/tmp/kw-ui-h/52-picked-titles.png)
- [53-after-first-parent-desktop.png](/tmp/kw-ui-h/53-after-first-parent-desktop.png)
- [54-after-first-parent-phone.png](/tmp/kw-ui-h/54-after-first-parent-phone.png)
- [55-child-declined-owner.png](/tmp/kw-ui-h/55-child-declined-owner.png)
- [56-child-declined-parent.png](/tmp/kw-ui-h/56-child-declined-parent.png)
- [57-rules-copied.png](/tmp/kw-ui-h/57-rules-copied.png)
- [58-extra-time-added.png](/tmp/kw-ui-h/58-extra-time-added.png)
- [59-extra-time-undone.png](/tmp/kw-ui-h/59-extra-time-undone.png)
- [60-bulk-undone.png](/tmp/kw-ui-h/60-bulk-undone.png)
- [61-help-phone.png](/tmp/kw-ui-h/61-help-phone.png)

## Handoff and rollback

No fixes were implemented. The next implementation pass can take the ranked items independently, preserving strict CSP and checking both desktop and 390-pixel layouts. No deployment or shared backlog edit was made. Revert the report-only commit to remove this review; screenshots and disposable fake data stay outside the repository.
