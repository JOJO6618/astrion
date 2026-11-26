use std::{io, path::{Path, PathBuf}};

#[cfg(target_os = "macos")]
#[path = "macos.rs"]
mod native;
#[cfg(windows)]
#[path = "windows.rs"]
mod native;

#[derive(Clone, Debug)]
pub(super) struct Application {
    pub path: PathBuf,
    pub label: String,
    pub bundle_id: Option<String>,
    pub is_default: bool,
}

pub(super) trait Platform: Send + Sync {
    fn has_dialogs(&self) -> bool;
    fn pick_folder(&self, prompt: &str) -> io::Result<Option<PathBuf>>;
    fn save_dialog(&self, prompt: &str, filename: &str) -> io::Result<Option<PathBuf>>;
    fn open_path(&self, path: &Path) -> io::Result<()>;
    fn open_url(&self, url: &str) -> io::Result<()>;
    fn applications(&self, path: &Path) -> Vec<Application>;
    fn open_with(&self, file: &Path, app: &Path) -> io::Result<()>;
    fn app_icon(&self, app: &Path) -> io::Result<Vec<u8>>;
}

pub(super) struct System;

impl Platform for System {
    fn has_dialogs(&self) -> bool {
        cfg!(any(target_os = "macos", windows))
    }

    fn pick_folder(&self, prompt: &str) -> io::Result<Option<PathBuf>> {
        native::dialog(prompt, None)
    }

    fn save_dialog(&self, prompt: &str, filename: &str) -> io::Result<Option<PathBuf>> {
        native::dialog(prompt, Some(filename))
    }

    fn open_path(&self, path: &Path) -> io::Result<()> {
        native::open(path.as_os_str())
    }

    fn open_url(&self, url: &str) -> io::Result<()> {
        native::open(url.as_ref())
    }

    fn applications(&self, path: &Path) -> Vec<Application> {
        native::applications(path)
    }

    fn open_with(&self, file: &Path, app: &Path) -> io::Result<()> {
        native::open_with(file, app)
    }

    fn app_icon(&self, app: &Path) -> io::Result<Vec<u8>> {
        #[cfg(target_os = "macos")]
        return native::app_icon(app);
        #[cfg(not(target_os = "macos"))]
        {
            let _ = app;
            Err(unsupported())
        }
    }
}

#[cfg(not(target_os = "macos"))]
fn unsupported() -> io::Error {
    io::Error::new(io::ErrorKind::Unsupported, "Native action unavailable on this platform")
}

#[cfg(not(any(target_os = "macos", windows)))]
mod native {
    use super::{Application, unsupported};
    use std::{ffi::OsStr, io, path::{Path, PathBuf}, process::Command};

    pub(super) fn open(path: &OsStr) -> io::Result<()> {
        super::launch(Command::new("xdg-open").arg(path))
    }

    pub(super) fn dialog(_: &str, _: Option<&str>) -> io::Result<Option<PathBuf>> {
        Err(unsupported())
    }

    pub(super) fn applications(_: &Path) -> Vec<Application> {
        Vec::new()
    }

    pub(super) fn open_with(_: &Path, _: &Path) -> io::Result<()> {
        Err(unsupported())
    }
}

pub(super) fn launch(command: &mut std::process::Command) -> io::Result<()> {
    use std::process::Stdio;
    let mut process = command.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn()?;
    // The opener or the selected application can outlive this HTTP request.
    std::thread::spawn(move || { let _ = process.wait(); });
    Ok(())
}

#[cfg(target_os = "macos")]
fn output(command: &mut std::process::Command, timeout: std::time::Duration) -> io::Result<(std::process::ExitStatus, Vec<u8>)> {
    use std::{io::Read, process::Stdio, thread, time::{Duration, Instant}};
    const LIMIT: u64 = 2 * 1024 * 1024;
    let mut child = command.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn()?;
    let stdout = child.stdout.take().expect("piped stdout");
    let output = thread::spawn(move || {
        let mut bytes = Vec::new();
        stdout.take(LIMIT + 1).read_to_end(&mut bytes)?;
        if bytes.len() as u64 > LIMIT {
            return Err(io::Error::other("Native action output is too large"));
        }
        Ok(bytes)
    });
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(status) = child.try_wait()? {
            let bytes = output.join().map_err(|_| io::Error::other("Native action output interrupted"))??;
            return Ok((status, bytes));
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err(io::Error::new(io::ErrorKind::TimedOut, "Native action timed out"));
        }
        thread::sleep(Duration::from_millis(25));
    }
}
