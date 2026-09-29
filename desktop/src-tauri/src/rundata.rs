//! 运行数据目录（对齐 Electron 壳的 rundata.js）。
//!
//! 职责：
//!   1. 壳侧设置文件 `~/.astrion/rundata_settings.json`（唯一权威，重启后仍生效）
//!   2. 数据根解析优先级：`ASTRION_DESKTOP_DATA_ROOT` 环境变量 > 设置文件 > 默认目录
//!   3. 待迁移执行：**在 spawn 后端之前**复制数据（此时没有任何写入方），
//!      因此校验是精确的、不存在撕裂快照
//!   4. 迁移进度状态（供桥的 /migration/progress 与进度窗口读取）
//!
//! 与 Electron 版的差异（有意为之）：
//!   - 迁移时机从「应用内迁移」改为「下次启动、spawn 后端之前」。Windows 上后端
//!     运行期间源文件持续被写入，Electron 那套「复制完成后再统计源做校验」会
//!     稳定误判 verification_failed（本机实测日志追加场景 10 次失败 9 次）。
//!   - 元数据忽略名单补 Windows 项（desktop.ini / Thumbs.db）。
//!   - 复制路径统一加 `\\?\` 前缀，规避未开启 LongPathsEnabled 机器上的 260 字符限制。

use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use serde::{Deserialize, Serialize};

/// 壳侧设置文件名（位于 `~/.astrion/` 下，与数据根本身解耦，用于「启动前」解析）
const SETTINGS_FILENAME: &str = "rundata_settings.json";
/// 默认数据根（相对用户主目录）
const DEFAULT_ROOT_REL: &str = ".astrion/astrion-desktop";
/// 环境变量覆盖（测试/调试用，存在时以它为准且在应用内不可改）
pub const ENV_DATA_ROOT: &str = "ASTRION_DESKTOP_DATA_ROOT";

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct PendingMigration {
    pub from: String,
    pub to: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct Settings {
    #[serde(default)]
    pub data_root: Option<String>,
    #[serde(default)]
    pub pending_migration: Option<PendingMigration>,
    /// 上一次待迁移的失败原因（i18n key 或短标识），设置页读取后展示
    #[serde(default)]
    pub last_error: Option<String>,
}

#[derive(Debug, Clone, Default)]
pub struct MigrationProgress {
    pub active: bool,
    /// idle | scanning | copying | verifying | done | error
    pub phase: String,
    pub files_done: u64,
    pub files_total: u64,
    pub bytes_done: u64,
    pub bytes_total: u64,
    pub error: Option<String>,
}

static MIGRATION: OnceLock<Mutex<MigrationProgress>> = OnceLock::new();

pub fn migration_progress() -> &'static Mutex<MigrationProgress> {
    MIGRATION.get_or_init(|| {
        Mutex::new(MigrationProgress {
            phase: "idle".to_string(),
            ..Default::default()
        })
    })
}

fn set_phase(phase: &str) {
    if let Ok(mut p) = migration_progress().lock() {
        p.active = phase != "idle" && phase != "done" && phase != "error";
        p.phase = phase.to_string();
    }
}

fn set_error(message: String) {
    if let Ok(mut p) = migration_progress().lock() {
        p.active = false;
        p.phase = "error".to_string();
        p.error = Some(message);
    }
}

// ──────────────────────────── 路径与设置文件 ────────────────────────────

pub fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("USERPROFILE").map(PathBuf::from))
}

pub fn default_data_root() -> PathBuf {
    match home_dir() {
        Some(home) => home.join(DEFAULT_ROOT_REL.replace('/', std::path::MAIN_SEPARATOR_STR)),
        None => PathBuf::from(DEFAULT_ROOT_REL),
    }
}

/// 设置文件路径覆盖（仅测试/调试用）。迁移是破坏性操作（会移动用户全部数据），
/// 必须有端到端可验证的手段，否则只能靠手工跑应用碰运气。
const SETTINGS_PATH_ENV: &str = "ASTRION_DESKTOP_SETTINGS_FILE";

