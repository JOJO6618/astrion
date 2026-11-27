//! 出站 HTTP 的统一代理解析与错误诊断。
//!
//! 优先级：环境变量 → 调用方给定的显式配置 → 操作系统系统代理。
//!
//! Python 版的 httpx 在 `trust_env=True` 时会通过 `urllib.request.getproxies()`
//! 读取 macOS 系统代理（CFNetwork）以及 Windows 的 Internet 设置，因此"只开系统
//! 代理"就能工作。reqwest 只读环境变量，不读系统代理，迁移到 Rust 后这层兜底
//! 丢失，表现为 `error sending request`。本模块补回这层能力，并让所有外部请求
//! 客户端共用同一套解析结果。
//!
//! 本地回环地址始终直连，避免把预览、MCP 本地服务等请求送进代理。

use reqwest::{ClientBuilder, NoProxy, Proxy};

/// 本地地址永远绕过代理。
const LOCAL_BYPASS: &str = "localhost,127.0.0.1,::1";

/// 代理解析来源，仅用于展示与诊断。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ProxySource {
    Env,
    Configured,
    System,
}

impl ProxySource {
    pub fn label(self) -> &'static str {
        match self {
            Self::Env => "env",
            Self::Configured => "configured",
            Self::System => "system",
        }
    }
}

/// 一次解析得到的代理设置。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProxyConfig {
    /// 完整的代理地址，例如 `http://127.0.0.1:7897`。
    pub url: String,
    /// 系统/环境给出的绕过列表（逗号分隔，可能为空）。
    pub no_proxy: Option<String>,
    pub source: ProxySource,
}

impl ProxyConfig {
    /// 给界面展示的代理地址。
    pub fn display_url(&self) -> &str {
        &self.url
    }
}

/// 解析当前应该使用的代理。
///
/// `configured` 是调用方手上的显式配置（例如 `codex_models.json` 的 `proxy`），
/// 它低于环境变量、高于系统代理。
pub fn resolve(configured: Option<&str>) -> Option<ProxyConfig> {
    if let Some(config) = from_env() {
        return Some(config);
    }
    if let Some(config) = configured.and_then(configured_proxy) {
        return Some(config);
    }
    system_proxy().and_then(|(raw, no_proxy)| {
        // 单独一项 `*` 才表示"全部直连"；`*.local` 这类通配不能当成全绕过。
        if no_proxy.as_deref().is_some_and(all_bypassed) {
            return None;
        }
        let url = normalize_url(&raw)?;
        Some(ProxyConfig {
            url,
            no_proxy,
            source: ProxySource::System,
        })
    })
}

/// 绕过列表里是否包含"全部直连"标记。
fn all_bypassed(list: &str) -> bool {
    list.split([',', ';']).any(|item| item.trim() == "*")
}

/// 环境变量解析（与 reqwest/httpx 的约定一致）。
pub fn from_env() -> Option<ProxyConfig> {
    for key in [
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ] {
        let Ok(value) = std::env::var(key) else {
            continue;
        };
        let value = value.trim();
        if value.is_empty() {
            continue;
        }
        let Some(url) = normalize_url(value) else {
            // socks/不支持协议的代理直接跳过，避免把请求送进无法建立通道的地址。
            continue;
        };
        return Some(ProxyConfig {
            url,
            no_proxy: env_no_proxy(),
            source: ProxySource::Env,
        });
    }
    None
}

fn env_no_proxy() -> Option<String> {
    ["NO_PROXY", "no_proxy"]
        .iter()
        .filter_map(|key| std::env::var(key).ok())
        .map(|value| value.trim().to_owned())
        .find(|value| !value.is_empty())
}

fn configured_proxy(value: &str) -> Option<ProxyConfig> {
    let url = normalize_url(value.trim())?;
    Some(ProxyConfig {
        url,
        no_proxy: env_no_proxy(),
        source: ProxySource::Configured,
    })
}

