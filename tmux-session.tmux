#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
printf -v command 'exec %q configure "%s" "%s"' "$ROOT/scripts/start.sh" '#{socket_path}' '#{pid}'
tmux run-shell -b "$command"
