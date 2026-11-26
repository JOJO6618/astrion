use std::{
    env,
    ffi::{OsStr, OsString},
    io::{self, Read},
    path::Path,
    process::{Command, ExitStatus, Stdio},
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};

use super::discovery::Repository;
use super::{GitError, GitResult};

const TIME_LIMIT: Duration = Duration::from_secs(15);
const OUTPUT_LIMIT: usize = 32 * 1024 * 1024;
const ERROR_LIMIT: usize = 2_000;

#[cfg(windows)]
pub(crate) const NULL_DEVICE: &str = "NUL";
#[cfg(not(windows))]
pub(crate) const NULL_DEVICE: &str = "/dev/null";

pub(super) fn top_level(path: &Path) -> GitResult<Vec<u8>> {
    Git::new(path).read(["rev-parse", "--show-toplevel"])
}

impl Repository {
    pub(super) fn git(&self) -> Git {
        let mut git = Git::new(&self.path);
        // Bind object queries to the validated metadata and omit working-tree
        // and index state, including uncommitted attributes.
        git.0.arg("--git-dir").arg(&self.git_dir).arg("--bare");
        git
    }
}

pub(crate) struct Git(Command, Duration);

impl Git {
    pub(crate) fn new(path: &Path) -> Self {
        let mut command = Command::new("git");
        for (key, _) in env::vars_os() {
            if key
                .to_string_lossy()
                .get(..4)
                .is_some_and(|prefix| prefix.eq_ignore_ascii_case("GIT_"))
            {
                command.env_remove(key);
            }
        }
        command
            .env("GIT_OPTIONAL_LOCKS", "0")
            .env("GIT_TERMINAL_PROMPT", "0")
            .env("GIT_LITERAL_PATHSPECS", "1")
            .env("GIT_CONFIG_GLOBAL", NULL_DEVICE)
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .env("GIT_NO_REPLACE_OBJECTS", "1")
            .env("GIT_GRAFT_FILE", NULL_DEVICE)
            .env("GIT_NO_LAZY_FETCH", "1")
            .env("GIT_ALLOW_PROTOCOL", "")
            .env("GIT_ATTR_NOSYSTEM", "1")
            .arg("--no-pager")
            .args([
                "-c",
                "core.quotePath=false",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "color.ui=false",
            ])
            .args([
                "-c",
                "log.showSignature=false",
                "-c",
                "diff.suppressBlankEmpty=false",
            ])
            .arg("-c")
            .arg(prefixed("core.attributesFile=", NULL_DEVICE))
            .arg("-c")
            .arg(prefixed("core.hooksPath=", NULL_DEVICE))
            .args(["-c", "submodule.recurse=false"])
            .arg("-c")
            .arg(prefixed("safe.directory=", path))
            .arg("-C")
            .arg(path)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        Self(command, TIME_LIMIT)
    }

    pub(crate) fn with_timeout(mut self, limit: Duration) -> Self {
        self.1 = limit;
        self
    }

    pub(crate) fn with_config(
        mut self,
        config: impl IntoIterator<Item = impl AsRef<OsStr>>,
    ) -> Self {
        for value in config {
            self.0.arg("-c").arg(value);
        }
        self
    }

    pub(super) fn attr_source(mut self, sha: &str) -> Self {
        self.0.env("GIT_ATTR_SOURCE", sha);
        self
    }

    pub(crate) fn read(
        self,
        args: impl IntoIterator<Item = impl AsRef<OsStr>>,
    ) -> GitResult<Vec<u8>> {
        let output = self.run(args)?;
        if output.status.success() {
            return Ok(output.stdout);
        }
        Err(output.error())
    }

    pub(crate) fn read_optional(
        self,
        args: impl IntoIterator<Item = impl AsRef<OsStr>>,
    ) -> GitResult<Option<Vec<u8>>> {
        let output = self.run(args)?;
        match output.status.code() {
            Some(0) => Ok(Some(output.stdout)),
            Some(1) => Ok(None),
            _ => Err(output.error()),
        }
    }

