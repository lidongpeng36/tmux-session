#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="$1" SOCKET="$2" SERVER_PID="$3"
BIN="$(tmux -S "$SOCKET" show-option -gqv @tmux-session-bin)"
if [ -z "$BIN" ]; then
  if ! BIN="$(TMUX_SESSION_SERVER_PID="$SERVER_PID" "$ROOT/scripts/install.sh")"; then
    tmux -S "$SOCKET" set -gq @tmux-session-state install-failed || true
    exit 1
  fi
fi
ARGS=("$MODE" --socket "$SOCKET" --server-pid "$SERVER_PID" --launcher "$ROOT/scripts/start.sh")
if [ "$#" -ge 4 ]; then ARGS+=(--token "$4"); fi
exec "$BIN" "${ARGS[@]}"
