# Usage

This guide describes every CLI workflow and the behavior that matters when
Jupydex is called by automation.

## Global options

```bash
jdx --version
jdx --pretty doctor
jdx <command> --help
```

`--pretty` formats JSON for humans. Compact JSON is the default for agents.
`JUPYDEX_PRETTY=1` has the same effect.

## Diagnose the connection

```bash
jdx doctor
```

The default result hides the server address, terminal name, remote working
directory, and private CA path:

```json
{
  "ok": true,
  "result": {
    "connected": true,
    "terminal_count": 2,
    "configured_terminal": {
      "configured": true,
      "online": true
    },
    "config": {
      "base_url": "https://<redacted>",
      "authentication": "token",
      "transport_security": "TLS"
    }
  }
}
```

For local troubleshooting only:

```bash
jdx doctor --show-config
```

That option reveals endpoint and path metadata, but never the token or cookie.

## List terminals

```bash
jdx list
```

This returns every terminal visible to the Jupyter credential. Terminal names
can contain project information, so treat this output as potentially
sensitive.

## Create a dedicated session

```bash
jdx create --name agent_shell --cwd /workspace/project
```

Use a unique, stable name. Allowed characters are:

```text
A-Z a-z 0-9 _
```

Jupydex validates the name before network access because Jupyter terminal
routes reject other characters on common server versions.

If `--cwd` is supplied, Jupydex creates the terminal, changes directory inside
that shell, and returns the resulting `pwd` check.

## Execute commands

For ordinary argument lists, put the command after `--`:

```bash
jdx exec -- python -V
jdx exec -- git status --short
jdx exec -- python script.py --input 'value with spaces'
```

Jupydex shell-quotes the arguments before sending them to the dedicated shell.

For pipes, redirection, variables, or shell operators, use `--shell`:

```bash
jdx exec --shell 'tail -n 100 app.log | grep ERROR'
jdx exec --shell 'printf "%s\n" "$PATH" > environment.txt'
```

`--shell` is intentionally arbitrary shell execution. Never interpolate
untrusted text into the shell string.

### Working directory

The precedence is:

1. `jdx exec --cwd`;
2. `JUPYDEX_CWD`;
3. saved `cwd`;
4. the terminal's current directory.

```bash
jdx exec --cwd /workspace/other-project -- pwd
```

Every `exec` command runs inside an independent `bash -lc` child shell. Shell
options such as `set -e`, `exit`, exports, and directory changes affect that
command only; they cannot terminate or mutate the outer terminal shell that
prints Jupydex's completion marker. Use `shell` or deliberate `send` calls when
you specifically need interactive shell state to persist.

### Timeouts

```bash
jdx exec --timeout 10 -- long-command
```

A timeout stops waiting locally but leaves the remote command running:

```json
{
  "exit_code": null,
  "timed_out": true
}
```

To send `Ctrl-C` when the timeout expires:

```bash
jdx exec --timeout 10 --interrupt-on-timeout -- long-command
```

Sending `Ctrl-C` is not a guarantee that the program stops. Confirm the exact
remote process state before taking another action.

### Output limits and ANSI control codes

```bash
jdx exec --max-chars 50000 -- command
jdx exec --raw -- command
```

By default, Jupydex:

- keeps only the latest configured number of characters;
- strips common ANSI control sequences;
- applies carriage-return and backspace behavior;
- removes its private completion markers.

Use `--raw` when exact terminal control bytes are required.

### Command privacy

The JSON result omits the command by default:

```bash
jdx exec -- secret-tool --credential-from-env
```

Only opt in when safe:

```bash
jdx exec --show-command -- python -V
```

Command output may still contain secrets produced by the remote program.

### Disconnect recovery

After a command is dispatched, Jupydex searches accumulated output so a
completion marker split across WebSocket frames is still recognized. Empty,
binary, and non-JSON frames are handled defensively. Non-JSON data is retained
with a `[JUPYDEX_NON_JSON_FRAME]` annotation instead of causing a JSON decoder
crash.

For a transient transport loss, Jupydex reconnects the same named terminal up
to three times with 1, 2, and 4 second delays. It never creates a replacement
terminal and never resends the command. Reconnect scrollback is searched for
the original completion marker.

If completion still cannot be confirmed, the CLI exits locally with code `2`
and reports:

```json
{
  "ok": false,
  "error": "RemoteOutcomeUnknownError",
  "remote_outcome": "unknown",
  "terminal": "agent_shell",
  "terminal_retained": true,
  "reconnect_attempts": 3
}
```

This means the remote command may not have started, may be partially complete,
or may have completed without its marker reaching the client. Do not repeat a
mutation until you inspect its durable state. `exec` never deletes its terminal
automatically; `close --yes` remains a separate explicit action.

## Durable operation state