pub fn settings_path() -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var(SETTINGS_PATH_ENV) {
        if !explicit.trim().is_empty() {
            return Some(PathBuf::from(explicit));
        }
    }
    home_dir().map(|h| h.join(".astrion").join(SETTINGS_FILENAME))
}

/// `~` / `~/x` / `~\x` 展开（Electron 版只认 `~` + 平台分隔符，Windows 上
/// 用户输入 `~/x` 会落到字面量 `~` 目录；这里两种斜杠都认）
pub fn expand_user_path(raw: &str) -> PathBuf {
    let trimmed = raw.trim();
    if trimmed == "~" {
        return home_dir().unwrap_or_else(|| PathBuf::from("~"));
    }
    let rest = trimmed
        .strip_prefix("~/")
        .or_else(|| trimmed.strip_prefix("~\\"));
    match (rest, home_dir()) {
        (Some(rest), Some(home)) => home.join(normalize_separators(rest)),
        _ => PathBuf::from(normalize_separators(trimmed)),
    }
}

/// 环境变量覆盖的数据根（已展开 `~`）
pub fn env_data_root() -> Option<PathBuf> {
    let raw = std::env::var(ENV_DATA_ROOT).ok()?;
    let raw = raw.trim().to_string();
    if raw.is_empty() {
        return None;
    }
    Some(expand_user_path(&raw))
}

pub fn read_settings() -> Settings {
    let Some(path) = settings_path() else {
        return Settings::default();
    };
    match std::fs::read_to_string(&path) {
        Ok(text) => serde_json::from_str::<Settings>(&text).unwrap_or_else(|e| {
            eprintln!("[astrion-desktop] 运行数据目录设置解析失败，按默认值处理: {e}");
            Settings::default()
        }),
        Err(_) => Settings::default(),
    }
}

/// 原子写设置文件（临时文件 + rename，避免写一半被读到）
pub fn write_settings(settings: &Settings) -> Result<(), String> {
    let Some(path) = settings_path() else {
        return Err("home_directory_unavailable".to_string());
    };
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("创建设置目录失败: {e}"))?;
    }
    let body = serde_json::to_string_pretty(settings).map_err(|e| format!("序列化设置失败: {e}"))?;
    let temp = path.with_extension(format!("{}.tmp", std::process::id()));
    std::fs::write(&temp, format!("{body}\n")).map_err(|e| format!("写设置临时文件失败: {e}"))?;
    std::fs::rename(&temp, &path).map_err(|e| {
        let _ = std::fs::remove_file(&temp);
        format!("替换设置文件失败: {e}")
    })
}

/// 当前生效的数据根：环境变量 > 设置文件 > 默认
pub fn resolve_data_root() -> PathBuf {
    if let Some(root) = env_data_root() {
        return root;
    }
    let settings = read_settings();
    if let Some(raw) = settings.data_root.as_deref() {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            return expand_user_path(trimmed);
        }
    }
    default_data_root()
}

// ──────────────────────────── 路径工具 ────────────────────────────

/// Windows 上把正斜杠统一成反斜杠：`\\?\` 长路径前缀会【跳过】系统路径归一化，
/// 带 '/' 的路径直接报 ERROR_INVALID_NAME(123)。用户手输 `D:/data/astrion` 很常见，
/// 而 `Path::join` 也不会帮忙换分隔符。
#[cfg(windows)]
fn normalize_separators(text: &str) -> String {
    text.replace('/', "\\")
}

#[cfg(not(windows))]
fn normalize_separators(text: &str) -> String {
    text.to_string()
}