/// 只接受 http(s) 代理；其他协议（socks 等）在没有对应 feature 时无法使用。
fn normalize_url(raw: &str) -> Option<String> {
    let raw = raw.trim();
    if raw.is_empty() {
        return None;
    }
    let with_scheme = if raw.contains("://") {
        raw.to_owned()
    } else {
        format!("http://{raw}")
    };
    let lower = with_scheme.to_ascii_lowercase();
    if !(lower.starts_with("http://") || lower.starts_with("https://")) {
        return None;
    }
    Some(with_scheme)
}

/// 合并绕过列表：系统/环境列表 + 本地回环。
fn merged_no_proxy(list: Option<&str>) -> String {
    let mut parts: Vec<String> = Vec::new();
    for item in list.unwrap_or_default().split([',', ';']) {
        let item = item.trim();
        if item.is_empty() {
            continue;
        }
        if !parts.iter().any(|existing| existing == item) {
            parts.push(item.to_owned());
        }
    }
    for local in LOCAL_BYPASS.split(',') {
        if !parts.iter().any(|existing| existing == local) {
            parts.push(local.to_owned());
        }
    }
    parts.join(",")
}

/// 把解析结果套到 reqwest 的构造器上。
pub fn apply(builder: ClientBuilder, proxy: Option<&ProxyConfig>) -> ClientBuilder {
    let Some(proxy) = proxy else {
        return builder;
    };
    match Proxy::all(&proxy.url) {
        Ok(configured) => {
            let no_proxy = merged_no_proxy(proxy.no_proxy.as_deref());
            match NoProxy::from_string(&no_proxy) {
                Some(no_proxy) => builder.proxy(configured.no_proxy(Some(no_proxy))),
                None => builder.proxy(configured),
            }
        }
        // 代理地址非法时退回直连，不影响原本可用的请求。
        Err(_) => builder,
    }
}

/// 代理指纹：用于判断缓存的客户端是否仍然适用。
pub fn fingerprint(proxy: Option<&ProxyConfig>) -> String {
    match proxy {
        Some(proxy) => format!(
            "{}|{}",
            proxy.url,
            proxy.no_proxy.as_deref().unwrap_or_default()
        ),
        None => String::new(),
    }
}

/// 当前生效代理的可读描述（界面/日志用）。
pub fn describe_current() -> Option<String> {
    resolve(None).map(|proxy| format!("{}({})", proxy.url, proxy.source.label()))
}

/// 把 reqwest 错误展开成可诊断的文本：分类 + 底层 cause 链。
///
/// 之前用 `error.without_url()` 只留下 "error sending request"，
/// 丢失了 DNS、连接、TLS 等真实原因，网络问题时完全无法定位。
pub fn describe_error(error: &reqwest::Error) -> String {
    let kind = if error.is_timeout() {
        "timeout"
    } else if error.is_connect() {
        "connect"
    } else if error.is_body() {
        "body"
    } else if error.is_decode() {
        "decode"
    } else if error.is_request() {
        "request"
    } else {
        "transport"
    };
    let mut text = format!("{kind}: {error}");
    let mut source = std::error::Error::source(error);
    let mut depth = 0;
    while let Some(cause) = source {
        text.push_str(&format!(" | {cause}"));
        source = cause.source();
        depth += 1;
        if depth >= 5 {
            break;
        }
    }
    if error.is_connect() && resolve(None).is_none() {
        text.push_str("（连接失败且未检测到代理：可能是 DNS 污染或网络被阻断）");
    }
    text
}

fn system_proxy() -> Option<(String, Option<String>)> {
    platform::system_proxy()
}

#[cfg(target_os = "macos")]
mod platform {
    use std::ffi::{CStr, CString, c_char, c_void};

    type CFTypeRef = *const c_void;
    type CFStringRef = *const c_void;
    type CFArrayRef = *const c_void;
    type CFIndex = isize;
    type CFStringEncoding = u32;
    type Boolean = u8;

    const UTF8: CFStringEncoding = 0x0800_0100;
    /// `kCFNumberSInt32Type`
    const NUMBER_SINT32: CFIndex = 3;
    const BUFFER: usize = 512;