Use operation status files for stop, deploy, restart, migration, or other
multi-stage mutations. Begin creates a local UUID and atomically writes
`STARTED` on the Jupyter host:

```bash
payload=$(jdx operation begin \
  --directory /workspace/project/logs/jupydex_ops)
operation_id=$(printf '%s' "$payload" | jq -r '.result.operation_id')
```

Read or atomically replace the state:

```bash
jdx operation get \
  --directory /workspace/project/logs/jupydex_ops \
  --id "$operation_id"

jdx operation set \
  --directory /workspace/project/logs/jupydex_ops \
  --id "$operation_id" \
  --state PIDS_VERIFIED
```

States must use uppercase ASCII letters, digits, and underscores. A practical
sequence is:

```text
STARTED
PIDS_VERIFIED
TERM_SENT
PROCESSES_STOPPED
FILES_DEPLOYED
CONTROLLER_RESTARTED
COMPLETE
```

Each update writes a unique temporary file in the status directory and renames
it over `<operation-id>.status`, so readers see an atomic checkpoint. The
checkpoint is the last confirmed boundary, not a substitute for verifying the
real system. If a connection is lost with state `PIDS_VERIFIED`, re-check the
exact PID, PPID, command line, working directory, and application state before
deciding whether `TERM` was sent.

Keep at least three idempotent phases:

1. read-only validation of exact processes, state, metrics, checkpoints, and
   resource ownership;
2. an exact normal stop that revalidates selected PIDs, sends only `TERM`, and
   reports survivors without escalating to broad `pkill` or `KILL`;
3. deployment and recovery only after the old workload is confirmed stopped,
   followed by version, process-tree, and resource-mapping verification.

## Interactive shell

```bash
jdx shell
```

Jupydex:

- switches the local terminal to raw mode;
- forwards keystrokes and resize events;
- prints remote terminal output directly;
- restores local terminal settings on exit.

Press `Ctrl-]` to detach without sending EOF or closing the remote session.
Pressing `Ctrl-D` sends EOF to the remote shell and may end it.

The interactive bridge currently requires a POSIX TTY.

## Watch output

```bash
jdx watch --seconds 2
jdx watch --seconds 10 --max-chars 100000
jdx watch --raw --seconds 1
```

On reconnect, Jupyter may send terminal scrollback. `watch` collects whatever
the server provides during the requested window; it is not a durable log
system. Long-running jobs should also write application logs.

## Send text and keys

```bash
jdx send --text 'echo hello' --enter
jdx send --key tab
jdx send --key ctrl-c
```

Supported keys:

- `ctrl-c`
- `ctrl-d`
- `enter`
- `escape`
- `tab`

`send` writes into the currently selected shell state. Do not use it blindly
when a full-screen program, pager, monitor, or unfinished multiline command may
be active.

## Interrupt

```bash
jdx interrupt
```

This is a convenience wrapper that sends `Ctrl-C` and briefly collects output.
It does not inspect or verify a process identifier.

## Close a terminal

```bash
jdx close --yes
```

Without `--yes`, Jupydex refuses the deletion. It sends DELETE only for the
exact configured or specified terminal name.

Closing the terminal ends that terminal session and may stop foreground or
child processes associated with it. Inspect the session before deletion.

## CLI exit codes

| Local code | Meaning |
|---:|---|
| `0` | Jupydex completed the requested API/transport operation |
| `2` | Configuration, authentication, gateway, or OS error |
| `130` | Local keyboard interruption |

The remote command's status is `result.exit_code`. A remote `false` produces a
successful Jupydex envelope with `exit_code: 1`.

## Troubleshooting

### Authentication redirect

Run `jdx doctor`. Reconfigure with the server base URL and a current token or
cookie. Jupydex disables automatic HTTP redirects so login redirects are
reported rather than silently followed.

### Terminal not found

Check `jdx list`, the exact terminal name, and whether a server-side culler
closed the session. Create a new dedicated terminal if appropriate.

### Name rejected

Replace hyphens or spaces with underscores.

### Output contains an old prompt or scrollback

Jupyter terminal WebSockets can send reconnect history. `exec` discards the
initial reconnect window before submitting a new command; `watch` intentionally
retains the data it receives.

### Timeout but job still running

This is expected. Inspect the job using its own status file, logs, or a verified
PID. Use `--interrupt-on-timeout` only when interruption is authorized.

### Remote outcome unknown

Keep the terminal. Query `jdx operation get`, durable logs, exact PIDs, and
application state through a read-only connection. Never infer failure from the
client exception and never blindly resend a stop, deploy, or restart command.

### Plaintext warning

Do not treat public HTTP/WS like SSH. Configure HTTPS/WSS, a VPN, or the
[SSH tunnel described in the installation guide](installation.md#recommended-ssh-tunnel).
