#!/usr/bin/env bash
# Build the declared minimum tmux on ephemeral Linux CI runners.
set -euo pipefail
TMUX_BUILD_ROOT="$(mktemp -d "${RUNNER_TEMP:-/tmp}/tmux-ci.XXXXXX")"
curl --proto '=https' --tlsv1.2 -fsSL https://github.com/tmux/tmux/releases/download/3.6/tmux-3.6.tar.gz -o "$TMUX_BUILD_ROOT/source.tar.gz"
actual="$(sha256sum "$TMUX_BUILD_ROOT/source.tar.gz" | awk '{print $1}')"
test "$actual" = 136db80cfbfba617a103401f52874e7c64927986b65b1b700350b6058ad69607
tar -xzf "$TMUX_BUILD_ROOT/source.tar.gz" -C "$TMUX_BUILD_ROOT"
cd "$TMUX_BUILD_ROOT/tmux-3.6"
./configure --prefix="$TMUX_BUILD_ROOT/prefix"
make -j2
make install
printf '%s\n' "$TMUX_BUILD_ROOT/prefix/bin" >> "$GITHUB_PATH"
