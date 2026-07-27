# Changelog

All notable changes to Jupydex are documented here.

## 0.3.0 - 2026-07-27

- Isolated every `exec` command in a `bash -lc` child shell and captured its
  status through an `errexit`-safe conditional so user or inherited shell
  options and `exit` cannot terminate the completion-marker shell.
- Added defensive handling for empty, binary, non-JSON, and disconnect
  WebSocket frames.
- Added cumulative completion-marker matching across split frames.
- Added three same-terminal reconnect attempts with 1, 2, and 4 second
  backoffs, without resending the remote command.
- Added structured `RemoteOutcomeUnknownError` results and explicit terminal
  retention when completion cannot be confirmed.
- Added atomic remote operation status files and documented safe, idempotent
  validation, stop, deployment, and recovery phases.
- Added regression tests for strict-shell failures, split markers, malformed
  frames, reconnect behavior, terminal retention, and state recovery.

## 0.2.0 - 2026-07-24

- Added an SSH-like interactive terminal with `Ctrl-]` detach.
- Added scriptable `exec`, `watch`, `send`, `interrupt`, and `close` commands.
- Added token and cookie authentication with private configuration storage.
- Added explicit terminal-name validation and safe deletion behavior.
- Redacted runtime connection details and commands by default.
- Added single-line command framing for clean real-terminal output.
- Added release privacy scanning, link checking, tests, and release packaging.
- Added GitHub CI, bilingual documentation, community templates, security
  policy, and MIT license.
