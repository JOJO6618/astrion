use std::{
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt},
    path::{Path, PathBuf},
    time::Duration,
};

use crate::protocol::{Network, invalid};

use super::{
    SERVICE_NAME, checked, output_timeout, privileged_command, random_token, trusted_path,
};

const CONFIG_LIMIT: u64 = 1_048_576;

pub(super) fn trusted_directory(path: &Path, mode: u32) -> io::Result<()> {
    match trusted_path(path, true) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            let parent = path
                .parent()
                .ok_or_else(|| invalid("Invalid installation directory"))?;
            trusted_directory(parent, 0o755)?;
            match fs::DirBuilder::new().mode(mode).create(path) {
                Ok(()) => (),
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => (),
                Err(error) => return Err(error),
            }
            trusted_path(path, true)
        }
        Err(error) => Err(error),
    }
}

pub(super) struct TemporaryDirectory(PathBuf);

impl TemporaryDirectory {
    pub(super) fn new(parent: &Path, prefix: &str) -> io::Result<Self> {
        let path = parent.join(format!("{prefix}-{}", random_token()?));
        fs::DirBuilder::new().mode(0o700).create(&path)?;
        Ok(Self(path))
    }

    pub(super) fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TemporaryDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

pub(super) fn write_file(path: &Path, mut source: impl Read, mode: u32) -> io::Result<()> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(mode)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)?;
    io::copy(&mut source, &mut file)?;
    file.flush()?;
    file.set_permissions(fs::Permissions::from_mode(mode))?;
    file.sync_all()
}