/// 长路径前缀（仅 Windows）：未开启 LongPathsEnabled 时，超过 MAX_PATH 的路径
/// 会在 std::fs 上直接失败；加 `\\?\` 前缀可绕开限制。
#[cfg(windows)]
fn long_path(path: &Path) -> PathBuf {
    let text = normalize_separators(&path.to_string_lossy());
    if text.starts_with(r"\\?\") {
        return PathBuf::from(text);
    }
    if let Some(rest) = text.strip_prefix(r"\\") {
        // UNC：\\server\share → \\?\UNC\server\share
        return PathBuf::from(format!(r"\\?\UNC\{rest}"));
    }
    if path.is_absolute() {
        return PathBuf::from(format!(r"\\?\{text}"));
    }
    PathBuf::from(text)
}

#[cfg(not(windows))]
fn long_path(path: &Path) -> PathBuf {
    path.to_path_buf()
}

/// 去掉 `\\?\` 前缀（仅用于展示给用户的文案）
#[cfg(windows)]
fn strip_long_prefix(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        return PathBuf::from(format!(r"\\{rest}"));
    }
    if let Some(rest) = text.strip_prefix(r"\\?\") {
        return PathBuf::from(rest.to_string());
    }
    path.to_path_buf()
}

#[cfg(not(windows))]
fn strip_long_prefix(path: &Path) -> PathBuf {
    path.to_path_buf()
}

/// 「规范化路径」：解析到最长的已存在祖先再拼回剩余部分。
/// （std::fs::canonicalize 要求整条路径存在，目标目录可能刚创建）
fn canonicalize_lenient(path: &Path) -> PathBuf {
    let mut cursor = strip_long_prefix(path);
    let mut tail: Vec<OsString> = Vec::new();
    loop {
        if let Ok(real) = std::fs::canonicalize(&cursor) {
            let real = strip_long_prefix(&real);
            return tail.iter().rev().fold(real, |acc, name| acc.join(name));
        }
        match (cursor.file_name(), cursor.parent()) {
            (Some(name), Some(parent)) if parent != cursor => {
                tail.push(name.to_os_string());
                cursor = parent.to_path_buf();
            }
            _ => return strip_long_prefix(path),
        }
    }
}

/// Windows 路径比较：统一小写 + 归一化分隔符（std 的 Path 比较是大小写敏感的）
#[cfg(windows)]
fn path_key(path: &Path) -> String {
    strip_long_prefix(&canonicalize_lenient(path))
        .to_string_lossy()
        .replace('/', "\\")
        .trim_end_matches('\\')
        .to_lowercase()
}

#[cfg(not(windows))]
fn path_key(path: &Path) -> String {
    canonicalize_lenient(path)
        .to_string_lossy()
        .trim_end_matches('/')
        .to_string()
}

/// 判定 `inner` 是否在 `outer` 之内（相等也返回 true）
fn path_within(outer: &Path, inner: &Path) -> bool {
    let outer = path_key(outer);
    let inner = path_key(inner);
    if outer.is_empty() || inner.is_empty() {
        return false;
    }
    if outer == inner {
        return true;
    }
    let sep = std::path::MAIN_SEPARATOR;
    inner.starts_with(&format!("{outer}{sep}"))
}

/// 校验源与目标不相交（相同或互相包含都拒绝）
pub fn assert_no_overlap(source: &Path, target: &Path) -> Result<(), String> {
    if path_key(source) == path_key(target) {
        return Err("same_directory".to_string());
    }
    if path_within(source, target) || path_within(target, source) {
        return Err("overlapping_directories".to_string());
    }
    Ok(())
}

/// 复制/校验时跳过的元数据文件（Finder + Windows 资源管理器）
pub fn is_metadata_file(name: &str) -> bool {
    name == ".DS_Store"
        || name == ".localized"
        || name.starts_with("._")
        || name.eq_ignore_ascii_case("desktop.ini")
        || name.eq_ignore_ascii_case("Thumbs.db")
}

