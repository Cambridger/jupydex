# Support

Jupydex is a small volunteer open-source project.

## Usage questions

Before opening an issue:

1. read [README.md](README.md);
2. check [Installation](docs/installation.md);
3. check [Usage](docs/usage.md);
4. run `jdx doctor`;
5. search existing GitHub issues.

If the question remains, open a
[GitHub issue](https://github.com/Cambridger/jupydex/issues/new/choose) with a
minimal synthetic example.

## What to include

- Jupydex version;
- Python version;
- operating system;
- Jupyter Server or JupyterLab version;
- authentication type (`token`, `cookie`, or `none`) without the credential;
- exact local command with secrets removed;
- sanitized JSON error;
- whether REST and WebSocket behavior differ.

## Never include

- tokens, cookies, passwords, private keys, or complete authorization headers;
- real IP addresses, private hostnames, usernames, or filesystem paths;
- terminal scrollback or logs that have not been reviewed;
- screenshots containing credentials or private infrastructure.

For vulnerabilities, use the private channel in [SECURITY.md](SECURITY.md).
Questions about Jupyter Server administration may be better suited to the
official Jupyter community.
