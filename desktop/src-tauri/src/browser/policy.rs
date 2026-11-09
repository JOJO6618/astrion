//! URL, identity and layout checks shared by all native browser entry points.
use serde_json::Value;
use sha2::{Digest, Sha256};
use tauri::Url;

pub const SECTIONS: [&str; 6] = [
    "history",
    "downloads",
    "settings",
    "passwords",
    "import",
    "clear",
];
pub fn id() -> String {
    uuid::Uuid::new_v4().to_string()
}
pub fn now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
pub fn profile_key(user: &str) -> String {
    format!(
        "{:x}",
        Sha256::digest(user.trim().to_lowercase().as_bytes())
    )[..32]
        .into()
}
pub fn internal(value: &str) -> Option<&str> {
    value
        .strip_prefix("astrion://browser/")
        .map(|s| s.trim_end_matches('/'))
        .filter(|s| SECTIONS.contains(s))
}
pub fn scope(value: &Value) -> Result<String, String> {
    let mode = value["mode"]
        .as_str()
        .filter(|s| ["agent", "chat"].contains(s))
        .ok_or("invalid_browser_scope")?;
    let workspace = value["workspace"].as_str().ok_or("invalid_browser_scope")?;
    let conversation = value["conversation"]
        .as_str()
        .filter(|s| !s.is_empty())
        .ok_or("invalid_browser_scope")?;
    if [workspace, conversation]
        .iter()
        .any(|s| s.len() > 1024 || s.chars().count() > 256 || s.chars().any(char::is_control))
    {
        return Err("invalid_browser_scope".into());
    }
    Ok(serde_json::json!([mode, workspace, conversation]).to_string())
}
pub fn route(path: &str) -> bool {
    path == "/"
        || path.trim_end_matches('/') == "/new"
        || path.strip_prefix('/').is_some_and(|s| {
            let id = s.trim_end_matches('/');
            id.len() == 19
                && id.bytes().enumerate().all(|(i, c)| {
                    if i == 8 || i == 15 {
                        c == b'_'
                    } else {
                        c.is_ascii_digit()
                    }
                })
        })
        || path.strip_prefix("/conv_").is_some_and(|s| {
            !s.is_empty()
                && s.trim_end_matches('/')
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
        })
        || path == "/chat"
        || path
            .strip_prefix("/chat/")
            .is_some_and(|s| !s.trim_end_matches('/').contains('/'))
}
pub fn trusted(source: &str, current: &str, origin: &str) -> bool {
    source == current
        && Url::parse(source)
            .is_ok_and(|url| url.origin().ascii_serialization() == origin && route(url.path()))
}
/// Keep in sync with desktop-electron/src/browser/policy.js SEARCH_ENGINES.
pub const SEARCH_ENGINES: [(&str, &str, &str); 4] = [
    ("google", "https://www.google.com/search", "q"),
    ("bing_cn", "https://cn.bing.com/search", "q"),
    ("bing", "https://www.bing.com/search", "q"),
    ("baidu", "https://www.baidu.com/s", "wd"),
];
pub fn search_url(engine: &str, query: &str) -> String {
    let (_, base, key) = SEARCH_ENGINES
        .iter()
        .find(|(name, _, _)| *name == engine)
        .unwrap_or(&SEARCH_ENGINES[0]);
    let mut url = Url::parse(base).unwrap();
    url.query_pairs_mut().append_pair(key, query);
    url.into()
}
pub fn navigation(input: &str, search: bool) -> Result<String, String> {
    navigation_with(input, search.then_some("google"))
}
/// `engine`: turn keywords into a search on that engine; None rejects non-URL input.
pub fn navigation_with(input: &str, engine: Option<&str>) -> Result<String, String> {
    let input = input.trim();
    if input.len() > 8192 {
        return Err("invalid_browser_url".into());
    }
    if input.is_empty() || input == "about:blank" {
        return Ok("about:blank".into());
    }
    if input.contains("://")
        || input.starts_with("javascript:")
        || input.starts_with("file:")
        || input.starts_with("data:")
        || input.starts_with("about:")
    {
        return validated(input);
    }
    let head = input.split('/').next().unwrap_or("");
    let looks_like_host = !input.chars().any(char::is_whitespace)
        && (head.contains('.') || head.starts_with("localhost") || head.starts_with("[::1]"));
    if let Some(engine) = engine.filter(|_| !looks_like_host) {
        return Ok(search_url(engine, input));
    }
    let local =
        head.starts_with("localhost") || head.starts_with("127.0.0.1") || head.starts_with("[::1]");
    validated(&format!(
        "{}://{input}",
        if local { "http" } else { "https" }
    ))
}
fn validated(input: &str) -> Result<String, String> {
    let url = Url::parse(input).map_err(|_| "invalid_browser_url")?;
    if !["http", "https"].contains(&url.scheme())
        || !url.username().is_empty()
        || url.password().is_some()
        || url.host_str().is_none()
    {
        return Err("unsupported_browser_url".into());
    }
    Ok(url.into())
}
/// Local file syntax; AI additionally requires a backend grant, credential origins stay web-only.
pub fn manual_navigation(input: &str, search: bool) -> Result<String, String> {
    manual_navigation_with(input, search.then_some("google"))
}
pub fn manual_navigation_with(input: &str, engine: Option<&str>) -> Result<String, String> {
    if !input.trim().to_ascii_lowercase().starts_with("file:") {
        return navigation_with(input, engine);
    }
    let url = Url::parse(input.trim()).map_err(|_| "invalid_browser_url")?;
    if input.len() > 8192
        || input.chars().any(char::is_control)
        || url.scheme() != "file"
        || url.host_str().is_some_and(|h| h != "localhost")
        || !url.username().is_empty()
        || url.password().is_some()
        || url.path().to_ascii_lowercase().contains("%2f")
        || url.path().to_ascii_lowercase().contains("%5c")
        || url.path().as_bytes().iter().enumerate().any(|(i, b)| {
            *b == b'%'
                && !url
                    .path()
                    .as_bytes()
                    .get(i + 1..i + 3)
                    .is_some_and(|s| s.iter().all(u8::is_ascii_hexdigit))
        })
    {
        return Err("unsupported_browser_url".into());
    }
    let path = url.to_file_path().map_err(|_| "unsupported_browser_url")?;
    if !path.is_absolute()
        || path.to_string_lossy().chars().any(char::is_control)
        || !matches!(path.components().next(), Some(std::path::Component::Prefix(p)) if matches!(p.kind(), std::path::Prefix::Disk(_)))
    {
        return Err("unsupported_browser_url".into());
    }
    Ok(url.into())
}
pub fn local_file(input: &str) -> bool {
    input.to_ascii_lowercase().starts_with("file:") && manual_navigation(input, false).is_ok()
}
pub fn origin(input: &str) -> Result<String, String> {
    Ok(Url::parse(&validated(input)?)
        .map_err(|_| "invalid_browser_url")?
        .origin()
        .ascii_serialization())
}
pub fn filename(input: &str) -> String {
    let name: String = input
        .chars()
        .map(|c| {
            if c.is_control() || "<>:\"/\\|?*".contains(c) {
                '_'
            } else {
                c
            }
        })
        .take(150)
        .collect();
    let name = name.trim_matches(['.', ' ']);
    if name.is_empty() {
        "download".into()
    } else {
        name.into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn denies_privileged_urls_and_credentials() {
        for url in [
            "file:///C:/x",
            "javascript:alert(1)",
            "data:text/html,x",
            "https://a:b@example.com/",
        ] {
            assert!(navigation(url, true).is_err(), "{url}");
        }
    }
    #[test]
    fn manual_local_files_do_not_grant_web_origins() {
        assert_eq!(
            manual_navigation("file:///C:/example%20dir/local.html#part", true).unwrap(),
            "file:///C:/example%20dir/local.html#part"
        );
        assert!(manual_navigation("file://localhost/C:/example.html", false).is_ok());
        for url in [
            "file://server/share/x.html",
            "file:////server/share/x.html",
            "file:///C:/bad%00name",
            "file:///C:/bad%2Fname",
            "file://user:pass@host/a",
        ] {
            assert!(manual_navigation(url, true).is_err(), "{url}");
        }
        assert!(origin("file:///C:/local.html").is_err());
        assert!(navigation("file:///C:/local.html", false).is_err());
    }
    #[test]
    fn searches_and_normalizes_hosts() {
        assert_eq!(
            navigation("localhost:8091", true).unwrap(),
            "http://localhost:8091/"
        );
        assert!(navigation("hello world", true)
            .unwrap()
            .ends_with("q=hello+world"));
    }
    #[test]
    fn sender_requires_exact_document_and_backend() {
        assert!(trusted(
            "http://127.0.0.1:42/new",
            "http://127.0.0.1:42/new",
            "http://127.0.0.1:42"
        ));
        assert!(!trusted(
            "http://127.0.0.1:43/new",
            "http://127.0.0.1:43/new",
            "http://127.0.0.1:42"
        ));
        assert!(!trusted(
            "http://127.0.0.1:42/settings",
            "http://127.0.0.1:42/settings",
            "http://127.0.0.1:42"
        ));
    }
    #[test]
    fn stable_scope_rejects_missing_owner() {
        assert!(
            scope(&serde_json::json!({"mode":"agent","workspace":"w","conversation":""})).is_err()
        );
    }
}
