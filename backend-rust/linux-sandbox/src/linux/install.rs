//! Install and verify the administrator-owned Linux sandbox helper.

mod seccomp;
mod transaction;

use std::{
    collections::{BTreeMap, BTreeSet},
    env,
    ffi::{CStr, CString},
    fs, io,
    os::{
        fd::AsRawFd,
        unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt, chown},
    },
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::Duration,
};

use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};

use crate::protocol::{
    Network, PROTOCOL_VERSION, RUNTIME_ROOT, Request, RunRequest, denied, invalid,
};

use super::{
    config::{
        CONFIG_PATH, Config, DEFAULT_SOCKET, EXECUTABLE_NAME, INSTALL_ROOT, RUNTIME_DIR,
        SERVICE_NAME, output_timeout, privileged_command, supported_platform, trusted_path,
        wait_timeout,
    },
    install_signal_handlers, random_token, stopping,
};
use transaction::{Installation, SavedFile, TemporaryDirectory, trusted_directory, write_file};

const PROFILE_PATH: &str = "/etc/apparmor.d/astrion-linux-sandbox";
const SERVICE_PATH: &str = "/etc/systemd/system/astrion-sandbox.service";
const PROGRAMS: &[&str] = &[
    "bwrap",
    "systemd-run",
    "systemctl",
    "bpftool",
    "apparmor_parser",
    "setpriv",
];
const SYSTEM_DIRECTORIES: &[&str] = &["/usr/sbin", "/usr/bin", "/sbin", "/bin"];

struct Options {
    user: Option<String>,
    dry_run: bool,
    install_dependencies: bool,
}

impl Options {
    fn parse(args: &[String]) -> io::Result<Self> {
        let mut options = Self {
            user: None,
            dry_run: false,
            install_dependencies: true,
        };
        let mut arguments = args.iter();
        while let Some(argument) = arguments.next() {
            match argument.as_str() {
                "--user" if options.user.is_none() => {
                    let user = arguments
                        .next()
                        .filter(|user| !user.is_empty() && !user.starts_with('-'))
                        .ok_or_else(|| invalid("Missing --user value"))?;
                    options.user = Some(user.clone());
                }
                "--dry-run" => options.dry_run = true,
                "--no-install-dependencies" => options.install_dependencies = false,
                "--install-dependencies" => options.install_dependencies = true,
                _ => return Err(invalid(format!("Unknown installer argument: {argument}"))),
            }
        }
        Ok(options)
    }
}

struct Account {
    name: String,
    uid: u32,
    gid: u32,
}

impl Account {
    fn lookup(user: &str) -> io::Result<Self> {
        let name = CString::new(user).map_err(|_| invalid("Invalid account name"))?;
        let mut passwd = std::mem::MaybeUninit::<libc::passwd>::uninit();
        let mut result = std::ptr::null_mut();
        let mut buffer = vec![0u8; 16_384];
        let uid = user.parse::<u32>().ok();
        loop {
            let length = buffer.len();
            let bytes = buffer.as_mut_ptr().cast();
            // The lookup writes passwd with pointers into buffer on success.
            let status = unsafe {
                if let Some(uid) = uid {
                    libc::getpwuid_r(uid, passwd.as_mut_ptr(), bytes, length, &mut result)
                } else {
                    libc::getpwnam_r(
                        name.as_ptr(),
                        passwd.as_mut_ptr(),
                        bytes,
                        length,
                        &mut result,
                    )
                }
            };
            if status == libc::ERANGE && length < 131_072 {
                buffer.resize(length * 2, 0);
                continue;
            }
            if status != 0 {
                return Err(io::Error::from_raw_os_error(status));
            }
            if result.is_null() {
                return Err(invalid("The selected OS account does not exist"));
            }
            let passwd = unsafe { passwd.assume_init() };
            if passwd.pw_uid == 0 || passwd.pw_uid == u32::MAX {
                return Err(denied("Authorize an ordinary OS account"));
            }
            let name = unsafe { CStr::from_ptr(passwd.pw_name) }
                .to_string_lossy()
                .into_owned();
            return Ok(Self {
                name,
                uid: passwd.pw_uid,
                gid: passwd.pw_gid,
            });
        }
    }
}

fn find_program(program: &str) -> Option<PathBuf> {
    SYSTEM_DIRECTORIES.iter().find_map(|base| {
        let path = Path::new(base).join(program).canonicalize().ok()?;
        trusted_path(&path, false).ok()?;
        (path.metadata().ok()?.mode() & 0o111 != 0).then_some(path)
    })
}