/// 目录是否为空（只含元数据算空）；不存在也算空
pub fn directory_is_empty(target: &Path) -> Result<bool, String> {
    match std::fs::read_dir(long_path(target)) {
        Ok(entries) => {
            for entry in entries.flatten() {
                if !is_metadata_file(&entry.file_name().to_string_lossy()) {
                    return Ok(false);
                }
            }
            Ok(true)
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(true),
        Err(e) => Err(format!("目标目录不可读: {e}")),
    }
}

// ──────────────────────────── 迁移 ────────────────────────────

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct TreeStats {
    pub count: u64,
    pub bytes: u64,
}

fn scan_tree(root: &Path, mut on_file: impl FnMut(u64)) -> Result<TreeStats, String> {
    let mut stats = TreeStats::default();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let entries = std::fs::read_dir(long_path(&dir)).map_err(|e| format!("读取目录失败: {e}"))?;
        for entry in entries {
            let entry = entry.map_err(|e| format!("读取目录项失败: {e}"))?;
            let name = entry.file_name().to_string_lossy().to_string();
            if is_metadata_file(&name) {
                continue;
            }
            let path = entry.path();
            let meta = entry.metadata().map_err(|e| format!("读取元数据失败: {e}"))?;
            stats.count += 1;
            if meta.is_dir() {
                stack.push(path);
            } else if meta.is_file() {
                stats.bytes += meta.len();
                on_file(meta.len());
            }
        }
    }
    Ok(stats)
}

fn copy_tree(root: &Path, target: &Path, progress: bool) -> Result<TreeStats, String> {
    let mut stats = TreeStats::default();
    let mut stack = vec![(root.to_path_buf(), target.to_path_buf())];
    while let Some((src_dir, dst_dir)) = stack.pop() {
        std::fs::create_dir_all(long_path(&dst_dir)).map_err(|e| format!("创建目录失败: {e}"))?;
        let entries = std::fs::read_dir(long_path(&src_dir)).map_err(|e| format!("读取目录失败: {e}"))?;
        for entry in entries {
            let entry = entry.map_err(|e| format!("读取目录项失败: {e}"))?;
            let name = entry.file_name();
            if is_metadata_file(&name.to_string_lossy()) {
                continue;
            }
            let src = entry.path();
            let dst = dst_dir.join(&name);
            let meta = entry.metadata().map_err(|e| format!("读取元数据失败: {e}"))?;
            stats.count += 1;
            if meta.is_dir() {
                stack.push((src, dst));
            } else if meta.is_file() {
                std::fs::copy(long_path(&src), long_path(&dst))
                    .map_err(|e| format!("复制文件失败: {e}"))?;
                stats.bytes += meta.len();
                if progress {
                    if let Ok(mut p) = migration_progress().lock() {
                        p.files_done += 1;
                        p.bytes_done += meta.len();
                    }
                }
            }
        }
    }
    Ok(stats)
}

