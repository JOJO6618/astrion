"""Backend i18n message pack: infrastructure user-visible error messages.

Covers modules/gui_file_manager.py (gui_file.*), modules/host_sandbox_runner.py
(sandbox.*), modules/user_container_manager.py (container_mgr.*),
modules/persistent_terminal/start.py (terminal_start.*),
modules/toolbox_container.py (toolbox.*) and
modules/background_command_manager.py (bg_cmd.*).

Pure data module — do not import anything here. Auto-discovered and merged by
modules/i18n.py at import time. zh-CN copy is verbatim from source; en-US is
concise product-level English (sentence case).
"""

MESSAGES = {
    # ── modules/gui_file_manager.py（GUI 文件管理专用服务） ──
    "gui_file.path_escape": {
        "zh-CN": "路径越界",
        "en-US": "Path escapes the workspace root",
    },
    "gui_file.dir_not_found": {
        "zh-CN": "目录不存在",
        "en-US": "Directory does not exist",
    },
    "gui_file.not_a_directory": {
        "zh-CN": "目标不是目录",
        "en-US": "Target is not a directory",
    },
    "gui_file.parent_not_found": {
        "zh-CN": "父目录不存在",
        "en-US": "Parent directory does not exist",
    },
    "gui_file.parent_not_directory": {
        "zh-CN": "父路径不是目录",
        "en-US": "Parent path is not a directory",
    },
    "gui_file.name_empty": {
        "zh-CN": "名称不能为空",
        "en-US": "Name cannot be empty",
    },
    "gui_file.name_exists": {
        "zh-CN": "同名文件或目录已存在",
        "en-US": "A file or directory with the same name already exists",
    },
    "gui_file.unsupported_type": {
        "zh-CN": "不支持的类型",
        "en-US": "Unsupported type",
    },
    "gui_file.target_not_found": {
        "zh-CN": "目标不存在",
        "en-US": "Target does not exist",
    },
    "gui_file.new_name_empty": {
        "zh-CN": "新名称不能为空",
        "en-US": "New name cannot be empty",
    },
    "gui_file.target_name_exists": {
        "zh-CN": "目标名称已存在",
        "en-US": "Target name already exists",
    },
    "gui_file.destination_not_directory": {
        "zh-CN": "目标目录不存在",
        "en-US": "Destination directory does not exist",
    },
    "gui_file.file_not_found": {
        "zh-CN": "文件不存在",
        "en-US": "File does not exist",
    },
    "gui_file.target_is_directory": {
        "zh-CN": "目标是目录",
        "en-US": "Target is a directory",
    },
    "gui_file.file_too_large": {
        "zh-CN": "文件过大，暂不支持直接编辑",
        "en-US": "File is too large to edit directly",
    },
    "gui_file.not_utf8": {
        "zh-CN": "文件不是 UTF-8 编码: {error}",
        "en-US": "File is not UTF-8 encoded: {error}",
    },
    "gui_file.content_too_large": {
        "zh-CN": "内容过大，超出限制",
        "en-US": "Content is too large, exceeds the limit",
    },
    "gui_file.upload_target_not_directory": {
        "zh-CN": "上传目标必须是目录",
        "en-US": "Upload target must be a directory",
    },
    "gui_file.filename_empty": {
        "zh-CN": "文件名不能为空",
        "en-US": "Filename cannot be empty",
    },

    # ── modules/host_sandbox_runner.py（宿主机沙箱执行器） ──
    "sandbox.unsupported_system": {
        "zh-CN": "不支持的宿主机系统: {system}",
        "en-US": "Unsupported host system: {system}",
    },
    "sandbox.macos_no_sandbox_exec": {
        "zh-CN": "macOS 未找到 sandbox-exec，拒绝执行宿主机命令。",
        "en-US": "sandbox-exec not found on macOS; refusing to run host commands",
    },
    "sandbox.macos_no_sandbox_exec_shell": {
        "zh-CN": "macOS 未找到 sandbox-exec，拒绝启动宿主机沙箱终端。",
        "en-US": "sandbox-exec not found on macOS; refusing to start a host sandbox terminal",
    },
    "sandbox.linux_no_bwrap_exec": {
        "zh-CN": "Linux 未找到 bubblewrap(bwrap)，拒绝执行宿主机命令。",
        "en-US": "bubblewrap (bwrap) not found on Linux; refusing to run host commands",
    },
    "sandbox.linux_no_bwrap_shell": {
        "zh-CN": "Linux 未找到 bubblewrap(bwrap)，拒绝启动宿主机沙箱终端。",
        "en-US": "bubblewrap (bwrap) not found on Linux; refusing to start a host sandbox terminal",
    },
    "sandbox.linux_no_bwrap_brief": {
        "zh-CN": "Linux 未找到 bubblewrap(bwrap)。",
        "en-US": "bubblewrap (bwrap) not found on Linux",
    },
    "sandbox.linux_no_seccomp_exec": {
        "zh-CN": "Linux 缺少 HOST_SANDBOX_LINUX_SECCOMP_BPF，拒绝执行宿主机命令。",
        "en-US": "HOST_SANDBOX_LINUX_SECCOMP_BPF is missing on Linux; refusing to run host commands",
    },
    "sandbox.linux_no_seccomp_shell": {
        "zh-CN": "Linux 缺少 HOST_SANDBOX_LINUX_SECCOMP_BPF，拒绝启动宿主机沙箱终端。",
        "en-US": "HOST_SANDBOX_LINUX_SECCOMP_BPF is missing on Linux; refusing to start a host sandbox terminal",
    },
    "sandbox.seccomp_bpf_not_found": {
        "zh-CN": "seccomp BPF 文件不存在: {path}",
        "en-US": "seccomp BPF file does not exist: {path}",
    },
    "sandbox.wsl_path_convert_failed": {
        "zh-CN": "无法转换为 WSL 路径（仅支持盘符路径）: {path}",
        "en-US": "Cannot convert to a WSL path (drive-letter paths only): {path}",
    },
    "sandbox.windows_no_wsl": {
        "zh-CN": "Windows 未找到 wsl.exe（WSL2），拒绝执行宿主机命令。",
        "en-US": "wsl.exe (WSL2) not found on Windows; refusing to run host commands",
    },
    "sandbox.wsl_setup_hint": {
        "zh-CN": "未找到可用的 WSL 沙箱发行版 '{distro}'。请先运行 scripts/setup-wsl-sandbox.ps1 创建专用沙箱发行版（必须关闭 interop，不可用 docker-desktop 或日常 Ubuntu 代替，详见 wsl2-sandbox-poc-report.md）。",
        "en-US": "No usable WSL sandbox distro '{distro}' found. Run scripts/setup-wsl-sandbox.ps1 to create a dedicated sandbox distro (interop must be disabled; docker-desktop or a daily Ubuntu distro cannot be used instead, see wsl2-sandbox-poc-report.md)",
    },
    "sandbox.wsl_distro_probe_failed": {
        "zh-CN": "WSL 沙箱发行版探测失败: {error}。{hint}",
        "en-US": "Failed to probe the WSL sandbox distro: {error}. {hint}",
    },
    "sandbox.wsl_bwrap_probe_failed": {
        "zh-CN": "WSL 沙箱 bwrap 探测失败: {error}。{hint}",
        "en-US": "Failed to probe bwrap in the WSL sandbox: {error}. {hint}",
    },
    "sandbox.wsl_distro_no_bwrap": {
        "zh-CN": "WSL 沙箱发行版 '{distro}' 内未安装 bubblewrap，请重新运行 scripts/setup-wsl-sandbox.ps1 或在发行版内执行 apk add bubblewrap。",
        "en-US": "bubblewrap is not installed inside the WSL sandbox distro '{distro}'. Re-run scripts/setup-wsl-sandbox.ps1 or run 'apk add bubblewrap' inside the distro",
    },

    # ── modules/sandbox_setup_manager.py（沙箱环境一键安装） ──
    "sandbox.setup_wsl_missing": {
        "zh-CN": "未检测到可用的 WSL2（Windows Subsystem for Linux）",
        "en-US": "No usable WSL2 (Windows Subsystem for Linux) detected",
    },
    "sandbox.setup_distro_missing": {
        "zh-CN": "未找到沙箱发行版 '{distro}'",
        "en-US": "Sandbox distro '{distro}' not found",
    },
    "sandbox.setup_bwrap_missing": {
        "zh-CN": "沙箱发行版 '{distro}' 缺少 bubblewrap 组件",
        "en-US": "Sandbox distro '{distro}' is missing bubblewrap",
    },
    "sandbox.setup_not_windows": {
        "zh-CN": "仅 Windows 宿主机模式支持一键安装沙箱",
        "en-US": "One-click sandbox setup is only supported on Windows host mode",
    },
    "sandbox.setup_not_host_mode": {
        "zh-CN": "当前不是宿主机模式，无法安装沙箱",
        "en-US": "Not in host mode; sandbox setup unavailable",
    },
    "sandbox.setup_script_missing": {
        "zh-CN": "安装脚本 scripts/setup-wsl-sandbox.ps1 不存在",
        "en-US": "Setup script scripts/setup-wsl-sandbox.ps1 not found",
    },
    "sandbox.setup_already_running": {
        "zh-CN": "安装任务正在运行中",
        "en-US": "A setup task is already running",
    },
    "sandbox.setup_enabling_wsl_log": {
        "zh-CN": "正在请求管理员授权…",
        "en-US": "Requesting administrator approval...",
    },
    "sandbox.setup_wsl_installing_log": {
        "zh-CN": "授权通过，正在下载并安装 WSL2 组件…",
        "en-US": "Approval granted; downloading and installing WSL2 components...",
    },
    "sandbox.setup_wsl_enabled_log": {
        "zh-CN": "WSL2 已启用，继续安装沙箱发行版…",
        "en-US": "WSL2 enabled, continuing with sandbox distro setup...",
    },
    "sandbox.setup_wsl_enable_failed": {
        "zh-CN": "启用 WSL2 失败（可能未通过管理员授权）",
        "en-US": "Failed to enable WSL2 (administrator approval may have been denied)",
    },
    "sandbox.setup_uac_timeout": {
        "zh-CN": "等待管理员授权超时",
        "en-US": "Timed out waiting for administrator approval",
    },
    "sandbox.setup_script_failed": {
        "zh-CN": "安装脚本执行失败，详情见日志",
        "en-US": "Setup script failed; see log for details",
    },
    "sandbox.setup_verify_failed": {
        "zh-CN": "安装完成但验收检测未通过: {detail}",
        "en-US": "Setup finished but verification failed: {detail}",
    },
    "sandbox.setup_bad_distro_name": {
        "zh-CN": "发行版名称 '{distro}' 含非法字符（仅允许字母、数字、点、下划线、连字符）",
        "en-US": "Distro name '{distro}' contains illegal characters (only letters, digits, dot, underscore, hyphen allowed)",
    },

    # ── modules/user_container_manager.py（用户容器管理器） ──
    "container_mgr.quota_exhausted": {
        "zh-CN": "资源繁忙：容器配额已用尽，请稍候再试。",
        "en-US": "Busy: container quota exhausted, please try again later",
    },
    "container_mgr.per_user_quota_exhausted": {
        "zh-CN": "资源繁忙：您的容器数量已达上限（{limit} 个），请先释放其他工作区的容器。",
        "en-US": "Busy: you have reached your container limit ({limit}); please release containers of other workspaces first",
    },
    "container_mgr.runtime_not_found": {
        "zh-CN": "未找到容器运行时 {runtime}",
        "en-US": "Container runtime not found: {runtime}",
    },
    "container_mgr.image_not_configured": {
        "zh-CN": "TERMINAL_SANDBOX_IMAGE 未配置，无法启动容器。",
        "en-US": "TERMINAL_SANDBOX_IMAGE is not configured; cannot start a container",
    },
    "container_mgr.docker_start_failed": {
        "zh-CN": "容器启动失败",
        "en-US": "Container failed to start",
    },
    "container_mgr.docker_mode_runtime_not_found": {
        "zh-CN": "Docker 模式启动失败：未找到容器运行时 {runtime}",
        "en-US": "Docker mode failed to start: container runtime not found: {runtime}",
    },
    "container_mgr.docker_daemon_unreachable": {
        "zh-CN": "Docker 模式启动失败：无法访问 Docker daemon: {error}",
        "en-US": "Docker mode failed to start: cannot reach the Docker daemon: {error}",
    },
    "container_mgr.docker_info_nonzero": {
        "zh-CN": "docker info 返回非零状态",
        "en-US": "docker info returned a non-zero status",
    },
    "container_mgr.docker_mode_failed": {
        "zh-CN": "Docker 模式启动失败：{message}",
        "en-US": "Docker mode failed to start: {message}",
    },

    # ── modules/persistent_terminal/start.py（持久化终端 start mixin） ──
    "terminal_start.host_sandbox_disabled": {
        "zh-CN": "宿主机沙箱已禁用（HOST_SANDBOX_ENABLED=0），拒绝启动 host 终端。",
        "en-US": "Host sandbox is disabled (HOST_SANDBOX_ENABLED=0); refusing to start a host terminal",
    },
    "terminal_start.container_not_running": {
        "zh-CN": "目标容器未运行: {container_name}",
        "en-US": "Target container is not running: {container_name}",
    },
    "terminal_start.image_not_configured": {
        "zh-CN": "TERMINAL_SANDBOX_IMAGE 未配置",
        "en-US": "TERMINAL_SANDBOX_IMAGE is not configured",
    },
    "terminal_start.runtime_not_found": {
        "zh-CN": "未找到容器运行时: {runtime}",
        "en-US": "Container runtime not found: {runtime}",
    },
    "terminal_start.runtime_exec_failed": {
        "zh-CN": "无法执行容器运行时: {runtime}",
        "en-US": "Cannot execute container runtime: {runtime}",
    },

    # ── modules/toolbox_container.py（工具容器管理器） ──
    "toolbox.container_start_failed": {
        "zh-CN": "工具容器启动失败，请检查 Docker 或本地 shell 环境。",
        "en-US": "Failed to start the toolbox container; check Docker or the local shell environment",
    },

    # ── modules/background_command_manager.py（后台命令管理器） ──
    "bg_cmd.container_name_missing": {
        "zh-CN": "容器模式下缺少 container_name",
        "en-US": "container_name is missing in container mode",
    },
}