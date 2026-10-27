# Connect KidsWatch on Android

KidsWatch is a parental companion for your own server, not a video player. The server owner provides the address. Keep using the website on Android, iPhone, or a computer whenever you prefer. This local server update does not publish an APK or install anything on a phone.

## Make the server reachable

Use a root HTTPS address with a certificate the phone trusts. Do not disable certificate or hostname checks. The Android app does not follow API redirects; when the address changes, review the new address and pair again. Browser sign-in remains separate from phone pairing. An interactive proxy login must not block the native bearer endpoints.

For an owner without a domain, [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel) is the documented default option: it exposes the app publicly over TLS using a tailnet DNS name, on supported external ports 443, 8443, or 10000. Review that public exposure before enabling it. [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) is tailnet-only; the phone needs Tailscale access. Inspect existing Serve/Funnel configuration and choose an unused port. Never overwrite an existing service's mapping blindly. No mapping or host configuration is changed by this update.

A Cloudflare Tunnel with the owner's domain or a home-only HTTPS address also works. Configure only the actual trusted proxy peers so forwarded client addresses and HTTPS scheme are interpreted correctly. Do not trust arbitrary Internet clients as proxies. Keep backend media ports and credentials private; expose only the KidsWatch application as intended.

Use the flags for the installed edition:

| Setting | Plex | Jellyfin |
| --- | --- | --- |
| Public root origin | `PK_PUBLIC_URL` | `KW_PUBLIC_URL` |
| Enable mobile | `PK_MOBILE_ENABLED` | `KW_MOBILE_ENABLED` |
| Explicit direct LAN origin | `PK_MOBILE_LAN_ORIGIN` | `KW_MOBILE_LAN_ORIGIN` |
| Secure browser cookies | `PK_SECURE_COOKIES` | `KW_SECURE_COOKIES` |

Prefer HTTPS, including tailnet-only Serve. Home LAN HTTP is optional and weaker: use only a separately configured, owner-selected direct LAN instance. Set that edition's public URL and mobile LAN origin to the same exact RFC1918 IPv4 or IPv6 ULA literal HTTP origin. For browser sign-in and code creation on that HTTP instance, explicitly set `PK_SECURE_COOKIES=0` (Plex) or `KW_SECURE_COOKIES=0` (Jellyfin); secure cookies otherwise cannot be sent over HTTP. Browser session cookies, connection codes, and bearer traffic are all unencrypted. Do not expose this instance through a public address, tunnel, or proxy.

The phone must separately consent and display **Home network only. Connection is not encrypted.** Someone on that network could read the session/code/token and control the account. It will not work away from home. Public, DNS-named, loopback, metadata, link-local, and proxy-forwarded HTTP connections are refused. Existing HTTPS/public instances retain `PK_SECURE_COOKIES=1` (Plex) or `KW_SECURE_COOKIES=1` (Jellyfin); never downgrade one to accommodate a LAN connection.

## Enable the local server feature

Back up the database correctly while the application is stopped, or use SQLite's coordinated backup API. Preserve protected configuration through the existing secret-storage process. Do not copy only the main file of a running WAL database.

After tests and review of the chosen server version, configure `PK_MOBILE_ENABLED=1` (Plex) or `KW_MOBILE_ENABLED=1` (Jellyfin) in that installation's existing configuration mechanism and set its canonical root public URL. Mobile is off by default. Complete **Admin > Owner setup** first; an existing parent row alone does not authorize pairing. This guide does not execute configuration changes or deployments.

## Pair a phone

1. Sign in on the KidsWatch website as yourself and open **Connect a phone**. Enable JavaScript for this pairing page; the rest of the website remains available without it.
2. Choose **Create code**. Keep the page private: its QR/code connects a phone with your authority. The code expires after two minutes and works once.
3. In the Android app choose **Scan to connect**. If camera access is denied, choose **Enter code instead** and enter the server address plus the displayed code.
4. Review **Connect to <host>?**, name the phone, and choose **Connect**. Never connect to an address you do not recognize. No media-server password/token is entered into the app.
5. Review the phone under **Connected phones**. Parents see their own phones; the owner can review all phones. A phone needing new access must be paired again.