/// 执行待迁移（**必须在 spawn 后端之前调用**）。
///
/// 返回最终应当使用的数据根：成功 → 新目录；失败 → 回退到原目录，并把错误写进设置文件。
pub fn run_pending_migration() -> PathBuf {
    let settings = read_settings();
    let Some(pending) = settings.pending_migration.clone() else {
        return resolve_data_root();
    };
    let source = expand_user_path(&pending.from);
    let target = expand_user_path(&pending.to);

    set_phase("scanning");
    let result = (|| -> Result<TreeStats, String> {
        if !source.exists() {
            // 源不存在（全新安装场景）：直接把目标目录建出来即可
            std::fs::create_dir_all(long_path(&target)).map_err(|e| format!("创建目标目录失败: {e}"))?;
            return Ok(TreeStats::default());
        }
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(long_path(parent)).map_err(|e| format!("创建目标父目录失败: {e}"))?;
        }
        let source_stats = scan_tree(&source, |_| {})?;
        if let Ok(mut p) = migration_progress().lock() {
            p.files_total = source_stats.count;
            p.bytes_total = source_stats.bytes;
            p.files_done = 0;
            p.bytes_done = 0;
        }
        set_phase("copying");
        // stage 必须建在**目标父目录**内：跨卷 rename 在 Windows 上会 EXDEV
        let parent = target
            .parent()
            .ok_or_else(|| "target_parent_invalid".to_string())?
            .to_path_buf();
        let stage = parent.join(format!(
            ".{}.astrion-migration-{}",
            target
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_else(|| "data".to_string()),
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(long_path(&stage));
        std::fs::create_dir_all(long_path(&stage)).map_err(|e| format!("创建暂存目录失败: {e}"))?;

        let cleanup = |stage: &Path| {
            let _ = std::fs::remove_dir_all(long_path(stage));
        };

        let copied = match copy_tree(&source, &stage, true) {
            Ok(stats) => stats,
            Err(e) => {
                cleanup(&stage);
                return Err(e);
            }
        };

        set_phase("verifying");
        let copied_check = match scan_tree(&stage, |_| {}) {
            Ok(stats) => stats,
            Err(e) => {
                cleanup(&stage);
                return Err(e);
            }
        };
        if copied.count != copied_check.count || copied.bytes != copied_check.bytes {
            cleanup(&stage);
            return Err("verification_failed".to_string());
        }

        // 目标若已存在且只含元数据，先清理再让位
        if target.exists() {
            if let Ok(entries) = std::fs::read_dir(long_path(&target)) {
                for entry in entries.flatten() {
                    if is_metadata_file(&entry.file_name().to_string_lossy()) {
                        let _ = std::fs::remove_file(entry.path());
                    }
                }
            }
            std::fs::remove_dir(long_path(&target)).map_err(|e| {
                cleanup(&stage);
                format!("目标目录无法让位: {e}")
            })?;
        }
        if let Err(e) = std::fs::rename(long_path(&stage), long_path(&target)) {
            cleanup(&stage);
            return Err(format!("移动暂存目录失败: {e}"));
        }
        Ok(copied)
    })();

    let mut next = settings.clone();
    match result {
        Ok(_) => {
            next.data_root = Some(target.to_string_lossy().to_string());
            next.pending_migration = None;
            next.last_error = None;
            let _ = write_settings(&next);
            set_phase("done");
            target
        }
        Err(err) => {
            eprintln!("[astrion-desktop] 运行数据目录迁移失败({err})，回退到原目录");
            next.data_root = Some(source.to_string_lossy().to_string());
            next.pending_migration = None;
            next.last_error = Some(if err.is_empty() { "unknown".to_string() } else { err.clone() });
            let _ = write_settings(&next);
            set_error(err);
            source
        }
    }
}

/// 供桥 `GET /rundata/info` 使用：字段与上游 Electron 版 `getRunDataInfo()` 对齐，
/// 额外带 `last_error`（上一次迁移失败的原因码，设置页展示后由下次成功操作清除）。
///
/// 语义细节（与上游一致）：环境变量存在时 `configured_path` 恒为空——环境变量优先，
/// 设置文件此时不生效，前端据此把输入区置为只读。
pub fn info_payload() -> String {
    let env_root = env_data_root();
    let settings = read_settings();
    let stored = if env_root.is_some() {
        None
    } else {
        settings
            .data_root
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(expand_user_path)
    };
    let active = env_root
        .clone()
        .or_else(|| stored.clone())
        .unwrap_or_else(default_data_root);
    let display = |path: &Path| path.to_string_lossy().to_string();
    serde_json::json!({
        "success": true,
        "env_locked": env_root.is_some(),
        "env_path": env_root.as_deref().map(display).unwrap_or_default(),
        "configured_path": stored.as_deref().map(display).unwrap_or_default(),
        "active_path": display(&active),
        "default_path": display(&default_data_root()),
        "last_error": settings.last_error,
    })
    .to_string()
}

/// 供桥 /rundata/apply 使用：校验并写入设置（含 pending_migration）
pub fn schedule_data_root_change(data_root: &str, migrate: bool) -> Result<PathBuf, String> {
    if env_data_root().is_some() {
        return Err("environment_locked".to_string());
    }
    if data_root.trim().is_empty() {
        return Err("path_required".to_string());
    }
    let target = expand_user_path(data_root);
    if !target.is_absolute() {
        return Err("path_required".to_string());
    }
    let source = resolve_data_root();
    assert_no_overlap(&source, &target)?;

    if let Ok(meta) = std::fs::symlink_metadata(&target) {
        if !meta.is_dir() {
            return Err("target_not_directory".to_string());
        }
    }
    if migrate && !directory_is_empty(&target)? {
        return Err("target_not_empty".to_string());
    }

    let mut settings = read_settings();
    settings.data_root = Some(target.to_string_lossy().to_string());
    settings.pending_migration = if migrate {
        Some(PendingMigration {
            from: source.to_string_lossy().to_string(),
            to: target.to_string_lossy().to_string(),
        })
    } else {
        None
    };
    settings.last_error = None;
    write_settings(&settings)?;
    Ok(target)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("astrion-rundata-test-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn sample_tree(root: &Path) {
        std::fs::create_dir_all(root.join("host/logs")).unwrap();
        std::fs::create_dir_all(root.join("host/data/versioning")).unwrap();
        std::fs::write(root.join("host/logs/backend.log"), "line\n".repeat(50)).unwrap();
        std::fs::write(root.join("host/data/users.json"), "{\"a\":1}").unwrap();
        std::fs::write(root.join("host/data/versioning/a_0001.bin"), vec![7u8; 4096]).unwrap();
        std::fs::write(root.join("host/data/versioning/a_0002.bin"), vec![9u8; 8192]).unwrap();
    }

    /// 两个迁移用例都要改写进程级 `ASTRION_DESKTOP_SETTINGS_FILE`（env 是全局状态，
    /// 而 cargo test 默认多线程并行）——必须串行，否则后一个用例的 remove_var 会让
    /// 前一个用例中途回退到真实用户设置文件，断言随机失败。
    fn env_lock() -> std::sync::MutexGuard<'static, ()> {
        static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
        LOCK.get_or_init(|| Mutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    #[test]
    fn pending_migration_moves_data_and_updates_settings() {
        let _env = env_lock();
        let base = temp_dir("migrate-chain");
        let src = base.join("src");
        let dst = base.join("dst");
        sample_tree(&src);
        let settings_file = base.join("rundata_settings.json");
        let quote = |p: &Path| serde_json::to_string(&p.to_string_lossy().to_string()).unwrap();
        std::fs::write(&settings_file, format!("{{\"data_root\": {}}}\n", quote(&src))).unwrap();

        std::env::set_var(SETTINGS_PATH_ENV, &settings_file);
        let outcome = std::panic::catch_unwind(|| {
            assert_eq!(resolve_data_root(), src);
            // 登记待迁移（设置页点「应用」走的就是这一个入口）
            schedule_data_root_change(&dst.to_string_lossy(), true).unwrap();
            assert!(read_settings().pending_migration.is_some());

            // 启动时执行：成功 → 目标就位，标记与错误都清空
            assert_eq!(run_pending_migration(), dst);
            let after = read_settings();
            assert_eq!(after.data_root.as_deref(), Some(dst.to_string_lossy().as_ref()));
            assert!(after.pending_migration.is_none(), "成功后应清除待迁移标记");
            assert!(after.last_error.is_none());
            assert!(dst.join("host/data/users.json").exists());
            assert_eq!(scan_tree(&src, |_| {}).unwrap(), scan_tree(&dst, |_| {}).unwrap());
            // 迁移是「复制 + 让位」，源目录内容保留
            assert!(src.join("host/logs/backend.log").exists());

            let info: serde_json::Value = serde_json::from_str(&info_payload()).unwrap();
            assert_eq!(info["env_locked"].as_bool(), Some(false));
            assert_eq!(info["configured_path"].as_str(), Some(dst.to_string_lossy().as_ref()));
            assert_eq!(info["active_path"].as_str(), Some(dst.to_string_lossy().as_ref()));
        });
        std::env::remove_var(SETTINGS_PATH_ENV);
        outcome.unwrap();
    }

    #[test]
    fn failed_migration_rolls_back_to_source_with_error() {
        let _env = env_lock();
        let base = temp_dir("migrate-rollback");
        let src = base.join("src");
        let dst = base.join("dst");
        sample_tree(&src);
        // 目标目录已存在且含真实数据 → rename 阶段让位失败
        // （手工造非法待迁移：schedule_data_root_change 本身会先以 target_not_empty 拦下）
        std::fs::create_dir_all(&dst).unwrap();
        std::fs::write(dst.join("occupied.txt"), b"busy").unwrap();

        let settings_file = base.join("rundata_settings.json");
        let quote = |p: &Path| serde_json::to_string(&p.to_string_lossy().to_string()).unwrap();
        std::fs::write(
            &settings_file,
            format!(
                "{{\"data_root\": {}, \"pending_migration\": {{\"from\": {}, \"to\": {}}}}}",
                quote(&src),
                quote(&src),
                quote(&dst)
            ),
        )
        .unwrap();

        std::env::set_var(SETTINGS_PATH_ENV, &settings_file);
        let outcome = std::panic::catch_unwind(|| {
            // 失败 → 回退到原目录启动，并把原因留给设置页展示
            assert_eq!(run_pending_migration(), src);
            let after = read_settings();
            assert_eq!(after.data_root.as_deref(), Some(src.to_string_lossy().as_ref()));
            assert!(after.pending_migration.is_none(), "失败后不应反复重试死磕");
            assert!(after.last_error.is_some(), "失败原因必须落盘供设置页展示");
            assert!(src.join("host/data/users.json").exists(), "源目录数据应原样保留");
            assert!(dst.join("occupied.txt").exists(), "目标目录不应被破坏");
            // stage 目录必须清干净（不留在目标父目录里）
            let leftovers: Vec<String> = std::fs::read_dir(&base)
                .unwrap()
                .flatten()
                .map(|e| e.file_name().to_string_lossy().to_string())
                .filter(|name| name.contains("astrion-migration"))
                .collect();
            assert!(leftovers.is_empty(), "失败后残留暂存目录: {leftovers:?}");
        });
        std::env::remove_var(SETTINGS_PATH_ENV);
        outcome.unwrap();
    }

    #[test]
    fn metadata_skip_covers_windows_and_finder() {
        for name in [".DS_Store", ".localized", "._x", "desktop.ini", "DESKTOP.INI", "Thumbs.db", "thumbs.db"] {
            assert!(is_metadata_file(name), "{name} 应被识别为元数据");
        }
        assert!(!is_metadata_file("data.json"));
        assert!(!is_metadata_file("thumbs.db.bak"));
    }

    #[test]
    fn expand_user_path_accepts_both_separators() {
        let home = home_dir().unwrap();
        assert_eq!(expand_user_path("~"), home);
        assert_eq!(expand_user_path("~/x/y"), home.join("x").join("y"));
        assert_eq!(expand_user_path("~\\x\\y"), home.join("x").join("y"));
        assert_eq!(expand_user_path("  ~/z  "), home.join("z"));
        assert_eq!(expand_user_path("C:\\plain"), PathBuf::from("C:\\plain"));
    }

    #[test]
    fn overlap_detection_rejects_nested_and_same() {
        let root = temp_dir("overlap");
        let inner = root.join("inner");
        std::fs::create_dir_all(&inner).unwrap();
        assert_eq!(assert_no_overlap(&root, &root).unwrap_err(), "same_directory");
        assert_eq!(
            assert_no_overlap(&root, &inner).unwrap_err(),
            "overlapping_directories"
        );
        assert_eq!(
            assert_no_overlap(&inner, &root).unwrap_err(),
            "overlapping_directories"
        );
        let sibling = temp_dir("overlap-sibling");
        assert!(assert_no_overlap(&root, &sibling).is_ok());
    }

    #[test]
    fn empty_dir_ignores_metadata_only() {
        let root = temp_dir("empty");
        assert!(directory_is_empty(&root).unwrap());
        std::fs::write(root.join("desktop.ini"), "x").unwrap();
        std::fs::write(root.join("Thumbs.db"), "x").unwrap();
        std::fs::write(root.join(".DS_Store"), "x").unwrap();
        assert!(directory_is_empty(&root).unwrap(), "只含元数据应算空");
        std::fs::write(root.join("real.json"), "{}").unwrap();
        assert!(!directory_is_empty(&root).unwrap());
    }

    #[test]
    fn copy_tree_is_exact_and_skips_metadata() {
        let base = temp_dir("copy");
        let src = base.join("src");
        let dst = base.join("dst");
        sample_tree(&src);
        std::fs::write(src.join("host/logs/desktop.ini"), "meta").unwrap();
        std::fs::write(src.join("host/logs/.DS_Store"), "meta").unwrap();

        let copied = copy_tree(&src, &dst, false).unwrap();
        let verified = scan_tree(&dst, |_| {}).unwrap();
        assert_eq!(copied.count, verified.count);
        assert_eq!(copied.bytes, verified.bytes);
        assert!(dst.join("host/logs/backend.log").exists());
        assert!(!dst.join("host/logs/desktop.ini").exists(), "元数据不应被复制");
        assert!(!dst.join("host/logs/.DS_Store").exists(), "元数据不应被复制");
        // 内容一致
        assert_eq!(
            std::fs::read(src.join("host/data/versioning/a_0001.bin")).unwrap(),
            std::fs::read(dst.join("host/data/versioning/a_0001.bin")).unwrap()
        );
    }

    #[test]
    fn stage_rename_lands_inside_target_parent() {
        // 复刻 run_pending_migration 的关键路径：stage 建在目标父目录内 → rename 同卷
        let base = temp_dir("stage");
        let src = base.join("src");
        sample_tree(&src);
        let target = base.join("nested/deeper/dst");
        std::fs::create_dir_all(target.parent().unwrap()).unwrap();
        let stage = target.parent().unwrap().join(".dst.astrion-migration-test");
        let stats = copy_tree(&src, &stage, false).unwrap();
        assert_eq!(stats, scan_tree(&stage, |_| {}).unwrap());
        std::fs::rename(&stage, &target).unwrap();
        assert!(target.join("host/data/users.json").exists());
    }

    #[test]
    fn long_path_prefix_is_applied_and_reversible() {
        let p = PathBuf::from(r"C:\a\b\c");
        let prefixed = long_path(&p);
        if cfg!(windows) {
            assert!(prefixed.to_string_lossy().starts_with(r"\\?\"));
            assert_eq!(strip_long_prefix(&prefixed), p);
        } else {
            assert_eq!(prefixed, p);
        }
    }

    #[test]
    fn forward_slashes_are_normalized_before_long_prefix() {
        // `\\?\` 前缀跳过系统归一化，带 '/' 的路径会直接报 ERROR_INVALID_NAME(123)：
        // 用户手输 D:/data 属常见操作，必须在加前缀前换掉分隔符。
        let prefixed = long_path(Path::new("C:/Users/me/astrion"));
        if cfg!(windows) {
            assert_eq!(prefixed.to_string_lossy(), r"\\?\C:\Users\me\astrion");
        } else {
            assert_eq!(prefixed, PathBuf::from("C:/Users/me/astrion"));
        }
        assert_eq!(
            expand_user_path("~/a/b"),
            home_dir().unwrap().join("a").join("b")
        );
    }

    #[test]
    fn path_key_is_case_insensitive_on_windows() {
        let a = path_key(Path::new(r"C:\Users\Foo\Data"));
        let b = path_key(Path::new(r"c:\users\foo\data\"));
        assert_eq!(a, b);
    }
}
