#!/usr/bin/env bash
# Download a pinned release, verify its checksum, then atomically cache it.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(cat "$ROOT/VERSION")"
case "$(uname -s)/$(uname -m)" in
  Darwin/arm64|Darwin/aarch64) TARGET=aarch64-apple-darwin ;;
  Darwin/x86_64) TARGET=x86_64-apple-darwin ;;
  Linux/x86_64) TARGET=x86_64-unknown-linux-musl ;;
  Linux/arm64|Linux/aarch64) TARGET=aarch64-unknown-linux-musl ;;
  *) echo "tmux-session: unsupported OS/architecture" >&2; exit 1 ;;
esac
CACHE="$ROOT/bin"
DEST="$CACHE/tmux-session-v$VERSION-$TARGET"
mkdir -p "$CACHE"
if [ -x "$DEST" ]; then printf '%s\n' "$DEST"; exit 0; fi
LOCK="$CACHE/.install-v$VERSION-$TARGET"
TEMP= DOWNLOAD_PID= WATCHDOG_PID= OWN_LOCK=0
cleanup() {
  if [ -n "$DOWNLOAD_PID" ]; then kill "$DOWNLOAD_PID" 2>/dev/null || true; wait "$DOWNLOAD_PID" 2>/dev/null || true; fi
  if [ -n "$WATCHDOG_PID" ]; then kill "$WATCHDOG_PID" 2>/dev/null || true; wait "$WATCHDOG_PID" 2>/dev/null || true; fi
  if [ -n "$TEMP" ]; then rm -rf "$TEMP"; fi
  if [ "$OWN_LOCK" = 1 ]; then rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null || true; fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
# A cold download lives inside a command substitution, so tmux cannot directly
# supervise every child. Cancel our own downloader when its server disappears.
if [[ "${TMUX_SESSION_SERVER_PID:-}" =~ ^[1-9][0-9]*$ ]]; then
  owner_pid=$$
  (
    while kill -0 "$owner_pid" 2>/dev/null; do
      if ! kill -0 "$TMUX_SESSION_SERVER_PID" 2>/dev/null; then kill -TERM "$owner_pid" 2>/dev/null || true; exit 0; fi
      sleep 0.2
    done
  ) &
  WATCHDOG_PID=$!
fi
for ((attempt=0; attempt<100; attempt++)); do
  if mkdir "$LOCK" 2>/dev/null; then
    OWN_LOCK=1
    printf '%s\n' "$$" > "$LOCK/pid"
    break
  fi
  if [ -x "$DEST" ]; then printf '%s\n' "$DEST"; exit 0; fi
  owner="$(cat "$LOCK/pid" 2>/dev/null || true)"
  # Only remove a stale lock with a recorded, dead numeric owner.
  case "$owner" in
    '') if [ "$attempt" -ge 5 ]; then rmdir "$LOCK" 2>/dev/null || true; fi ;;
    *[!0-9]*) ;;
    *) if ! kill -0 "$owner" 2>/dev/null; then rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null || true; fi ;;
  esac
  sleep 1
done
if [ "${attempt}" -eq 100 ]; then echo "tmux-session: installer lock timed out" >&2; exit 1; fi
TEMP="$(mktemp -d "$CACHE/.download.XXXXXX")"
if [ -x "$DEST" ]; then printf '%s\n' "$DEST"; exit 0; fi
BASE="${TMUX_SESSION_RELEASE_BASE:-https://github.com/lidongpeng36/tmux-session/releases/download/v$VERSION}"
ARCHIVE="tmux-session-v$VERSION-$TARGET.tar.gz"
download() {
  if command -v curl >/dev/null 2>&1; then
    curl --proto '=https' --tlsv1.2 -fsSL --retry 2 --connect-timeout 5 --max-time 60 "$BASE/$1" -o "$TEMP/$1" &
  elif command -v wget >/dev/null 2>&1; then
    wget -q --https-only --timeout=60 --tries=3 "$BASE/$1" -O "$TEMP/$1" &
  else echo 'tmux-session: curl or wget is required for first installation' >&2; return 1; fi
  DOWNLOAD_PID=$!
  wait "$DOWNLOAD_PID"
  DOWNLOAD_PID=
}
download "$ARCHIVE"
download "$ARCHIVE.sha256"
expected="$(awk 'NR==1 {print $1}' "$TEMP/$ARCHIVE.sha256")"
if [[ ! "$expected" =~ ^[[:xdigit:]]{64}$ ]]; then echo 'tmux-session: invalid checksum manifest' >&2; exit 1; fi
if command -v sha256sum >/dev/null 2>&1; then
  actual="$(sha256sum "$TEMP/$ARCHIVE" | awk '{print $1}')"
else actual="$(shasum -a 256 "$TEMP/$ARCHIVE" | awk '{print $1}')"; fi
if [ "$expected" != "$actual" ]; then echo 'tmux-session: checksum mismatch' >&2; exit 1; fi
# Extract only our expected regular binary, not arbitrary archive paths.
tar -xzf "$TEMP/$ARCHIVE" -C "$TEMP" tmux-session
if [ ! -f "$TEMP/tmux-session" ] || [ -L "$TEMP/tmux-session" ]; then echo 'tmux-session: invalid binary archive' >&2; exit 1; fi
chmod 755 "$TEMP/tmux-session"
if [ "$("$TEMP/tmux-session" --version)" != "tmux-session $VERSION" ]; then echo 'tmux-session: release version mismatch' >&2; exit 1; fi
mv -f "$TEMP/tmux-session" "$DEST"
printf '%s\n' "$DEST"
