#!/usr/bin/env bash
set -euo pipefail

if ! command -v jq >/dev/null 2>&1; then
  echo "This example requires jq." >&2
  exit 2
fi

payload=$(jdx exec --timeout 30 -- python -V)
ok=$(printf '%s' "$payload" | jq -r '.ok')
remote_status=$(printf '%s' "$payload" | jq -r '.result.exit_code')
timed_out=$(printf '%s' "$payload" | jq -r '.result.timed_out')

if [[ "$ok" != "true" ]]; then
  printf '%s\n' "$payload" >&2
  exit 2
fi

if [[ "$timed_out" == "true" ]]; then
  echo "The remote command exceeded the wait timeout and may still be running." >&2
  exit 1
fi

if [[ "$remote_status" != "0" ]]; then
  printf '%s\n' "$payload" >&2
  exit 1
fi

printf '%s\n' "$payload" | jq -r '.result.output'
