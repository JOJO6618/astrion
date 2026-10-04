# Astrion 桌面壳（Electron / Chromium）

> 2026-09-28 起替代 `desktop/` 的 Tauri/WKWebView 壳。
> 换引擎动机：WKWebView 反复出现引擎级兼容问题（折叠动画 content-visibility 离散过渡
> 不生效、rAF 写入样式延迟 1-2 帧、virtua 首项不挂载、HTML5 拖放被壳吞掉、上滚时
> virtua 估值补偿可见抖动），逐个修成本高且无法预测下一个；统一 Chromium 后与
> Windows（WebView2）/ Chrome web 同一引擎，测试矩阵塌缩到 1 个。

## 架构

与 Tauri 壳完全一致，**Python 后端与前端业务代码零改动**：

```
Electron 主进程
├─ backend.js    Python sidecar：探测解释器（.venv → homebrew → PATH）→ spawn
│                -m server.app --port <动态端口> → 轮询 /api/host-mode-enabled 就绪
├─ shell_env.js  从 macOS 用户登录 shell 读取 PATH（限时；仅 PATH 传给后端）
├─ bridge.js     127.0.0.1 控制桥（与 Tauri 版端点契约逐字节一致）：
│                GET /version · POST /update/install · GET /update/progress
│                · POST /window/drag（兼容空操作）· POST /chrome/dispatch
├─ window.js     BaseWindow + 双 WebContentsView：
│                chrome 视图（顶部 46px 独立标签条，加载 /chrome，常驻不闪动）
│                + main 视图（加载 /，前后端同源）
└─ menu.js       应用菜单（保留 Edit 菜单保 Cmd+C/V；不占用 Cmd+W/Cmd+T 给前端标签）
```

- 桌面命令环境：启动时最多等待 10 秒读取用户登录 shell 的 PATH（zsh/bash/fish）；只把 PATH 合并进后端环境，其他 shell 变量不导入。读取失败时记录状态并沿用原 PATH，不阻断启动。
- 后端契约：`ASTRION_DESKTOP_VERSION` / `ASTRION_DESKTOP_BRIDGE_PORT` /
  `ASTRION_DATA_ROOT`（~/.astrion/astrion-desktop）环境变量语义不变；
  控制桥五个端点行为与 Tauri 版一致（`server/status/desktop_update.py` 无感知）。
- 前端契约：`window.__ASTRION_DESKTOP__`（双视图经 preload + contextBridge 注入）、
  `window.__ASTRION_CHROME__`（仅 chrome 视图）、`window.__astrionChromeDispatch`
  （主页面 index.html 既有 shim，桥 executeJavaScript 调用）。
- 窗口拖拽：chrome 条 `-webkit-app-region: drag`（_tab-strip.scss），交互元素
  no-drag。Tauri 时代的 mousedown → /window/drag → start_dragging 链路在
  Electron 下由桥端点空操作兼容。
- 红绿灯：`trafficLightPosition: {x:15, y:15}`（一行配置替代 Tauri 的 objc2 手动定位）。
- Cmd+W/Cmd+T：菜单不注册这两个加速器，前端双视图各自 keydown 处理（关闭/新建标签）；
  关窗口 = Cmd+Shift+W。

## 开发

```bash
npm install
npm run dev        # 开发模式：系统 python + 仓库源码树（同 Tauri 壳）
# ASTRION_DESKTOP_REPO=/path/to/repo 可覆盖仓库根
```

调试 DevTools：View 菜单（dev 构建含 Toggle DevTools，作用于聚焦视图）。

## 打包与发布

```bash
npm run build      # = prepare-backend（复用 desktop/scripts/prepare_backend.py）+ electron-builder
bash scripts/release_mac.sh [版本]   # 半一键发布（见脚本头注释）
```

- 产物：`dist/Astrion-<ver>-arm64.dmg`（安装）+ `Astrion-<ver>-arm64-mac.zip`（自动更新包）
  + `latest-mac.yml`（electron-updater 清单）。
- 签名：`electron-builder.yml` 使用钥匙串中的 Apple Development 证书；
  electron-updater mac 要求应用签名且更新包身份匹配，minisign 私钥体系随之退役。
- 更新清单与安装包托管 `https://astrion.cyjai.com/downloads/`（与 Tauri 的
  latest-darwin-aarch64.json 并存，latest-mac.yml 文件名不冲突）。

## 快捷对话

在「设置 → 快捷对话」启用，并选择双击修饰键、默认工作区及图片模型。留空时跟随全局默认工作区和模型，每次唤起刷新配置。首次使用按系统提示授权输入监控和屏幕录制。

启用后，启动应用保留后台服务和菜单栏，主窗口通过程序坞或菜单栏按需打开；关闭主窗口不退出。快捷页复用同一后端。当前必须先启动应用，不包含登录自启动。详情见 [QUICK_ENTRY.md](QUICK_ENTRY.md)。

## 与 Tauri 壳的差异备忘

| 能力 | Tauri 壳 | Electron 壳 |
|---|---|---|
| 渲染引擎 | 系统 WKWebView | 内置 Chromium（与 Win/Chrome 同引擎） |
| 安装包体积 | ~100-150MB | ~300MB（多 Chromium ~150-200MB） |
| Ctrl+Return 发送 | 需 NSEvent 拦截器 | Chromium 原生正常（待实测确认） |
| 拖文件进窗口 | 需 disable_drag_drop_handler | 原生正常 |
| 预览「外部打开」 | window.open 静默失败 | setWindowOpenHandler → 系统浏览器，可用 |
| 更新验签 | minisign 公私钥 | Apple 代码签名身份匹配 |
| single-instance | 待办 | 已实现（requestSingleInstanceLock） |
