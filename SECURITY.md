# Security policy

## Supported versions

Jupydex is currently pre-1.0. Security fixes are applied to the latest release
line only.

| Version | Supported |
|---|---|
| Latest release | Yes |
| Older releases | No |

## Threat model

Jupydex is a client for an already-running Jupyter Server. Anyone who can use
the configured Jupyter credential through Jupydex may be able to execute shell
commands with the operating-system permissions of the Jupyter server process.

Jupydex does not provide:

- transport encryption;
- a sandbox;
- command authorization;
- operating-system user separation;
- encrypted local credential storage;
- protection from a compromised Jupyter Server or client machine.

Jupyter Server describes terminal WebSocket access as arbitrary shell
execution and generally equivalent to broad account permissions. Use a
dedicated non-root account and restrict network access to trusted clients.

## Secure deployment baseline

1. Bind Jupyter to loopback or a private network whenever possible.
2. Use HTTPS/WSS, a trusted VPN, or an SSH tunnel.
3. Keep Jupyter authentication enabled.
4. Use a dedicated, least-privileged, non-root OS account.
5. Give each agent a dedicated terminal name.
6. Configure server-side terminal culling appropriate for the workload.
7. Keep Jupyter Server, JupyterLab, the reverse proxy, and Jupydex updated.
8. Back up and test recovery before changing firewall or authentication rules.

See:

- [Jupyter Server security](https://jupyter-server.readthedocs.io/en/stable/operators/security.html)
- [Running a public Jupyter Server](https://jupyter-server.readthedocs.io/en/stable/operators/public-server.html)
- [Installation: recommended SSH tunnel](docs/installation.md#recommended-ssh-tunnel)

## Credential handling

- Prefer `jdx configure` with no URL argument when a copied URL contains a
  token. Both prompts are hidden.
- Never commit `.env`, `config.json`, cookies, tokens, private keys, terminal
  output, or captured command results.
- Jupydex rejects a credential-bearing config file unless its POSIX
  permissions exclude group and other access.
- `doctor` redacts endpoint and path metadata by default.
- `exec` omits the executed command from JSON by default.
- `exec` isolates user commands in a child shell, reconnects the same terminal
  without resending, and retains the terminal when completion is unconfirmed.
- The local config is permission-protected, not encrypted.

If a credential appears in chat, an issue, CI output, terminal transcript,
shell history, screenshot, or Git commit:

1. revoke or rotate it immediately;
2. update every legitimate client;
3. inspect access logs and recent activity;
4. remove the exposed value from current content;
5. rewrite Git history if applicable;
6. do not assume deletion made the old credential safe.

## Reporting a vulnerability

Do not open a public issue containing exploit details or credentials. Use
[GitHub private vulnerability reporting](https://github.com/Cambridger/jupydex/security/advisories/new).

Include:

- Jupydex version or commit;
- Python and Jupyter Server versions;
- affected operating system;
- minimal reproduction;
- expected security impact;
- whether the issue is already public.

Replace all real endpoints, usernames, paths, terminal output, cookies, and
tokens with synthetic values. A maintainer should acknowledge a complete
report within seven days, but no fixed resolution deadline is promised for
this volunteer project.

## Out of scope

- vulnerabilities in Jupyter Server, JupyterLab, Python, `httpx`, or
  `websockets` that Jupydex does not introduce;
- risks inherent to granting shell access to an authenticated Jupyter user;
- attacks that require an already-compromised client or server account;
- public servers intentionally configured without authentication.

Relevant upstream issues should be reported to their respective projects.
