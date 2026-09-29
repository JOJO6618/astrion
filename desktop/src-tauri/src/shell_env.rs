//! 桌面命令环境：把「用户真实的 PATH」补进后端子进程环境。
//!
//! 背景与口径（与 Electron 壳的 shell_env.js 对比）：
//!   - macOS 上 GUI 进程 PATH 天生贫瘠（launchd 只给系统路径），所以 Electron 壳去
//!     读用户登录 shell（zsh/bash/fish）的 PATH。
//!   - Windows 上**没有登录 shell 概念**：环境变量的权威来源是注册表（用户级
//!     `HKCU\Environment` + 机器级 `HKLM\...\Session Manager\Environment`），
//!     PowerShell 团队给 VS Code 的建议也是「读注册表，且只做增量而不是整体替换」
//!     （整体替换会把「用户专门带环境启动」的场景破坏掉）。
//!
//! 因此本模块只做一件事：**进程 PATH 保持原顺序在前，注册表里进程没有的条目追加到末尾**。
//! 严格只增不减，不改变任何已有条目的相对顺序。
//!
//! 已知局限（与上游同款 agent runner 一致）：`%VAR%` 展开的变量表来自进程环境，
//! 所以「壳启动之后才新增的变量」在本次运行内展开不出来（重启后正常）。

/// 按当前平台的行分隔符切分（Windows ';'，其余 ':'）。
///
/// 注意：Windows 上绝不能再把 ':' 一并当分隔符——盘符里的冒号会被切开，
/// `C:\Tools\Node` 会碎成 `C` 和 `\Tools\Node` 两个假条目，大小写去重也跟着失效。
fn split_path(value: &str) -> Vec<String> {
    let sep = if cfg!(windows) { ';' } else { ':' };
    value
        .split(sep)
        .map(|item| item.trim().trim_matches('"').trim().to_string())
        .filter(|item| !item.is_empty())
        .collect()
}

/// 去重比较用的键：Windows 不区分大小写、末尾分隔符无关
fn entry_key(entry: &str) -> String {
    let trimmed = entry.trim().trim_matches('"').trim_end_matches(['\\', '/']);
    if cfg!(windows) {
        trimmed.to_lowercase()
    } else {
        trimmed.to_string()
    }
}

/// 把 `additions` 里进程 PATH 尚未包含的条目追加到 `process_path` 末尾。
/// 只增不减：已有条目的顺序与内容原样保留。
pub fn merge_path_entries(process_path: &str, additions: &[String]) -> String {
    let sep = if cfg!(windows) { ';' } else { ':' };
    let existing = split_path(process_path);
    let mut seen: Vec<String> = existing.iter().map(|e| entry_key(e)).collect();
    let mut merged = existing;
    for candidate in additions {
        for entry in split_path(candidate) {
            let key = entry_key(&entry);
            if key.is_empty() || seen.iter().any(|s| s == &key) {
                continue;
            }
            seen.push(key);
            merged.push(entry);
        }
    }
    merged.join(&sep.to_string())
}

/// `%NAME%` 展开（用 `lookup` 解析变量名）。未找到的变量保持原样（与 Windows 行为一致）。
pub fn expand_percent_vars(value: &str, lookup: &dyn Fn(&str) -> Option<String>) -> String {
    let mut out = String::with_capacity(value.len());
    let chars: Vec<char> = value.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '%' {
            if let Some(close) = chars[i + 1..].iter().position(|c| *c == '%') {
                let name: String = chars[i + 1..i + 1 + close].iter().collect();
                if !name.is_empty() {
                    if let Some(resolved) = lookup(&name) {
                        out.push_str(&resolved);
                        i += close + 2;
                        continue;
                    }
                }
            }
        }
        out.push(chars[i]);
        i += 1;
    }
    out
}

#[cfg(windows)]
mod registry {
    use super::expand_percent_vars;
    use windows_sys::Win32::Foundation::ERROR_SUCCESS;
    use windows_sys::Win32::System::Registry::{
        RegCloseKey, RegOpenKeyExW, RegQueryValueExW, HKEY, HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE,
        KEY_READ, REG_EXPAND_SZ, REG_SZ,
    };

    const MACHINE_ENV_KEY: &str = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment";
    const USER_ENV_KEY: &str = "Environment";

    fn wide(value: &str) -> Vec<u16> {
        value.encode_utf16().chain(std::iter::once(0)).collect()
    }

    /// 读一个 REG_SZ / REG_EXPAND_SZ 值；类型不符或不存在返回 None
    fn read_string(root: HKEY, subkey: &str, name: &str) -> Option<String> {
        let mut hkey: HKEY = std::ptr::null_mut();
        let subkey_w = wide(subkey);
        let rc = unsafe { RegOpenKeyExW(root, subkey_w.as_ptr(), 0, KEY_READ, &mut hkey) };
        if rc != ERROR_SUCCESS {
            return None;
        }
        let name_w = wide(name);
        let mut kind: u32 = 0;
        let mut size: u32 = 0;
        let rc = unsafe {
            RegQueryValueExW(
                hkey,
                name_w.as_ptr(),
                std::ptr::null(),
                &mut kind,
                std::ptr::null_mut(),
                &mut size,
            )
        };
        if rc != ERROR_SUCCESS || size == 0 {
            unsafe { RegCloseKey(hkey) };
            return None;
        }
        let mut buf = vec![0u8; size as usize];
        let rc = unsafe {
            RegQueryValueExW(
                hkey,
                name_w.as_ptr(),
                std::ptr::null(),
                &mut kind,
                buf.as_mut_ptr(),
                &mut size,
            )
        };
        unsafe { RegCloseKey(hkey) };
        if rc != ERROR_SUCCESS || (kind != REG_SZ && kind != REG_EXPAND_SZ) {
            return None;
        }
        let mut units: Vec<u16> = buf
            .chunks_exact(2)
            .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
            .collect();
        while units.last() == Some(&0) {
            units.pop();
        }
        String::from_utf16(&units).ok()
    }

