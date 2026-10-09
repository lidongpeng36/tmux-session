use clap::{Parser, ValueEnum};
use std::{
    fs::{self, DirBuilder, File, OpenOptions},
    io::{Read, Write},
    os::unix::{
        fs::{DirBuilderExt, MetadataExt, OpenOptionsExt},
        process::CommandExt,
    },
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};

type Result<T> = std::result::Result<T, String>;
#[derive(Clone, Copy, Debug, ValueEnum)]
enum Mode {
    Configure,
    Tick,
    Restore,
}
#[derive(Parser, Debug)]
#[command(version, about)]
struct Args {
    #[arg(value_enum)]
    mode: Mode,
    #[arg(long)]
    socket: PathBuf,
    #[arg(long)]
    server_pid: u32,
    #[arg(long)]
    launcher: PathBuf,
    #[arg(long, default_value = "tmux")]
    tmux_bin: PathBuf,
    #[arg(long, default_value = "")]
    token: String,
}
struct Config {
    interval: f64,
    save: PathBuf,
    restore: PathBuf,
    directory: PathBuf,
    pending: String,
    restore_pending: String,
    initialized: bool,
    auto_restore: bool,
    start_time: f64,
    legacy_last: f64,
    next: f64,
}
fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}
fn quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
}
fn path(value: &str) -> PathBuf {
    if let Some(tail) = value.strip_prefix("~/") {
        PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(tail)
    } else {
        value.into()
    }
}
fn command(args: &Args, values: &[&str]) -> Result<String> {
    let output = Command::new(&args.tmux_bin)
        .arg("-S")
        .arg(&args.socket)
        .args(values)
        .output()
        .map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).trim().into());
    }
    Ok(String::from_utf8_lossy(&output.stdout)
        .trim_end_matches('\n')
        .into())
}
fn options(args: &Args, values: &[(&str, String)]) -> Result<()> {
    let mut words = Vec::new();
    for (i, (key, value)) in values.iter().enumerate() {
        if i > 0 {
            words.push(";".into());
        }
        words.extend([
            "set-option".into(),
            "-gq".into(),
            (*key).into(),
            value.clone(),
        ]);
    }
    command(args, &words.iter().map(String::as_str).collect::<Vec<_>>())?;
    Ok(())
}
fn state(args: &Args, value: &str) -> Result<()> {
    options(args, &[("@tmux-session-state", value.into())])
}
fn config(args: &Args) -> Result<Config> {
    let fields = [
        "pid",
        "socket_path",
        "@tmux-session-interval",
        "@continuum-save-interval",
        "@resurrect-save-script-path",
        "@resurrect-restore-script-path",
        "@resurrect-dir",
        "@tmux-session-pending",
        "@tmux-session-restore-pending",
        "@tmux-session-initialized",
        "@tmux-session-restore",
        "@continuum-restore",
        "start_time",
        "@continuum-save-last-timestamp",
        "@continuum-boot",
        "@tmux-session-next",
    ];
    let format = fields
        .iter()
        .map(|k| format!("#{{{k}}}"))
        .collect::<Vec<_>>()
        .join("\n")
        + "\nEND";
    let text = command(args, &["display-message", "-p", &format])?;
    let f = text.split('\n').collect::<Vec<_>>();
    if f.len() != fields.len() + 1 || f.last() != Some(&"END") {
        return Err("configuration must not contain newlines".into());
    }
    if f[0].parse::<u32>().ok() != Some(args.server_pid) || Path::new(f[1]) != args.socket {
        return Err("server identity changed".into());
    }
    let raw = if !f[2].is_empty() {
        f[2]
    } else if !f[3].is_empty() {
        f[3]
    } else {
        "15"
    };
    let minutes = raw
        .parse::<f64>()
        .map_err(|_| "invalid autosave interval")?;
    if !minutes.is_finite()
        || !(0.0..=10080.0).contains(&minutes)
        || (minutes > 0.0 && minutes < 1.0 / 60.0)
    {
        return Err("interval must be 0 (off) or 1 second..7 days, in minutes".into());
    }
    let restore = if f[10].is_empty() { f[11] } else { f[10] };
    if !["", "on", "off"].contains(&restore) {
        return Err("restore must be on/off".into());
    }
    if f[14] == "on" {
        return Err("continuum OS autostart is not managed here; use launchd/systemd".into());
    }
    let directory = path(if f[6].is_empty() {
        "~/.tmux/resurrect"
    } else {
        f[6]
    });
    // Isolated/custom servers must opt into their own snapshot directory.
    if args.socket.file_name().is_none_or(|n| n != "default") && f[6].is_empty() {
        return Err("custom server needs an explicit @resurrect-dir".into());
    }
    Ok(Config {
        interval: minutes * 60.0,
        save: path(f[4]),
        restore: path(f[5]),
        directory,
        pending: f[7].into(),
        restore_pending: f[8].into(),
        initialized: f[9] == "yes",
        auto_restore: restore == "on",
        start_time: f[12].parse().unwrap_or(0.0),
        legacy_last: f[13].parse().unwrap_or(0.0),
        next: f[15].parse().unwrap_or(0.0),
    })
}
fn lock_file(file: &Path) -> Result<File> {
    let f = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(file)
        .map_err(|e| e.to_string())?;
    f.lock().map_err(|e| e.to_string())?;
    Ok(f)
}
fn server_lock(args: &Args) -> Result<File> {
    let directory =
        std::env::temp_dir().join(format!("tmux-session-{}", unsafe { libc::getuid() }));
    match DirBuilder::new().mode(0o700).create(&directory) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(e) => return Err(e.to_string()),
    }
    let meta = fs::symlink_metadata(&directory).map_err(|e| e.to_string())?;
    if !meta.is_dir() || meta.uid() != unsafe { libc::getuid() } || meta.mode() & 0o077 != 0 {
        return Err("unsafe runtime directory".into());
    }
    lock_file(&directory.join(format!("{}.lock", args.server_pid)))
}
fn claim(args: &Args, c: &Config) -> Result<File> {
    fs::create_dir_all(&c.directory).map_err(|e| e.to_string())?;
    let lock = lock_file(&c.directory.join(".tmux-session.lock"))?;
    let owner_path = c.directory.join(".tmux-session-owner");
    let mut owner = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(owner_path)
        .map_err(|e| e.to_string())?;
    let mut data = String::new();
    owner.read_to_string(&mut data).map_err(|e| e.to_string())?;
    let parts = data.lines().collect::<Vec<_>>();
    if parts.len() == 2
        && let Ok(pid) = parts[0].parse::<u32>()
        && (pid != args.server_pid || Path::new(parts[1]) != args.socket)
        && Path::new(parts[1]).exists()
        && unsafe { libc::kill(pid as i32, 0) } == 0
    {
        return Err("snapshot directory belongs to another live tmux server".into());
    }
    owner.set_len(0).map_err(|e| e.to_string())?;
    use std::io::Seek;
    owner.rewind().map_err(|e| e.to_string())?;
    write!(owner, "{}\n{}\n", args.server_pid, args.socket.display()).map_err(|e| e.to_string())?;
    Ok(lock)
}
fn schedule(args: &Args, mode: Mode, delay: f64) -> Result<()> {
    let token = format!(
        "{}-{}-{}",
        args.server_pid,
        std::process::id(),
        (now() * 1e6) as u64
    );
    let (verb, key) = match mode {
        Mode::Tick => ("tick", "@tmux-session-pending"),
        Mode::Restore => ("restore", "@tmux-session-restore-pending"),
        Mode::Configure => unreachable!(),
    };
    let words = [
        args.launcher.to_string_lossy().into_owned(),
        verb.into(),
        args.socket.to_string_lossy().into_owned(),
        args.server_pid.to_string(),
        token.clone(),
    ];
    // Escape both shell quoting and tmux's format expansion of run-shell.
    let job = format!(
        "exec {}",
        words.iter().map(|s| quote(s)).collect::<Vec<_>>().join(" ")
    )
    .replace('#', "##");
    let delay = delay.max(0.05).to_string();
    options(args, &[(key, token.clone())])?;
    if let Err(error) = command(args, &["run-shell", "-b", "-d", &delay, &job]) {
        options(args, &[(key, "".into())])?;
        return Err(error);
    }
    if matches!(mode, Mode::Tick) {
        options(
            args,
            &[(
                "@tmux-session-next",
                (now() + delay.parse::<f64>().unwrap()).to_string(),
            )],
        )?;
    }
    Ok(())
}
fn run_script(args: &Args, script: &Path) -> Result<()> {
    if !script.is_file() {
        return Err("tmux-resurrect script unavailable; load resurrect first".into());
    }
    let stopped = Arc::new(AtomicBool::new(false));
    let mut handlers = Vec::new();
    for signal in [libc::SIGTERM, libc::SIGHUP, libc::SIGINT] {
        handlers
            .push(signal_hook::flag::register(signal, stopped.clone()).map_err(|e| e.to_string())?);
    }
    let mut child = Command::new(script)
        .arg("quiet")
        .env(
            "TMUX",
            format!("{},{},0", args.socket.display(), args.server_pid),
        )
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .process_group(0)
        .spawn()
        .map_err(|e| e.to_string())?;
    let status = loop {
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            break status;
        }
        if stopped.load(Ordering::Relaxed)
            || !args.socket.exists()
            || unsafe { libc::kill(args.server_pid as i32, 0) } != 0
        {
            // tmux terminates the job PID, not its children. Own and reap the
            // save/restore process group explicitly on normal server exit.
            unsafe {
                libc::kill(-(child.id() as i32), libc::SIGTERM);
            }
            // Do not reap the child before the final group signal: its PID
            // reserves our process-group identity until cleanup is complete.
            std::thread::sleep(Duration::from_millis(100));
            unsafe {
                libc::kill(-(child.id() as i32), libc::SIGKILL);
            }
            let _ = child.wait();
            for handler in handlers {
                signal_hook::low_level::unregister(handler);
            }
            return Err("save/restore cancelled with tmux shutdown".into());
        }
        std::thread::sleep(Duration::from_millis(50));
    };
    for handler in handlers {
        signal_hook::low_level::unregister(handler);
    }
    if status.success() {
        Ok(())
    } else {
        Err(format!("resurrect exited {status}"))
    }
}
fn work(args: &Args) -> Result<()> {
    let _server_lock = server_lock(args)?;
    let c = config(args)?;
    options(
        args,
        &[("@tmux-session-version", env!("CARGO_PKG_VERSION").into())],
    )?;
    match args.mode {
        Mode::Configure => {
            let _owner = claim(args, &c)?;
            if c.interval > 0.0 && !c.save.is_file() {
                return Err("load tmux-resurrect before tmux-session".into());
            }
            if c.interval > 0.0 && (c.pending.is_empty() || c.next <= now()) {
                let delay = if c.legacy_last > 0.0 {
                    (c.legacy_last + c.interval - now()).clamp(0.05, c.interval)
                } else {
                    c.interval
                };
                schedule(args, Mode::Tick, delay)?;
            }
            if !c.initialized
                && c.auto_restore
                && now() - c.start_time <= 10.0
                && !path("~/tmux_no_auto_restore").exists()
            {
                schedule(args, Mode::Restore, 1.0)?;
            }
            options(args, &[("@tmux-session-initialized", "yes".into())])?;
            state(
                args,
                if c.interval > 0.0 {
                    "scheduled"
                } else {
                    "disabled"
                },
            )?;
        }
        Mode::Tick => {
            if args.token.is_empty() || args.token != c.pending {
                return Ok(());
            }
            options(
                args,
                &[
                    ("@tmux-session-pending", "".into()),
                    ("@tmux-session-next", "".into()),
                ],
            )?;
            if c.interval == 0.0 {
                return state(args, "disabled");
            }
            let result = (|| {
                let _owner = claim(args, &c)?;
                state(args, "saving")?;
                run_script(args, &c.save)
            })();
            // Schedule after completion: slow saves cannot overlap or accumulate.
            schedule(args, Mode::Tick, c.interval)?;
            match result {
                Ok(()) => {
                    options(
                        args,
                        &[
                            ("@tmux-session-last-success", now().to_string()),
                            ("@continuum-save-last-timestamp", (now() as u64).to_string()),
                        ],
                    )?;
                    state(args, "scheduled")?;
                }
                Err(e) => {
                    state(args, "save-failed")?;
                    options(args, &[("@tmux-session-error", e)])?;
                }
            }
        }
        Mode::Restore => {
            if args.token.is_empty() || args.token != c.restore_pending {
                return Ok(());
            }
            options(args, &[("@tmux-session-restore-pending", "".into())])?;
            if c.auto_restore && !path("~/tmux_no_auto_restore").exists() {
                let _owner = claim(args, &c)?;
                run_script(args, &c.restore)?;
            }
        }
    }
    Ok(())
}
fn main() {
    let args = Args::parse();
    if let Err(error) = work(&args) {
        let _ = state(&args, "configuration-error");
        let _ = options(&args, &[("@tmux-session-error", error.clone())]);
        eprintln!("tmux-session: {error}");
        std::process::exit(1);
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn shell_quote_handles_apostrophes() {
        assert_eq!(quote("a'b"), "'a'\\''b'");
    }
}
