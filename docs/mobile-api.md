# Mobile API v1

The mobile feature is off by default (`PK_MOBILE_ENABLED=0`). This implementation follows the Android v1 design, including the nullable legacy request date and request author/cancel amendments. Web routes remain supported. No playback, push registration, arbitrary URL fetch, or credential export endpoint is provided.

The reusable modules are `app/mobile_protocol.py`, `app/mobile_core.py`, `app/mobile_routes.py`, `app/mobile_web.py`, and `app/static/mobile.js`. `app/mobile_plex.py` owns Plex authorization, domain serialization, and policy verification. The common code uses SQLite connections with named rows, adapter transactions, and current edition authorization. An adapter lock precedes remote refresh and SQLite locks; no SQLite transaction spans a network call. Adapter authorization and issuer hooks must perform local checks only inside transactions. The existing QR encoder is reused locally.

## Transport and authorization

Use a canonical root HTTPS origin: scheme, IDNA ASCII hostname, optional nondefault port. Userinfo, query, fragment, non-root paths, control characters, and port zero are rejected. The phone confirms the host before networking and uses only fixed relative API paths, with redirects disabled and normal certificate verification. Never forward tokens to another origin.

For a direct home LAN connection only, the owner can set `PK_MOBILE_LAN_ORIGIN` to the exact configured HTTP public origin, using an RFC1918 IPv4 or IPv6 ULA literal. The peer must also be on a private LAN. Proxy headers, DNS names, loopback, link-local, public, and metadata addresses are refused in this mode. HTTPS secure-cookie defaults are preserved. The phone must separately consent and persistently display that the connection is unencrypted. An HTTP address is never an automatic fallback.

All API requests use `Accept: application/json` and `X-KidsWatch-Client: android/<positive-version-code>`. Writes use `Content-Type: application/json`. Only `/meta` and `/pairings/redeem` are public. Other routes require `Authorization: Bearer <token>`. Cookies are ignored as API credentials and never renewed. Supplying bearer plus the edition's browser session cookie is rejected. API writes with `Origin` are rejected, no CORS origins are enabled, and CSRF tokens do not substitute for bearer credentials.

Browser `/mobile/*` uses only authenticated cookie sessions, current role, setup completion, and CSRF. A supplied Origin must exactly match the configured origin; cross-site Fetch Metadata is refused. Bearer credentials are refused on browser routes.

Every API result, including errors and unknown routes, is JSON with `Cache-Control: no-store` and `Vary: Authorization`. Backend exception text and credentials never enter DTOs. Bodies are limited to 32 KiB, duplicate JSON keys and nonfinite numbers are refused, writes reject unknown fields and wrong types, and unexpected query fields are rejected. IDs are URL-safe ASCII strings up to 128 characters. Page sizes are 1 through 100, default 50. Title batches have at most 100 unique IDs. All timestamps are UTC RFC3339 with `Z`; dates and household local times are separate values.

## Pairing and devices

Signed-in eligible parents and the verified owner open **Connect a phone** and create a code with POST `/mobile/pairings`. Setup must be explicitly complete. GET never issues a code. Each code has 12 random Crockford Base32 characters, lasts 120 seconds, and replaces the issuer's previous open code. Codes bind subject, role, current browser session hash, global and subject epoch, origin, and instance. Only keyed HMAC hashes are stored. HMAC keys are derived in memory from the existing runtime owner secret with separate pairing, rate, and cursor domains.

QR payload: `https://server.example/#kwpair=1&code=<12characters>&instance=<id>`. Secret material stays in the fragment, never in a server URL query. QR generation is local. Browser pairing pages have no-store and no-referrer policies and a manual server-address/code fallback. Normalization accepts ASCII lowercase, spaces, and hyphens; it never guesses ambiguous characters. There is no backend QR fetch.

