# Mobile server rollout and undo

See [Android connection and reachability](android-install.md) for owner instructions and the assistant install prompt. See [the v1 protocol](mobile-api.md) for authentication, routes, limits, and privacy rules.

Use the flags for the installed edition:

| Setting | Plex | Jellyfin |
| --- | --- | --- |
| Public root origin | `PK_PUBLIC_URL` | `KW_PUBLIC_URL` |
| Enable mobile | `PK_MOBILE_ENABLED` | `KW_MOBILE_ENABLED` |
| Explicit direct LAN origin | `PK_MOBILE_LAN_ORIGIN` | `KW_MOBILE_LAN_ORIGIN` |
| Secure browser cookies | `PK_SECURE_COOKIES` | `KW_SECURE_COOKIES` |

The mobile feature defaults off. Set `PK_MOBILE_ENABLED=1` (Plex) or `KW_MOBILE_ENABLED=1` (Jellyfin) only in a reviewed installation with completed owner setup and a canonical root public URL for that edition. Existing web routes continue to work while mobile is off. The server uses the runtime owner secret to derive pairing keys; do not provision or store a second plaintext secret. No backend credentials belong in a QR code, phone, fixture, or repository.

Prefer HTTPS, including tailnet-only Serve. If the owner explicitly chooses direct unencrypted LAN mode, use a separately configured LAN-only instance: set `PK_PUBLIC_URL` and `PK_MOBILE_LAN_ORIGIN` (Plex), or `KW_PUBLIC_URL` and `KW_MOBILE_LAN_ORIGIN` (Jellyfin), to the same exact RFC1918 IPv4 or IPv6 ULA literal HTTP origin. Browser sign-in and code creation require `PK_SECURE_COOKIES=0` or `KW_SECURE_COOKIES=0` on that HTTP instance, because secure browser cookies cannot be sent over HTTP. Browser cookies, connection codes, and bearer traffic are all unencrypted, so require explicit warned phone consent and no public, tunnel, or proxy exposure. This is not a general HTTP enable switch; public/DNS/loopback/link-local origins and proxy forwarding remain forbidden. Existing HTTPS/public instances retain `PK_SECURE_COOKIES=1` (Plex) or `KW_SECURE_COOKIES=1` (Jellyfin); never downgrade one for LAN use.

Before updating, stop the app and make a consistent database backup, or use the SQLite backup API with proper coordination. Preserve protected configuration using the approved secret-storage process. Test migrations on disposable data first. Migrations add versioned mobile tables, configuration revision metadata, and compatible domain revision columns/triggers; existing identifiers and web routes are retained.

Rollback order: disable mobile, revoke phones and rotate the session epoch, then revert the server commits. Leave additive schema in place. Do not restore an old database solely to remove mobile tables, because it can erase recent web work and resurrect tokens. A full disaster restore requires stopped processes, matching configuration, epoch rotation, and re-pairing before reopening traffic.

The current branch is local-only work. These documents authorize no deployment, routing change, main merge, public release, or optional push integration.
