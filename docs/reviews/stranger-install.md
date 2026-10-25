# Stranger install review

Reviewed October 24, 2026, at source revision `492324f`.

The Docker image builds and the documented container configuration boots with
placeholder Plex settings. The landing page, static assets, and `/healthz` serve
successfully. Installation still requires knowledge that the guide does not
fully supply, and the settings restart instruction needs correction. This is a
read-only code review: no application, deployment, or existing documentation
files were changed.

## Verification performed

- Docker Engine 29.9.1 and Compose 5.6.0 were available. Plain `docker version`
  failed with socket permission denied; `sudo -n docker` worked.
- Built the repository Dockerfile with
  `sudo -n docker build -t kidswatch-install-review:task-ff1d279c5901 .`.
  The build downloaded and installed the declared dependencies successfully.
- Copied the Compose and environment examples into a temporary directory, made
  `data/` and `secrets/` owned by UID/GID 1000 with mode 0700 using the README's
  `install` command, and supplied a mode-0600 placeholder `Preferences.xml`.
  The XML contained only `PlexOnlineToken="placeholder-not-a-token"` and
  `ProcessedMachineIdentifier="placeholder-machine"`. No real credentials or
  household data were used.
- The final startup used the example's settings unchanged, including secure
  cookies, required owner codes, and the running enforcer. Only the absolute
  build context, placeholder file path, and unique image/container/project names
  differed. `docker compose config --quiet` and `docker compose up -d --build`
  succeeded. The final network and loopback port binding matched the example.
- An initial isolation experiment used an internal Docker network; this host did
  not publish its port in that configuration. Requests inside that container
  succeeded. That temporary network was then removed, and the following checks
  were repeated through the host's published port with the normal example
  network. The isolation experiment is not counted as a repository defect.
- Host requests to `http://127.0.0.1:8080/`, `/healthz`, `/static/style.css`,
  `/static/app.js`, and `/static/fonts/figtree-latin.woff2` all returned 200.
  The landing page contained the KidsWatch branding. Its cookie had `HttpOnly`,
  `SameSite=Lax`, and `Secure`; CSP, HSTS, frame denial, and MIME-sniffing
  protection headers were present.
- Docker inspection confirmed user `1000:1000`, read-only root filesystem,
  dropped capabilities, and `no-new-privileges`.
- Changed the temporary `.env`, tested `restart` versus container recreation,
  and separately checked health with the keeper disabled. Results are below.
- Ran the requested Python test suite with bytecode and pytest cache writes
  disabled: `PYTHONDONTWRITEBYTECODE=1 ~/Work/plex-kids/.venv/bin/python -m pytest
  -q -p no:cacheprovider`. Result: **120 passed**, one Starlette/httpx deprecation
  warning, in 10.18 seconds.

No real Plex sign-in, notification delivery to a real account, child connection,
library restriction, or playback stop was performed. Those require a configured
Plex server, credentials, and a receiver; a placeholder boot cannot establish
that those integrations work. No HTTPS reverse proxy was provisioned.

## Findings, ranked by severity

P2 means a behavior or operational instruction should be corrected before users
rely on it. P3 means a setup or reproducibility gap. No P0 or P1 finding was
established within this install review.

### 1. P2: Restarting does not apply edits to the Compose environment file

**Evidence:** `README.md:119` says to restart after settings changes.
`docker-compose.example.yml:8` loads settings with `env_file: .env`, and
`app/config.py:29` reads the process environment.

**Reproduction:** Changed `PK_OWNER_NAME=Admin` to `PK_OWNER_NAME=RestartProbe`
in the temporary `.env`. After `docker compose restart`, `docker exec ...
printenv PK_OWNER_NAME` still printed `Admin`. After `docker compose up -d
--no-build`, it printed `RestartProbe`.

**Impact:** A new installer can follow the restart instruction after configuring
code delivery, correcting a URL, or changing a security switch and remain on the
old configuration without understanding why.

**Suggested fix:** Document `docker compose up -d --force-recreate kidswatch`
after `.env` changes. Distinguish this from a process restart when the environment
has already been supplied correctly. Mention recreation in notification setup
and recovery instructions as well.

### 2. P2: The health check does not establish Plex or enforcement readiness

**Evidence:** `README.md:101` uses a successful `/healthz` request in the quick
start, and `README.md:251` uses it after updates. `app/main.py:449` always returns
HTTP 200 with `ok: true`. `Dockerfile:12` checks only HTTP status.
`app/enforcer.py:84` skips the Plex session request when there are no linked kids;
`app/enforcer.py:51` then records a successful tick.

**Reproduction:** With a fresh database, placeholder owner token and machine ID,
and the unresolved example Plex hostname, `/healthz` returned
`{"ok":true,"keeper":"ok"}`. With `PK_START_ENFORCER=0`, it returned
`{"ok":true,"keeper":"starting-or-stale"}` with HTTP 200, and the exact Python
command used by Docker's health check exited successfully.

**Impact:** A green health result can be mistaken for a successful Plex setup or
working time limits. The README's separate instruction to test a real playback
stop at `README.md:115` is valuable and limits this risk, but the health response
itself does not explain its narrow meaning.

