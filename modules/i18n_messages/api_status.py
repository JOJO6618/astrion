"""Backend i18n message pack: server/status/* user-visible messages.

Domain prefixes:
- status_file_open.      server/status/file_open.py
- status_host_workspace. server/status/host_workspace.py
- status_git.            server/status/git.py
- status_base.           server/status/base.py

status_docker.*（Docker 项目路由）与 status_app.*（Android APK 路由）已随
Docker fork / Android 工程移出主仓。
"""

MESSAGES = {
    # ── server/status/file_open.py ──
    "status_file_open.invalid_path": {
        "zh-CN": "文件路径无效",
        "en-US": "Invalid file path",
    },
    "status_file_open.file_not_found": {
        "zh-CN": "文件不存在",
        "en-US": "File not found",
    },
    "status_file_open.workspace_not_found": {
        "zh-CN": "工作区不存在",
        "en-US": "Workspace not found",
    },
    "status_file_open.cannot_open_file_manager": {
        "zh-CN": "无法打开文件管理器",
        "en-US": "Cannot open file manager",
    },
    "status_file_open.host_mode_only": {
        "zh-CN": "仅宿主机模式可用",
        "en-US": "Available in host mode only",
    },
    "status_file_open.pick_folder_prompt": {
        "zh-CN": "选择工作区文件夹",
        "en-US": "Choose a workspace folder",
    },
    "status_file_open.pick_folder_unsupported": {
        "zh-CN": "当前平台不支持系统文件夹选择",
        "en-US": "Native folder picker is not supported on this platform",
    },
    "status_file_open.pick_folder_failed": {
        "zh-CN": "无法打开系统文件夹选择窗口",
        "en-US": "Failed to open the native folder picker",
    },
    "status_file_open.save_file_prompt": {
        "zh-CN": "保存文件到…",
        "en-US": "Save file as…",
    },
    "status_file_open.save_file_unsupported": {
        "zh-CN": "当前平台不支持系统保存窗口",
        "en-US": "Native save dialog is not supported on this platform",
    },
    "status_file_open.save_file_failed": {
        "zh-CN": "无法打开系统保存窗口",
        "en-US": "Failed to open the native save dialog",
    },
    "status_file_open.save_file_folder_unsupported": {
        "zh-CN": "暂不支持另存文件夹",
        "en-US": "Saving a folder is not supported yet",
    },
    "status_file_open.save_file_copy_failed": {
        "zh-CN": "保存失败：{error}",
        "en-US": "Failed to save file: {error}",
    },
    "status_file_open.invalid_external_url": {
        "zh-CN": "无效的外部链接",
        "en-US": "Invalid external URL",
    },
    "status_file_open.open_external_failed": {
        "zh-CN": "无法在系统浏览器中打开链接",
        "en-US": "Failed to open the link in the system browser",
    },
    "status_file_open.app_icon_unavailable": {
        "zh-CN": "应用图标不可用",
        "en-US": "App icon unavailable",
    },
    "status_file_open.app_not_found": {
        "zh-CN": "应用不存在",
        "en-US": "App not found",
    },
    "status_file_open.app_icon_not_found": {
        "zh-CN": "应用图标不存在",
        "en-US": "App icon not found",
    },
    "status_file_open.icon_tool_unavailable": {
        "zh-CN": "图标转换工具不可用",
        "en-US": "Icon conversion tool unavailable",
    },
    "status_file_open.app_not_selected": {
        "zh-CN": "未选择应用",
        "en-US": "No app selected",
    },
    "status_file_open.app_unavailable": {
        "zh-CN": "应用不可用",
        "en-US": "App unavailable",
    },
    "status_file_open.open_file_failed": {
        "zh-CN": "打开文件失败",
        "en-US": "Failed to open file",
    },

    # ── server/status/desktop_update.py ──
    "desktop_update.not_desktop": {
        "zh-CN": "当前环境不是桌面应用",
        "en-US": "Not running inside the desktop app",
    },
    "desktop_update.unsupported_platform": {
        "zh-CN": "当前平台不支持自动更新",
        "en-US": "Auto-update is not supported on this platform",
    },
    "desktop_update.check_failed": {
        "zh-CN": "检查更新失败，请稍后重试",
        "en-US": "Failed to check for updates, please try again later",
    },
    "desktop_update.bridge_unavailable": {
        "zh-CN": "更新服务不可用，请重启应用后重试",
        "en-US": "Update service unavailable, please restart the app and retry",
    },
    "desktop_update.bridge_unreachable": {
        "zh-CN": "无法连接更新服务",
        "en-US": "Cannot reach the update service",
    },
    "desktop_update.chrome_invalid_action": {
        "zh-CN": "不支持的标签操作",
        "en-US": "Unsupported tab action",
    },

    # ── server/status/host_workspace.py ──
    "status_host_workspace.host_mode_only": {
        "zh-CN": "仅宿主机模式可用",
        "en-US": "Available in host mode only",
    },
    "status_host_workspace.missing_path": {
        "zh-CN": "缺少 path",
        "en-US": "Missing path",
    },
    "status_host_workspace.missing_workspace_id": {
        "zh-CN": "缺少 workspace_id",
        "en-US": "Missing workspace_id",
    },
    "status_host_workspace.workspace_has_running_tasks": {
        "zh-CN": "该工作区有运行中的任务，暂不能删除",
        "en-US": "This workspace has running tasks and cannot be deleted yet",
    },
    "status_host_workspace.workspace_name_required": {
        "zh-CN": "工作区名称不能为空",
        "en-US": "Workspace name is required",
    },
    "status_host_workspace.workspace_id_not_found": {
        "zh-CN": "workspace_id 不存在",
        "en-US": "workspace_id does not exist",
    },

    # ── server/status/base.py ──
    "status_base.file_manager_not_initialized": {
        "zh-CN": "文件管理器未初始化",
        "en-US": "File manager is not initialized",
    },
}