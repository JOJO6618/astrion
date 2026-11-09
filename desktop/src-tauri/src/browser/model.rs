use super::policy;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
};
use webview2_com::Microsoft::Web::WebView2::Win32::*;
use windows::Win32::Foundation::HWND;

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct SavedOwner {
    pub urls: Vec<String>,
    pub active: usize,
    pub at: u64,
}
#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    /// Empty means "not chosen yet"; navigation falls back to Google and the panel stores a
    /// language-based default on first connect.
    #[serde(rename = "searchEngine")]
    pub search_engine: String,
}
#[derive(Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct Shortcut {
    pub url: String,
    pub title: String,
}
/// New-tab shortcuts: pinned tiles (user-added or pinned) and site keys hidden from
/// "most visited". Same contract as desktop-electron/src/browser/profile.js.
#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Shortcuts {
    pub pinned: Vec<Shortcut>,
    pub hidden: Vec<String>,
}
const MAX_PINNED: usize = 10;
const MAX_HIDDEN: usize = 200;
impl Shortcuts {
    /// strict: reject the whole value on any invalid entry (UI commands); otherwise drop
    /// invalid entries (data loaded from disk).
    pub fn normalize(value: &Value, strict: bool) -> Result<Self, String> {
        let fail = |out: Self| {
            if strict {
                Err("invalid_browser_shortcuts".to_string())
            } else {
                Ok(out)
            }
        };
        let mut out = Self::default();
        if !value.is_object() {
            return fail(out);
        }
        match &value["pinned"] {
            Value::Null => {}
            Value::Array(rows) => {
                if strict && rows.len() > MAX_PINNED {
                    return fail(out);
                }
                for row in rows.iter().take(MAX_PINNED) {
                    let raw = row["url"].as_str().unwrap_or("").trim();
                    let lower = raw.to_ascii_lowercase();
                    let url = (lower.starts_with("http://") || lower.starts_with("https://"))
                        .then(|| policy::navigation(raw, false).ok())
                        .flatten();
                    let Some(url) = url else {
                        if strict {
                            return fail(out);
                        }
                        continue;
                    };
                    if out.pinned.iter().any(|item| item.url == url) {
                        continue;
                    }
                    let title: String = row["title"]
                        .as_str()
                        .unwrap_or("")
                        .chars()
                        .map(|c| if c.is_control() { ' ' } else { c })
                        .collect::<String>()
                        .trim()
                        .chars()
                        .take(60)
                        .collect();
                    let title = if title.is_empty() {
                        tauri::Url::parse(&url)
                            .ok()
                            .and_then(|u| {
                                u.host_str()
                                    .map(|h| h.trim_start_matches("www.").to_owned())
                            })
                            .unwrap_or_default()
                    } else {
                        title
                    };
                    out.pinned.push(Shortcut { url, title });
                }
            }
            _ => return fail(out),
        }
        match &value["hidden"] {
            Value::Null => {}
            Value::Array(keys) => {
                if strict && keys.len() > MAX_HIDDEN {
                    return fail(out);
                }
                for key in keys.iter().take(MAX_HIDDEN) {
                    let valid = key.as_str().filter(|k| {
                        !k.is_empty()
                            && k.len() <= 260
                            && k.chars()
                                .all(|c| c.is_ascii_alphanumeric() || ".:[]-".contains(c))
                    });
                    match valid {
                        Some(k) => {
                            let k = k.to_ascii_lowercase();
                            if !out.hidden.contains(&k) {
                                out.hidden.push(k);
                            }
                        }
                        None if strict => return fail(out),
                        None => {}
                    }
                }
            }
            _ => return fail(out),
        }
        Ok(out)
    }
}
#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Data {
    pub version: u32,
    pub history: Vec<Value>,
    pub downloads: Vec<Value>,
    pub owners: HashMap<String, SavedOwner>,
    pub settings: Settings,
    pub shortcuts: Shortcuts,
}
pub struct Profile {
    pub directory: PathBuf,
    pub environment: Option<ICoreWebView2Environment>,
    pub storage: Option<ICoreWebView2Controller>,
    pub data: Data,
    pub vault: super::vault::Vault,
}
impl Profile {
    pub fn load(root: &Path, key: &str) -> Result<Self, String> {
        let directory = root.join("browser").join(key);
        std::fs::create_dir_all(directory.join("webview2")).map_err(|e| e.to_string())?;
        let file = directory.join("metadata.json");
        let mut data: Data = match std::fs::read(&file) {
            Ok(bytes) => {
                serde_json::from_slice(&bytes).map_err(|_| "browser_profile_read_failed")?
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Data::default(),
            Err(e) => return Err(e.to_string()),
        };
        data.version = 1;
        data.downloads.truncate(100);
        if !policy::SEARCH_ENGINES
            .iter()
            .any(|(name, _, _)| *name == data.settings.search_engine)
        {
            data.settings.search_engine.clear();
        }
        data.shortcuts = Shortcuts::normalize(
            &serde_json::to_value(&data.shortcuts).unwrap_or_default(),
            false,
        )
        .unwrap_or_default();
        for row in &mut data.downloads {
            if row["state"] == "progressing" {
                row["state"] = json!("interrupted");
            }
        }
        let vault = super::vault::Vault::open(&directory);
        Ok(Self {
            directory,
            environment: None,
            storage: None,
            data,
            vault,
        })
    }
    pub fn save(&self) -> Result<(), String> {
        atomic(
            &self.directory.join("metadata.json"),
            &serde_json::to_vec(&self.data).map_err(|e| e.to_string())?,
        )
    }
    pub fn search_engine(&self) -> &str {
        if self.data.settings.search_engine.is_empty() {
            "google"
        } else {
            self.data.settings.search_engine.as_str()
        }
    }
    pub fn record(&mut self, url: &str, title: &str) {
        if !url.starts_with("http") {
            return;
        }
        if let Some(last) = self.data.history.first_mut().filter(|last| {
            last["url"] == url
                && policy::now().saturating_sub(last["at"].as_u64().unwrap_or(0)) < 30000
        }) {
            last["title"] = json!(title);
        } else {
            self.data.history.insert(
                0,
                json!({"id":policy::id(),"url":url,"title":title,"at":policy::now()}),
            );
        }
        self.data.history.truncate(20000);
    }
}
impl Drop for Profile {
    fn drop(&mut self) {
        if let Some(controller) = self.storage.take() {
            unsafe {
                let _ = controller.Close();
            }
        }
    }
}
// ReplaceFile/MoveFileEx replace atomically on Windows, including an existing destination.
pub fn atomic(file: &Path, bytes: &[u8]) -> Result<(), String> {
    use windows::{
        core::HSTRING,
        Win32::Storage::FileSystem::{
            MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
        },
    };
    let temp = file.with_extension(format!("{}.tmp", policy::id()));
    let result = (|| {
        use std::io::Write;
        let mut stream = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)?;
        stream.write_all(bytes)?;
        stream.sync_all()?;
        drop(stream);
        unsafe {
            MoveFileExW(
                &HSTRING::from(temp.as_path()),
                &HSTRING::from(file),
                MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
            )
        }
        .map_err(std::io::Error::other)
    })()
    .map_err(|e: std::io::Error| e.to_string());
    if result.is_err() {
        let _ = std::fs::remove_file(temp);
    }
    result
}
pub struct Tab {
    pub id: String,
    pub owner: String,
    pub profile: String,
    pub section: Option<String>,
    pub url: String,
    pub title: String,
    pub favicon: String,
    pub error: String,
    pub pending: bool,
    pub loading: bool,
    pub controller: Option<ICoreWebView2Controller>,
    pub webview: Option<ICoreWebView2>,
    pub host: HWND,
    pub zoom: f64,
    pub find: Option<Value>,
    pub previous: String,
    pub document: String,
    pub contexts: HashMap<i64, String>,
    pub frame: String,
    pub committed: bool,
    pub retired: bool,
    pub local_target: std::rc::Rc<std::cell::RefCell<Option<String>>>,
}
impl Tab {
    pub fn new(owner: &str, profile: &str, url: &str, previous: String) -> Self {
        Self {
            id: policy::id(),
            owner: owner.into(),
            profile: profile.into(),
            section: policy::internal(url).map(str::to_owned),
            url: url.into(),
            title: String::new(),
            favicon: String::new(),
            error: String::new(),
            pending: false,
            loading: false,
            controller: None,
            webview: None,
            host: HWND::default(),
            zoom: 1.,
            find: None,
            previous,
            document: String::new(),
            contexts: HashMap::new(),
            frame: String::new(),
            committed: false,
            retired: false,
            local_target: Default::default(),
        }
    }
    pub fn snapshot(&self) -> Value {
        let (mut back, mut forward) = (
            windows::core::BOOL::default(),
            windows::core::BOOL::default(),
        );
        if let Some(wc) = &self.webview {
            unsafe {
                let _ = wc.CanGoBack(&mut back);
                let _ = wc.CanGoForward(&mut forward);
            }
        }
        let mut row = json!({"id":self.id,"url":self.url,"title":self.title,"favicon":self.favicon,"error":self.error,"loading":self.loading || self.pending,"pending":self.pending,"canGoBack":back.as_bool(),"canGoForward":forward.as_bool(),"zoom":(self.zoom*100.).round(),"find":self.find});
        if let Some(section) = &self.section {
            row["section"] = json!(section);
        }
        row
    }
}
impl Drop for Tab {
    fn drop(&mut self) {
        unsafe {
            if let Some(controller) = self.controller.take() {
                let _ = controller.Close();
            }
            if !self.host.is_invalid() {
                let _ = windows::Win32::UI::WindowsAndMessaging::DestroyWindow(self.host);
            }
        }
    }
}
