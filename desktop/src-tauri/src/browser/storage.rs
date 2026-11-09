//! A hidden raw controller keeps CookieManager/Profile available on internal pages.
use super::{manager::Browser, *};
use tauri::Manager;
use windows::Win32::Foundation::HWND;
impl Browser {
    pub fn create_storage(
        &self,
        key: &str,
        environment: &ICoreWebView2Environment,
    ) -> Result<(), String> {
        let parent = self
            .app
            .get_window("main")
            .ok_or("browser_window_missing")?
            .hwnd()
            .map_err(|e| e.to_string())?;
        let app = self.app.clone();
        let key = key.to_owned();
        unsafe {
            native_error(environment.CreateCoreWebView2Controller(
                HWND(parent.0),
                &CreateCoreWebView2ControllerCompletedHandler::create(Box::new(
                    move |status, controller| {
                        enqueue(
                            &app,
                            Event::Storage(
                                key,
                                status.map_err(|e| e.to_string()).and_then(|_| {
                                    controller.ok_or("browser_storage_controller_missing".into())
                                }),
                            ),
                        );
                        Ok(())
                    },
                )),
            ))
        }
    }
    pub fn storage_ready(&mut self, key: &str, result: Result<ICoreWebView2Controller, String>) {
        match result {
            Ok(controller) => {
                unsafe {
                    let _ = controller.SetIsVisible(false);
                }
                if let Some(profile) = self.profiles.get_mut(key) {
                    profile.storage = Some(controller);
                } else {
                    unsafe {
                        let _ = controller.Close();
                    }
                }
            }
            Err(error) => self.message(error),
        }
    }
    pub fn storage_view(&self) -> Result<ICoreWebView2, String> {
        let profile = self
            .profiles
            .get(&self.profile)
            .ok_or("browser_profile_missing")?;
        if let Some(controller) = &profile.storage {
            return unsafe { native_error(controller.CoreWebView2()) };
        }
        self.tabs
            .iter()
            .find(|t| t.profile == self.profile && t.webview.is_some())
            .and_then(|t| t.webview.clone())
            .ok_or("browser_storage_initializing_retry".into())
    }
}
