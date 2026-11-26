use std::{path::PathBuf, process::Stdio, sync::{Arc, Mutex}, time::{Duration, Instant}};

use axum::{Json, Router, extract::{Query, State}, routing::{get, post}};
use serde::Deserialize;
use serde_json::{Value, json};

use super::{Runtime, RuntimeError, host_policy::{Payload, Reply, blocking, failure, options}};

#[derive(Clone)]
struct Setup {
    runtime: Runtime,
    resources: PathBuf,
    progress: Arc<Mutex<Value>>,
    status_cache: Arc<Mutex<Option<(Instant, Value)>>>,
}

#[derive(Deserialize)]
struct SetupQuery {
    force: Option<String>,
}

pub(super) fn router(runtime: Runtime, resources: PathBuf) -> Router {
    Router::new()
        .route("/api/sandbox/status", get(status).options(options))
        .route("/api/sandbox/setup", post(start).options(options))
        .route("/api/sandbox/setup/status", get(progress).options(options))
        .with_state(Setup { runtime, resources, progress: Arc::new(Mutex::new(initial_progress())),
            status_cache: Arc::new(Mutex::new(None)) })
}

fn initial_progress() -> Value {
    json!({"active":false,"phase":"idle","step_index":0,"step_total":6,"step_title":"","log_tail":[],
        "download_bytes":null,"download_total":null,"error":null,"error_kind":null,"updated_at":super::now()})
}

async fn status(State(setup): State<Setup>, Query(query): Query<SetupQuery>) -> Reply {
    blocking(move || {
        let mut status = setup.status(query.force.as_deref() == Some("1"));
        let progress = setup.progress.lock().map_err(RuntimeError::storage)?;
        status["setup_running"] = progress["active"].clone();
        Ok(json!({"success":true,"data":status}))
    }).await
}

async fn progress(State(setup): State<Setup>) -> Reply {
    blocking(move || {
        let progress = setup.progress.lock().map_err(RuntimeError::storage)?;
        Ok(json!({"success":true,"data":*progress}))
    }).await
}

async fn start(State(setup): State<Setup>, payload: Payload) -> Reply {
    let Ok(Json(body)) = payload else {
        return failure(RuntimeError::bad_request("Expected a JSON object"));
    };
    let enable_if_needed = body["enable_wsl_if_needed"] == true;
    let background = setup.clone();
    blocking(move || {
        let platform = setup.status(false);
        let mut progress = setup.progress.lock().map_err(RuntimeError::storage)?;
        if progress["active"] == true {
            return Err(RuntimeError::new(409, "Sandbox installation is already running"));
        }
        let launcher = setup.installer(enable_if_needed, platform["state"] == "wsl_missing")
            .ok_or_else(|| RuntimeError::new(409, "Automatic sandbox installation is unavailable"))?;
        let handle = tokio::runtime::Handle::try_current().map_err(RuntimeError::storage)?;
        *progress = initial_progress();
        progress["active"] = json!(true);
        progress["phase"] = json!("installing");
        handle.spawn(async move { background.install(launcher).await; });
        Ok(json!({"success":true,"data":*progress}))
    }).await
}

struct Installer {
    install: std::process::Command,
    enable: Option<std::process::Command>,
}

impl Setup {
    fn status(&self, force: bool) -> Value {
        let mut cached = self.status_cache.lock().unwrap_or_else(|error| error.into_inner());
        if !force && let Some((checked, value)) = cached.as_ref() && checked.elapsed() < Duration::from_secs(5) {
            return value.clone();
        }
        let sandbox_available = self.runtime.host_network_permissions_available();
        #[cfg(target_os = "linux")]
        let mut value = crate::process_runtime::linux::sandbox_status();
        #[cfg(windows)]
        let mut value = crate::process_runtime::windows::sandbox_status();
        #[cfg(not(any(target_os = "linux", windows)))]
        let mut value = json!({"applicable":false,"platform":"darwin","state":"not_applicable","detail":""});
        value["sandbox_available"] = json!(sandbox_available && (value["state"] == "ready" || !value["applicable"].as_bool().unwrap_or(false)));
        value["can_install"] = json!(value["state"] != "unsupported" && value["state"] != "ordinary_user_required"
            && self.installer(true, value["state"] == "wsl_missing").is_some());
        *cached = Some((Instant::now(), value.clone()));
        value
    }

    fn installer(&self, _enable_if_needed: bool, _wsl_missing: bool) -> Option<Installer> {
        #[cfg(target_os = "linux")]
        return linux_installer(&self.resources);
        #[cfg(windows)]
        return windows_installer(&self.resources, _enable_if_needed, _wsl_missing);
        #[cfg(not(any(target_os = "linux", windows)))]
        { None }
    }

    async fn install(&self, launcher: Installer) {
        let result = async {
            if let Some(enable) = launcher.enable {
                self.run(enable).await?;
                #[cfg(windows)]
                if !tokio::task::spawn_blocking(crate::process_runtime::windows::host_usable).await.unwrap_or(false) {
                    return Ok(());
                }
            }
            self.run(launcher.install).await
        }.await;
        let checking = self.clone();
        let state = tokio::task::spawn_blocking(move || checking.status(true)).await.unwrap_or(Value::Null);
        let mut progress = self.progress.lock().unwrap_or_else(|error| error.into_inner());
        progress["active"] = json!(false);
        progress["updated_at"] = json!(super::now());
        match result {
            Ok(()) if state["state"] == "ready" => {
                progress["phase"] = json!("done");
                progress["step_index"] = progress["step_total"].clone();
            }
            Ok(()) if cfg!(windows) && state["state"] == "wsl_missing" => {
                progress["phase"] = json!("needs_reboot");
            }
            failure => {
                progress["phase"] = json!("error");
                progress["error_kind"] = json!("script_failed");
                progress["error"] = json!(failure.err().unwrap_or_else(|| "The installed sandbox did not pass its status check".into()));
            }
        }
    }