**Suggested fix:** Explicitly describe this as a web-process liveness check.
Document what `keeper` means, including the empty-database and disabled-keeper
cases. Add a separate readiness check or startup diagnostic for credentials,
Plex reachability, configured libraries, and recent enforcement success; do not
claim that a 200 response verifies those things.

### 3. P2: Documented positive poll intervals are not validated

**Evidence:** `README.md:137` describes `PK_POLL_SECONDS` as positive whole
seconds. `app/config.py:39` performs only `int(...)` conversion.
`app/enforcer.py:68` passes the value directly to the wait operation, and
`app/enforcer.py:88` uses it to cap counted time.

**Reproduction:** Calling `load()` in the built image accepted both `0` and `-1`.
A nonnumeric value raised an unhelpful raw `ValueError`. The enforcer was not
started with a nonpositive interval.

**Impact:** An accidental zero or negative value can remove the delay between
polls and make time accounting invalid. A syntax-valid Compose configuration
does not catch this error.

**Suggested fix:** Reject values below 1 at startup with an error naming the
setting and permitted range. Keep the positive-value rule in the README and
include a troubleshooting example for invalid settings.

### 4. P3: The required notification receiver has no complete starter recipe

**Evidence:** `README.md:89` requires configuring a receiver before first admin
sign-in. `README.md:146` labels the section optional, while `README.md:157`
provides only a description of a Home Assistant automation and
`README.md:164` refers to an unspecified alternative receiver.
`app/main.py:541` correctly blocks owner sign-in when no receiver is configured;
`app/notify.py:32` supplies the exact JSON payload.

**Impact:** A Plex owner without an existing webhook automation cannot complete
the quick start from these instructions alone. Home Assistant is optional, but
some suitable receiver is effectively required in the default configuration.

**Suggested fix:** Rename the section to make that distinction clear. Add one
complete Home Assistant UI walkthrough or placeholder automation and one named,
tested non-Home-Assistant setup. Include a delivery test and file ownership
check. Keep real webhook URLs out of commands, logs, and source control, and keep
the owner-code requirement enabled.

### 5. P3: Finding Plex preferences and library keys assumes prior API knowledge

**Evidence:** `README.md:73` asks for an existing `Preferences.xml` without
explaining where typical Plex installations keep it. `README.md:84` directs the
reader to `/library/sections` using a local authenticated client, but does not
name a client or show how to identify the key in the response.
`docker-compose.example.yml:19` and `.env.example:10` deliberately contain
placeholders, and `app/config.py:42` uses the supplied keys without discovery.

**Impact:** Someone familiar only with Plex's web interface must leave the guide
to find the two main pieces of Plex-specific setup information. Guessing the
default `1,2` can select the wrong libraries or no libraries.

**Suggested fix:** Add common Linux/Docker/NAS preferences locations or a precise
Plex reference, plus a worked placeholder library response showing `key`, title,
and type. Provide a local helper or safe procedure that reads the existing
preferences file, authenticates using a header, and prints only library names
and IDs. Do not ask users to paste owner tokens into shell history or URLs.

### 6. P3: HTTPS proxy setup remains a separate, unspecified installation task

**Evidence:** `README.md:51` requires an HTTPS reverse proxy, and
`README.md:104` gives the forwarding destination but no complete configuration.
`README.md:212` requires sanitizing forwarded identity headers.
`Dockerfile:15` trusts forwarded headers from every source, and
`app/main.py:118` prefers `CF-Connecting-IP` over the connection address.

**Impact:** A reader without a working proxy needs additional instructions to
finish sign-in safely. A generic proxy example copied elsewhere may preserve a
client-supplied `CF-Connecting-IP`, undermining the stated address-based limits.
The existing loopback binding and warning are correct; this is a missing recipe,
not a demonstrated remote bypass of the supplied deployment.

**Suggested fix:** Include one tested host-proxy configuration with TLS,
forwarded-header handling, and explicit removal or trusted replacement of the
optional Cloudflare headers. Link a separate example for containerized proxies
with a private shared network and no public app port.

### 7. P3: Docker socket permissions are an unstated prerequisite

**Evidence:** `README.md:40` lists Docker/Compose requirements, but commands at
`README.md:98` assume the current user can access the Docker daemon. The directory
creation command at `README.md:65` already uses `sudo`.

**Reproduction:** The plain Docker command on this host failed with permission
denied at `/var/run/docker.sock`. The same command via `sudo -n` succeeded.

**Impact:** A normal rootful Linux installation with an unprivileged login stops
at the app startup commands despite Docker being installed.

**Suggested fix:** State that the commands require daemon access, and consistently
show the `sudo docker compose ...` variant for rootful installations where needed.
Do not suggest making the Docker socket world-writable.

### 8. P3: The image build resolves an unpinned transitive dependency

**Evidence:** `requirements.txt:1` describes exact pinned versions, and
`Dockerfile:5` installs the requirements without a lock or constraints file.
`requirements.txt:7` pins FastAPI, but the successful fresh build also resolved
`opentelemetry-api>=1.44.0` to `opentelemetry-api==1.45.1`; that package is absent
from the requirements list.