Lost pairing response: revoke the unknown new phone on the website, then generate a new code. There is no secret token recovery page. Codes are not child TV connection codes.

Signing out in the browser leaves connected phones active. **Sign everyone out** invalidates browser sessions, phone devices, and open connection codes. Removing a phone locally while offline does not revoke its server token; use **Connected phones** on the website. Reinstalling or restoring a phone requires new pairing. Owner phones expire after seven days at most (12 hours idle); parent phones after 30 days at most (14 days idle).

Foreground polling needs no Firebase, distributor, or push setup. Updates may be delayed or unavailable when the app is closed. A saved control is not proof that playback stopped. Child setup, backend account linking, and owner setup remain website workflows.

## Ask an assistant to help install

Copy this prompt and replace the nonsecret placeholders:

> Help me connect KidsWatch Android to my existing KidsWatch server at `<server address>`. Inspect the selected edition/version and current deployment without changing it first. Use only the intended server, never a work machine or another family's data. Confirm that the server has the reviewed mobile API, explicit completed owner setup, and a canonical root HTTPS origin with a valid certificate. Explain how my phone reaches it. For no-domain access, discuss Tailscale Funnel's public exposure and compare tailnet-only Serve; inspect current mappings and do not replace an occupied port. A domain tunnel or home-only HTTPS is also acceptable. Native bearer endpoints must work without an interactive proxy login. Use narrowly configured trusted proxies. Use the installed edition's flags from the table: Plex uses `PK_PUBLIC_URL`, `PK_MOBILE_ENABLED`, `PK_MOBILE_LAN_ORIGIN`, and `PK_SECURE_COOKIES`; Jellyfin uses `KW_PUBLIC_URL`, `KW_MOBILE_ENABLED`, `KW_MOBILE_LAN_ORIGIN`, and `KW_SECURE_COOKIES`. If I specifically choose unencrypted home LAN mode, use a separately configured direct LAN instance with matching private-literal public URL/LAN origin and explicit warned phone consent. Browser sign-in and code creation there require `PK_SECURE_COOKIES=0` (Plex) or `KW_SECURE_COOKIES=0` (Jellyfin); explain that browser cookies, codes, and bearer traffic are unencrypted, with no public, tunnel, or proxy exposure. Retain `PK_SECURE_COOKIES=1` (Plex) or `KW_SECURE_COOKIES=1` (Jellyfin) on HTTPS/public instances; never silently downgrade HTTPS. Make an undoable database/configuration backup before any authorized installation change and tell me the rollback steps. Use my approved password manager for real secrets; do not print them or save them in plaintext. Never request my Plex token in the app. Explain website pairing with QR and manual fallback, phone revocation, sign-everyone-out, and polling limitations. Keep the website supported. Do not publish an APK or enable optional push services without separate authorization.

## Disposable emulator testing

Use only synthetic data. `tests/mobile_dev_server.py` creates a new temporary database, fictional adults/child/titles, and fake Plex calls; its `/dev/login` hook is defined only in the test module. Production application routes contain no dev login or transport bypass.

Run `python -m tests.mobile_dev_server --origin <test HTTPS origin> --cert <debug certificate> --key <debug-only key>`. It binds only to loopback by default. Supply a certificate trusted by the test browser and the Android debug build; never add trust-all code to the APK. Configure emulator reachability separately (for example, the emulator host alias or an explicit adb reverse mapping) and keep the configured origin/certificate names consistent. Do not advertise a loopback URL as a reachable phone address.

The standalone `python -m tests.run_mobile_browser` uses installed Node, Chromium, and Playwright to check pairing at a phone width against an ephemeral, fake-only HTTPS fixture. Its temporary certificate exception belongs solely to this browser test, not application transport. No package installation, real account, family database, or live server is needed. Camera decoding and real Android emulator end-to-end tests remain the separate Android validation phase.