fn dependencies() -> (BTreeMap<&'static str, PathBuf>, Vec<&'static str>) {
    let mut paths = BTreeMap::new();
    let mut missing = Vec::new();
    for &program in PROGRAMS {
        match find_program(program) {
            Some(path) => {
                paths.insert(program, path);
            }
            None => missing.push(program),
        }
    }
    if let Some(bpftool) = paths.get("bpftool") {
        let output = output_timeout(
            privileged_command(bpftool).arg("version"),
            Duration::from_secs(5),
        );
        if !output.is_ok_and(|output| output.status.success()) {
            missing.push("bpftool for the current kernel");
        }
    }
    if seccomp::Library::open().is_err() {
        missing.push("libseccomp2");
    }
    (paths, missing)
}

fn require_runtime() -> io::Result<()> {
    if !Path::new("/run/systemd/system").is_dir()
        || !Path::new("/sys/fs/cgroup/cgroup.controllers").is_file()
    {
        return Err(denied("A booted systemd system with cgroup v2 is required"));
    }
    if !Path::new("/sys/kernel/security/apparmor/profiles").is_file() {
        return Err(denied("An enabled AppArmor kernel is required"));
    }
    Ok(())
}

fn checked(command: &mut Command, seconds: u64) -> io::Result<()> {
    let status = wait_timeout(command.stdin(Stdio::null()), Duration::from_secs(seconds))?;
    if status.success() {
        Ok(())
    } else {
        Err(io::Error::other(format!(
            "{} exited with {status}",
            command.get_program().to_string_lossy()
        )))
    }
}

fn ensure_installing() -> io::Result<()> {
    if stopping() {
        return Err(io::Error::new(
            io::ErrorKind::Interrupted,
            "Sandbox installation interrupted",
        ));
    }
    Ok(())
}

fn install_packages() -> io::Result<()> {
    let apt = find_program("apt-get").ok_or_else(|| invalid("Missing Ubuntu package manager"))?;
    let kernel = fs::read_to_string("/proc/sys/kernel/osrelease")?;
    let kernel = kernel.trim();
    if kernel.is_empty()
        || !kernel
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b".+_-".contains(&byte))
    {
        return Err(invalid("Invalid system kernel release"));
    }
    checked(privileged_command(&apt).arg("update"), 600)?;
    ensure_installing()?;
    checked(
        privileged_command(&apt).args([
            "install",
            "-y",
            "bubblewrap",
            "apparmor-utils",
            "libseccomp2",
            "linux-tools-common",
            &format!("linux-tools-{kernel}"),
        ]),
        1200,
    )
}

fn profile_text() -> String {
    let mut profile = String::new();
    for network in Network::ALL {
        profile.push_str(&format!(
            "profile {} flags=(attach_disconnected,mediate_deleted) {{\n",
            network.profile()
        ));
        profile.push_str(concat!(
            "  / r,\n  /** mrwklix,\n  capability,\n  network,\n  unix,\n",
            "  signal,\n  ptrace,\n  mount,\n  umount,\n  pivot_root,\n",
        ));
        if network != Network::Full {
            profile.push_str("  deny unix (connect, send, receive) peer=(addr=\"@**\"),\n  deny unix (bind) addr=\"@**\",\n");
        }
        profile.push_str("}\n\n");
    }
    profile
}

fn service_text(config: &Config) -> String {
    format!(
        concat!(
            "[Unit]\nDescription=Astrion restricted Linux sandbox helper\n",
            "After=apparmor.service\nRequires=apparmor.service\n\n",
            "[Service]\nType=simple\nExecStart={} serve {}\n",
            "Restart=on-failure\nRestartSec=2\nKillMode=mixed\nTimeoutStopSec=20\nUMask=0077\n",
            "RuntimeDirectory=astrion-sandbox\nRuntimeDirectoryMode=0755\nExecStartPre=/usr/bin/mkdir -p {}\n\n",
            "[Install]\nWantedBy=multi-user.target\n",
        ),
        config.program().display(),
        CONFIG_PATH,
        RUNTIME_DIR
    )
}

