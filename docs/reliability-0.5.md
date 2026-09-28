# Reliability review: 0.5.0

This release follows a review of repeated operational reports and the client
implementation. This is a sanitized summary, not a publication of private
conversations, endpoints, credentials, command output, or experiments.

## What the reports showed

| Symptom | Finding | Response |
|---|---|---|
| REST works, terminal WebSocket fails or returns 404 | A field workaround reused a server-issued identity cookie. The client forwarded only configured credentials, not its REST cookie jar. | Bootstrap the session and reuse scoped cookies for the handshake. Add an actionable terminal/session diagnostic. |
| Long command hangs with an unfinished quote | Reports warned about physical PTY lines around 4 KB; one-line quoting did not avoid terminal input limits. | Short-line encoded transport, with real interactive PTY regression tests. |
| Variables disappear in nested shell commands | The caller's local shell can expand variables before Jupydex receives them. Transport escaping cannot undo that. | Add UTF-8 `--file` / stdin input and document quoted local here-documents. |
| New URL/token does not restore the connection | Repeated credential rotations and alternate config locations were operational pain points. Review found saved-token precedence and profile-reset defects. | Global config selection; fresh URL-token precedence; preserve same-server defaults; do not carry saved credentials to another server. |
| Control command times out, but training is still alive | Busy terminals, slow remote commands, disconnects, and remote process lifetimes are distinct. | Never retry mutations or kill jobs automatically. Bound connection/dispatch waits and retain unknown-outcome reporting. |
| REST healthy while WebSocket is broken | REST health alone was sometimes treated as evidence of command-channel health. | Retain separate `doctor --websocket` diagnostics and shared proxy policy from 0.4; add session identity handling. |

Additional defects identified during code review, rather than attributed to a
specific historical incident:

- `:1` at the end of one frame could be mistaken for a complete exit code even
  if `23` arrived next. Only a newline-terminated marker is now accepted.
- A failed reconnect with expired authentication could hide that the command
  had already been dispatched. It now reports `RemoteOutcomeUnknownError`.
- Malformed upstream responses and pre-start terminal echo could reveal data
  outside the intended result. Bodies are redacted and command echo is withheld.
- UI path matching could shorten unrelated segments such as `laboratory`.
- NaN/infinite timeouts could bypass positive-number validation.

## Recovery boundaries

The cookie jar lives for one client instance. A server that scopes terminal
ownership to a rotating anonymous identity can still require an explicit,
stable session cookie across separate CLI invocations. Configure the correct
cookie securely; Jupydex does not save server-issued session cookies to disk,
guess another terminal, or silently recreate an inaccessible terminal.

Bootstrap requires REST terminal-list access as well as WebSocket access.
If the server denies that REST endpoint, fix its authorization/proxy policy;
Jupydex does not bypass it. An explicit configured Cookie header takes priority
over learned cookies.

The remote shell must support Bash syntax. Long/multiline commands also need
`base64 -d` and `/dev/fd`. Short commands continue to use `bash -lc`; encoded
scripts use a login Bash reading a separate script descriptor, keeping stdin
available to the command. Encoding prevents transport interpretation, not
disclosure: it is not encryption, and terminal history/scrollback may retain it.

Do not run overlapping commands on the same terminal or inject a new command
while a previous command might still be running. A timeout is not evidence of
termination. Use `watch`, durable operation state, and a separately authorized
control terminal to inspect the real process. The client cannot determine
shell readiness from an arbitrary prompt and does not implement a distributed
terminal lock.

GPU OOM, external signals, checkpoint consistency, scheduler handoff, storage
capacity, and training restarts are **not fixed by this release**. A terminal
gateway is not a persistent job supervisor. Use an independently managed
service with durable logs/checkpoints; verify its exact PID and real activity.
No production workload was restarted as part of this client upgrade.

## Verification

The regression suite uses synthetic HTTP/WS fixtures, a loopback WebSocket
server, and an actual interactive Bash PTY. It covers session cookies,
multi-frame status digits, credential failure after dispatch, deadlines,
redaction, token rotation, literal script input, a line exceeding 12 KB, a
script exceeding the ordinary argument-size limit, and a subsequent command
in the same terminal. No private server is required for CI.

Tests do not prove that every Jupyter deployment or reverse proxy is healthy.
Before a real mutation, run `jdx --config PATH doctor --websocket` and inspect
both REST and WebSocket fields. Keep the existing no-resend recovery contract.

See [Usage](usage.md), [Agent integration](agent-integration.md), and
[Security](../SECURITY.md).
