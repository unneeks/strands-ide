#!/usr/bin/env bash
# Start both the AgentCore agent runtime and the IDE.
# They are deliberately separate processes:
#   - The agent runtime (agent/) is "the server" — the AgentCore app the dev brings.
#   - The IDE (ide/) invokes that runtime over HTTP at http://localhost:8080.
#
# Usage:  ./run.sh            (foreground, logs interleaved with prefixes)
#         ./run.sh --bg      (background)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Pick a python. Prefer the project venv if present, else fall back to `python`.
if [ -x ".venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi

# Shared workspace the agent and IDE operate on. Override with WORKSPACE_DIR.
export WORKSPACE_DIR="${WORKSPACE_DIR:-$ROOT/workspace}"

# Default ports (override via env).
export AGENT_PORT="${AGENT_PORT:-8080}"
export IDE_PORT="${IDE_PORT:-3000}"
export AGENT_URL="${AGENT_URL:-http://localhost:$AGENT_PORT}"

BG=0
[ "${1:-}" = "--bg" ] && BG=1

cleanup() {
  pkill -f "agent.agent" 2>/dev/null || true
  pkill -f "ide.server" 2>/dev/null || true
}
trap cleanup INT TERM

start() {
  local name="$1"; shift
  if [ "$BG" = "1" ]; then
    "$@" >"$ROOT/.${name}.log" 2>&1 &
    echo "[start] $name -> background (log: .$name.log, pid $!)"
  else
    "$@" 2>&1 | sed "s/^/[$name] /" &
    echo "[start] $name -> foreground (pid $!)"
  fi
}

start agent     "$PY" -m agent.agent
sleep 2   # let the runtime come up before the IDE starts polling it
start ide       "$PY" -m ide.server

if [ "$BG" = "1" ]; then
  echo
  echo "Agent runtime: http://localhost:$AGENT_PORT   (POST /invocations, GET /ping)"
  echo "IDE:           http://localhost:$IDE_PORT"
  echo "Stop with:     pkill -f 'agent.agent'; pkill -f 'ide.server'"
else
  wait
fi
