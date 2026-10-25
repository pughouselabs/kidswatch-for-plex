# Onboarding: one guided setup, with honest Plex limits

Design and feasibility only. Checked **October 24, 2026**. No application changes, live account creation, invitations, or share changes were made for this report.

## Recommendation

Build one **Add a child** wizard that keeps progress on the parent's page, sends authentication to Plex, shows the returned account for confirmation, and updates automatically through connection and readiness. Approving a parent is the trust decision: approved parents add and connect children without a second owner approval. Use a user-opened popup on desktop and a normal page on phones. Offer **Use another device** with a QR code as the primary fallback. Private browsing becomes an optional troubleshooting escape hatch, rather than the first instruction.

There are two release gates behind this simple interface:

1. **Account eligibility:** Plex's current terms require an adult account holder and put minors under a parent or guardian's account. Its current privacy policy directs under-13 use toward managed accounts. Do not promise a separate regular account for every child, even with a parent's email alias. For minors in the server owner's actual Plex Home, recommend managed accounts, once KidsWatch supports them. For minors in another household, show an honest unsupported state until Plex confirms a permitted account arrangement. The regular-account wizard below is a technical design, conditional on that eligibility gate; it is not a recommendation that minors independently register.
2. **Account switching:** a popup shares the browser's Plex cookies. It does not isolate the child's login. Verify the Plex-owned account-switch and sign-up experience before advertising a seamless popup. A correct-account review is mandatory even after that check.

For owners, keep the existing guided install prompt and manual Compose route, then add **Finish setup**, **Invite parents**, and a parent approval inbox reached from private phone notifications. Add the owner setting **Approved parents can add their own children**, on by default for new installs. Child connection sends an informational notification; owners retain pause and removal controls. Start with authenticated in-app approval; add notification action buttons later. Installing a self-hosted app still requires a host, HTTPS, Plex configuration access, and notification bootstrap, so a single button cannot honestly replace that work.

## 1. What Plex permits and what is verified

### Owner decision, incorporated 2026-10-24

The default journey has **no per-child owner approval and no child waiting-for-owner screen**. Once an enabled parent confirms an eligible account, create and verify its restricted Plex share, activate the child, queue allowed-title sync, and notify the owner. The Admin approval queue contains parent requests only; all connected children remain manageable in Admin. An optional stricter mode can retain the existing child approval behavior. Parent site-access waiting, Plex invitation acceptance, and library sync remain distinct states because those cannot be skipped by this trust setting.

### Source register

All sources below are official Plex pages, except S10, Google's own explanation of Gmail address tags. All were consulted on **2026-10-24**. Publication dates are included only when exposed by the source. Some Plex pages rejected direct fetching; where noted, their official search-indexed text was readable. That is weaker evidence for today's interactive behavior than a completed browser test.

