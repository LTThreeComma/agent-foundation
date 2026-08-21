#!/usr/bin/env bash
set -euo pipefail

backend_pid=""
frontend_pid=""

cleanup() {
  local status=$?
  trap - EXIT INT TERM HUP

  for pid in "$backend_pid" "$frontend_pid"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done
  for pid in "$backend_pid" "$frontend_pid"; do
    if [[ -n "$pid" ]]; then
      wait "$pid" 2>/dev/null || true
    fi
  done

  exit "$status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

uv run --locked foundation-service serve &
backend_pid=$!

npm --prefix apps/foundation-web run dev &
frontend_pid=$!

printf 'Foundation Service: http://127.0.0.1:8000\n'
printf 'Foundation Web:     http://127.0.0.1:5173\n'

while true; do
  for pid in "$backend_pid" "$frontend_pid"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      set +e
      wait "$pid"
      status=$?
      set -e
      exit "$status"
    fi
  done
  sleep 0.2
done
