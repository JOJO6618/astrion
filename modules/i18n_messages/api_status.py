"""Backend i18n message pack: server/status/* API user-visible messages.

Domain prefixes:
- status_file_open.      server/status/file_open.py
- status_docker.         server/status/docker.py
- status_host_workspace. server/status/host_workspace.py
- status_git.            server/status/git.py
- status_app.            server/status/app.py
- status_base.           server/status/base.py
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
    "status_file_open.project_not_found": {
        "zh-CN": "项目不存在",
        "en-US": "Project not found",
    },
    "status_file_open.cannot_open_file_manager": {
        "zh-CN": "无法打开文件管理器",
        "en-US": "Cannot open file manager",
    },
    "status_file_open.host_mode_only": {
        "zh-CN": "仅宿主机模式可用",
        "en-US": "Available in host mode only",
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

    # ── server/status/docker.py ──
    "status_docker.docker_web_only": {
        "zh-CN": "仅 Docker Web 模式可用",
        "en-US": "Available in Docker Web mode only",
    },
    "status_docker.missing_project_id": {
        "zh-CN": "缺少项目 ID",
        "en-US": "Missing project ID",
    },
    "status_docker.project_not_found": {
        "zh-CN": "项目不存在",
        "en-US": "Project not found",
    },
    "status_docker.project_name_required": {
        "zh-CN": "项目名称不能为空",
        "en-US": "Project name is required",
    },
    "status_docker.default_project_not_deletable": {
        "zh-CN": "默认项目不能删除",
        "en-US": "The default project cannot be deleted",
    },
    "status_docker.project_has_running_tasks": {
        "zh-CN": "该项目有运行中的任务，暂不能删除",
        "en-US": "This project has running tasks and cannot be deleted yet",
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

    # ── server/status/app.py ──
    "status_app.apk_not_found": {
        "zh-CN": "APK 不存在，请先构建 release 包",
        "en-US": "APK not found; please build the release package first",
    },

    # ── server/status/base.py ──
    "status_base.file_manager_not_initialized": {
        "zh-CN": "文件管理器未初始化",
        "en-US": "File manager is not initialized",
    },
}