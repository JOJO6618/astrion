// Astrion 桌面壳入口：系统 WebView + 内嵌 Python 后端。
//
// 架构（对齐 opencode desktop 的 sidecar 模式，但前端由后端同源 serve）：
//   1. 选取空闲端口 → spawn Python 后端（开发期系统 python；生产期 sidecar 解释器）
//   2. 轮询后端就绪（/api/host-mode-enabled，无需鉴权）
//   3. 创建主窗口加载 http://127.0.0.1:<port>/ —— 前后端同源，
//      cookie 会话 / CSRF / 相对路径 API 全部零改动复用 Web 版行为
//   4. 主窗口关闭 → 退出进程 → RunEvent::Exit 时 kill 后端子进程

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;

use tauri::Manager;

fn main() {
    tauri::Builder::default()
        .manage(backend::BackendState::default())
        .setup(|app| {
            if let Err(err) = backend::start_backend_and_create_window(app.handle()) {
                // 后端起不来属致命错误：打日志并以非零码退出。
                // （生产期再升级为应用内错误页，骨架阶段不过度设计）
                eprintln!("[astrion-desktop] 启动后端失败: {err}");
                app.handle().exit(1);
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                if window.label() == "main" {
                    window.app_handle().exit(0);
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Astrion desktop")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                backend::shutdown_backend(app);
            }
        });
}
