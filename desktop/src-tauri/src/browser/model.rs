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
pub struct Data {
    pub version: u32,
    pub history: Vec<Value>,
    pub downloads: Vec<Value>,
    pub owners: HashMap<String, SavedOwner>,
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