    #[link(name = "CoreFoundation", kind = "framework")]
    unsafe extern "C" {
        fn CFStringCreateWithCString(
            allocator: CFTypeRef,
            c_str: *const c_char,
            encoding: CFStringEncoding,
        ) -> CFStringRef;
        fn CFStringGetCString(
            the_string: CFStringRef,
            buffer: *mut c_char,
            buffer_size: CFIndex,
            encoding: CFStringEncoding,
        ) -> Boolean;
        fn CFDictionaryGetValue(dict: *const c_void, key: CFTypeRef) -> CFTypeRef;
        fn CFNumberGetValue(number: CFTypeRef, the_type: CFIndex, value: *mut c_void) -> Boolean;
        fn CFArrayGetCount(array: CFArrayRef) -> CFIndex;
        fn CFArrayGetValueAtIndex(array: CFArrayRef, index: CFIndex) -> CFTypeRef;
        fn CFRelease(value: CFTypeRef);
    }

    #[link(name = "CFNetwork", kind = "framework")]
    unsafe extern "C" {
        fn CFNetworkCopySystemProxySettings() -> *const c_void;
    }

    #[link(name = "SystemConfiguration", kind = "framework")]
    unsafe extern "C" {
        /// `store` 传空指针即默认会话，与 `scutil --proxy`、Python `_scproxy` 同源。
        fn SCDynamicStoreCopyProxies(store: *const c_void) -> *const c_void;
    }

    /// 读取 macOS 系统代理。
    ///
    /// 先用 `SCDynamicStoreCopyProxies`（网络扩展写入的配置也能读到，
    /// 与 Python `getproxies()` 行为一致），`CFNetworkCopySystemProxySettings` 作回退。
    pub(super) fn system_proxy() -> Option<(String, Option<String>)> {
        unsafe {
            let settings = SCDynamicStoreCopyProxies(std::ptr::null());
            if !settings.is_null() {
                let result = read(settings);
                CFRelease(settings);
                if result.is_some() {
                    return result;
                }
            }
            let settings = CFNetworkCopySystemProxySettings();
            if settings.is_null() {
                return None;
            }
            let result = read(settings);
            CFRelease(settings);
            result
        }
    }

    fn read(settings: *const c_void) -> Option<(String, Option<String>)> {
        let (host, port) = https_endpoint(settings)
            .or_else(|| http_endpoint(settings))
            .map(|(host, port)| (strip_brackets(host), port))?;
        Some((format!("http://{host}:{port}"), exceptions(settings)))
    }

    fn https_endpoint(settings: *const c_void) -> Option<(String, u16)> {
        if !enabled(settings, "HTTPSEnable") {
            return None;
        }
        let host = string(settings, "HTTPSProxy")?;
        if host.trim().is_empty() {
            return None;
        }
        let port = number(settings, "HTTPSPort")?;
        Some((host, port))
    }

    fn http_endpoint(settings: *const c_void) -> Option<(String, u16)> {
        if !enabled(settings, "HTTPEnable") {
            return None;
        }
        let host = string(settings, "HTTPProxy")?;
        if host.trim().is_empty() {
            return None;
        }
        let port = number(settings, "HTTPPort")?;
        Some((host, port))
    }

    /// `*Enable` 为 1 才算开启；键缺失时按开启处理，避免漏掉只写地址的配置。
    fn enabled(settings: *const c_void, key: &str) -> bool {
        flag(settings, key) != Some(0)
    }

    fn strip_brackets(host: String) -> String {
        host.trim().trim_matches(['[', ']']).trim().to_owned()
    }

    fn value(settings: *const c_void, key: &str) -> CFTypeRef {
        unsafe {
            let Ok(c_key) = CString::new(key) else {
                return std::ptr::null();
            };
            let cf_key = CFStringCreateWithCString(std::ptr::null(), c_key.as_ptr(), UTF8);
            if cf_key.is_null() {
                return std::ptr::null();
            }
            let value = CFDictionaryGetValue(settings, cf_key);
            CFRelease(cf_key);
            value
        }
    }

    fn string(settings: *const c_void, key: &str) -> Option<String> {
        unsafe {
            let value = value(settings, key);
            if value.is_null() {
                return None;
            }
            let mut buffer = [0 as c_char; BUFFER];
            if CFStringGetCString(value, buffer.as_mut_ptr(), BUFFER as CFIndex, UTF8) == 0 {
                return None;
            }
            Some(CStr::from_ptr(buffer.as_ptr()).to_string_lossy().into_owned())
        }
    }

