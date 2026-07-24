# Contributing

Thank you for helping improve Jupydex.

## Before opening an issue

- Search existing issues.
- Remove all private endpoints, usernames, paths, tokens, cookies, terminal
  output, and internal project names.
- Use a minimal synthetic example.
- For security-sensitive reports, follow [SECURITY.md](SECURITY.md) instead of
  opening a public issue.

## Development setup

```bash
git clone https://github.com/Cambridger/jupydex.git
cd jupydex
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

Run the same checks used by CI:

```bash
python -m unittest discover -s tests -v
python tools/check_release.py
python tools/check_links.py
python -m build
```

The WebSocket integration test binds only to loopback and uses synthetic
credentials. The public test suite must never depend on a private Jupyter
server.

## Pull requests

1. Fork the repository and create a focused branch.
2. Make one coherent change.
3. Add or update tests.
4. Update documentation and `CHANGELOG.md` when behavior changes.
5. Run the full validation sequence.
6. Inspect the complete diff for private data.
7. Open a pull request using the repository template.

Keep pull requests small enough to review. Explain behavior and tradeoffs, not
only which files changed.

## Code guidelines

- Support Python 3.10 through 3.13.
- Prefer standard-library features when they keep the client small.
- Keep the CLI JSON contract machine-readable.
- Preserve safe defaults: explicit terminal selection, no implicit process
  killing, redacted diagnostics, and confirmed deletion.
- Raise `ConfigurationError` for invalid local configuration and
  `GatewayError` subclasses for remote transport behavior.
- Add a regression test for every bug fix.
- Avoid adding runtime dependencies without a clear need.

## Security and privacy requirements

Never add real:

- IP addresses or private hostnames;
- access tokens, cookies, passwords, or key material;
- usernames or user-specific absolute paths;
- terminal scrollback, application logs, or command results;
- organization-specific project names.

Use reserved documentation domains, loopback, and synthetic fixtures.

Run:

```bash
python tools/check_release.py
```

The scanner is defense in depth, not proof that a tree is safe. If a real
credential enters Git history, revoke it first. Removing it from the latest
revision is insufficient.

## Documentation

README content should remain useful on GitHub without requiring a separate
website. Relative links must pass:

```bash
python tools/check_links.py
```

Keep English as the canonical technical documentation and update
`README.zh-CN.md` when quick-start behavior changes.

## Community

By participating, you agree to follow
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). General usage questions should
follow [SUPPORT.md](SUPPORT.md).
