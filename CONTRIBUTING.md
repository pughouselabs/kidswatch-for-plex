# Contributing

Bug reports, focused fixes, documentation improvements, and feature ideas are
welcome. Search existing issues before opening a new one. For larger changes,
open a feature request first so maintainers can discuss the scope with you.
Follow the [code of conduct](CODE_OF_CONDUCT.md).

## Local setup

Use Python 3.12 or later. From your checkout's root directory:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
```

Keep dependencies inside this virtual environment. If you change dependencies,
update the appropriate requirements file and explain the reason in your pull
request.

## Tests

Run the full suite from the repository root:

```sh
python -m pytest -q
```

The tests use fake Plex accounts and temporary databases; they do not need a live
Plex server. Add tests for behavior changes, especially access control, title
restrictions, and screen-time enforcement. GitHub Actions runs the suite on
Python 3.12.

## Development preview

Start the local preview described in [README.md](README.md#development-and-licenses):

```sh
python -m tests.dev_server
```

Open a sample parent or admin session:

- Parent: <http://127.0.0.1:8791/dev-login?u=alex&next=/home>
- Admin: <http://127.0.0.1:8791/dev-login?u=owner&next=/admin>

The preview uses fictional accounts, fake Plex responses, and a temporary
database. It bypasses authentication and binds only to loopback. Never deploy it
or expose it to a network. Stop it with Ctrl+C when finished.

## Style and pull requests

- Match nearby code, use four spaces for Python indentation, and prefer clear
  names and small functions. Keep changes focused on the problem being solved.
- Use US spelling and plain language in documentation and interface text. Avoid
  em dashes.
- Keep scripts and styles in static files to preserve the app's content security
  policy. Give controls accessible names and images useful alternative text.
- Preserve existing settings, database compatibility, and privacy protections.
  Explain any migration or rollback requirements.
- Use fictional data in examples, tests, and screenshots. Never commit Plex
  settings, tokens, notification URLs, databases, or family information.
- Describe the problem, resulting behavior, and test results in your pull
  request. Include screenshots for visible changes and update relevant docs.

Report security vulnerabilities privately as described in
[SECURITY.md](SECURITY.md), rather than in a public issue or pull request.

Contributions are provided under the project's [MIT license](LICENSE). Preserve
third-party license notices when modifying bundled assets.
