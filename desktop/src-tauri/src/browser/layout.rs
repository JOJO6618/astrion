//! Native region clipping keeps DOM menus visible without replacing or reloading pages.
use super::manager::Browser;
use serde_json::Value;
use tauri::Manager;
use windows::{
    core::Interface,
    Win32::{
        Foundation::{HWND, POINT, RECT},
        Graphics::Gdi::*,
        UI::WindowsAndMessaging::*,
    },
};

fn number(value: &Value, key: &str) -> Option<f64> {
    value[key]
        .as_f64()
        .filter(|n| n.is_finite() && n.abs() < 100000.)
}
pub fn valid_rect(value: &Value) -> bool {
    ["x", "y", "width", "height"]
        .iter()
        .all(|key| number(value, key).is_some())
        && value["width"].as_f64().unwrap() > 0.
        && value["height"].as_f64().unwrap() > 0.
}
impl Browser {
    pub fn layout(&mut self) {
        let Some(window) = self.app.get_window("main") else {
            return;
        };
        let Ok(parent) = window.hwnd() else { return };
        let mut main_bounds = RECT::default();
        let mut wrapper = HWND::default();
        let mut zoom = 1.;
        unsafe {
            if self.main.Bounds(&mut main_bounds).is_err()
                || self.main.ParentWindow(&mut wrapper).is_err()
            {
                return;
            }
            let _ = self.main.ZoomFactor(&mut zoom);
        }
        let factor = window.scale_factor().unwrap_or(1.) * zoom;
        let mut position = RECT::default();
        unsafe {
            let _ = GetWindowRect(wrapper, &mut position);
        }
        let mut offset = POINT {
            x: position.left,
            y: position.top,
        };
        unsafe {
            let _ = ScreenToClient(HWND(parent.0), &mut offset);
        }
        let rect = &self.geometry["rect"];
        let visible =
            !self.lease.is_empty() && self.geometry["visible"] == true && valid_rect(rect);
        let active = self.active_id();
        let mut x = number(rect, "x").unwrap_or(0.);
        let y = number(rect, "y").unwrap_or(0.);
        let mut width = number(rect, "width").unwrap_or(0.);
        let mut height = number(rect, "height").unwrap_or(0.);
        if let (Some(old_width), Some(old_height)) = (
            number(&self.geometry["viewport"], "width"),
            number(&self.geometry["viewport"], "height"),
        ) {
            let current_width = (main_bounds.right - main_bounds.left) as f64 / factor;
            let current_height = (main_bounds.bottom - main_bounds.top) as f64 / factor;
            let right = old_width - x - width;
            let panel = number(&self.geometry["viewport"], "panelWidth")
                .unwrap_or(width)
                .max(380.)
                .min((current_width - 360.).max(0.));
            width =
                (panel - number(&self.geometry["viewport"], "surfaceInset").unwrap_or(0.)).max(0.);
            x = current_width - right - width;
            height = (height + current_height - old_height).max(0.);
        }
        let left = (x.max(0.) * factor).round() as i32;
        let top = (y.max(0.) * factor).round() as i32;
        let width = (width * factor)
            .floor()
            .max(0.)
            .min((main_bounds.right - left).max(0) as f64) as i32;
        let height = (height * factor)
            .floor()
            .max(0.)
            .min((main_bounds.bottom - top).max(0) as f64) as i32;
        let mut refocus_main = false;
        for tab in &self.tabs {
            let Some(controller) = &tab.controller else {
                continue;
            };
            let show = visible
                && tab.id == active
                && tab.owner == self.owner
                && tab.section.is_none()
                && tab.error.is_empty()
                && !tab.pending
                && tab.url != "about:blank"
                && width > 0
                && height > 0;
            unsafe {
                if !show {
                    // A hidden child HWND keeps keyboard focus on Windows; hand it back to the
                    // app UI so the address bar / new-tab page can receive typing.
                    refocus_main |= IsWindowVisible(tab.host).as_bool();
                    let _ = controller.SetIsVisible(false);
                    let _ = ShowWindow(tab.host, SW_HIDE);
                    continue;
                }
                let _ = SetWindowPos(
                    tab.host,
                    Some(HWND_TOP),
                    offset.x + left,
                    offset.y + top,
                    width,
                    height,
                    SWP_NOACTIVATE | SWP_SHOWWINDOW,
                );
                let _ = controller.SetBounds(RECT {
                    left: 0,
                    top: 0,
                    right: width,
                    bottom: height,
                });
                if let Ok(controller)=controller.cast::<webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2Controller2>(){
                    let raw=u32::from_str_radix(self.surface.trim_start_matches('#'),16).unwrap_or(0xfaf9f5);
                    let _=controller.SetDefaultBackgroundColor(webview2_com::Microsoft::Web::WebView2::Win32::COREWEBVIEW2_COLOR{A:255,R:(raw>>16)as u8,G:(raw>>8)as u8,B:raw as u8});
                }
                let region = CreateRectRgn(0, 0, width, height);
                if let Some(obstructions) = self.geometry["occlusions"].as_array() {
                    for cut in obstructions.iter().take(128).filter(|cut| valid_rect(cut)) {
                        let cut_x = (cut["x"].as_f64().unwrap() * factor).floor() as i32 - left;
                        let cut_y = (cut["y"].as_f64().unwrap() * factor).floor() as i32 - top;
                        let hole = CreateRectRgn(
                            cut_x,
                            cut_y,
                            cut_x + (cut["width"].as_f64().unwrap() * factor).ceil() as i32,
                            cut_y + (cut["height"].as_f64().unwrap() * factor).ceil() as i32,
                        );
                        CombineRgn(Some(region), Some(region), Some(hole), RGN_DIFF);
                        let _ = DeleteObject(hole.into());
                    }
                }
                if SetWindowRgn(tab.host, Some(region), true) == 0 {
                    let _ = DeleteObject(region.into());
                }
                let _ = controller.SetIsVisible(true);
            }
        }
        if refocus_main || self.geometry["focusFind"] == true {
            self.focus_main();
        }
    }
    pub fn focus_main(&self) {
        if let Some(main) = self.app.get_webview("main") {
            let _ = main.set_focus();
        }
    }
    pub fn apply_layout(&mut self, value: &Value) -> Result<(), String> {
        let sequence = value["sequence"]
            .as_u64()
            .ok_or("invalid_browser_layout_sequence")?;
        if sequence <= self.layout_sequence {
            return Ok(());
        }
        if value["visible"] == true && !valid_rect(&value["rect"]) {
            return Err("invalid_browser_layout_rect".into());
        }
        if let Some(cuts) = value["occlusions"].as_array() {
            if cuts.len() > 128 || cuts.iter().any(|cut| !valid_rect(cut)) {
                return Err("invalid_browser_occlusion".into());
            }
        }
        self.layout_sequence = sequence;
        self.geometry = value.clone();
        self.apply_theme(value);
        self.layout();
        Ok(())
    }
    /// 接受完整主题对象（layout 载荷）或单独的 surfaceColor 字符串。
    pub fn apply_theme(&mut self, value: &Value) {
        let (surface, text) = match value.as_str() {
            Some(color) => (color, ""),
            None => (value["surfaceColor"].as_str().unwrap_or(""), value["textColor"].as_str().unwrap_or("")),
        };
        let mut changed = false;
        if super::text_document::is_color(surface) {
            if surface != self.surface {
                self.surface = surface.into();
                changed = true;
            }
        }
        if super::text_document::is_color(text) {
            if text != self.text {
                self.text = text.into();
                changed = true;
            }
        }
        // 主题真正变化时重刷已加载的纯文本类文档（普通网页脚本自行退出）。
        if changed {
            self.refresh_text_documents();
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_nonfinite_and_negative_rects() {
        assert!(!valid_rect(
            &serde_json::json!({"x":0,"y":0,"width":-1,"height":4})
        ));
        assert!(valid_rect(
            &serde_json::json!({"x":0,"y":46,"width":400,"height":200})
        ));
    }
}