fn configured_accounts(existing: &SavedFile, uid: u32) -> io::Result<Vec<u32>> {
    let mut allowed = BTreeSet::from([uid]);
    if let Some(bytes) = existing.bytes() {
        let saved: Value = serde_json::from_slice(bytes)?;
        let entries = saved["allowed_uids"]
            .as_array()
            .ok_or_else(|| invalid("The existing authorized-account list is invalid"))?;
        for uid in entries {
            let uid = uid
                .as_u64()
                .and_then(|value| u32::try_from(value).ok())
                .filter(|uid| *uid != 0 && *uid != u32::MAX)
                .ok_or_else(|| {
                    invalid("The existing authorized-account list has an invalid UID")
                })?;
            allowed.insert(uid);
        }
    }
    Ok(allowed.into_iter().collect())
}

fn verify_install(config: &Config, setpriv: &Path, account: &Account) -> io::Result<()> {
    let directory = TemporaryDirectory::new(Path::new("/var/tmp"), "astrion-sandbox-check")?;
    fs::set_permissions(directory.path(), fs::Permissions::from_mode(0o755))?;
    let workspace = directory.path().join("workspace");
    fs::DirBuilder::new().mode(0o700).create(&workspace)?;
    chown(&workspace, Some(account.uid), Some(account.gid))?;
    let workspace = workspace
        .to_str()
        .ok_or_else(|| invalid("Invalid self-test workspace path"))?
        .to_owned();
    for _ in 0..30 {
        ensure_installing()?;
        if Path::new(&config.socket).exists() {
            break;
        }
        thread::sleep(Duration::from_millis(100));
    }
    for (network, readonly) in [
        (Network::Full, false),
        (Network::Restricted, false),
        (Network::Restricted, true),
        (Network::None, false),
    ] {
        ensure_installing()?;
        let mode = if readonly { "readonly" } else { "writable" };
        let request = Request::Run(RunRequest {
            workspace: workspace.clone(),
            cwd: workspace.clone(),
            reads: vec![],
            writes: vec![],
            readonly,
            workspace_only: true,
            network,
            argv: vec![
                format!("{RUNTIME_ROOT}/uid-exec"),
                "check".to_owned(),
                account.uid.to_string(),
                mode.to_owned(),
            ],
            env: BTreeMap::new(),
            umask: 0o022,
            workspace_write_root: None,
        });
        let encoded = STANDARD.encode(serde_json::to_vec(&request)?);
        let output = output_timeout(
            privileged_command(setpriv)
                .args([
                    &format!("--reuid={}", account.uid),
                    &format!("--regid={}", account.gid),
                    "--init-groups",
                ])
                .arg(config.program())
                .args(["run", "--request", &encoded]),
            Duration::from_secs(40),
        )?;
        if !output.status.success()
            || !serde_json::from_slice::<Value>(&output.stdout)
                .is_ok_and(|check| check["ok"] == true && check["uid"] == account.uid)
        {
            return Err(io::Error::other(format!(
                "Sandbox install check failed ({}, {mode}): {}",
                network.as_str(),
                String::from_utf8_lossy(&output.stderr).trim()
            )));
        }
    }
    Ok(())
}

fn progress(index: usize, title: &str) -> io::Result<()> {
    use std::io::Write;
    ensure_installing()?;
    let mut out = io::stdout().lock();
    writeln!(out, "==> [{index}/6] {title}")?;
    out.flush()
}