POST `/api/mobile/v1/pairings/redeem` accepts exactly `code`, `instance_id`, and `device_label` (1 through 60 characters). It returns HTTP 201 with `token_type: "Bearer"`, `access_token`, Device, and Session. Conditional consume, device insertion, and audit share one immediate transaction. Expiry is checked after locks and again at consume. Concurrent workers produce one success and one generic `pairing_unavailable`. Wrong, used, expired, revoked, and wrong-instance codes have the same HTTP 400 error.

Durable rolling limits apply before code lookup: issuance 3 per 10 minutes per subject and 10 per hour per session; redemption 5 per 5 minutes per IPv4 or IPv6 /64 and 60 per minute globally. At most five codes are open per instance. Live buckets are never evicted to admit new identities, and global capacity is reserved. Trusted-proxy client-address handling reuses existing configuration.

Tokens are random 256-bit opaque values stored only as SHA-256 hashes. Parent idle/absolute lifetimes are 14/30 days; owner lifetimes are 12 hours/7 days. Every request checks expiry, revocation, current role/identity, epochs, and object scope. Role changes require reconnecting. Last-used writes occur at most once a minute and cannot extend absolute expiry. Owners see/revoke all phones; parents see/revoke only their own. Browser logout leaves paired phones active but invalidates its unredeemed codes. **Sign everyone out** and disabling a parent invalidate that scope's phones and codes.

There is no token recovery or pairing secret replay. If a redemption response is lost, revoke the unknown phone from the website and create a new code.

## Envelopes, resources, and endpoints

Success: `{"data": ..., "meta":{"request_id":"...","server_time":"...Z","api_version":1}}`. Lists contain `{"items":[],"next_cursor":null}` inside data. Authenticated cursors bind subject, filter, and order and expire after 15 minutes. Each page rechecks scope. Response fields may be added without changing v1.

The checked synthetic envelopes are in `tests/fixtures/mobile-v1.json`; `validate_wire` validates nested resources, types, dates, and enums. All examples are fictional. Schemas include Metadata, Session with capabilities, Child, ChildSettings, Schedule, Title, Activity, Request, Approval, Invite, Device, Mutation, Events, and errors. IDs remain strings, including Plex identifiers. Requests include safe nullable `submitter_display` and current per-resource `can_cancel`; never infer cancellation from ownership of a shared child. An owner reading another adult's request cannot cancel it. Unknown historical request creation dates may be null; display **Date unavailable**. No media URLs or artwork enter title DTOs.

| Method | Path relative to `/api/mobile/v1` | Result |
| --- | --- | --- |
| GET | `/meta` | Public instance, edition, supported versions, minimum client, pairing enabled |
| POST | `/pairings/redeem` | One-time token, Device, Session |
| GET | `/session` | Current subject, device expiry, timezone, date, rights, enforcement notice |
| GET | `/children` | Scoped Child list |
| GET | `/children/{id}` | Child and shared configuration revision |
| GET / PATCH | `/children/{id}/settings` | Settings; nonempty subset of name, age_level, mode, library_ids |
| GET / PUT | `/children/{id}/schedule` | Complete schedule; PUT excludes revision |
| POST | `/children/{id}/controls` | pause, resume, bonus, later |
| GET / POST | `/children/{id}/titles` | Filtered titles; complete validated choice batch |
| GET | `/children/{id}/activity` | Inclusive date range up to 31 household dates, default seven |
| GET / POST | `/requests` | Own requests; owner reads all eligible requests; create a request |
| POST | `/requests/{id}/cancel` | Submitter's open, cancellable request |
| POST | `/requests/{id}/decision` | Owner's advertised decision and note |
| GET | `/owner/approvals` | Parent and child approval adapter results |
| POST | `/owner/approvals/{id}/decision` | Approve or decline with current prerequisites |
| GET | `/owner/invite-recipients` | Fresh eligible adults, safe display fields |
| GET / POST | `/owner/invites` | Invitations; creation returns the join link once |
| DELETE | `/owner/invites/{id}` | Revoke an unclaimed invitation |
| GET | `/devices` | Scoped Device list with no token or hash |
| DELETE | `/devices/{id}` | Revoke a phone, including itself |
| GET | `/operations/{id}` | Authorized mutation outcome and original result revision |
| GET | `/events` | Scoped change hints, seven-day retention, reset/refetch on old cursor |