**Impact:** The build works now, but two users building the same source later
can receive different dependency sets. The pinned base image does not make the
Python environment fully reproducible.

**Suggested fix:** Resolve and pin the complete production dependency closure,
or use a checked-in lock/constraints file and validate a fresh image build against
it. Clarify the current requirements comment until the closure is locked.

## Settings coverage

Compared every `env("PK_...")` call in `app/config.py` with the README settings
table and assignments in `.env.example`. All three contain exactly the same
**16 settings**: no missing setting and no documented obsolete setting. The
README's listed application defaults match the implementation; its distinction
between defaults and example deployment values is correct.

| Setting | Code line | Example line | README line | Result |
|---|---:|---:|---:|---|
| `PK_PUBLIC_URL` | 31 | 3 | 129 | Present; example intentionally uses HTTPS |
| `PK_PLEX_PREFS` | 32 | 4 | 130 | Present |
| `PK_PLEX_URL` | 33 | 5 | 131 | Present; placeholder needs replacement |
| `PK_TAUTULLI_URL` | 34 | 19 | 132 | Present; optional |
| `PK_TAUTULLI_CONFIG` | 35 | 20 | 133 | Present; optional |
| `PK_DB` | 36 | 6 | 134 | Present |
| `PK_TZ` | 37 | 7 | 135 | Present |
| `PK_HA_WEBHOOK_FILE` | 38 | 17 | 136 | Present; empty intentionally blocks owner sign-in |
| `PK_POLL_SECONDS` | 39 | 8 | 137 | Present; validation gap in finding 3 |
| `PK_SEED_PARENTS` | 40 | 9 | 138 | Present |
| `PK_KID_SECTIONS` | 42 | 10 | 139 | Present |
| `PK_SECURE_COOKIES` | 43 | 11 | 140 | Present |
| `PK_OWNER_NAME` | 44 | 12 | 141 | Present |
| `PK_START_ENFORCER` | 45 | 13 | 142 | Present |
| `PK_OWNER_CODE` | 47 | 15 | 143 | Present |
| `PK_MEDIA_REQUEST_URL` | 49 | 22 | 144 | Present; unsupported schemes become empty |

Boolean settings implement the documented exact `1` comparison. Parent seed
names are trimmed and lowercased, and only affect first-time shared users
(`app/main.py:515`). Changing the public URL, owner name, or seed list does not
choose the owner account; the owner is determined through Plex
(`app/main.py:499`).

## Security notes checked against implementation

The core security notes match the inspected code, with the operational gaps
above. This is not a full penetration test or a live Plex API compatibility test.

| Documented behavior | Implementation evidence | Assessment |
|---|---|---|
| Read-only Plex file still grants powerful API access | `app/plex.py:129`, `docker-compose.example.yml:21` | Correct; the mount does not limit token permissions |
| Signing key derived once from owner token | `app/main.py:137`, `app/main.py:148` | Correct; restarting after rotation is required |
| Immediate session revocation control | `app/main.py:198` checks the stored session epoch | Consistent with the documented design |
| Parent and child connection tokens are not persisted | `app/main.py:505`, `app/main.py:1288`; database schema in `app/db.py:9` | No persistence of those Plex tokens found; local link tokens are separately stored |
| CSRF checks and bounded request bodies | `app/main.py:83`, `app/main.py:227` | Implemented; oversized POST bodies are bounded before form parsing |
| Secure cookies and security headers by default | `app/config.py:43`, `app/main.py:148`, `app/main.py:155` | Confirmed in the running container |
| Parent access limited to shared, enabled accounts | `app/main.py:205`, `app/main.py:515` | Implemented; shared-user refresh can retain its last known result during outages (`app/service.py:38`) |
| Missing notification setup blocks owner sign-in | `app/main.py:541` | Correct; default failure is explicit, not an accidental bypass |
| Five-minute codes and five incorrect attempts | `app/main.py:69`, `app/main.py:570`, `app/main.py:575` | Matches documentation; pending codes are process-local |
| Notification URL kept in a file | `app/notify.py:17`, `app/notify.py:32` | Read at delivery time; failure message omits the URL and code body |
| Tautulli key sent in request parameters | `app/plex.py:519` | README correctly calls for a private network or HTTPS |
| Forwarded client headers require trusted proxy handling | `Dockerfile:15`, `app/main.py:118`, `app/main.py:552` | Correct warning; a complete setup recipe is still missing |

## Cleanup and rollback

Removed the test container, both temporary network instances, the tagged review
image, and all temporary configuration/database fixtures. The original build's
image ID was already absent after the Compose rebuild; an explicit removal
attempt reported no such image. Final container, image-tag, and network queries
returned no review resources. Shared Docker build cache and preexisting base
layers were not globally pruned.

Only this report is a repository change. Revert its commit to remove the review;
no application rollback or Plex cleanup is needed. No image was pushed, no
branch was merged, and no live deployment was changed.
