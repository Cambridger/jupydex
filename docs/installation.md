# Installation

This guide covers client installation, Jupyter Server prerequisites,
authentication, private CAs, and the recommended SSH-tunnel topology.

## Requirements

Client:

- Python 3.10 or newer;
- macOS, Linux, or Windows through WSL for the interactive `shell` command;
- network access to the Jupyter Server endpoint.

Server:

- Jupyter Server or JupyterLab with terminal support enabled;
- an authenticated token or cookie;
- a shell account with only the permissions the automation actually needs.

Jupydex does not need to be installed on the Jupyter host. It runs on the
client and talks to the existing Jupyter Server.

## Install as an isolated tool

### pipx

```bash
pipx install git+https://github.com/Cambridger/jupydex.git
```

Upgrade:

```bash
pipx upgrade jupydex
```

Uninstall:

```bash
pipx uninstall jupydex
```

### uv

```bash
uv tool install git+https://github.com/Cambridger/jupydex.git
```

Upgrade:

```bash
uv tool upgrade jupydex
```

Uninstall:

```bash
uv tool uninstall jupydex
```

## Install in a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install git+https://github.com/Cambridger/jupydex.git
```

On Windows PowerShell, activate with:

```powershell
.\.venv\Scripts\Activate.ps1
```

The noninteractive commands work on native Windows, but `jdx shell` currently
requires a POSIX TTY. Use WSL for the complete experience.

## Install from a source checkout

```bash
git clone https://github.com/Cambridger/jupydex.git
cd jupydex
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Verify:

```bash
jdx --version
jdx --help
```

## Confirm Jupyter terminal support

In JupyterLab, open the Launcher and check that **Terminal** is available.
Server operators can also confirm that the terminal extension is enabled.
JupyterLab terminals run on the server host with the permissions of the
Jupyter server user.

Jupydex needs access to:

- `GET /api/status`;
- `GET`, `POST`, and `DELETE /api/terminals`;
- the terminal WebSocket endpoint.

Reverse proxies must support WebSocket upgrades for terminal connections.

## Configure a token

The safest setup is interactive:

```bash
jdx configure
```

The URL prompt is hidden because a URL copied from Jupyter may contain
`?token=...`. Press Enter at the token prompt to reuse a token found in the
hidden URL.

If your base URL contains no token:

```bash
jdx configure \
  --url 'https://jupyter.example/user/alice' \
  --auth token \
  --terminal agent_shell \
  --cwd /workspace/project
```

Jupydex deliberately rejects a token inside the `--url` argument. Arguments
may be exposed through shell history and process inspection.

## Configure a JupyterHub cookie

```bash
jdx configure \
  --url 'https://jupyter.example/user/alice' \
  --auth cookie \
  --terminal agent_shell
```

Paste the complete `Cookie` header at the hidden prompt. When the cookie
contains `_xsrf`, Jupydex forwards the matching `X-XSRFToken` header.

Cookie authentication is usually more operationally fragile than a dedicated
token because sessions expire. Prefer the authentication method supported by
your Jupyter deployment and rotate credentials regularly.

## Saved configuration

The default file is:

```text
~/.config/jupydex/config.json
```

Jupydex creates the directory with mode `0700` and the file with mode `0600`.
It refuses to load a credential-bearing file readable by group or other users.
The file is permission-protected, not encrypted.

Use another profile:

```bash
export JUPYDEX_CONFIG=/private/path/profile.json
jdx doctor
```

Disable saved-config loading and use only environment variables:

```bash
export JUPYDEX_CONFIG=
export JUPYDEX_URL=https://jupyter.example/user/alice
export JUPYDEX_TOKEN=replace-at-runtime
export JUPYDEX_TERMINAL=agent_shell
jdx doctor
```

For automation, inject real credentials from a secret manager rather than a
committed `.env` file.

## Private certificate authority

Keep certificate verification enabled:

```bash
jdx configure \
  --url 'https://jupyter.internal.example' \
  --ca-bundle /secure/certs/team-ca.pem \
  --terminal agent_shell
```

`--no-verify-tls` exists for controlled diagnostics, but it permits
machine-in-the-middle attacks and should not be used as a permanent fix.

## Recommended SSH tunnel

On the Jupyter host, bind Jupyter Server to loopback. From the client:

```bash
ssh -N \
  -L 127.0.0.1:18888:127.0.0.1:8888 \
  -p 22 user@jupyter-host.example
```

In another terminal:

```bash
jdx configure \
  --url 'http://127.0.0.1:18888' \
  --terminal agent_shell
jdx doctor
```

The local HTTP hop is confined to loopback while SSH encrypts the remote
transport. Jupydex cannot verify that a loopback port is really backed by SSH,
so the diagnostic warning remains intentionally conservative.

Do not close a public Jupyter port until the tunnel is tested and a rollback
path is available.

## Smoke check

These commands do not create a terminal:

```bash
jdx doctor
jdx list
```

Then create a dedicated session and run a harmless command:

```bash
jdx create --name agent_shell
jdx exec -- printf '%s\n' JUPYDEX_OK
```

Expected remote status:

```json
{"exit_code": 0, "timed_out": false}
```

## Common installation problems

### `jdx: command not found`

Ensure the pipx or uv tool bin directory is on `PATH`, or invoke the virtual
environment's executable directly.

### `ModuleNotFoundError`

Do not install with `--no-deps`. Jupydex requires `httpx` and `websockets`.

### Login page or redirect

The configured URL may point to a UI route rather than the server base, or the
credential may have expired. `jdx configure` accepts copied `/lab/...` and
`/tree/...` URLs and normalizes them.

### WebSocket rejected but REST works

Check the reverse proxy's WebSocket upgrade configuration, `Origin` policy,
JupyterHub prefix, and certificate chain. Use `--origin` only when the server
operator has specified the required value.

## Upstream documentation

- [JupyterLab terminals](https://jupyterlab.readthedocs.io/en/stable/user/terminal.html)
- [Jupyter Server REST API](https://jupyter-server.readthedocs.io/en/stable/developers/rest-api.html)
- [Jupyter Server security](https://jupyter-server.readthedocs.io/en/stable/operators/security.html)
- [Running a public Jupyter Server](https://jupyter-server.readthedocs.io/en/stable/operators/public-server.html)
- [uv tool installation](https://docs.astral.sh/uv/guides/tools/)
