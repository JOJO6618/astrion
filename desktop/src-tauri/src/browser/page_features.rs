use super::{manager::Browser, views, *};
use serde_json::{json, Value};
use windows::{
    core::{Interface, HSTRING},
    Win32::System::Com::*,
};

impl Browser {
    pub fn find(&mut self, id: &str, value: &Value) -> Result<(), String> {
        let term = value["text"]
            .as_str()
            .filter(|s| s.len() <= 4000)
            .ok_or("invalid_browser_find")?;
        let index = self.index(id)?;
        let wc = self.wc(id)?;
        let find = unsafe { native_error(wc.cast::<ICoreWebView2_28>().and_then(|wc| wc.Find()))? };
        if term.is_empty() {
            unsafe {
                native_error(find.Stop())?;
            }
            self.tabs[index].find = None;
            return Ok(());
        }
        if value["next"] == true {
            unsafe {
                native_error(if value["forward"] == false {
                    find.FindPrevious()
                } else {
                    find.FindNext()
                })?;
            }
            return Ok(());
        }
        let environment = self.profiles[&self.profile]
            .environment
            .as_ref()
            .ok_or("browser_environment_not_ready")?;
        let options = unsafe {
            native_error(
                environment
                    .cast::<ICoreWebView2Environment15>()
                    .and_then(|e| e.CreateFindOptions()),
            )?
        };
        unsafe {
            native_error(options.SetFindTerm(&HSTRING::from(term)))?;
            native_error(options.SetSuppressDefaultFindDialog(true))?;
            native_error(options.SetShouldHighlightAllMatches(true))?;
            let mut token = 0;
            let app = self.app.clone();
            let tab = id.to_owned();
            native_error(find.add_MatchCountChanged(
                &FindMatchCountChangedEventHandler::create(Box::new(move |_, _| {
                    enqueue(&app, Event::Update(tab.clone()));
                    Ok(())
                })),
                &mut token,
            ))?;
            let app = self.app.clone();
            let tab = id.to_owned();
            native_error(find.add_ActiveMatchIndexChanged(
                &FindActiveMatchIndexChangedEventHandler::create(Box::new(move |_, _| {
                    enqueue(&app, Event::Update(tab.clone()));
                    Ok(())
                })),
                &mut token,
            ))?;
            native_error(find.Start(
                &options,
                &FindStartCompletedHandler::create(Box::new(|status| status)),
            ))?;
        }
        self.tabs[index].find = Some(json!({"matches":0,"active":0}));
        Ok(())
    }
    pub fn capture(&self, request: &Value, wc: &ICoreWebView2) -> Result<(), String> {
        use tauri::Manager;
        use windows::Win32::UI::Shell::*;
        let parent = self
            .app
            .get_window("main")
            .ok_or("browser_window_missing")?
            .hwnd()
            .map_err(|e| e.to_string())?;
        let path = unsafe {
            (|| -> windows::core::Result<std::path::PathBuf> {
                let dialog: IFileSaveDialog =
                    CoCreateInstance(&FileSaveDialog, None, CLSCTX_INPROC_SERVER)?;
                dialog.SetTitle(&HSTRING::from("Save webpage screenshot"))?;
                dialog.SetFileName(&HSTRING::from("webpage.png"))?;
                dialog.SetDefaultExtension(&HSTRING::from("png"))?;
                dialog.SetOptions(FOS_FORCEFILESYSTEM | FOS_OVERWRITEPROMPT | FOS_PATHMUSTEXIST)?;
                dialog.Show(Some(windows::Win32::Foundation::HWND(parent.0)))?;
                let item = dialog.GetResult()?;
                let value = item.GetDisplayName(SIGDN_FILESYSPATH)?;
                Ok(std::path::PathBuf::from(webview2_com::take_pwstr(value)))
            })()
        }
        .map_err(|e| format!("browser_capture_cancelled_or_failed: {e}"))?;
        let app = self.app.clone();
        let request = request.clone();
        let lease = self.lease.clone();
        views::cdp(
            wc,
            "Page.captureScreenshot",
            json!({"format":"png","captureBeyondViewport":false}),
            move |result| {
                use base64::Engine;
                let result = result.and_then(|value| {
                    let data = value["data"].as_str().ok_or("browser_capture_empty")?;
                    let bytes = base64::engine::general_purpose::STANDARD
                        .decode(data)
                        .map_err(|e| e.to_string())?;
                    std::fs::write(path, bytes).map_err(|e| e.to_string())
                });
                enqueue(
                    &app,
                    Event::Command {
                        request,
                        lease,
                        result,
                    },
                );
            },
        )
    }
}
