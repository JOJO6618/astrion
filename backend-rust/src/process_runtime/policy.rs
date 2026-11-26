use std::{collections::BTreeSet, fs, io, path::{Path, PathBuf}, process::Command};

use serde_json::Value;
use crate::approval::ExecutionMode;

pub(crate) const DEFAULT_DENY_PATHS: &[&str] = &[
    "~/.ssh", "~/.aws", "~/.azure", "~/.gcp", "~/.google", "~/.kube", "~/.docker",
    "~/.gnupg", "~/.npmrc", "~/.netrc", "~/.pypirc", "~/.git-credentials", "~/.bash_history",
    "~/.zsh_history", "~/.psql_history", "~/.mysql_history", "~/.pgpass", "/Library/Keychains",
    "~/Library/Keychains",
];
pub(crate) const DEFAULT_DENY_REGEX: &str = r"^/.*\.env(\.[^/]*)?$";
pub(crate) const WINDOWS_DENY_PATHS: &[&str] = &[
    "~/.ssh", "~/.aws", "~/.azure", "~/.gcp", "~/.google", "~/.kube", "~/.docker",
    "~/.gnupg", "~/.npmrc", "~/.netrc", "~/.git-credentials",
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Network {
    None,
    Restricted,
    Full,
}

impl Network {
    pub(crate) fn from_metadata(value: &Value) -> io::Result<Self> {
        match value {
            Value::Null => Ok(Self::Restricted),
            Value::String(value) if value == "restricted" => Ok(Self::Restricted),
            Value::String(value) if value == "none" => Ok(Self::None),
            Value::String(value) if value == "full" => Ok(Self::Full),
            _ => Err(denied("Invalid conversation network policy")),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct SandboxAccess {
    pub(crate) write: bool,
    pub(crate) network: Network,
}

#[derive(Clone)]
pub(crate) struct HostPolicy {
    deploy: PathBuf,
    data: PathBuf,
}

fn shell_script(script: Option<&str>) -> &str {
    script.unwrap_or("builtin printf '\\036ASTRION_TERMINAL_READY\\037'; exec /bin/bash --noprofile --norc -s")
}

fn clean_env(mut command: Command, cwd: &Path) -> Command {
    command.env_clear().current_dir(cwd)
        .env("PATH",std::env::var_os("PATH").unwrap_or_else(||"/usr/bin:/bin:/usr/sbin:/sbin".into()))
        .env("GIT_CONFIG_GLOBAL","/dev/null").env("PYTHONUNBUFFERED","1");
    for key in ["HOME","LANG","LC_ALL","LC_CTYPE","TMPDIR","TERM","SystemRoot","WINDIR"] {
        if let Some(value) = std::env::var_os(key) { command.env(key,value); }
    }
    command
}

fn plugin_interpreter(mode: ExecutionMode) -> io::Result<PathBuf> {
    #[cfg(windows)]
    if mode == ExecutionMode::Sandbox {
        return super::windows::plugin_interpreter().map(PathBuf::from);
    }
    let _ = mode;
    let available = |path: &Path| path.is_absolute() && path.is_file();
    if let Some(override_path) = std::env::var_os("ASTRION_CUSTOM_PYTHON").filter(|value|!value.is_empty()) {
        let path = PathBuf::from(override_path);
        return if available(&path) { Ok(path) } else { Err(denied("ASTRION_CUSTOM_PYTHON 必须指向可用的绝对 Python 3 路径")) };
    }
    #[cfg(windows)]
    let candidates = std::env::var_os("PATH").map(|path|std::env::split_paths(&path).map(|dir|dir.join("python.exe"))
        .collect::<Vec<_>>()).unwrap_or_default();
    #[cfg(not(windows))]
    let candidates = ["/opt/homebrew/bin/python3","/usr/local/bin/python3","/usr/bin/python3"].map(PathBuf::from);
    candidates.into_iter().find(|path|available(path))
        .ok_or_else(||denied("缺少自定义工具解释器，请安装 Python 3 或设置 ASTRION_CUSTOM_PYTHON"))
}

enum Launch<'a> { Shell(Option<&'a str>), Fixed(&'a [&'a str]) }

impl HostPolicy {
    pub(crate) fn new(deploy: &Path, data: &Path) -> io::Result<Self> {
        fs::create_dir_all(deploy)?;
        Ok(Self { deploy: deploy.canonicalize()?, data: data.canonicalize()? })
    }

    pub(crate) fn supports_processes(&self) -> bool {
        if std::env::var("HOST_SANDBOX_ENABLED").is_ok_and(|value| {
                matches!(value.trim().to_ascii_lowercase().as_str(), "0" | "false" | "no" | "off")
        }) {
            return false;
        }
        #[cfg(target_os = "linux")]
        return super::linux::helper_installed();
        #[cfg(windows)]
        return super::windows::helper_installed();
        #[cfg(not(any(target_os = "linux", windows)))]
        { cfg!(target_os = "macos") && Path::new("/usr/bin/sandbox-exec").is_file() }
    }

    pub(crate) fn deploy(&self) -> &Path { &self.deploy }

    pub(crate) fn supports_direct(&self) -> bool {
        #[cfg(windows)]
        return super::windows::system_program("cmd.exe").is_some();
        #[cfg(target_os = "linux")]
        return super::linux::ordinary_user() && Path::new("/bin/bash").is_file();
        #[cfg(not(any(windows, target_os = "linux")))]
        { cfg!(target_os = "macos") && Path::new("/bin/bash").is_file() }
    }

    pub(crate) fn terminal_allowed(&self) -> io::Result<bool> {
        terminal_allowed(&self.data)
    }

    pub(crate) fn validate_command(&self, command: &str) -> io::Result<()> {
        let config = blocking_config(&self.data)?;
        let enabled = config["enabled"] == true;
        let rules = strings(config.get("rules"))?;
        let command = command.to_lowercase();
        if enabled && rules.iter().any(|rule| command.contains(&rule.trim().to_lowercase())) {
            return Err(denied("Command is blocked by the current command policy"));
        }
        Ok(())
    }

    pub(crate) fn command(
        &self,
        workspace: &Path,
        cwd: &Path,
        access: SandboxAccess,
        mode: ExecutionMode,
        script: Option<&str>,
        fixed_write_root: Option<&Path>,
    ) -> io::Result<Command> {
        self.launch(workspace,cwd,access,mode,Launch::Shell(script),fixed_write_root)
    }

    pub(super) fn command_plugin(&self, workspace: &Path, cwd: &Path, access: SandboxAccess, mode: ExecutionMode)
        -> io::Result<Command> {
        let python = plugin_interpreter(mode)?;
        let program=python.to_str().ok_or_else(||denied("插件解释器路径必须为 UTF-8"))?;
        let argv = [program,"-I","-X","utf8","-u","-c",include_str!("custom_plugin.py")];
        self.launch(workspace,cwd,access,mode,Launch::Fixed(&argv),None)
    }

    fn launch(
        &self, workspace: &Path, cwd: &Path, access: SandboxAccess, mode: ExecutionMode, launch: Launch<'_>,
        fixed_write_root: Option<&Path>,
    ) -> io::Result<Command> {
        if !(match mode { ExecutionMode::Sandbox => self.supports_processes(), ExecutionMode::Direct => self.supports_direct() }) {
            return Err(denied("The selected Host execution environment is unavailable"));
        }
        if workspace.parent().is_none() || workspace.starts_with(&self.data) || workspace.starts_with(&self.deploy)
            || !cwd.starts_with(workspace) || workspace.canonicalize()? != workspace || cwd.canonicalize()? != cwd
            || !cwd.is_dir() || cwd.starts_with(&self.data) || cwd.starts_with(&self.deploy)
        {
            return Err(denied("The bound execution directory is outside the sandbox scope"));
        }
        if let Some(root) = fixed_write_root
            && (mode != ExecutionMode::Sandbox || !access.write || !root.starts_with(workspace)
                || root.canonicalize()? != root || !root.is_dir())
        {
            return Err(denied("The fixed write directory is outside the sandbox scope"));
        }
        #[cfg(windows)]
        {
            if mode == ExecutionMode::Direct {
                return match launch {
                    Launch::Shell(script) => super::windows::direct(cwd,script),
                    Launch::Fixed(argv) => {
                        let mut command = clean_env(Command::new(argv[0]),cwd);
                        command.args(&argv[1..]);
                        Ok(command)
                    }
                };
            }
            if self.data.starts_with(workspace) || self.deploy.starts_with(workspace) {
                return Err(denied("A work directory containing private application data cannot be mounted"));
            }
            let (_, writable, readable) = self.configured_paths(workspace)?;
            let resolve = |paths: Vec<String>| paths.iter().map(|path| {
                let path = expand(path)?.canonicalize()?;
                if path.starts_with(&self.data) || path.starts_with(&self.deploy)
                    || self.data.starts_with(&path) || self.deploy.starts_with(&path)
                {
                    return Err(denied("A private application directory cannot be mounted"));
                }
                path_text(&path).map(str::to_owned)
            }).collect::<io::Result<Vec<_>>>();
            let writable = if fixed_write_root.is_some() { Vec::new() } else { resolve(writable)? };
            return match launch {
                Launch::Shell(script) => super::windows::command(workspace,cwd,access,shell_script(script),resolve(readable)?,writable,fixed_write_root),
                Launch::Fixed(argv) => super::windows::command_argv(workspace,cwd,access,argv,resolve(readable)?,writable,fixed_write_root),
            };
        }
        #[cfg(not(windows))]
        {
        let shell;
        let argv: &[&str] = match launch {
            Launch::Shell(script) => { shell = ["/bin/bash","--noprofile","--norc","-c",shell_script(script)]; &shell }
            Launch::Fixed(argv) => argv,
        };
        #[cfg(target_os = "linux")]
        if mode == ExecutionMode::Sandbox {
            if self.data.starts_with(workspace) || self.deploy.starts_with(workspace) {
                return Err(denied("A work directory containing private application data cannot be mounted"));
            }
            let (_, writable, readable) = self.configured_paths(workspace)?;
            let expand = |paths: Vec<String>| paths.iter().map(|path| {
                let path = expand(path)?;
                if path.starts_with(&self.data) || path.starts_with(&self.deploy)
                    || self.data.starts_with(&path) || self.deploy.starts_with(&path)
                {
                    return Err(denied("A private application directory cannot be mounted"));
                }
                path_text(&path).map(str::to_owned)
            }).collect::<io::Result<Vec<_>>>();
            let writable = if fixed_write_root.is_some() { Vec::new() } else { expand(writable)? };
            return super::linux::command_argv(workspace, cwd, access, argv, expand(readable)?, writable, fixed_write_root);
        }
        let command = match mode {
            ExecutionMode::Sandbox => {
                let profile = self.profile(workspace, access, fixed_write_root)?;
                let mut command = Command::new("/usr/bin/sandbox-exec");
                command.args(["-p",&profile]).args(argv);
                command
            }
            ExecutionMode::Direct => {
                let mut command = Command::new(argv[0]);
                command.args(&argv[1..]);
                command
            }
        };
        let mut command = clean_env(command,cwd);
        if let Some(root) = fixed_write_root { command.env("TMPDIR", root); }
        Ok(command)
        }
    }

    fn configured_paths(&self, workspace: &Path) -> io::Result<(Value, Vec<String>, Vec<String>)> {
        let policy = read_object(&self.deploy.join("host_sandbox_policy.json"))?;
        let workspaces = match policy.get("workspaces") {
            None => None,
            Some(Value::Object(workspaces)) => Some(workspaces),
            _ => return Err(denied("Invalid workspace paths policy")),
        };
        let local = workspaces.and_then(|paths| workspace.to_str().and_then(|path| paths.get(path)));
        if local.is_some_and(|value| !value.is_object()) {
            return Err(denied("Invalid workspace paths policy"));
        }
        let mut writable = strings(policy.get("macos_writable_paths"))?;
        writable.extend(strings(local.and_then(|paths| paths.get("writable")))?);
        if let Some(paths) = std::env::var_os("HOST_SANDBOX_MACOS_WRITABLE_PATHS") {
            let paths = paths.into_string().map_err(|_| denied("Invalid configured sandbox write paths"))?;
            writable.extend(paths.split(',').map(str::trim).filter(|path| !path.is_empty()).map(str::to_owned));
        }
        let mut readable = strings(policy.get("macos_readable_extra_paths"))?;
        readable.extend(strings(local.and_then(|paths| paths.get("readable_extra")))?);
        readable.extend(writable.iter().cloned());
        Ok((policy, writable, readable))
    }

    fn profile(&self, workspace: &Path, access: SandboxAccess, fixed_write_root: Option<&Path>) -> io::Result<String> {
        let (policy, mut writable, mut readable) = self.configured_paths(workspace)?;
        readable.extend([
            "/bin", "/sbin", "/usr", "/lib", "/System", "/Library/Apple",
            "/Library/Developer/CommandLineTools", "/Library/Preferences", "/Applications",
            "/etc", "/private/etc", "/dev", "/tmp", "/private/tmp", "/var", "/private/var", "/opt/homebrew",
        ].into_iter().map(str::to_owned));
        readable.push(path_text(workspace)?.to_owned());

        let mut profile = String::from("(version 1)\n(deny default)\n(allow sysctl-read)\n(allow process*)\n(allow mach-lookup (global-name \"com.apple.bsd.dirhelper\"))\n");
        let mut literals = BTreeSet::new();
        let mut subpaths = BTreeSet::new();
        for item in readable {
            let path = expand(&item)?;
            for form in std::iter::once(path.clone()).chain(canonical_if_present(&path)?) {
                literals.extend(form.ancestors().skip(1).map(Path::to_owned));
                subpaths.insert(form);
            }
        }
        for path in literals { rule(&mut profile, "allow file-read*", "literal", &path)?; }
        for path in subpaths { rule(&mut profile, "allow file-read*", "subpath", &path)?; }

        match access.network {
            Network::None => (),
            Network::Full => profile.push_str("(allow network-outbound)\n(allow network-inbound)\n"),
            Network::Restricted => profile.push_str(concat!(
                "(allow network-outbound (remote ip \"localhost:*\"))\n",
                "(allow network-bind (local ip \"localhost:*\"))\n",
                "(allow network-inbound (local ip \"localhost:*\"))\n",
            )),
        }
        rule(&mut profile, "allow file-write*", "literal", Path::new("/dev/null"))?;
        if access.write {
            if let Some(root) = fixed_write_root {
                writable = vec![path_text(root)?.to_owned()];
            } else {
                writable.extend([path_text(workspace)?.to_owned(), "/tmp".into(), "/private/tmp".into()]);
                if let Some(temp) = std::env::var_os("TMPDIR") {
                    let temp = PathBuf::from(temp);
                    if let Some(path) = canonical_if_present(&temp)?
                        && path.starts_with("/private/var/folders") && path.components().count() == 7
                        && path.file_name().is_some_and(|name| name == "T")
                        && let Some(parent) = path.parent()
                    {
                        writable.push(path_text(parent)?.to_owned());
                    }
                }
            }
            let mut write_paths = BTreeSet::new();
            for item in writable {
                let path = expand(&item)?;
                write_paths.insert(canonical_if_present(&path)?.unwrap_or(path));
            }
            for path in write_paths {
                rule(&mut profile, "allow file-write*", "subpath", &path)?;
                if access.network == Network::Restricted {
                    let quoted = quote(path_text(&path)?)?;
                    profile.push_str(&format!("(allow network-bind (local unix-socket (subpath {quoted})))\n(allow network-outbound (remote unix-socket (subpath {quoted})))\n"));
                }
            }
        }
        // Apply explicit denies after every allow, including workspace, authorization and temp paths.
        let mut denied_paths = strings(policy.get("macos_deny_read_paths"))?;
        denied_paths.extend(DEFAULT_DENY_PATHS.iter().map(|path| (*path).to_owned()));
        for item in denied_paths {
            let path = expand(&item)?;
            for form in std::iter::once(path.clone()).chain(canonical_if_present(&path)?) {
                rule(&mut profile, "deny file-read*", "subpath", &form)?;
            }
        }
        let mut patterns = strings(policy.get("macos_deny_read_regexes"))?;
        patterns.push(DEFAULT_DENY_REGEX.into());
        for pattern in patterns {
            // SBPL regex literals use raw backslashes. Reject anything that could
            // terminate the literal or consume its closing quote.
            if pattern.contains('"') || pattern.ends_with('\\') || pattern.chars().any(char::is_control) {
                return Err(denied("Unsupported sandbox deny expression"));
            }
            profile.push_str(&format!("(deny file-read* (regex #\"{pattern}\"))\n"));
        }
        let preferences = self.data.join("personalization.json");
        let shared_preferences = match fs::symlink_metadata(&preferences) {
            Ok(info) if info.is_symlink() => Some(preferences.canonicalize()?),
            Ok(_) => None,
            Err(error) if error.kind() == io::ErrorKind::NotFound => None,
            Err(error) => return Err(error),
        };
        for private in [&self.data, &self.deploy] {
            rule(&mut profile, "deny file-read*", "subpath", private)?;
            rule(&mut profile, "deny file-write*", "subpath", private)?;
        }
        if let Some(preferences) = shared_preferences {
            for protected in [preferences.clone(), preferences.with_file_name("command_blocking.json")] {
                let canonical = canonical_if_present(&protected)?;
                for form in std::iter::once(protected).chain(canonical) {
                    rule(&mut profile, "deny file-read*", "literal", &form)?;
                    rule(&mut profile, "deny file-write*", "literal", &form)?;
                }
            }
        }
        Ok(profile)
    }
}

/// Same effective Host UI policy as the authenticated terminal page.
pub(crate) fn terminal_allowed(data: &Path) -> io::Result<bool> {
    let admin = read_object(&data.join("admin_policy.json"))?;
    let mut blocked = false;
    for scope in [&admin["global"], &admin["roles"]["admin"], &admin["invites"][""], &admin["users"]["host"]] {
        if let Some(value) = scope.get("ui_blocks").and_then(|ui| ui.get("block_realtime_terminal")) {
            blocked = value.as_bool().ok_or_else(|| denied("Invalid terminal policy"))?;
        }
    }
    Ok(!blocked)
}

pub(crate) fn blocking_path(data: &Path) -> io::Result<PathBuf> {
    let preferences = data.join("personalization.json");
    let directory = match fs::symlink_metadata(&preferences) {
        Ok(info) if info.is_symlink() => preferences.canonicalize()?.parent()
            .ok_or_else(|| denied("Invalid personalization directory"))?.to_owned(),
        Ok(_) => data.to_owned(),
        Err(error) if error.kind() == io::ErrorKind::NotFound => data.to_owned(),
        Err(error) => return Err(error),
    };
    Ok(directory.join("command_blocking.json"))
}

pub(crate) fn blocking_config(data: &Path) -> io::Result<Value> {
    let patch = blocking_patch(&read_object(&blocking_path(data)?)?)?;
    let mut config = serde_json::json!({"enabled":true,"rules":[]});
    config.as_object_mut().unwrap().extend(patch);
    Ok(config)
}

pub(crate) fn blocking_patch(value: &Value) -> io::Result<serde_json::Map<String, Value>> {
    let fields = value.as_object().ok_or_else(|| denied("Invalid command blocking configuration"))?;
    let mut patch = serde_json::Map::new();
    let mut seen = BTreeSet::new();
    for (name, entry) in fields {
        let value = match name.as_str() {
            "enabled" if entry.is_boolean() => entry.clone(),
            "rules" => {
                let rules = entry.as_array().filter(|rules| rules.len() <= 500)
                    .ok_or_else(|| denied("Invalid command blocking rule list"))?;
                let mut clean = Vec::new();
                for rule in rules {
                    let text = rule.as_str().filter(|text| text.chars().count() <= 1000)
                        .ok_or_else(|| denied("Invalid command blocking rule"))?.trim();
                    if text.contains(['\r', '\n']) { return Err(denied("Each command blocking rule must occupy one line")); }
                    if !text.is_empty() && seen.insert(text.to_lowercase()) { clean.push(text); }
                }
                serde_json::json!(clean)
            }
            _ => return Err(denied("Invalid command blocking configuration")),
        };
        patch.insert(name.clone(), value);
    }
    Ok(patch)
}

pub(crate) fn read_object(path: &Path) -> io::Result<Value> {
    match fs::read(path) {
        Ok(bytes) => {
            let value: Value = serde_json::from_slice(&bytes)?;
            if !value.is_object() { return Err(denied("Invalid process policy")); }
            Ok(value)
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            // A broken policy link cannot stand in for a missing configuration.
            match fs::symlink_metadata(path) {
                Err(missing) if missing.kind() == io::ErrorKind::NotFound => Ok(serde_json::json!({})),
                _ => Err(error),
            }
        }
        Err(error) => Err(error),
    }
}

pub(crate) fn strings(value: Option<&Value>) -> io::Result<Vec<String>> {
    let Some(value) = value else { return Ok(Vec::new()); };
    value.as_array().ok_or_else(|| denied("Invalid process policy list"))?.iter().map(|item| {
        item.as_str().map(str::to_owned).ok_or_else(|| denied("Invalid process policy entry"))
    }).filter(|item| item.as_ref().map_or(true, |text| !text.trim().is_empty())).collect()
}

pub(crate) fn expand(path: &str) -> io::Result<PathBuf> {
    let path = path.trim();
    let expanded = if path == "~" || path.starts_with("~/") {
        let home = std::env::home_dir().filter(|home| home.is_absolute()).ok_or_else(|| denied("Trusted home directory unavailable"))?;
        match path.strip_prefix("~/") { Some(rest) => home.join(rest), None => home }
    } else {
        PathBuf::from(path)
    };
    if !expanded.is_absolute() || expanded.parent().is_none()
        || expanded.components().any(|component| component == std::path::Component::ParentDir)
    {
        return Err(denied("Invalid configured process path"));
    }
    Ok(expanded)
}

fn canonical_if_present(path: &Path) -> io::Result<Option<PathBuf>> {
    match path.canonicalize() {
        Ok(path) => Ok(Some(path)),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

fn path_text(path: &Path) -> io::Result<&str> {
    path.to_str().ok_or_else(|| denied("Sandbox path is not UTF-8"))
}

fn quote(text: &str) -> io::Result<String> {
    if text.chars().any(char::is_control) { return Err(denied("Invalid sandbox path")); }
    Ok(serde_json::to_string(text)?)
}

fn rule(profile: &mut String, operation: &str, matcher: &str, path: &Path) -> io::Result<()> {
    profile.push_str(&format!("({operation} ({matcher} {}))\n", quote(path_text(path)?)?));
    Ok(())
}

pub(super) fn denied(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::PermissionDenied, message)
}