    fn flag(settings: *const c_void, key: &str) -> Option<i32> {
        unsafe {
            let value = value(settings, key);
            if value.is_null() {
                return None;
            }
            let mut number: i32 = 0;
            if CFNumberGetValue(value, NUMBER_SINT32, (&mut number as *mut i32).cast()) == 0 {
                return None;
            }
            Some(number)
        }
    }

    fn number(settings: *const c_void, key: &str) -> Option<u16> {
        flag(settings, key)
            .and_then(|value| u16::try_from(value).ok())
            .filter(|port| *port > 0)
    }

    fn exceptions(settings: *const c_void) -> Option<String> {
        unsafe {
            let list = value(settings, "ExceptionsList");
            if list.is_null() {
                return None;
            }
            let count = CFArrayGetCount(list);
            if count <= 0 {
                return None;
            }
            let mut items = Vec::new();
            for index in 0..count {
                let item = CFArrayGetValueAtIndex(list, index);
                if item.is_null() {
                    continue;
                }
                let mut buffer = [0 as c_char; BUFFER];
                if CFStringGetCString(item, buffer.as_mut_ptr(), BUFFER as CFIndex, UTF8) == 0 {
                    continue;
                }
                let text = CStr::from_ptr(buffer.as_ptr()).to_string_lossy();
                let text = text.trim();
                if !text.is_empty() {
                    items.push(text.to_owned());
                }
            }
            (!items.is_empty()).then(|| items.join(","))
        }
    }
}

#[cfg(windows)]
mod platform {
    use std::ffi::OsStr;
    use std::os::windows::ffi::OsStrExt;

    use windows_sys::Win32::Foundation::ERROR_SUCCESS;
    use windows_sys::Win32::System::Registry::{
        HKEY, HKEY_CURRENT_USER, KEY_READ, REG_DWORD, REG_SZ, RegCloseKey, RegOpenKeyExW,
        RegQueryValueExW,
    };

    const SUBKEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings";

    /// 读取 Windows「Internet 选项 → 连接 → 局域网设置」里的系统代理。
    pub(super) fn system_proxy() -> Option<(String, Option<String>)> {
        unsafe {
            let mut key: HKEY = std::ptr::null_mut();
            let subkey = wide(SUBKEY);
            if RegOpenKeyExW(HKEY_CURRENT_USER, subkey.as_ptr(), 0, KEY_READ, &mut key)
                != ERROR_SUCCESS
            {
                return None;
            }
            let enabled = read_dword(key, "ProxyEnable").unwrap_or(0) == 1;
            let server = read_string(key, "ProxyServer");
            let overrides = read_string(key, "ProxyOverride");
            RegCloseKey(key);
            if !enabled {
                return None;
            }
            let server = server?;
            let url = select_server(&server)?;
            Some((url, normalize_overrides(overrides.as_deref())))
        }
    }

    fn wide(text: &str) -> Vec<u16> {
        OsStr::new(text)
            .encode_wide()
            .chain(std::iter::once(0))
            .collect()
    }

    unsafe fn read_raw(key: HKEY, name: &str) -> Option<(u32, Vec<u8>)> {
        unsafe {
            let name = wide(name);
            let mut kind: u32 = 0;
            let mut size: u32 = 0;
            let status = RegQueryValueExW(
                key,
                name.as_ptr(),
                std::ptr::null(),
                &mut kind,
                std::ptr::null_mut(),
                &mut size,
            );
            if status != ERROR_SUCCESS || size == 0 {
                return None;
            }
            let mut data = vec![0u8; size as usize];
            let status = RegQueryValueExW(
                key,
                name.as_ptr(),
                std::ptr::null(),
                &mut kind,
                data.as_mut_ptr(),
                &mut size,
            );
            if status != ERROR_SUCCESS {
                return None;
            }
            data.truncate(size as usize);
            Some((kind, data))
        }
    }