pub fn install(args: &[String]) -> io::Result<()> {
    let options = Options::parse(args)?;
    let uid = unsafe { libc::getuid() };
    let effective = unsafe { libc::geteuid() };
    if uid != effective {
        return Err(denied("Run the installer through sudo or pkexec"));
    }
    let user = options
        .user
        .or_else(|| env::var("SUDO_USER").ok())
        .or_else(|| (options.dry_run && uid != 0).then(|| uid.to_string()))
        .ok_or_else(|| invalid("Specify --user with an ordinary operating-system account"))?;
    let account = Account::lookup(&user)?;
    if options.dry_run {
        let (_, missing) = dependencies();
        println!(
            "{}",
            serde_json::to_string_pretty(&json!({
                "supported":supported_platform(), "user":account.name, "uid":account.uid,
                "install_root":INSTALL_ROOT, "socket":DEFAULT_SOCKET, "config":CONFIG_PATH,
                "service":SERVICE_PATH, "profile":PROFILE_PATH, "missing":missing,
                "install_dependencies":options.install_dependencies,
            }))?
        );
        return Ok(());
    }
    if uid != 0 {
        return Err(denied("Run install through sudo or pkexec"));
    }
    if !supported_platform() {
        return Err(denied("The installer supports Ubuntu 24.04"));
    }
    install_signal_handlers()?;
    progress(1, "检查系统与管理员安装条件")?;
    require_runtime()?;

    let install_root = Path::new(INSTALL_ROOT);
    let parent = install_root.parent().expect("installation parent");
    let config_path = Path::new(CONFIG_PATH);
    let config_dir = config_path.parent().expect("configuration directory");
    trusted_directory(config_dir, 0o700)?;
    let lock_path = config_dir.join("install.lock");
    let lock = fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK)
        .open(&lock_path)?;
    trusted_path(&lock_path, false)?;
    if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        return Err(denied("Another sandbox installation is in progress"));
    }

    progress(2, "检查并安装依赖")?;
    if options.install_dependencies {
        install_packages()?;
    }
    let (paths, missing) = dependencies();
    if !missing.is_empty() {
        return Err(invalid(format!(
            "Missing sandbox dependencies: {}",
            missing.join(", ")
        )));
    }
    let help = output_timeout(
        privileged_command(&paths["bwrap"]).arg("--help"),
        Duration::from_secs(5),
    )?;
    let arguments = String::from_utf8_lossy(&help.stdout);
    if !help.status.success()
        || !arguments.contains("--bind-fd")
        || !arguments.contains("--ro-bind-fd")
    {
        return Err(denied("Bubblewrap with FD-based bind mounts is required"));
    }
    let program = |name: &str| {
        paths[name]
            .to_str()
            .map(str::to_owned)
            .ok_or_else(|| invalid("Invalid sandbox dependency path"))
    };

    progress(3, "准备原生特权助手和 seccomp 策略")?;
    trusted_directory(parent, 0o755)?;
    let stage = TemporaryDirectory::new(parent, ".astrion-sandbox-stage")?;
    write_file(
        stage.path().join(EXECUTABLE_NAME).as_path(),
        fs::File::open("/proc/self/exe")?,
        0o755,
    )?;
    let library = seccomp::Library::open()?;
    library.export(&stage.path().join("policy.bpf"), Network::Full)?;
    library.export(&stage.path().join("policy-none.bpf"), Network::None)?;
    fs::set_permissions(stage.path(), fs::Permissions::from_mode(0o755))?;
    let saved_config = SavedFile::read(config_path)?;
    let saved_profile = SavedFile::read(Path::new(PROFILE_PATH))?;
    let saved_service = SavedFile::read(Path::new(SERVICE_PATH))?;
    let config = Config {
        protocol: PROTOCOL_VERSION,
        allowed_uids: configured_accounts(&saved_config, account.uid)?,
        socket: DEFAULT_SOCKET.to_owned(),
        runtime: RUNTIME_DIR.to_owned(),
        install_root: INSTALL_ROOT.to_owned(),
        bwrap: program("bwrap")?,
        systemd_run: program("systemd-run")?,
        systemctl: program("systemctl")?,
        bpftool: program("bpftool")?,
        max_jobs: 16,
        runtime_max_sec: 86_400,
    };
    let profile = profile_text();
    let mut configuration = serde_json::to_vec_pretty(&config)?;
    configuration.push(b'\n');
    let service = service_text(&config);
    let mut transaction = Installation::start(
        install_root,
        &paths["systemctl"],
        &paths["apparmor_parser"],
        [saved_config, saved_profile, saved_service],
    )?;
    let result = (|| {
        progress(4, "安装强制策略与助手服务")?;
        transaction.publish(
            stage.path(),
            &configuration,
            profile.as_bytes(),
            service.as_bytes(),
        )?;
        progress(5, "启动并授权普通系统账户")?;
        transaction.start_service()?;
        progress(6, "以普通账户验收执行与隔离")?;
        verify_install(&config, &paths["setpriv"], &account)?;
        ensure_installing()?;
        transaction.enable_service()?;
        ensure_installing()
    })();
    if let Err(error) = result {
        if let Err(rollback) = transaction.rollback() {
            return Err(io::Error::other(format!(
                "{error}. Rollback encountered: {rollback}"
            )));
        }
        return Err(error);
    }
    println!(
        "{}",
        json!({"phase":"done", "user":account.name, "install_root":INSTALL_ROOT})
    );
    Ok(())
}