    fn run(mut self, args: impl IntoIterator<Item = impl AsRef<OsStr>>) -> GitResult<Output> {
        let deadline = Instant::now() + self.1;
        let mut child = self.0.args(args).spawn().map_err(|error| {
            if error.kind() == io::ErrorKind::NotFound {
                GitError("未安装 Git，无法读取项目记录。".into())
            } else {
                GitError(format!("无法执行 Git 查询：{error}"))
            }
        })?;
        let mut stdout = child.stdout.take().expect("piped Git stdout");
        let stderr = child.stderr.take().expect("piped Git stderr");
        let (sender, receiver) = mpsc::channel();
        let stdout_result = sender.clone();
        thread::spawn(move || {
            let _ = stdout_result.send(read_stdout(&mut stdout).map(Stream::Stdout));
        });
        thread::spawn(move || {
            let _ = sender.send(read_stderr(stderr).map(Stream::Stderr));
        });

        let result = (|| {
            let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
            for _ in 0..2 {
                match receiver.recv_timeout(deadline.saturating_duration_since(Instant::now())) {
                    Ok(Ok(Stream::Stdout(bytes))) => stdout = bytes,
                    Ok(Ok(Stream::Stderr(bytes))) => stderr = bytes,
                    Ok(Err(error)) => return Err(error),
                    Err(_) => return Err(timeout()),
                }
            }
            loop {
                if let Some(status) = child.try_wait()? {
                    return Ok(Output {
                        status,
                        stdout,
                        stderr,
                    });
                }
                let left = deadline.saturating_duration_since(Instant::now());
                if left.is_zero() {
                    return Err(timeout());
                }
                thread::sleep(left.min(Duration::from_millis(2)));
            }
        })();
        if result.is_err() {
            let _ = child.kill();
            let _ = child.wait();
        }
        result
    }
}

pub(super) fn commit_sha(repo: &Repository, reference: &str) -> GitResult<String> {
    if reference.is_empty()
        || reference.chars().count() > 512
        || reference.starts_with('-')
        || reference.contains(['\0', '\r', '\n'])
    {
        return Err("无效的提交或分支。".into());
    }
    let expression = format!("{reference}^{{commit}}");
    object_id(
        repo.git()
            .read(["rev-parse", "--verify", "--end-of-options", &expression])?,
    )
}

pub(crate) fn object_id(output: Vec<u8>) -> GitResult<String> {
    let value = String::from_utf8_lossy(&output);
    let sha = value.trim();
    if !matches!(sha.len(), 40 | 64) || !sha.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("Git 返回了无效的对象 ID。".into());
    }
    Ok(sha.to_owned())
}

fn prefixed(prefix: &str, value: impl AsRef<OsStr>) -> OsString {
    let mut result = OsString::from(prefix);
    result.push(value);
    result
}

fn timeout() -> GitError {
    "Git 查询超时或目录不可访问。".into()
}

fn read_stdout(reader: &mut impl Read) -> GitResult<Vec<u8>> {
    let mut bytes = Vec::new();
    reader
        .take((OUTPUT_LIMIT + 1) as u64)
        .read_to_end(&mut bytes)?;
    if bytes.len() > OUTPUT_LIMIT {
        return Err("内容超过单次读取上限，请缩小查询范围或选择较小的文件。".into());
    }
    Ok(bytes)
}

fn read_stderr(mut reader: impl Read) -> GitResult<Vec<u8>> {
    let mut bytes = Vec::new();
    reader
        .by_ref()
        .take(ERROR_LIMIT as u64)
        .read_to_end(&mut bytes)?;
    io::copy(&mut reader, &mut io::sink())?;
    Ok(bytes)
}

enum Stream {
    Stdout(Vec<u8>),
    Stderr(Vec<u8>),
}

struct Output {
    status: ExitStatus,
    stdout: Vec<u8>,
    stderr: Vec<u8>,
}

impl Output {
    fn error(&self) -> GitError {
        let detail = String::from_utf8_lossy(&self.stderr);
        let detail = detail.trim();
        if detail.is_empty() {
            return "Git 查询失败。".into();
        }
        GitError(detail.to_owned())
    }
}