    fn read_dword(key: HKEY, name: &str) -> Option<u32> {
        unsafe {
            let (kind, data) = read_raw(key, name)?;
            if kind != REG_DWORD || data.len() < 4 {
                return None;
            }
            Some(u32::from_le_bytes([data[0], data[1], data[2], data[3]]))
        }
    }

    fn read_string(key: HKEY, name: &str) -> Option<String> {
        unsafe {
            let (kind, data) = read_raw(key, name)?;
            if kind != REG_SZ {
                return None;
            }
            let units: Vec<u16> = data
                .chunks_exact(2)
                .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
                .take_while(|unit| *unit != 0)
                .collect();
            if units.is_empty() {
                return None;
            }
            Some(String::from_utf16_lossy(&units))
        }
    }

    /// `ProxyServer` 可能是 `host:port`，也可能是
    /// `http=host:port;https=host:port` 形式，优先取 https。
    fn select_server(raw: &str) -> Option<String> {
        let raw = raw.trim();
        if raw.is_empty() {
            return None;
        }
        if !raw.contains('=') {
            return Some(with_scheme(raw));
        }
        let mut http = None;
        let mut https = None;
        for part in raw.split(';') {
            let Some((scheme, address)) = part.split_once('=') else {
                continue;
            };
            let address = address.trim();
            if address.is_empty() {
                continue;
            }
            match scheme.trim().to_ascii_lowercase().as_str() {
                "https" => https = Some(address.to_owned()),
                "http" => http = Some(address.to_owned()),
                _ => {}
            }
        }
        https.or(http).map(|address| with_scheme(&address))
    }

    fn with_scheme(address: &str) -> String {
        if address.contains("://") {
            address.to_owned()
        } else {
            format!("http://{address}")
        }
    }

    /// `ProxyOverride` 用分号分隔，`<local>` 表示本地地址，`*` 表示全部直连。
    fn normalize_overrides(raw: Option<&str>) -> Option<String> {
        let raw = raw?;
        let mut items = Vec::new();
        for item in raw.split(';') {
            let item = item.trim();
            if item.is_empty() || item.eq_ignore_ascii_case("<local>") {
                continue;
            }
            if item == "*" {
                return Some("*".to_owned());
            }
            items.push(item.to_owned());
        }
        (!items.is_empty()).then(|| items.join(","))
    }
}

#[cfg(not(any(target_os = "macos", windows)))]
mod platform {
    /// Linux 等平台没有统一的系统代理来源，沿用环境变量与显式配置。
    pub(super) fn system_proxy() -> Option<(String, Option<String>)> {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_bare_host_port() {
        assert_eq!(
            normalize_url("127.0.0.1:7897").as_deref(),
            Some("http://127.0.0.1:7897")
        );
        assert_eq!(
            normalize_url(" http://127.0.0.1:7897 ").as_deref(),
            Some("http://127.0.0.1:7897")
        );
        assert_eq!(normalize_url("socks5://127.0.0.1:7897"), None);
        assert_eq!(normalize_url(""), None);
    }

    #[test]
    fn merges_local_bypass() {
        let merged = merged_no_proxy(Some("*.local,169.254/16"));
        assert!(merged.contains("*.local"));
        assert!(merged.contains("localhost"));
        assert!(merged.contains("127.0.0.1"));
    }

    /// `*.local` 这类通配不能当成"全部直连"，否则系统代理会被整个丢弃。
    #[test]
    fn wildcard_entries_are_not_all_bypassed() {
        assert!(!all_bypassed("*.local,<local>,127.0.0.1"));
        assert!(!all_bypassed(""));
        assert!(!all_bypassed("*.crashlytics.com"));
        assert!(all_bypassed("*"));
        assert!(all_bypassed("localhost;*"));
    }

    #[test]
    fn fingerprint_changes_with_proxy() {
        let a = ProxyConfig {
            url: "http://127.0.0.1:7897".into(),
            no_proxy: None,
            source: ProxySource::System,
        };
        let b = ProxyConfig {
            url: "http://127.0.0.1:7898".into(),
            no_proxy: None,
            source: ProxySource::System,
        };
        assert_ne!(fingerprint(Some(&a)), fingerprint(Some(&b)));
        assert_eq!(fingerprint(None), "");
    }
}