fn publish_file(path: &Path, bytes: &[u8], mode: u32) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| invalid("Missing parent for installed file"))?;
    trusted_path(parent, true)?;
    match trusted_path(path, false) {
        Ok(()) => (),
        Err(error) if error.kind() == io::ErrorKind::NotFound => (),
        Err(error) => return Err(error),
    }
    let temporary = parent.join(format!(".astrion-sandbox-new-{}", random_token()?));
    let result = (|| {
        write_file(&temporary, bytes, mode)?;
        fs::rename(&temporary, path)?;
        File::open(parent)?.sync_all()
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

pub(super) struct SavedFile {
    path: PathBuf,
    data: Option<(Vec<u8>, u32)>,
    touched: bool,
}

impl SavedFile {
    pub(super) fn read(path: &Path) -> io::Result<Self> {
        let data = match trusted_path(path, false) {
            Ok(()) => {
                let file = OpenOptions::new()
                    .read(true)
                    .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
                    .open(path)?;
                let mode = file.metadata()?.mode() & 0o777;
                let mut bytes = Vec::new();
                file.take(CONFIG_LIMIT + 1).read_to_end(&mut bytes)?;
                if bytes.len() as u64 > CONFIG_LIMIT {
                    return Err(invalid(format!(
                        "Existing installation file is too large: {}",
                        path.display()
                    )));
                }
                Some((bytes, mode))
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => None,
            Err(error) => return Err(error),
        };
        Ok(Self {
            path: path.to_owned(),
            data,
            touched: false,
        })
    }

    pub(super) fn bytes(&self) -> Option<&[u8]> {
        self.data.as_ref().map(|(bytes, _)| bytes.as_slice())
    }

    fn publish(&mut self, bytes: &[u8], mode: u32) -> io::Result<()> {
        self.touched = true;
        publish_file(&self.path, bytes, mode)
    }

    fn restore(&self) -> io::Result<()> {
        if !self.touched {
            return Ok(());
        }
        if let Some((bytes, mode)) = &self.data {
            return publish_file(&self.path, bytes, *mode);
        }
        match fs::remove_file(&self.path) {
            Ok(()) => File::open(self.path.parent().expect("installed file parent"))?.sync_all(),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(error),
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Enablement {
    Persistent,
    Runtime,
    Absent,
}

pub(super) struct Installation<'a> {
    target: &'a Path,
    systemctl: &'a Path,
    parser: &'a Path,
    files: [SavedFile; 3],
    parent: &'a Path,
    backup: Option<PathBuf>,
    published_directory: bool,
    stopped_old: bool,
    started_new: bool,
    attempted_enable: bool,
    old_active: bool,
    old_enabled: Enablement,
}

impl<'a> Installation<'a> {
    pub(super) fn start(
        target: &'a Path,
        systemctl: &'a Path,
        parser: &'a Path,
        files: [SavedFile; 3],
    ) -> io::Result<Self> {
        let inspect = |arg| -> io::Result<String> {
            let output = output_timeout(
                privileged_command(systemctl).args([arg, SERVICE_NAME]),
                Duration::from_secs(10),
            )?;
            Ok(String::from_utf8_lossy(&output.stdout).trim().to_owned())
        };
        let old_enabled = match inspect("is-enabled")?.as_str() {
            "enabled" => Enablement::Persistent,
            "enabled-runtime" => Enablement::Runtime,
            "disabled" | "static" | "indirect" | "not-found" | "" => Enablement::Absent,
            state => {
                return Err(invalid(format!(
                    "Unsupported sandbox service enablement: {state}"
                )));
            }
        };
        let old_active = match inspect("is-active")?.as_str() {
            "active" | "reloading" => true,
            "inactive" | "failed" | "unknown" => false,
            state => {
                return Err(invalid(format!(
                    "Unexpected sandbox service state: {state}"
                )));
            }
        };
        let parent = target
            .parent()
            .ok_or_else(|| invalid("Missing sandbox installation parent"))?;
        Ok(Self {
            target,
            systemctl,
            parser,
            files,
            parent,
            backup: None,
            published_directory: false,
            stopped_old: false,
            started_new: false,
            attempted_enable: false,
            old_active,
            old_enabled,
        })
    }

    fn systemctl(&self, args: &[&str], seconds: u64) -> io::Result<()> {
        checked(privileged_command(self.systemctl).args(args), seconds)
    }

    pub(super) fn publish(
        &mut self,
        stage: &Path,
        config: &[u8],
        profile: &[u8],
        service: &[u8],
    ) -> io::Result<()> {
        let had_previous = match trusted_path(self.target, true) {
            Ok(()) => true,
            Err(error) if error.kind() == io::ErrorKind::NotFound => false,
            Err(error) => return Err(error),
        };
        if self.old_active {
            self.stopped_old = true;
            self.systemctl(&["stop", SERVICE_NAME], 25)?;
        }
        if had_previous {
            let backup = self
                .parent
                .join(format!("astrion-sandbox.backup-{}", random_token()?));
            fs::rename(self.target, &backup)?;
            self.backup = Some(backup);
        }
        File::open(stage)?.sync_all()?;
        fs::rename(stage, self.target)?;
        self.published_directory = true;
        File::open(self.parent)?.sync_all()?;
        self.files[0].publish(config, 0o600)?;
        self.files[1].publish(profile, 0o644)?;
        self.files[2].publish(service, 0o644)?;
        checked(
            privileged_command(self.parser)
                .args(["--skip-cache", "-r"])
                .arg(&self.files[1].path),
            40,
        )
    }

    pub(super) fn start_service(&mut self) -> io::Result<()> {
        self.systemctl(&["daemon-reload"], 40)?;
        self.started_new = true;
        self.systemctl(&["start", SERVICE_NAME], 40)
    }

    pub(super) fn enable_service(&mut self) -> io::Result<()> {
        if self.old_enabled == Enablement::Persistent {
            return Ok(());
        }
        self.attempted_enable = true;
        self.systemctl(&["enable", SERVICE_NAME], 40)
    }

    pub(super) fn rollback(&self) -> io::Result<()> {
        if self.started_new {
            self.systemctl(&["stop", SERVICE_NAME], 25)?;
        }
        let mut failures = Vec::new();
        let mut report = |result: io::Result<()>| {
            if let Err(error) = result {
                failures.push(error.to_string());
            }
        };
        if self.attempted_enable {
            report(self.systemctl(&["disable", SERVICE_NAME], 25));
        }
        let profile = &self.files[1];
        if profile.touched && profile.data.is_none() {
            report(
                fs::read_to_string("/sys/kernel/security/apparmor/profiles").and_then(|loaded| {
                    if Network::ALL.iter().any(|network| {
                        loaded
                            .lines()
                            .any(|line| line.starts_with(&format!("{} (", network.profile())))
                    }) {
                        checked(
                            privileged_command(self.parser)
                                .args(["--skip-cache", "-R"])
                                .arg(&profile.path),
                            25,
                        )
                    } else {
                        Ok(())
                    }
                }),
            );
        }
        if self.published_directory {
            let failed = self
                .parent
                .join(format!("astrion-sandbox.failed-{}", random_token()?));
            fs::rename(self.target, failed)?;
        }
        if let Some(backup) = &self.backup {
            fs::rename(backup, self.target)?;
        }
        if self.published_directory || self.backup.is_some() {
            report(File::open(self.parent).and_then(|dir| dir.sync_all()));
        }
        for file in &self.files {
            report(file.restore());
        }
        if profile.touched && profile.data.is_some() {
            report(checked(
                privileged_command(self.parser)
                    .args(["--skip-cache", "-r"])
                    .arg(&profile.path),
                40,
            ));
        }
        if self.files[2].touched {
            report(self.systemctl(&["daemon-reload"], 40));
        }
        if self.attempted_enable && self.old_enabled == Enablement::Runtime {
            report(self.systemctl(&["enable", "--runtime", SERVICE_NAME], 40));
        }
        if self.stopped_old {
            report(self.systemctl(&["start", SERVICE_NAME], 40));
        }
        if failures.is_empty() {
            Ok(())
        } else {
            Err(io::Error::other(failures.join(". ")))
        }
    }
}