Browser routes: GET `/mobile/pairings` displays the form; POST creates; POST `/mobile/pairings/cancel` cancels the current issuer's code; GET `/mobile/devices` displays scoped phones; POST `/mobile/devices/{id}/revoke` redirects to the fixed devices path. A safe status poll reports only open, used, or expired to the issuer.

The native controls accept `expected_day` equal to the server household date. Pause value is `"indefinite"`, `"30"`, `"60"`, or `"tomorrow"`; resume value is null; bonus is integer 15/30/60 and later is integer 30/60. Numeric strings are invalid. Plex caps actual accumulated bonus/later at 600/180 minutes. Schedule writes require the configured IANA timezone, nullable minutes (unlimited), zero (no watching), HH:mm times, and explicit any_time for equal times. Shared `rules.py` controls DST, overnight windows, inclusive school breaks, and tomorrow pauses. Usage ticks do not advance the configuration revision.

Requests use exactly the Session's advertised keys and message limit. Plex keys are title, feature, more_access, problem, other. Existing open/recent-cancel limits apply. Parent access and child creation use approval routes, never ordinary request decisions. Invitations do not grant Plex access. Native add-child, account linking, setup, and other fragile lifecycle workflows remain web continuations.

## Revisions, retry, and verification

All domain writes require a UUID `Idempotency-Key`. Updates/deletes/decisions also require `If-Match: "r<N>"`; missing is 428, stale is 412. Child settings, schedule, choices, and controls share one revision, including web changes and co-parent membership changes. Mobile intent, audit, revision, operation, and idempotency reference commit together. Additive web and phone controls use the same atomic increment method.

Identical retries return the original status/operation/result revision after current authorization. They do not repeat effects, even if the old If-Match is now stale. Different intent with the same key is 409. Records last 24 hours with a further seven-day tombstone window; an expired retry is refused. No private response bodies are persisted. Invitation replay omits join_url and returns `link_available:false`.

Applied local configuration returns 200. Saved policy needing verification returns 202/pending. Check the returned operation ID; an apply failure is not a fabricated success. Operation readback uses only remote reads and checks the exact intended configuration before changing its outcome. Interrupted operations never blindly repeat upstream effects. Policy apply is distinct from observing playback stop. Unknown usage observations have `today.checked_at:null`; `sync.checked_at` separately records title policy verification.

Error data has fixed safe `code`, `message`, `fields`, and `retryable` values plus standard meta. Codes/statuses: 400 invalid_json/pairing_unavailable; 401 reconnect_required; 403 access_removed/csrf_failed; 404 not_found (also out of scope); 409 day_changed/operation_in_progress/prerequisite_required/idempotency_conflict/idempotency_expired; 412 revision_changed; 413 payload_too_large; 415 json_required; 422 invalid_fields/unsupported_action; 426 upgrade_required; 428 revision_required; 429 rate_limited; 503 authorization_unavailable/server_unavailable. Rate, in-progress, and unavailable responses include Retry-After.

## Migration and rollback

Use a stopped-instance backup or SQLite's coordinated backup API before enabling migrations. Do not copy only a running database's main file. Migrations are additive, versioned separately, and tested twice and with competing workers. No domain identities are converted or private history imported.

To undo, first turn the mobile flag off, revoke phones/bump the session epoch, then revert the application commits. Leave additive tables/columns inert. Keep web changes made since upgrade. Restoring an old database can resurrect access and lose new work; disaster recovery requires stopped services, the paired configuration, a new epoch, and re-pairing every phone before reopening. No live migration, deployment, or restore is performed by this local work.
