# Changelog

All notable changes to Jupydex are documented here.

## 0.5.0 - 2026-09-28

- Bootstrap REST identity before a terminal WebSocket handshake and forward
  server-issued cookies with domain/path/Secure scoping. Preserve explicit
  cookie overrides and diagnose inaccessible terminals without replacing them.
- Add `exec --file PATH` and `exec --file -` to avoid local shell expansion.
  Encode long, multiline, or control-character commands into short physical
  PTY lines and execute through a separate Bash script descriptor, avoiding
  both terminal line truncation and command argument-size limits.
- Require the complete newline-terminated exit marker, including when status
  digits arrive in separate WebSocket frames. Report output truncation and
  unknown timeout outcomes explicitly; do not return unconfirmed command echo.
- Bound handshake and dispatch waits by the command deadline. Authentication
  and other reconnect failures after dispatch now preserve unknown-outcome
  semantics, with no automatic command resubmission or terminal deletion.
- Redact malformed REST JSON and non-JSON WebSocket response bodies.
- Add global `--config PATH`. Token rotation preserves same-server connection
  defaults; a fresh URL token takes precedence over a saved token, and saved
  credentials are not inherited by a different server URL.
- Normalize only whole `/lab` and `/tree` path segments and reject non-finite
  timeout settings.
- Add synthetic identity, framing, recovery, configuration, and real interactive
  PTY regression coverage. Publish a sanitized field-issue review and explicit
  limits of transport recovery in [Reliability notes](docs/reliability-0.5.md).

## 0.4.0 - 2026-08-04

- Added a unified `auto`, `none`, or explicit proxy policy for HTTP REST and
  terminal WebSocket connections.
- Added `JUPYDEX_PROXY`, the global `--proxy` override, and persisted
  `jdx configure --proxy` settings.
- Added the `socks` installation extra for both HTTPX and WebSocket SOCKS
  dependencies.
- Extended `doctor --websocket` to report REST and terminal WebSocket health
  separately without sending terminal input.
- Added structured, redacted proxy remediation and removed upstream response
  bodies from error messages.
- Added proxy, `NO_PROXY`, missing-dependency, handshake, and privacy regression
  tests, including a real loopback WebSocket transport check.

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
