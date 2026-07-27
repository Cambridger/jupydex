# Agent integration

Jupydex is designed to be a narrow bridge: the agent chooses a command and a
specific terminal, while Jupydex handles Jupyter authentication, transport,
framing, and terminal output cleanup.

## Recommended operating contract

An agent integration should:

1. use a dedicated terminal name;
2. run `doctor` before the first mutation;
3. prefer `exec` over keystroke-level `send`;
4. parse `result.exit_code` and `result.timed_out`;
5. keep durable job output in remote log files;
6. never infer success from a terminal disappearing;
7. require separate authorization for destructive commands;
8. avoid placing secrets in commands or captured output.

## Parse JSON with jq

```bash
payload=$(jdx exec --timeout 30 -- python -V)

if [ "$(printf '%s' "$payload" | jq -r '.ok')" != "true" ]; then
  printf '%s\n' "$payload" >&2
  exit 2
fi

remote_status=$(printf '%s' "$payload" | jq -r '.result.exit_code')
timed_out=$(printf '%s' "$payload" | jq -r '.result.timed_out')

if [ "$timed_out" = "true" ] || [ "$remote_status" != "0" ]; then
  printf '%s\n' "$payload" >&2
  exit 1
fi
```

See [`examples/run-and-check.sh`](../examples/run-and-check.sh) for a reusable
version.

## Python subprocess example

```python
from __future__ import annotations

import json
import subprocess


completed = subprocess.run(
    ["jdx", "exec", "--timeout", "30", "--", "python", "-V"],
    check=True,
    capture_output=True,
    text=True,
)
payload = json.loads(completed.stdout)
result = payload["result"]

if result["timed_out"]:
    raise TimeoutError("remote command is still running")
if result["exit_code"] != 0:
    raise RuntimeError(result["output"])

print(result["output"])
```

`check=True` catches local Jupydex failures. The explicit `exit_code` check
catches remote command failures.

## Python library API

The Python API is useful when an agent host already has an asyncio event loop:

```python
from __future__ import annotations

import asyncio

from jupydex import JupyterTerminalClient, Settings


async def main() -> None:
    settings = Settings.from_env()
    async with JupyterTerminalClient(settings) as client:
        result = await client.execute(
            "agent_shell",
            "python -V",
            timeout=30,
        )
        if result.timed_out:
            raise TimeoutError("remote command is still running")
        if result.exit_code != 0:
            raise RuntimeError(result.output)
        print(result.output)


asyncio.run(main())
```

The CLI is the more stable public integration surface during the pre-1.0
period. Pin versions if using the Python API.

## Result fields

Typical `exec` result:

| Field | Type | Meaning |
|---|---|---|
| `terminal` | string | Exact selected terminal |
| `output` | string | Cleaned captured output |
| `exit_code` | integer or null | Remote shell status, null on timeout/disconnect |
| `timed_out` | boolean | Local wait deadline expired |
| `elapsed_seconds` | number | Client-side elapsed time |
| `command` | string, opt-in | Present only with `--show-command` |

Do not store the complete payload unless remote output is safe to retain.

## Unknown remote outcomes

Transport failure after dispatch is not a remote exit status. Jupydex retries
the same terminal three times without resending the command. If the completion
marker remains unconfirmed, stderr contains a structured
`RemoteOutcomeUnknownError`:

```json
{
  "ok": false,
  "error": "RemoteOutcomeUnknownError",
  "terminal": "agent_shell",
  "remote_outcome": "unknown",
  "terminal_retained": true,
  "reconnect_attempts": 3
}
```

The integration must treat this as an indeterminate transaction. It must not
translate it to `exit_code != 0`, repeat the command, or delete the terminal.

## Recoverable mutation operations

For a stop, deploy, restart, migration, or similar mutation, create a durable
operation before the first write:

```bash
operation_dir=/workspace/project/logs/jupydex_ops
begin=$(jdx operation begin --directory "$operation_dir")
operation_id=$(printf '%s' "$begin" | jq -r '.result.operation_id')
```

Update checkpoints only after each boundary has been verified:

```bash
jdx operation set --directory "$operation_dir" \
  --id "$operation_id" --state PIDS_VERIFIED
jdx operation set --directory "$operation_dir" \
  --id "$operation_id" --state TERM_SENT
jdx operation set --directory "$operation_dir" \
  --id "$operation_id" --state PROCESSES_STOPPED
```

Every update uses a temporary file plus atomic rename. After any disconnect, a
new read-only call can recover the last confirmed state:

```bash
jdx operation get --directory "$operation_dir" --id "$operation_id"
```

Use separate, idempotent phases:

1. **Validate:** read exact PID files, PPIDs, full commands, working
   directories, application state, checkpoints, and resource ownership. Do not
   mutate anything.
2. **Stop:** revalidate only the exact approved PIDs, send normal `TERM`, and
   verify exit. Report survivors; do not automatically escalate to `KILL` or a
   broad process match.
3. **Deploy and recover:** proceed only when the prior workload is confirmed
   stopped. Deploy tested files, validate versions/configuration, resume from
   the intended checkpoint, and verify the new process tree and resource map.

The status file records a confirmed boundary. If it shows the state before a
mutation, inspect the real system before deciding whether the mutation ran;
never blindly repeat a kill, overwrite, or launch.

## Long-running jobs

Prefer a documented remote launcher that writes:

- one exact PID file;
- one durable log file;
- machine-readable status or completion markers;
- output artifacts in unique, non-overwriting directories.

Then use short read-only calls:

```bash
jdx exec -- tail -n 100 /workspace/project/logs/job.log
jdx exec -- test -f /workspace/project/status/complete.json
```

A Jupydex timeout does not mean the job failed, and a closed terminal does not
mean the job succeeded.

## Terminal ownership

Use a deterministic mapping such as:

```text
agent_<workflow>_<environment>
```

Examples:

```text
agent_docs_dev
agent_release_prod
```

The allowed character set intentionally excludes punctuation. Never reuse an
unknown user terminal simply because its name looks relevant.

## Guardrails for autonomous agents

Recommended policy:

| Action | Default |
|---|---|
| `doctor`, `list`, read-only `exec` | Allow within the configured server scope |
| `create` with a dedicated name | Allow |
| file edits under an approved project root | Require task authorization |
| `interrupt` | Require confirmation of the intended foreground job |
| `close --yes` | Require terminal ownership and process-state checks |
| package installation or service restart | Require explicit authorization |
| deleting data, killing processes, changing firewall/auth | Require explicit authorization |

Jupydex enforces a few mechanical boundaries, but the caller remains
responsible for command authorization and remote-system safety.

## Credential hygiene

- Use `jdx configure` with hidden input for token-bearing URLs.
- Prefer injected environment variables from a secret manager for CI.
- Do not use `--show-config` or `--show-command` in shared logs.
- Redact terminal output before attaching it to issues.
- Rotate a credential after any suspected exposure.
- Do not publish private endpoint names in test fixtures.

Run `python tools/check_release.py` before releasing an integration that embeds
Jupydex examples or captured output.
