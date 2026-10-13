# tmux-session

Rust orchestration for tmux-resurrect autosave and optional startup restore on
Linux and macOS. tmux native timers perform the waiting: there is no resident
scheduler and no status-bar shell interpolation.

Load after [tmux-resurrect](https://github.com/tmux-plugins/tmux-resurrect):

```tmux
set -g @plugin 'tmux-plugins/tmux-resurrect'
set -g @plugin 'lidongpeng36/tmux-session'
```

TPM installs the plugin; its entry point automatically downloads the pinned
release for Linux/macOS x86_64 or ARM64, verifies SHA-256, and caches the binary.
After first installation, reloads work offline. No Rust compiler is required.
Keep TPM's install-before-load bootstrap if automatic cloning is required too.

## Defaults and optional configuration

Autosave runs every **15 minutes**, starting after the initial interval. Startup
restore is **off**. Resurrect's manual save/restore bindings, snapshot format,
pane contents, hooks, and Neovim session strategy remain available unchanged.

```tmux
# Optional overrides; omit these to use the defaults.
set -g @tmux-session-interval 15     # minutes; 0 disables autosave
set -g @tmux-session-restore on     # opt into startup restore
```

The existing `@continuum-save-interval` and `@continuum-restore` options are read
when their new counterparts are absent. Do not load continuum simultaneously:
it would add an independent save loop. `@continuum-boot` is not implemented;
use launchd/systemd for OS autostart. An enabled legacy boot option is rejected
rather than silently ignored.

Intervals can be fractional minutes, from one second to seven days. Changes
apply when the pending timer fires; reload does not reset or duplicate the
pending timer. Disabling prevents its next save. A successful save schedules
the next interval after completion, so slow saves cannot overlap. A failed save
records an error and retries after the configured interval.

Automatic restore applies once when first loaded within ten seconds of server
startup, after a one-second native delay. `~/tmux_no_auto_restore` disables it.
For cold installations taking longer than ten seconds, restore manually with
resurrect, then subsequent cached starts can restore automatically.

## Multiple servers and lifecycle

The default socket uses resurrect's existing snapshot directory: `~/.tmux/resurrect`
when that legacy directory exists, otherwise `${XDG_DATA_HOME:-~/.local/share}/tmux/resurrect`.
Explicit directories expand leading `~`, `$HOME` and `$HOSTNAME` without shell evaluation. A custom
socket must set its own `@resurrect-dir`; a directory owned by another live
server is refused. Per-server locking serializes configuration and callbacks;
a separate directory lock protects save/restore operations between servers.
Manual resurrect operations do not acquire this plugin's locks: avoid running
a manual restore while an automatic save is in progress.

The native delayed job is cancelled when the server ends, including when its
last session exits. During save/restore, Rust handles termination and cleans up
its own child process group. Cold-download cancellation and incomplete-cache
cleanup follow the same lifecycle. No GUI session or background OS service is
created by this plugin. Small lock/ownership files persist on disk; they are not
running processes and stale ownership is reclaimed after its server exits.

## Inspection and rollback

```sh
tmux show-environment -gh | grep TMUX_SESSION
```

`TMUX_SESSION_STATE` reports `scheduled`, `saving`, `disabled`, `save-failed`,
`configuration-error`, or `install-failed`. Version, directory, pending token, next
due time, last success and error are hidden global environment values prefixed
`TMUX_SESSION_`. They are not exported to child processes. Configuration remains
in `@tmux-session-*` options. Runtime updates do not use `set-option` and therefore
do not trigger a full client redraw during automatic saves.

On the first invocation after upgrade, legacy runtime options are imported under
the server lock, including a pending timer token and its deadline. A migration
marker prevents empty completed tokens falling back to stale legacy values.
Existing timers call the launcher and use the new binary without adding another
scheduler. Reload retries overdue or interrupted scheduling. Old diagnostic
options remain a historical snapshot; they are no longer updated.

For rollback, disable autosave and let pending work finish before switching to
an older binary; old runtime options may contain stale tokens. Snapshot files
remain compatible. Do not run old and new scheduler binaries simultaneously.

To stop, set `@tmux-session-interval 0` and reload. To revert to continuum,
replace this plugin in the TPM list, keep its interval disabled until the old
pending timer has fired, and reload. Snapshot files remain compatible.

## Development

Rust 1.95+, tmux 3.6+, Python 3.9+; real restoration tests use a pinned resurrect
checkout at `vendor/resurrect` or `TMUX_RESURRECT_PLUGIN`.

```sh
cargo fmt --all -- --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
cargo build --locked --release
python3 tests/installer.py
python3 tests/cold_install_lifecycle.py
python3 tests/migration.py
python3 tests/lifecycle.py
python3 tests/directories.py
python3 tests/restore.py
```

To count idle helper starts against an installed continuum checkout:

```sh
python3 tests/overhead.py --continuum "$HOME/.tmux/plugins/tmux-continuum" --output /tmp/tmux-session-overhead.json
```

The counter uses wrappers in an isolated real attached tmux server. It excludes
startup and records helper starts only; wrapper overhead means it must not be
used to estimate CPU time, latency or energy.

Tests use isolated sockets and temporary snapshot directories, covering reload
idempotence, retries, slow saves, conflicting server ownership, real restore,
and termination during an active save. They do not restore production sessions.

MIT licensed. Resurrect remains an independently installed upstream dependency;
its program-restoration limits and save-format behavior still apply.