    async fn run(&self, command: std::process::Command) -> Result<(), String> {
        use tokio::io::{AsyncBufReadExt, BufReader};
        let mut command = tokio::process::Command::from(command);
        let mut child = command.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped())
            .spawn().map_err(|error| error.to_string())?;
        let (stdout, stderr) = (child.stdout.take().unwrap(), child.stderr.take().unwrap());
        let monitor = |mut lines: tokio::io::Lines<_>| async move {
            while let Ok(Some(line)) = lines.next_line().await {
                self.log(&line);
            }
        };
        let stdout = async {
            let mut lines = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = lines.next_line().await { self.log(&line); }
        };
        let (_, _, result) = tokio::join!(stdout, monitor(BufReader::new(stderr).lines()), child.wait());
        if result.map_err(|error| error.to_string())?.success() { Ok(()) }
        else { Err("Sandbox installer exited with an error".into()) }
    }

    fn log(&self, line: &str) {
        let line: String = line.chars().take(1_000).collect();
        let Ok(mut progress) = self.progress.lock() else { return; };
        let logs = progress["log_tail"].as_array_mut().expect("structured sandbox progress");
        if logs.len() >= 80 { logs.remove(0); }
        logs.push(json!(line));
        if let Some((step, title)) = line.strip_prefix("==> [").and_then(|line| line.split_once(']'))
            && let Some((index, total)) = step.split_once('/')
            && let (Ok(index), Ok(total)) = (index.parse::<usize>(), total.parse::<usize>())
        {
            progress["step_index"] = json!(index);
            progress["step_total"] = json!(total);
            progress["step_title"] = json!(title.trim());
        }
        progress["updated_at"] = json!(super::now());
    }
}

#[cfg(target_os = "linux")]
fn linux_installer(resources: &std::path::Path) -> Option<Installer> {
    use std::{fs, os::unix::fs::MetadataExt, path::Path, process::Command};

    if unsafe { libc::getuid() == 0 } { return None; }
    let root = resources.canonicalize().ok()?;
    let trusted = |path: &Path| fs::symlink_metadata(path).ok().is_some_and(|metadata| {
        !metadata.is_symlink() && metadata.uid() == 0 && metadata.mode() & 0o022 == 0
    });
    let script = root.join("scripts/setup-linux-sandbox.sh");
    let support = root.join("modules/linux_sandbox");
    if !script.is_file() || !root.ancestors().all(trusted)
        || ![script.as_path(), script.parent()?, support.as_path(), support.parent()?].into_iter().all(trusted)
        || !fs::read_dir(&support).ok()?.all(|entry| entry.is_ok_and(|entry| trusted(&entry.path())))
        || !Path::new("/usr/bin/sudo").is_file()
    {
        return None;
    }
    let output = Command::new("/usr/bin/id").arg("-un").env_clear().output().ok()?;
    let username = String::from_utf8(output.stdout).ok()?;
    let username = username.trim();
    if !output.status.success() || username.is_empty()
        || !username.bytes().all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.'))
    {
        return None;
    }
    let authorized = Command::new("/usr/bin/sudo").args(["-n", "-l", "/bin/bash"])
        .arg(&script).args(["--user", username]).env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin").output()
        .ok().is_some_and(|output| output.status.success());
    if !authorized { return None; }
    let mut install = Command::new("/usr/bin/sudo");
    install.args(["-n", "/bin/bash"]).arg(script).args(["--user", username]).env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin");
    Some(Installer { install, enable: None })
}

#[cfg(windows)]
fn windows_installer(resources: &std::path::Path, enable_if_needed: bool, wsl_missing: bool) -> Option<Installer> {
    use std::{os::windows::process::CommandExt, process::Command};
    use windows_sys::Win32::System::Threading::CREATE_NO_WINDOW;
    use base64::{Engine as _, engine::general_purpose::STANDARD};
    use crate::process_runtime::windows;

    let script = resources.join("scripts/setup-wsl-sandbox.ps1").canonicalize().ok()?;
    if !script.starts_with(resources.canonicalize().ok()?) || !script.is_file() { return None; }
    let powershell = windows::system_program("WindowsPowerShell/v1.0/powershell.exe")?;
    let distro = windows::distro().ok()?;
    let mut install = Command::new(&powershell);
    install.args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"])
        .arg(script).args(["-DistroName", &distro]).creation_flags(CREATE_NO_WINDOW).env("WSL_UTF8", "1");
    if distro != "astrion-sandbox" {
        install.arg("-InstallDir").arg(std::env::home_dir()?.join(".astrion").join(format!("wsl-sandbox-{distro}")));
    }
    let enable = if wsl_missing {
        if !enable_if_needed { return None; }
        let mut command = Command::new(powershell);
        let enable = "Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux,VirtualMachinePlatform -NoRestart -All -ErrorAction Stop";
        let encoded = STANDARD.encode(enable.encode_utf16().flat_map(u16::to_le_bytes).collect::<Vec<_>>());
        let elevate = format!("$p=Start-Process -FilePath ($PSHOME+'\\powershell.exe') -ArgumentList '-NoProfile','-NonInteractive','-EncodedCommand','{encoded}' -Verb RunAs -Wait -PassThru; exit $p.ExitCode");
        command.args(["-NoProfile", "-NonInteractive", "-Command", &elevate])
            .creation_flags(CREATE_NO_WINDOW);
        Some(command)
    } else {
        None
    };
    Some(Installer { install, enable })
}
