use super::{manager::Browser, policy, *};
use serde_json::json;
use windows::{
    core::{Interface, HSTRING},
    Win32::UI::Shell::*,
};

pub struct Permission {
    pub tab: String,
    pub owner: String,
    pub profile: String,
    pub kind: String,
    pub origin: String,
    pub args: ICoreWebView2PermissionRequestedEventArgs,
    pub deferral: ICoreWebView2Deferral,
    pub until: u64,
}
fn permission_kind(kind: COREWEBVIEW2_PERMISSION_KIND) -> Option<&'static str> {
    match kind {
        COREWEBVIEW2_PERMISSION_KIND_GEOLOCATION => Some("geolocation"),
        COREWEBVIEW2_PERMISSION_KIND_NOTIFICATIONS => Some("notifications"),
        COREWEBVIEW2_PERMISSION_KIND_CAMERA | COREWEBVIEW2_PERMISSION_KIND_MICROPHONE => {
            Some("media")
        }
        COREWEBVIEW2_PERMISSION_KIND_CLIPBOARD_READ => Some("clipboard-read"),
        _ => None,
    }
}
pub fn downloads_directory() -> Result<std::path::PathBuf, String> {
    unsafe {
        native_error(SHGetKnownFolderPath(
            &FOLDERID_Downloads,
            KF_FLAG_DEFAULT,
            None,
        ))
        .map(|path| std::path::PathBuf::from(webview2_com::take_pwstr(path)).join("Astrion"))
    }
}
impl Browser {
    pub fn permission(
        &mut self,
        id: &str,
        args: ICoreWebView2PermissionRequestedEventArgs,
        deferral: ICoreWebView2Deferral,
    ) {
        let result = (|| -> Result<(), String> {
            if id != self.active_id() || self.lease.is_empty() {
                return Err("inactive_browser_permission".into());
            }
            let tab = &self.tabs[self.index(id)?];
            let mut kind = COREWEBVIEW2_PERMISSION_KIND_UNKNOWN_PERMISSION;
            unsafe {
                native_error(args.PermissionKind(&mut kind))?;
                if let Ok(args) = args.cast::<ICoreWebView2PermissionRequestedEventArgs3>() {
                    native_error(args.SetSavesInProfile(false))?;
                }
            }
            let kind = permission_kind(kind)
                .ok_or("unsupported_browser_permission")?
                .to_owned();
            let origin = policy::origin(&unsafe { native_error(text(|out| args.Uri(out)))? })?;
            let current = policy::origin(&tab.url)?;
            if current != origin || tab.pending {
                return Err("stale_browser_permission".into());
            }
            let key = format!("{}:{origin}:{kind}", tab.profile);
            if self.grants.contains(&key) {
                unsafe {
                    native_error(args.SetState(COREWEBVIEW2_PERMISSION_STATE_ALLOW))?;
                    native_error(deferral.Complete())?;
                }
                return Ok(());
            }
            self.permissions.insert(
                policy::id(),
                Permission {
                    tab: id.into(),
                    owner: self.owner.clone(),
                    profile: self.profile.clone(),
                    kind,
                    origin,
                    args: args.clone(),
                    deferral: deferral.clone(),
                    until: policy::now() + 60000,
                },
            );
            Ok(())
        })();
        if result.is_err() {
            unsafe {
                let _ = args.SetState(COREWEBVIEW2_PERMISSION_STATE_DENY);
                let _ = deferral.Complete();
            }
        }
        self.publish();
    }
    pub fn resolve_permission(&mut self, id: &str, allow: bool) -> Result<(), String> {
        let permission = self
            .permissions
            .remove(id)
            .ok_or("unknown_browser_permission")?;
        let valid = permission.owner == self.owner
            && permission.profile == self.profile
            && permission.until > policy::now()
            && self.tabs.iter().any(|tab| {
                tab.id == permission.tab
                    && !tab.pending
                    && policy::origin(&tab.url).ok().as_deref() == Some(&permission.origin)
            });
        let allow = allow && valid;
        if allow {
            self.grants.insert(format!(
                "{}:{}:{}",
                permission.profile, permission.origin, permission.kind
            ));
        }
        unsafe {
            let state = if allow {
                COREWEBVIEW2_PERMISSION_STATE_ALLOW
            } else {
                COREWEBVIEW2_PERMISSION_STATE_DENY
            };
            let result = permission.args.SetState(state);
            let _ = permission.deferral.Complete();
            native_error(result)?;
        }
        Ok(())
    }
    pub fn cancel_permissions(&mut self) {
        for (_, permission) in self.permissions.drain() {
            unsafe {
                let _ = permission.args.SetState(COREWEBVIEW2_PERMISSION_STATE_DENY);
                let _ = permission.deferral.Complete();
            }
        }
    }
    pub fn deny_tab_permissions(&mut self, id: &str) {
        let ids: Vec<_> = self
            .permissions
            .iter()
            .filter(|(_, p)| p.tab == id)
            .map(|(id, _)| id.clone())
            .collect();
        for id in ids {
            let _ = self.resolve_permission(&id, false);
        }
    }
    pub fn expire(&mut self) {
        if self
            .imports
            .preview
            .as_ref()
            .is_some_and(|preview| preview.until <= policy::now())
        {
            self.imports.preview = None;
        }
        let ids: Vec<_> = self
            .permissions
            .iter()
            .filter(|(_, p)| p.until <= policy::now())
            .map(|(id, _)| id.clone())
            .collect();
        let changed = !ids.is_empty();
        for id in ids {
            let _ = self.resolve_permission(&id, false);
        }
        let credentials_changed = self.credentials.expire();
        if changed || credentials_changed {
            self.publish();
        }
    }
    pub fn download(
        &mut self,
        id: &str,
        args: ICoreWebView2DownloadStartingEventArgs,
        deferral: ICoreWebView2Deferral,
    ) {
        let result = (|| -> Result<(), String> {
            let index = self
                .tabs
                .iter()
                .position(|tab| tab.id == id)
                .ok_or("unknown_browser_download_page")?;
            let operation = unsafe { native_error(args.DownloadOperation())? };
            let suggested = unsafe { native_error(text(|out| args.ResultFilePath(out)))? };
            let name = std::path::Path::new(&suggested)
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or("download".into());
            let name = policy::filename(&name);
            let directory = match self
                .ai
                .download_dirs
                .get(id)
                .filter(|(_, until)| *until > policy::now())
            {
                Some((directory, _)) => directory.clone(),
                None => downloads_directory()?,
            };
            std::fs::create_dir_all(&directory).map_err(|e| e.to_string())?;
            let name_path = std::path::Path::new(&name);
            let stem = name_path.file_stem().unwrap_or_default().to_string_lossy();
            let extension = name_path
                .extension()
                .map(|s| format!(".{}", s.to_string_lossy()))
                .unwrap_or_default();
            let mut target = None;
            for suffix in 0..10000 {
                let file = directory.join(if suffix == 0 {
                    name.clone()
                } else {
                    format!("{stem} ({suffix}){extension}")
                });
                match std::fs::OpenOptions::new()
                    .create_new(true)
                    .write(true)
                    .open(&file)
                {
                    Ok(_) => {
                        target = Some(file);
                        break;
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                    Err(error) => return Err(error.to_string()),
                }
            }
            let target = target.ok_or("browser_download_name_exhausted")?;
            unsafe {
                native_error(args.SetResultFilePath(&HSTRING::from(target.as_path())))?;
                native_error(args.SetHandled(true))?;
            }
            let download_id = policy::id();
            self.ai_event(
                id,
                "native.download",
                json!({"id":download_id,"path":target,"name":name,"state":"progressing"}),
            );
            let profile = self.tabs[index].profile.clone();
            self.profiles.get_mut(&profile).unwrap().data.downloads.insert(0,json!({"id":download_id,"name":target.file_name().unwrap().to_string_lossy(),"path":target,"received":0,"total":0,"state":"progressing","at":policy::now(),"tabId":id}));
            self.profiles
                .get_mut(&profile)
                .unwrap()
                .data
                .downloads
                .truncate(100);
            self.downloads
                .insert(download_id.clone(), operation.clone());
            let app = self.app.clone();
            let key = download_id.clone();
            let mut token = 0;
            unsafe {
                native_error(operation.add_BytesReceivedChanged(
                    &BytesReceivedChangedEventHandler::create(Box::new(move |_, _| {
                        enqueue(&app, Event::DownloadUpdate(key.clone()));
                        Ok(())
                    })),
                    &mut token,
                ))?;
            }
            let app = self.app.clone();
            let key = download_id.clone();
            unsafe {
                native_error(operation.add_StateChanged(
                    &StateChangedEventHandler::create(Box::new(move |_, _| {
                        enqueue(&app, Event::DownloadUpdate(key.clone()));
                        Ok(())
                    })),
                    &mut token,
                ))?;
            }
            enqueue(&self.app, Event::DownloadUpdate(download_id.clone()));
            if !self.tabs[index].committed {
                self.tabs[index].retired = true;
                self.tabs[index].pending = false;
                let owner = self.tabs[index].owner.clone();
                let previous = self.tabs[index].previous.clone();
                if self.active_id() == id {
                    let next = self
                        .tabs
                        .iter()
                        .find(|tab| tab.id == previous && !tab.retired)
                        .or_else(|| {
                            self.tabs
                                .iter()
                                .find(|tab| tab.owner == owner && !tab.retired)
                        })
                        .map(|tab| tab.id.clone());
                    if let Some(next) = next {
                        self.selected.insert(owner.clone(), next);
                    } else {
                        self.create_tab("about:blank")?;
                    }
                }
                self.persist(&owner);
            } else {
                self.tabs[index].pending = false;
                self.tabs[index].loading = false;
            }
            self.profiles[&profile].save()?;
            Ok(())
        })();
        if let Err(error) = result {
            unsafe {
                let _ = args.SetCancel(true);
            }
            self.message(error);
        }
        unsafe {
            let _ = deferral.Complete();
        }
        self.layout();
        self.publish();
    }
    pub fn download_update(&mut self, id: &str) {
        let Some(operation) = self.downloads.get(id) else {
            return;
        };
        let (mut received, mut total) = (0, 0);
        let mut state = COREWEBVIEW2_DOWNLOAD_STATE_IN_PROGRESS;
        unsafe {
            let _ = operation.BytesReceived(&mut received);
            let _ = operation.TotalBytesToReceive(&mut total);
            let _ = operation.State(&mut state);
        }
        let done = state != COREWEBVIEW2_DOWNLOAD_STATE_IN_PROGRESS;
        let mut retired = None;
        for profile in self.profiles.values_mut() {
            if let Some(row) = profile
                .data
                .downloads
                .iter_mut()
                .find(|row| row["id"] == id)
            {
                row["received"] = json!(received);
                row["total"] = json!(total);
                row["state"] = json!(if state == COREWEBVIEW2_DOWNLOAD_STATE_COMPLETED {
                    "completed"
                } else if done {
                    "interrupted"
                } else {
                    "progressing"
                });
                if done {
                    retired = row["tabId"].as_str().map(str::to_owned);
                    let _ = profile.save();
                }
                break;
            }
        }
        if done {
            self.downloads.remove(id);
            if let Some(tab) = retired {
                if self.tabs.iter().any(|t| t.id == tab && t.retired) {
                    let _ = self.close_tab(&tab);
                }
            }
        }
        if done || policy::now().saturating_sub(self.download_published) >= 200 {
            self.download_published = policy::now();
            self.publish();
        }
    }
    pub fn download_command(&mut self, action: &str, id: &str) -> Result<(), String> {
        let profile = self
            .profiles
            .get_mut(&self.profile)
            .ok_or("browser_profile_missing")?;
        let index = profile
            .data
            .downloads
            .iter()
            .position(|row| row["id"] == id)
            .ok_or("unknown_browser_download")?;
        match action {
            "download-cancel" => {
                if let Some(operation) = self.downloads.get(id) {
                    unsafe {
                        native_error(operation.Cancel())?;
                    }
                }
            }
            "download-reveal" => {
                let path = profile.data.downloads[index]["path"]
                    .as_str()
                    .ok_or("browser_download_path_missing")?;
                std::process::Command::new("explorer.exe")
                    .arg(format!("/select,{path}"))
                    .spawn()
                    .map_err(|e| e.to_string())?;
            }
            "download-remove" => {
                if self.downloads.contains_key(id) {
                    return Err("download_still_running".into());
                }
                profile.data.downloads.remove(index);
                profile.save()?;
            }
            _ => return Err("unsupported_browser_download_action".into()),
        }
        Ok(())
    }
    pub fn context(
        &mut self,
        id: &str,
        args: ICoreWebView2ContextMenuRequestedEventArgs,
        deferral: ICoreWebView2Deferral,
    ) {
        // WebView2 owns its native page menu, including link/image/media actions. It is
        // separate from application DOM menus and has no access to the trusted bridge.
        if id != self.active_id() || self.lease.is_empty() {
            unsafe {
                let _ = args.SetHandled(true);
            }
        }
        unsafe {
            let _ = deferral.Complete();
        }
    }
}
