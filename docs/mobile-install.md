# Mobile server rollout and undo

See [Android connection and reachability](android-install.md) for owner instructions and the assistant install prompt. See [the v1 protocol](mobile-api.md) for authentication, routes, limits, and privacy rules.

The mobile feature defaults off. Set `PK_MOBILE_ENABLED=1` only in a reviewed installation with completed owner setup and a canonical root `PK_PUBLIC_URL`. Existing web routes continue to work while mobile is off. The server uses the runtime owner secret to derive pairing keys; do not provision or store a second plaintext secret. No backend credentials belong in a QR code, phone, fixture, or repository.

Prefer HTTPS. If the owner explicitly chooses direct unencrypted LAN mode, set `PK_MOBILE_LAN_ORIGIN` to exactly the same private-literal HTTP origin as `PK_PUBLIC_URL`. This is not a general HTTP enable switch. Proxy forwarding and public/DNS/loopback/link-local origins remain forbidden. Keep secure-cookie defaults for HTTPS.

Before updating, stop the app and make a consistent database backup, or use the SQLite backup API with proper coordination. Preserve protected configuration using the approved secret-storage process. Test migrations on disposable data first. Migrations add versioned mobile tables, configuration revision metadata, and compatible domain revision columns/triggers; existing identifiers and web routes are retained.

Rollback order: disable mobile, revoke phones and rotate the session epoch, then revert the server commits. Leave additive schema in place. Do not restore an old database solely to remove mobile tables, because it can erase recent web work and resurrect tokens. A full disaster restore requires stopped processes, matching configuration, epoch rotation, and re-pairing before reopening traffic.

The current branch is local-only work. These documents authorize no deployment, routing change, main merge, public release, or optional push integration.