    /// 进程 PATH 未包含的注册表 PATH 条目（机器级在前、用户级在后），已展开 `%VAR%`
    pub fn path_entries() -> Option<Vec<String>> {
        let machine = read_string(HKEY_LOCAL_MACHINE, MACHINE_ENV_KEY, "Path");
        let user = read_string(HKEY_CURRENT_USER, USER_ENV_KEY, "Path");
        if machine.is_none() && user.is_none() {
            return None;
        }
        // 展开用的变量表：进程环境优先（两级顺序与 Windows 构建环境块时一致）
        let lookup = |name: &str| -> Option<String> {
            std::env::var(name)
                .ok()
                .or_else(|| read_string(HKEY_CURRENT_USER, USER_ENV_KEY, name))
                .or_else(|| read_string(HKEY_LOCAL_MACHINE, MACHINE_ENV_KEY, name))
        };
        let expand = |raw: Option<String>| -> Vec<String> {
            raw.map(|v| expand_percent_vars(&v, &lookup))
                .map(|v| {
                    // 注册表 PATH 常见空条目（尾部 ';' 或 ';;'），原样带回会在调用方
                    // 变成“空路径”参与拼接
                    v.split(';')
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect()
                })
                .unwrap_or_default()
        };
        let mut entries = expand(machine);
        entries.extend(expand(user));
        Some(entries)
    }
}

/// 要合并进后端子进程环境的 PATH 条目；Windows 之外恒为 None
pub fn shell_path_additions() -> Option<Vec<String>> {
    #[cfg(windows)]
    {
        registry::path_entries()
    }
    #[cfg(not(windows))]
    {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn owned(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    fn sep() -> &'static str {
        if cfg!(windows) {
            ";"
        } else {
            ":"
        }
    }

    #[test]
    fn merge_appends_only_missing_and_keeps_order() {
        let process = owned(&["C:\\A", "C:\\B"]);
        let additions = owned(&["C:\\C", "C:\\A", "C:\\D"]);
        let merged = merge_path_entries(&process.join(sep()), &additions);
        let parts: Vec<&str> = merged.split(if cfg!(windows) { ';' } else { ':' }).collect();
        assert_eq!(parts, vec!["C:\\A", "C:\\B", "C:\\C", "C:\\D"]);
    }

    #[test]
    fn merge_is_case_insensitive_on_windows() {
        let process = owned(&["C:\\Tools\\Node"]);
        let additions = owned(&["c:\\tools\\node\\", "c:\\TOOLS\\NODE"]);
        let merged = merge_path_entries(&process.join(sep()), &additions);
        let count = merged.split(if cfg!(windows) { ';' } else { ':' }).count();
        if cfg!(windows) {
            assert_eq!(count, 1, "大小写/尾分隔符不同不应重复追加");
        } else {
            assert_eq!(count, 3, "非 Windows 区分大小写");
        }
    }

    #[test]
    fn merge_ignores_empty_and_quoted_duplicates() {
        let process = owned(&["C:\\A"]);
        let additions = owned(&["", "   ", "\"C:\\A\"", "C:\\B"]);
        let merged = merge_path_entries(&process.join(sep()), &additions);
        let parts: Vec<&str> = merged.split(if cfg!(windows) { ';' } else { ':' }).collect();
        assert_eq!(parts, vec!["C:\\A", "C:\\B"]);
    }

    #[test]
    fn merge_never_drops_process_entries() {
        let process = owned(&["C:\\Keep1", "", "C:\\Keep2"]);
        let merged = merge_path_entries(&process.join(sep()), &owned(&[]));
        let parts: Vec<&str> = merged.split(if cfg!(windows) { ';' } else { ':' }).collect();
        assert_eq!(parts, vec!["C:\\Keep1", "C:\\Keep2"]);
    }

    #[test]
    fn expand_percent_vars_resolves_and_keeps_unknown() {
        let lookup = |name: &str| -> Option<String> {
            match name.to_ascii_uppercase().as_str() {
                "SYSTEMROOT" => Some(r"C:\Windows".to_string()),
                "USERPROFILE" => Some(r"C:\Users\me".to_string()),
                _ => None,
            }
        };
        assert_eq!(
            expand_percent_vars(r"%SystemRoot%\system32;%USERPROFILE%\bin", &lookup),
            r"C:\Windows\system32;C:\Users\me\bin"
        );
        assert_eq!(
            expand_percent_vars(r"%UNKNOWN%\x", &lookup),
            r"%UNKNOWN%\x",
            "未知变量应原样保留（与 Windows 一致）"
        );
        assert_eq!(expand_percent_vars("100% done", &lookup), "100% done");
        assert_eq!(expand_percent_vars("%%", &lookup), "%%");
    }

    #[test]
    fn real_registry_read_produces_entries_on_windows() {
        if !cfg!(windows) {
            return;
        }
        let entries = shell_path_additions();
        // 机器级 PATH 必定存在，因此不应为 None
        assert!(entries.is_some(), "应能读到注册表 PATH");
        let entries = entries.unwrap();
        assert!(!entries.is_empty(), "注册表 PATH 不应为空");
        assert!(
            entries.iter().all(|e| !e.is_empty()),
            "空条目应在合并阶段被丢弃，但原始条目里不应出现纯空串"
        );
    }
}