| ID | Official source and source date | Evidence used |
| --- | --- | --- |
| S1 | [Plex Terms of Service](https://www.plex.tv/about/privacy-legal/plex-terms-of-service/), revised October 30, 2025 | Sections 1, Authorized Users; 2, Generally and User Account. Official indexed text available; direct fetch blocked. |
| S2 | [Plex Privacy Policy](https://www.plex.tv/about/privacy-legal/), revised May 6, 2026 | Does Plex collect information from children? Direct page read, including managed-account protections. |
| S3 | [Plex Accounts](https://support.plex.tv/articles/201862428-plex-accounts/), modified July 24, 2025 | Email, Google and Apple registration, account identity, verification email. Official indexed article read. |
| S4 | [Managed Accounts](https://support.plex.tv/articles/203948776-managed-users/) | Home-admin creation, no email/password, no direct sign-in, access only from Home admin. Official indexed article read. |
| S5 | [Managing Library Access](https://support.plex.tv/articles/201105738-creating-and-managing-server-shares/), modified February 28, 2025 | Invitations by username/email, account creation during acceptance, email and web acceptance. Direct article read. |
| S6 | [Connect a Player App to Your Plex Account](https://support.plex.tv/articles/203395277-connect-app-to-your-plex-account/), modified July 7, 2025 | Four-character TV codes, optional QR, correct account, player refresh. Direct article read. |
| S7 | [Plex developer documentation](https://developer.plex.tv/pms/), reference version 1.2.3 | Traditional Token Authentication and Authenticating with Plex. Official indexed authentication sections read; public HTML also retrieved. |
| S8 | [Example Plex Home Setup](https://support.plex.tv/articles/204234313-example-plex-home-setup/) and [What is Plex Home?](https://support.plex.tv/articles/203815766-what-is-plex-home/) | Home membership and Plex Pass requirements. Official indexed text read. |
| S9 | [Restrictions on Library Access](https://support.plex.tv/articles/204232573-restricting-the-shares/) | Owner Plex Pass required for custom label/rating restrictions. Official indexed article read. |
| S10 | [Google: 2 hidden ways to get more from your Gmail address](https://gmail.googleblog.com/2008/03/2-hidden-ways-to-get-more-from-your.html), March 2008 | Plus-address delivery to the existing inbox. |
| S11 | [plex.tv/link](https://plex.tv/link/) | Public HTML retrieved on the check date: four-character entry and a different-user sign-in control. No code submitted. |

### Account creation and age are separate questions

**Can KidsWatch create a Plex account for someone? No supported account-provisioning integration was found in the official documentation reviewed.** The app must not automate registration, submit passwords, scrape signup forms, or treat a private registration endpoint as a supported API. What it can do is open Plex's registration page, authenticate an existing account through Plex, or send a server invitation that lets the recipient register on Plex during acceptance. Creating a managed account as a Home admin is a different capability, not creation of an independent Plex account. [Plex account registration](https://support.plex.tv/articles/201862428-plex-accounts/), [invitation signup](https://support.plex.tv/articles/201105738-creating-and-managing-server-shares/), [managed accounts](https://support.plex.tv/articles/203948776-managed-users/) (checked 2026-10-24).

**Do not simplify the age rule to "13 and up may make accounts with consent."** The current terms say the person accepting represents adult age, at least 18 or the higher applicable age of majority. Minors use Plex with parental involvement and acceptance, solely as an Authorized User under the parent's or guardian's account. The current Authorized Users paragraph covers immediate family; older Apple-specific terms found in search have different wording and are not the basis for this design. Parent consent is required for minor use, but is not documented permission for a minor's independent account. [Current terms, sections 1 and 2](https://www.plex.tv/about/privacy-legal/plex-terms-of-service/) (revision October 30, 2025; checked 2026-10-24).

**Under 13 needs particular care.** Plex says it will delete under-13 personal data collected outside managed accounts, and explicitly provides managed accounts under a parent/guardian. A checkbox in KidsWatch cannot override that policy. Use an age band, not a birth date, to route setup; the content rating selection is a separate question. Do not suggest false birthdays or treating an alias as age compliance. [Current privacy policy, children's section](https://www.plex.tv/about/privacy-legal/) (revision May 6, 2026; checked 2026-10-24).

Product consequence, based on those sources: support the documented managed route for minors first where the server owner is also their Home admin. Ask Plex for written clarification before shipping any separate adult-controlled regular-account arrangement used by a minor in another household. This report does not claim such an arrangement is either explicitly approved or categorically forbidden; the reviewed pages do not establish it as a supported alternative. No inquiry was sent as part of this task.

### PIN authentication, signup, and account switching

Plex documents a PIN authorization flow: generate a PIN, open the hosted Auth App, and obtain `authToken` by checking the PIN after authorization. Long `strong=true` codes suit browser redirects; short codes suit the link page. The documented browser URL is `https://app.plex.tv/auth#?` with `clientID`, `code`, `forwardUrl`, and product context. Polling is an alternative to returning through `forwardUrl`. The newer documentation also describes JWK/JWT authentication; KidsWatch currently uses the traditional token variant. This is commonly called Plex OAuth, but the current code is not a standard OAuth authorization-code/PKCE exchange. [Official authentication reference](https://developer.plex.tv/pms/) (checked 2026-10-24).

**Can signup happen in the Plex window?** Plex's invitation documentation explicitly describes a hosted Sign In/Sign Up window, including **Sign Up with Email**. Registration itself belongs on Plex. The documentation reviewed does not guarantee that switching from a KidsWatch PIN authorization to signup preserves that PIN and return destination. Treat in-window signup followed by automatic return as a feasibility gate, not a verified feature. An eligible adult can always create the account on Plex and then choose **Continue with Plex** again. A minor must follow the eligibility decision above. [Plex's documented signup window](https://support.plex.tv/articles/201105738-creating-and-managing-server-shares/) (checked 2026-10-24).

The official Auth App parameter list reviewed does not document `forceLogin`, `prompt=login`, `login_hint`, or a guaranteed choose-account option. Do not build the design around invented flags. Public `plex.tv/link` HTML does expose **Sign in as a different user**; this is evidence for that page, not proof of equivalent Auth App behavior. A full interactive browser was unavailable during this investigation, so popup cookie handling, signup return, account switching, mobile handoff, and password-manager behavior remain untested. [Auth reference](https://developer.plex.tv/pms/), [link page](https://plex.tv/link/) (checked 2026-10-24).

### Regular accounts, managed accounts, shares, and email

| Question | Verified behavior | Design consequence |
| --- | --- | --- |
| What is a managed account? | A profile created by the Home admin, without its own email/password or direct login. A full Home member signs the device in, then switches profiles. | Do not show a managed child an independent sign-in form. |
| Can a friend's managed child receive this server's share? | Managed accounts can receive server content only from their Home admin. A parent's incoming share does not propagate to their managed profiles. | The README's reason for regular accounts is technically correct, but does not resolve age eligibility. |
| What if the parent is already in this owner's Home? | A profile created in that same Home can receive the Home admin's server content. Only the Home admin creates it. Membership alone does not let another parent create profiles as admin. | Candidate future managed-user support; verify actual Home/profile membership and identity mapping. |
| Can regular Home members receive outside shares? | A regular account remains distinct from a managed profile. Joining a Home does not turn it into a managed account. | Do not assume all Home members have the same sharing limitations. |
| What email is needed? | Direct regular signup requires an email and password; Google/Apple alternatives exist. Managed accounts need no email. Plex has email verification and recovery. | Use a working address controlled by the adult account holder; do not promise verification is unnecessary. |
| Who accepts a server share? | The recipient can accept by email or in Plex Web. An invite can lead to registration if no account exists. | Track Plex invitation acceptance separately from parent site access and library readiness. |

Sources: [managed-account differences](https://support.plex.tv/articles/203948776-managed-users/), [Home example](https://support.plex.tv/articles/204234313-example-plex-home-setup/), [account methods](https://support.plex.tv/articles/201862428-plex-accounts/), [share acceptance](https://support.plex.tv/articles/201105738-creating-and-managing-server-shares/) (checked 2026-10-24).

Email helper copy, shown only in an eligible regular-account flow: **"Use an email you can check. If you use Gmail, a tagged address such as name+kid@gmail.com reaches your existing inbox. Use a different tag for each account. Other providers vary."** This does not create a new inbox, establish privacy separation, or grant account eligibility. Do not append a tag automatically, strip tags during comparisons, recommend Gmail dot variations as distinct addresses, or claim Plex's current signup always accepts every alias. Verify a test address and Plex acceptance during the feasibility spike. [Google's plus-address explanation](https://gmail.googleblog.com/2008/03/2-hidden-ways-to-get-more-from-your.html) (checked 2026-10-24).

### What the repository already does

Inspected at base commit `434dbea` on 2026-10-24:

| Code | Observed behavior, not a new live API test |
| --- | --- |
| `app/plex.py`: `create_pin`, `auth_url`, `check_pin`, `account` | POST `https://clients.plex.tv/api/v2/pins?strong=true`; open hosted Auth App; GET the PIN with its code; GET `https://plex.tv/api/v2/user`. Product and client identifier are headers. |
| `app/main.py`: `/login`, `/auth/done` | Parent/owner sign-in; tokens discarded; existing accepted server share needed for a non-owner; owner phone-code check remains separate. |
| `app/main.py`: `/kids/{id}/connect`, `/link/{token}/start`, `/link/{token}/done` | One-use link, server-held PIN, callback tied to link, atomic claim and fresh validity checks. Link lasts 30 minutes; child PIN callback allows 15 minutes; ordinary login allows 3 minutes. |
| `app/service.py`: `connect` | Reject owner, registered parent, duplicate child account, and an unrelated preexisting share. Create label-restricted share and verify restrictions; remove unsafe shares. No child token persisted. |
| `app/plex.py`: `accept_invite_from_owner` | GET `/api/invites/requests`, then PUT matching owner's `/api/invites/requests/{id}` with its flags. Deliberately skips Home invites. Only this owner's library invitation is accepted. |
| `app/service.py`: connect and reconciliation | Acceptance can fail independently. Existing share's `acceptedAt` and reconciliation track progress; owner approval controls labels separately. Online-source changes are attempted and read back. |
| Templates and `docs/reviews/ux-flows.md` | Signup elsewhere, private-window instructions, QR already available, callback completion page; review found waiting/acceptance ambiguity and misleading used-link messaging. |
| `app/config.py`, `app/notify.py`, README | Startup environment is currently authoritative. Notifications send a private webhook message and URL, not approve/deny actions. Owner credential is read from a read-only Plex configuration mount. |

The share/invite endpoints above are existing integration evidence, not a claim of a new, fully documented public cloud API contract. `update_share` records a prior live check on 2026-10-19; this task did not repeat it. Mock tests establish app behavior, not present Plex service behavior.

## 2. Parent options and tradeoffs

Counts below are **navigation/submit taps** from a signed-in parent home, excluding typing, selecting form fields, credential autofill, cookie prompts, verification mail, and provider-specific screens. Extra selection taps and likely Plex actions are listed explicitly. Any waiting for initial parent access or the optional stricter mode is not counted. These are design targets, not measured usability results.

| Option | What the parent sees and does | Target count | Risk and feasibility |
| --- | --- | --- | --- |
| **Primary: one Add a child wizard** | Add a child; name/content level and account eligibility; Continue; Continue with Plex; complete Plex; review account; Connect account; watch progress. | 4 app taps, plus eligibility/content selections and typically 1-3 Plex actions for an existing correct account. Signup adds Plex steps, usually 2-5 actions plus typing/verification. | Best continuity. Cookie sharing can select parent; popup can be blocked; signup may lose return state. Regular branch remains eligibility-gated. |
| **Fallback: QR or copied link on another device** | Complete child details; Use another device; scan QR; Continue with Plex on that device; confirm identity; Connect account. Original page updates. | 6 app taps including opening device link, plus camera handoff and Plex actions. | Reuses existing link model and QR. Another device can also have the wrong Plex account; same confirmation and eligibility checks apply. Bearer link must expire and reveal minimal data. |
| Native Plex TV code | Finish KidsWatch connection first; open Plex on TV; Sign In; phone opens plex.tv/link; sign into the intended regular account, enter the TV's code and submit. For a managed child, a parent signs in as a Home member and selects the child. | After KidsWatch setup: normally 2 TV actions and 2-4 phone actions, plus login and code typing. | Good playback setup. The TV owns its PIN/token; KidsWatch cannot intercept it to identify/connect a child. Parent-account authorization would sign the TV into the parent account. |
| KidsWatch short-code pairing | A KidsWatch browser page on another screen displays a short code; parent enters it at this instance's Pair device page; that page starts the normal account wizard. | About 7-9 app actions plus Plex login. | New pairing system with guessing/expiry risks. Does not install a KidsWatch TV app or automatically sign a native Plex player in. Defer; QR is cheaper. |
| Same-Home managed profile | Add a child; choose managed profile; owner selects or creates it in Plex Home; bind the actual profile; review; connect without further KidsWatch approval. Devices use existing Home login and profile switcher. | Target 4 app taps after profile exists, plus 1-3 selection taps; creating it manually adds roughly 4-6 Plex actions. | Best supported minor-account model. Requires actual same Home and Home-admin cooperation. Current KidsWatch account/share logic does not support it. Not an external-household workaround. |
| Invitation-led registration | Owner sends a restricted Plex invitation; eligible recipient opens email, registers on Plex, accepts, returns to KidsWatch, then connects. | Roughly 7-10 actions across email/Plex/app, plus setup selections. | Plex documents this signup route. Current `connect` rejects unrelated preexisting shares, so supporting it for children needs a deliberately linked pending invite and restriction checks. Parent invitations can use this route sooner. |

The QR opens the **KidsWatch connection page**, not a page containing a child's token. It connects KidsWatch only. A TV QR from the native Plex app links that TV instead. Label those two QR types clearly. [Plex TV connection behavior](https://support.plex.tv/articles/203395277-connect-app-to-your-plex-account/) (checked 2026-10-24).

### Primary-path rules

- Start with **Add a child** in one place. Save a resumable draft before leaving for Plex; a canceled popup does not make another child.
- Keep the parent KidsWatch session intact. The child callback must never call `/auth/done` or replace the parent's role/session. Plex's own selected browser account may change; explain that on the wrong-account screen.
- Read the authorized identity first, then show **Connect this account**. No shares, invitations, online-source changes, or permanent child binding before that confirmation. Reject forbidden identities before showing an enabled confirmation action.
- Keep the candidate token only in short-lived server memory, never cookies, URLs, local storage, logs, or database. Expire it after five minutes or the upstream PIN's shorter remaining lifetime. A restart returns to sign-in. Final confirmation rereads identity and current authorization, then runs the existing safety checks.
- In the default trusted-parent mode, connection immediately creates the restricted share, activates the child and queues allowed-title labels after restriction verification. There is no child approval request or waiting-for-owner step. Do not show **Ready to watch** until accepted share, active child, verified restrictions and applied labels are confirmed. Optional stricter mode keeps requested children unlabeled until explicitly approved. Show unresolved online-source changes separately.
- On phones, use top-level navigation by default. On desktop, open a same-origin launch page synchronously from a click, then send it to Plex. If blocked, offer same-tab navigation and QR immediately.
- Do not rely on popup scripting across Plex's origin. Poll a same-origin authenticated progress endpoint; completion can say to close the window. If using `postMessage` as a hint, validate exact origin/source and confirm state on the server. Preserve strict CSP and referrer protection.

## 3. Owner options and installation boundary

| Option | Owner journey | Target effort | What can ship |
| --- | --- | --- | --- |
| Existing guided install | Read install page; Copy setup prompt; paste into chosen local setup tool; review host/HTTPS/backup plan; run setup; open app. | 2 website actions, plus host-specific work and existing confirmations. | Already represented by `site/server-owners.html`; keep manual instructions. This worktree has no general one-command installer script, so do not claim one was verified. |
| Reviewed one-command installer | Run a version-pinned setup command from the host; answer host address, Plex mount, time zone, delivery method; inspect plan; apply; open app. | One command plus roughly 4-6 host-specific answers. | Future optional wrapper. Must back up existing configuration, preserve unrelated services, avoid Docker socket exposure, and print rollback instructions. |
| First-run app wizard | Owner Plex sign-in and existing phone verification; choose movie/TV libraries; select provisioned notification method and test it; Finish setup. | About 5-7 app actions plus library selections, Plex actions, and code entry. | Primary owner experience after host bootstrap. Persist non-secret setup choices in a versioned settings store. |
| Invite parents | Invite parents; choose existing Plex recipient or create a request-only invitation; Create invite; Copy message or show QR. | 3-4 app actions, plus recipient selection. | First support people who already have accepted Plex server access. Add new-to-server recipients explicitly later. |
| Phone approvals | Private notification; Open request; Approve parent. Child connections notify without requesting approval. | 2 actions with a current owner session; one approval tap once on the request. | First release uses existing notifier deep links and authenticated CSRF-protected forms. |
| Actionable push | Approve a parent directly from a private notification. | 1 action, subject to platform unlock/authentication. | Later adapter and server endpoint, not supported by today's notifier payload. |

### Avoid first-run deadlock

Today owner sign-in intentionally requires configured code delivery. A wizard shown only after sign-in cannot be the sole place that first configures delivery. Keep the installer/host-console step that provisions the receiver from the owner's password manager before sign-in; the app wizard selects/tests that provisioned method. If a simpler bootstrap UI is later needed, make it a separate host-authorized, short-lived, single-use setup session, never a public first-visitor-becomes-owner page. Do not turn off `PK_OWNER_CODE` to make the wizard work.

The application must never edit its read-only Plex mount or Docker host configuration. UI settings need documented precedence: existing explicit deployment environment values remain authoritative; the database holds UI-editable values only where an override is absent. Show a lock and installation instructions for fixed values. Refactor the current nonempty default library selection deliberately so defaults do not prevent the new chooser from operating.

### Approved parents are trusted to add children

Use one setting, **Approved parents can add their own children**. Set it **on for new installs**. An existing installation keeps its previous stricter behavior during migration until its owner explicitly enables the setting; do not silently expand authority on upgrade. Show the control in first-run setup and Admin settings, with this copy: **"Parents you approve can connect children without asking you again. You will be notified and can pause or remove a child in Admin."** Turning it off selects optional per-child approval mode rather than adding another nested setting.

Required future code changes:

- `app/service.py`: after account confirmation, freshly verify the initiating parent remains enabled and still has accepted Plex access. In trusted mode, create the restricted share immediately, verify it, accept the invitation when possible, set the child `active`, and queue title sync. This is the same activation outcome as the existing owner-child route. Existing share rollback, duplicate-account rejection, online-source readback and fail-safe restrictions remain mandatory. Policy changes cannot excuse an unsafe share.
- `app/main.py`: the successful child-connect handler currently inserts an `add_child` request. In trusted mode, skip that insertion and send an informational **"An approved parent connected a child"** notification instead. The Admin Requests view lists parent access approvals only by default. Child records stay in Admin Children, with existing owner pause/removal and audit controls. Setup-help requests remain ordinary help, not disguised child approvals.
- Templates and progress: remove the child waiting-for-owner panel and approval language from the default overview, connection completion, parent home and help. `waiting.html` still handles initial parent site access. Plex invitation acceptance and title-sync progress remain visible; neither is a child approval screen.
- Optional strict mode reuses the existing `requested -> active` transition, child approval request and owner notification. Label that mode in Admin, and show its child queue only when enabled. Avoid duplicating the connection service or account-safety logic.
- Changing the setting affects new connections. Do not silently activate already-requested children when enabling it, or revoke active children when disabling it. Surface preexisting pending children in a clearly labeled legacy/strict section until explicitly reviewed, even if trusted mode is now on. Persist the policy/version used for a connection in the audit record and serialize activation with relevant policy/access changes so a disabled parent cannot finish from an old popup.

Tests must cover new-install default on, upgrade preservation, turning the setting on/off, active and pending children surviving policy changes, the no-owner-wait path, strict-mode approval, disabled-parent races, duplicate callbacks, safe share readback, and notification failure. A failed informational notification must not roll back a safely connected child; display the connection in Admin and record a delivery failure without leaking the receiver URL.

### Invitation and approval boundaries

An **Invite parents** link is a request to use this KidsWatch instance, not a Plex server share or automatic site membership. Once the owner approves the parent, the default trust setting lets that parent create and connect children without further approval. Generate a random, hashed-at-rest capability with an owner-selected expiration, revoke control, and optional intended Plex identity; default to one recipient and seven days. A public landing GET only previews the invitation. Claiming requires adult Plex sign-in, CSRF protection, fresh invitation validity, and owner approval. Link scanners must not consume it.

For the first release, invite only existing accepted server users and explain how to grant Plex access separately. Later, let an authenticated owner explicitly start Plex's library invitation flow, then recheck accepted server access before enabling the parent. New parent signup can happen in Plex's supported invitation experience. A generic KidsWatch link alone must not bypass today's accepted-share rule or disclose the server's libraries.

Notification links open an authenticated detail page and never mutate on GET. An actionable-push adapter must bind a single-use action to the exact request/version and approving owner, require the receiver's authenticated identity, expire quickly, and apply the same checks as the web form. Do not put a reusable owner session or a standalone approval bearer link in a lock-screen notification. Approval must be idempotent and audit who approved; stale or already-decided requests show their current result. Offer **Undo approval** through the normal audited access-removal workflow, with a warning that playback may already have occurred.

## 4. Clickable-level wireframes and copy

These are plain-text interaction specifications. Brackets are controls; arrows name destinations. `{owner}`, `{child}`, and account examples are substitutions. All new routes are proposed, not existing endpoints. Plex-owned labels describe the external destination and must be verified during the spike; KidsWatch does not recreate those forms. Screens must work at 390 px, support keyboard focus and screen readers, and expose progress using a polite live region rather than color alone.

### Owner setup and invitations

```text
O0  Install KidsWatch for Plex                         [install page]
    Choose what your family can watch and when.
    You'll need your own Plex server and Plex Pass.
    [Copy setup prompt] -> clipboard, inline "Setup prompt copied."
    [Follow the manual steps] -> existing Compose instructions
    [Open KidsWatch] -> O1 after installation
    Host setup supplies HTTPS, read-only Plex settings and private code delivery.

O1  Finish setting up KidsWatch                       /setup
    Sign in with the account that owns this Plex server.
    [Sign in with Plex] -> Plex -> O2 if matching owner
    Wrong owner: "This account does not own this Plex server."
    [Try another account] -> Plex; [Installation help] -> setup help

O2  Check your phone                                 existing /verify
    Enter the sign-in code sent to your private notification account.
    Sign-in code [______]  [Continue] -> O3
    [Send a new code] -> O2 with rate limit
    "No code? Check notification delivery on your server."
    [Delivery help] -> host-console recovery instructions

O3  Choose libraries for children                    /setup/libraries
    Choose which libraries parents can use for their children's picks.
    [ ] Family Movies   [ ] Family Shows   [ ] Other Movies
    Only movie and TV libraries appear. Start with nothing selected.
    Time zone [America/Denver v]
    [Continue] -> validate selection, O4; [Back] -> O1 summary
    Error: "Choose at least one movie or TV library."
    Fixed setting: "Managed by your installation." [How to change it]

O4  Choose how you get requests                      /setup/notifications
    Get parent access requests and notices when parents connect children.
    ( ) Configured phone notifications
    ( ) Another configured private receiver
    No method configured: "Finish private notification setup on your server."
    [Setup instructions] -> instructions, no secret entry field
    [Send test notification] -> "Test sent. Check your device."
    [ ] I received the test on my private device
    [x] Approved parents can add their own children
    "Parents you approve can connect children without asking you again.
    You will be notified and can pause or remove a child in Admin."
    Optional off state: "Ask me to approve each child."
    [Finish setup] -> fresh checks, save, O5; [Back] -> O3
    Failure: "The test could not be sent. Check your receiver and try again."

O5  KidsWatch is ready                               /admin
    Libraries: Family Movies, Family Shows
    Notifications: Confirmed     Playback stop: Not tested
    [Invite parents] -> O6
    [Add my child] -> P1
    [Test playback limits] -> existing real-client checklist
    "Test a playback stop before your family relies on screen-time limits."

O6  Invite parents                                   /admin/invites/new
    Invite someone you know to set up their family.
    Parent [Choose a Plex user v]   Link expires [7 days v]
    "This invites them to KidsWatch. You still approve their access."
    [Create invite] -> O7; [Cancel] -> O5
    [They don't have Plex access yet] -> O6a

O6a Give this parent Plex access first
    Invite them in Plex, then return after they accept.
    They can make their own account on Plex while accepting the invitation.
    [Open Plex library access] -> Plex settings, normal new tab
    [Check again] -> fresh accepted-user check, O6 if found
    [Back] -> O6

O7  Your invitation is ready                         /admin/invites/{id}
    [QR: KidsWatch invitation]  {instance}/join/{opaque invitation}
    "Share this privately. It expires on {date}."
    Message:
    "I'd like to invite your family to KidsWatch for Plex. Open {invite URL},
    sign in with your own Plex account, and I'll approve your access.
    Then we'll check which setup works for your children."
    [Copy message] -> "Invitation message copied."
    [Copy link] -> "Invitation link copied."
    [Show QR code] -> enlarged O7; [Revoke invitation] -> confirm O7a
    [Done] -> O5

O7a Revoke this invitation?
    Anyone with this link will no longer be able to request access with it.
    [Revoke invitation] -> POST, O7 "Invitation revoked."
    [Keep invitation] -> O7

O8  Private notification                             owner's device
    Parent request: "A parent access request needs your review."
    [Open request] -> O9, sign in through O1/O2 if needed
    Default lock-screen copy contains no child name or email.
    Child connection: "An approved parent connected a child."
    [View child] -> authenticated Admin child details, no approval button
    [Pause child] / [Remove child] -> normal authenticated controls

O9  Review parent access                             /admin/requests/{id}
    Parent: {verified Plex username}
    "Approving this parent lets them add and connect their own children
    while Approved parents can add their own children is on."
    [Approve parent] -> POST, fresh checks, O10
    [Decline] -> O9a; [Back to requests] -> /admin
    Changed request: "This request changed. Review the latest details."

O9a Decline this request?
    Reply to the parent (optional) [________________]
    [Decline request] -> POST, O10; [Keep reviewing] -> O9

O10 Parent approved / Request declined
    Show the current decision and any Plex sync still in progress.
    [Back to requests] -> /admin
    If approved: [Undo approval] -> named confirmation and audited removal
```

### Parent entry, eligibility, and primary account wizard

```text
P0  You're invited to KidsWatch for Plex              /join/{invite}
    {owner} invited your family to choose titles and set screen time.
    Use your own adult Plex account to get started.
    [Sign in with Plex] -> Plex -> P0a
    [How it works] -> help; no invitation consumed on GET

P0a Ask {owner} for access
    Signed in as {verified parent account}.
    [Request access] -> CSRF POST, P0b
    If share missing: "Accept {owner}'s Plex library invitation first."
    [Open Plex invitations] -> Plex Web; [Check again] -> fresh share check

P0b Waiting for {owner}
    Your request was sent. This page will update when it is approved.
    [Check access] -> server check; approved -> parent home
    [Sign out] -> normal sign-out
    Declined: "Your request was declined. Contact {owner} if you need help."

P-home My kids                                       /home
    [Add a child] -> P1; existing draft [Finish setup] -> saved step

P1  Add a child                                      /kids/new
    Child's first name or nickname [________]
    What can they watch? [Younger kids / Older kids / Teens]
    "This sets content ratings. It does not change Plex account eligibility."
    Child's age band [Under 13 / 13 to 17 / Adult]
    [ ] I am their parent or legal guardian, or I am setting up my own access.
    [Continue] -> save draft; minor -> P2m; adult -> P2
    [Cancel] -> parent home, no share created
    Minor copy: "Children use Plex under a parent or guardian's account."
    [Plex account rules] -> S1 and S2

P2m Choose a Plex setup for {child}
    Does {child} use a managed profile in {owner}'s Plex Home?
    [Yes, use that profile] -> M1, only when managed support is available
    [No, we use another Plex Home] -> P2u
    [I'm not sure] -> profile explanation with [Ask {owner} for help]
    [Back] -> P1
    Before managed support ships: show P2u with an explanatory Home help link.

P2u This setup needs {owner}'s help
    Plex's account rules and sharing limits do not give us a supported
    independent-account setup for a child in another Plex Home yet.
    Your draft is saved. No Plex access has been granted.
    [Ask {owner} for help] -> in-app request form, prefilled with setup question
    [Read Plex's family setup guide] -> S4
    [Back to my kids] -> parent home
    No "continue anyway", false-age prompt, or email-alias workaround.

P2  Connect the Plex account                         /kids/{id}/setup/account
    Plex handles sign-in and account creation. We never see your password.
    Use a separate eligible account, rather than your parent or server account.
    [Continue with Plex] -> P3 launch, then Plex-owned authorization
    [Use another device] -> Q1
    [Need a new account?] -> eligibility reminder and P2a
    [Back] -> P1; [Finish later] -> parent home

P2a Create an account on Plex
    Only an eligible adult account holder should register.
    [Open Plex sign-up] -> official sign-up page in normal new tab
    [Email address tips] -> optional alias helper, section 1 copy
    "After signing up, return here to connect the account."
    [Continue with Plex] -> P3
    If the spike verifies signup inside Auth App preserves the transaction,
    merge this into P3's hosted flow; otherwise keep this explicit two-part step.

P3  Finish on Plex                                   parent page, progress
    Complete sign-in in the Plex window, then return here.
    [Open Plex again] -> current safe launch or a renewed transaction
    [Use this tab instead] -> same-tab authorization
    [Use another device] -> Q1; [Cancel sign-in] -> P2, invalidate attempt
    "If Plex shows another account, choose its different-account option."
    Sign-in completes -> P4 automatically; no permanent binding yet

P4  Is this the right Plex account?                  parent review
    Connect to: {child}
    Plex username: {username}   Email: {masked email}
    "This account will receive only the titles allowed for {child}."
    [Connect this account] -> CSRF POST, fresh checks, P5
    [Use a different account] -> invalidate candidate, P3
    [Cancel] -> P2, discard candidate token
    Wrong known adult: "Plex signed in to a parent or server account."
    Disabled [Connect this account]
    [Choose another account on Plex] -> verified Plex switch route, then P3
    If no reliable switch route exists: [Use another device] -> Q1

P5  Setting up {child}'s access                      live progress
    Account confirmed              Done
    Plex library invitation        Accepted / Needs your attention
    Allowed titles                 Updating / Ready
    Plex's free streaming sources   Off / Needs your attention
    "You can leave this page. Your progress is saved."
    [Choose titles] -> existing titles page
    [Set screen time] -> existing schedule page
    [Check progress] -> authenticated status refresh
    Invitation pending -> P6; all readiness conditions -> P7

P6  Accept the Plex invitation
    Sign in to Plex with the account you just connected, then accept
    the library invitation from {owner}. It is also in that account's email.
    [Open Plex invitations] -> Plex Manage Library Access
    [Reconnect to accept here] -> P2 with existing child/account lock
    [Check again] -> fresh accepted-share check, P5
    "Accept only the library invitation. Joining a Plex Home is separate."

P7  {child} is ready to watch
    The account is connected and the allowed titles are ready in Plex.
    [Set up Plex on a TV] -> T1
    [Open Plex] -> Plex app/web; [Back to my kids] -> parent home
    If online sources were not disabled:
    "Plex's free movies and Live TV still need attention."
    [Review Plex streaming settings] -> existing online-source help
    Never imply KidsWatch controls Plex's unrelated online catalog.
```

`Adult` in P1 is a conservative eligibility gate for the technical regular-account path, not a suggestion to relabel children. If Plex later approves an adult-held account arrangement for minors, replace the blocked branch with accurately worded holder/guardian consent and audit that decision. Do not collect exact birthdays or a child's password.

Optional stricter mode adds only these screens and changes. These never appear in the default trusted-parent journey:

```text
S1  Waiting for {owner}'s approval                    after P4/P5, strict only
    Your account is connected. This server asks its owner to approve each child.
    The Plex share remains restricted to no allowed titles until approval.
    [Choose titles] -> titles editor; [Set screen time] -> schedule
    [Check progress] -> authenticated status; approved -> P5 sync, then P7

S2  Review {child}'s access                           Admin, strict only
    Parent: {verified parent}   Plex identity: {confirmed identity}
    Content level: {level}
    [Approve child] -> CSRF POST, verify restrictions, activate, queue sync
    [Decline] -> named confirmation and optional reply, then audited removal
    [Back to requests] -> strict-mode child queue

S3  Child approved / Child request declined
    {child}'s current status and any remaining invitation or sync work.
    [Back to requests] -> strict-mode queue
    Parent sees current result automatically, without signing in again.
```

### QR fallback, managed profiles, and TV setup

```text
Q1  Connect on another device                        /kids/{id}/setup/device
    Scan this with the phone or tablet you'll use to sign in to Plex.
    [QR: Connect KidsWatch]  {instance}/link/{opaque token}
    "This connects KidsWatch. It does not sign your TV in."
    "Use once within 30 minutes. Keep this link private."
    A link opens a connection attempt; it is consumed only on final confirmation.
    [Copy link] -> "Connection link copied."
    [Make a new link] -> CSRF POST, invalidate old link, refresh QR
    [Back] -> P2; original parent page polls server progress

Q2  Connect {child} to KidsWatch                      /link/{token}
    {parent display name} started this setup.
    Sign in with the intended account on Plex, then check it here.
    [Continue with Plex] -> hosted Plex -> Q3
    Minimal page, no family history or library details.

Q3  Is this the right Plex account?
    Plex username: {username}   Email: {masked email}
    [Connect this account] -> same checks as P4, Q4
    [Use a different account] -> clear candidate, Q2/Plex switch help
    Wrong parent, duplicate, existing-share cases use E3 below.

Q4  Plex account connected
    Your setup page will update. You can close this window.
    "Plex invitation acceptance or title updates may still be needed."
    Original authenticated parent sees P5; Q4 exposes no parent navigation.

M1  Use a managed Plex profile                       future /kids/{id}/setup/home
    Choose a profile assigned to your family in this server owner's Plex Home.
    [Choose managed profile] -> verified family-scoped profile list, review, connect
    [The profile is missing] -> profile-creation help, M2
    No KidsWatch approval is requested for an approved parent.
    [Back] -> P2m

M2  A Plex Home profile is needed
    Only the Home admin can create the profile in Plex. This is a Plex
    setup requirement, not another KidsWatch approval. Your draft is saved.
    [Ask {owner} to create a profile] -> setup-help request
    [Check progress] -> authenticated refresh
    [Back to my kids] -> parent home

M3  Owner: Choose {child}'s managed profile
    Eligible profiles from this owner's actual Plex Home [Choose profile v]
    [Create a profile in Plex] -> official Plex Home settings
    [Refresh profiles] -> fresh membership query
    [Assign profile to family] -> verify family and profile, M1
    Owner-as-parent: [Connect profile] -> verify, activate, sync, notify
    [Cancel] -> Admin children
    Ready requires verified profile mapping, limits, and allowed titles -> M4

M4  {child}'s profile is ready
    On a shared device, sign in as a regular member of this Plex Home,
    then choose {child}'s managed profile. Protect adult profiles with PINs.
    [Set up a TV] -> T1; [Back to my kids] -> parent home

T1  Sign in to Plex on your TV                        help in KidsWatch
    1. Open the Plex app on your TV and choose Sign In.
    2. Use the TV's QR code, or open plex.tv/link on your phone.
    3. Check the account, enter the TV's four-character code, and link it.
    Managed profile: sign in as a regular Home member, then select {child}.
    Regular account: use the same eligible account connected to {child}.
    [Open plex.tv/link] -> official Plex page
    [Back to {child}] -> authenticated child overview
    KidsWatch does not ask you to enter the TV's code here.

T2  Plex-owned link page                             outside KidsWatch
    Enter the code displayed on your TV.
    Account: {Plex selected user}
    [Sign in as a different user] -> Plex-owned authentication
    Code [____] [Link] -> Plex links its own player
    Return to TV; choose child profile if applicable.
    Do not reproduce or submit this form from KidsWatch.
```

M1-M4 are a conditional future flow, not a launch promise. In particular, an API to list/switch Home users and update a managed profile's restrictions has not been verified by this task. Profile support requires a separate integration spike and must not reuse `invited_email` to pretend a managed profile is an independent share. An approved parent can connect an already assigned profile without another KidsWatch approval; creating a missing profile still requires the Plex Home admin because Plex restricts that operation.

### Recovery states shared by the flows

| ID | Exact heading and copy | Actions and behavior |
| --- | --- | --- |
| E1 | **Plex isn't answering** / "Your draft is saved. Try connecting again in a moment." | **Try again** returns to P2/Q2; **Finish later** returns to parent home if authenticated. No mutation was completed merely because authorization started. |
| E2 | **Sign-in took too long** / "Your child is saved. Start a new Plex sign-in to continue." | **Start sign-in again** creates a new bound attempt; invalidates the old one. Do not extend expired authorization silently. |
| E3 | **That account can't be connected** / Specific reason: parent/owner, already connected to another child, existing unmanaged share, or a different account from the one already bound. | **Use another account**; **Use another device**; existing-share conflict also **Ask {owner} for help**. Do not remove existing access automatically or show another child's details. |
| E4 | **This connection link is no longer available** / "If you already finished, check the child's page in your original window. Otherwise, make a new link there." | **Sign in as a parent** only. The bearer page does not reveal whether a child is connected, who they are, or which reason invalidated the token. |
| E5 | **Access needs attention** / "Plex did not keep the restrictions. We are checking that access has been removed. Do not use this account yet." | **Check progress**, **Ask {owner} for help**. If removal is verified, say "The new share was removed." Never claim safe removal while it is failing. Preserve existing retry and alert logic. |
| E6 | **Your session expired** / "Sign in again to continue setting up your saved child." | **Sign in as a parent** with validated same-origin return; final confirmation must be repeated, never replayed automatically. |
| E7 | **Invitation unavailable** / "This invitation may have expired or been revoked. Ask the person who sent it for a new one." | **Sign in with Plex** for existing members; no family details exposed. |
| E8 | **Request already reviewed** / "This request has already been {approved/declined}." | **View current status**, owner-authenticated; stale push actions do not reapply changes. |

## 5. Implementation plan and parallel work

Proposed file names below are ownership boundaries for a future implementation, not files created by this task. Assign one integration owner for `main.py` route registration and shared templates; workers should add separate modules/templates rather than concurrently edit that file. No deployment is included in this design.

| Task | Owned files | Dependencies and acceptance tests |
| --- | --- | --- |
| **F0: account-policy and hosted-auth feasibility** | `docs/design/onboarding-feasibility.md`; a private manual test checklist with no credentials or identifying screenshots committed | First: verify anonymous, parent-signed-in, target-signed-in, Google/Apple and email signup, signup return, account switching, popup blocked/closed, iOS Safari and Android Chrome. Obtain Plex clarification of minor account arrangements before enabling regular accounts for minors. Report supported, unsupported, and unknown separately. Do not automate account creation or use real child data. |
| **F1: managed-profile feasibility** | `docs/design/managed-profile-feasibility.md` | Can run beside F0. Use an owner-authorized test Home to verify listing, numeric session identity, same-Home restrictions, playback stop and adult PIN isolation. Determine exact supported endpoints and token lifetime. No production Home membership changes. Gate M screens on results. |
| **B1: connection transaction service** | New `app/onboarding.py`, `app/onboarding_routes.py`; focused `tests/test_onboarding.py` | F0 determines switch/signup copy, but transaction structure can proceed independently. Bind attempt to instance, child, initiating parent, browser state, expiry and one-use capability. Separate authorized identity from confirmed connection. Test swapped child/PIN/callback, two tabs, parent-session preservation, expired/used/replaced links, restart loss, disabled parent, owner rejection, duplicate and existing-share rejection, missing CSRF and simultaneous confirmations. Reuse service safety logic. Test approved-parent activation and informational notification with no add-child approval request; optional strict mode remains requested until approved. |
| **B2: wizard UI and progress** | New `app/templates/onboarding_*.html`, `app/static/onboarding.js`, scoped CSS; planned updates to `kid_new.html`, `_connect.html`, `link.html`, `link_done.html`; `tests/test_onboarding_ui.py` | Can draft against B1's status contract in parallel. Eligibility routing, account confirmation, QR, same-tab fallback, resume draft, focus restoration, polite announcements, 390 px and keyboard checks. No external scripts, weakened CSP, child token in browser, or popup dependence. Verify exact failure copy. |
| **O1: first-run settings** | New `app/setup.py`, `app/setup_routes.py`, `app/templates/setup_*.html`, settings migration in `app/db.py`; `tests/test_setup.py` | Parallel to B1. Config owner handles `app/config.py` once, documenting environment precedence. Test non-owner denial, owner-code requirement, no first-visitor claim, empty/invalid library selection, movies/TV only, read-only mount, fixed environment settings, restart persistence, atomic save and setup resume. Notification test requires human receipt confirmation. Add the trust setting, default on only for new installs; verify migration and change semantics below. |
| **O2: parent invitations** | New `app/invitations.py`, `app/invitation_routes.py`, `app/templates/invite_*.html`; `tests/test_invitations.py` | Parallel to B1/O1; database changes go through O1 migration owner. Test hash-only storage, expiry/revocation, GET scanner safety, double claim, identity-bound claim, missing share, wrong parent, no auto-enable, no libraries on public pages, and fresh approval checks. First release selects existing accepted Plex users. |
| **O3: parent approval, child management and waiting state** | New `app/approval_routes.py`, `app/templates/approval_*.html`; `waiting.html`, owner approval area in `admin.html`; `tests/test_approval_flow.py` | Parallel after existing request semantics are mapped. Preserve existing owner auth and CSRF. Default approval list contains parents only; child notifications deep-link to management, with pause/remove still available. Test GET never approves, repeated/stale decisions are idempotent, disabled owner/session fails, waiting parent upgrades only after server approval and accepted-share check, and optional strict child queue is isolated. |
| **O4: optional push action adapter** | New `app/notification_actions.py`; future `app/notify.py` changes; `tests/test_notification_actions.py`; receiver instructions in README | Depends on O3 and a chosen receiver with authenticated actions. Test forged/replayed/expired actions, request-version mismatch, receiver identity, device unlock expectations and audit identity. Keep ordinary notification deep links as fallback. Do not use a signed URL alone as owner authorization. |
| **M1: managed-profile support** | New `app/managed_profiles.py`, Home setup templates, identity-model migration and trusted-parent service adapter; `tests/test_managed_profiles.py` | Only after F1. Differentiate regular-account versus managed-profile identity explicitly. Verify owner Home, parent assignment, restriction readback, fail-safe removal/recovery, per-profile session counting and stop, duplicate binding and migration compatibility. Share safety tests must pass for both identity types; existing assigned profiles connect without a KidsWatch owner-approval request. |
| **I1: integration and release checks** | `app/main.py`, `app/service.py`, `app/plex.py`, `app/security.py` only where needed; README and `docs/live-checklist.md` updates | Sole integration owner merges modules. Run existing suite plus new focused tests, install/readiness tests, negative auth/race cases, disposable live share readback and a real playback stop. Recheck current Plex sources before release. Preserve route/link compatibility and protected notification bootstrap. |

### Suggested waves and what can ship first

1. **Wave 1, parallel:** F0, F1, O1, O2, O3; B1 and B2 agree on a small status/confirmation contract. Ship owner library setup, existing-parent invitations, parent approval deep links, the trusted-parent activation policy, **Check access**, honest progress states, and better link recovery first. These reduce friction without pretending the external-minor account question is solved. Managed-child support and any existing minor-account deployments need explicit product review before broader claims.
2. **Wave 2:** ship the regular-account wizard for verified eligible use after B1/B2 and the browser checks pass. Provide QR and same-tab fallbacks on day one. If signup return fails, ship the explicit Plex signup-and-return step; do not hold useful owner improvements for an unverified one-window promise.
3. **Wave 3:** implement same-Home managed profiles after F1, then enable that minor route. Add authenticated notification actions if they justify their receiver complexity. Defer a separate KidsWatch short-code system and any native TV client.

Success measures: existing eligible account connects with four app navigation/submit taps; zero manual URL copying on the primary path; parent session remains valid after connection; wrong-account attempts never change shares; approved parents connect eligible children without owner approval; pending Plex invitation and initial parent approval have distinct next actions; owner can invite an existing Plex user in four app actions; parent approval updates a waiting page without signing out; child connections notify the owner and remain pausable/removable; no unsupported minor-account registration prompt appears. Validate these with two adults unfamiliar with the app using fictional household names, without collecting children's information.

### Rollback and compatibility

This document-only change is reversed by reverting its commit. No running app, deployment settings, accounts, or server shares changed.

Before future implementation, back up the SQLite database with its backup API and copy deployment configuration using the established protected procedure. Feature flags should disable the new wizard/invitation/setup entry points while preserving the old child pages and `/link/{token}` handlers. Make settings and invitation schema additions backward compatible; keep current environment deployments working. In-flight ephemeral auth attempts may require a new sign-in after rollback, but saved child drafts and existing access must remain intact. Rolling code back does not revoke an already-applied Plex share; use the existing audited removal workflow, verify Plex readback, and restore the database only with an explicit share-reconciliation plan.

## 6. Decisions still needed before implementation is called ready

- Plex confirmation of an account arrangement for minors outside the server owner's Home. Neither parental consent nor an email alias is a substitute for that answer.
- Browser proof that account switching and signup return work for the hosted Auth App. Until then, promise guided steps and recovery rather than guaranteed automatic signup-and-connect.
- Verified managed-profile API and playback identity behavior before enabling the recommended same-Home minor path.
- A real chosen notification receiver before designing one-tap push action credentials. Existing authenticated deep links can ship sooner.

The attainable promise is: **one guided place to start, Plex-owned authentication, a clear account check, and no lost progress**. The broader promise that every parent's child can create an independent account and connect in one click is not supported by the current official evidence.
