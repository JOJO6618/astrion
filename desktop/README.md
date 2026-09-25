# Astrion Desktop（Tauri 2 桌面壳）

系统 WebView + 内嵌 Python 后端的桌面应用形态。与 opencode desktop 同构（sidecar 模式），
但**前端由后端同源 serve**：WebView 直接加载 `http://127.0.0.1:<port>/`，
cookie 会话 / CSRF / 相对路径 API 全部零改动复用 Web 版。

## 架构

```
┌─────────────────────────────────────┐
│  Tauri 壳（Rust）                    │
│  ├─ 选空闲端口                        │
│  ├─ spawn Python 后端（server.app）   │
│  ├─ 轮询 /api/host-mode-enabled 就绪  │
│  └─ 创建 WebView 窗口 → 127.0.0.1:port│
├─────────────────────────────────────┤
│  Python 后端（现有代码，零改动）        │
│  └─ Flask serve static/dist + API    │
└─────────────────────────────────────┘
```

- 后端进程随 App 退出被 kill（RunEvent::Exit + Drop 双保险）
- 启动失败显示内联错误页（不静默退出）
- 登录页检测 `window.__TAURI__` + host 模式 → 自动免登录进入主界面

## 开发

```bash
# 1. 构建 Web 前端（壳加载的是后端的静态产物，保持最新）
npm run build              # 仓库根；或 npm run dev 持续监听

# 2. 启动桌面 App（首次 cargo 编译需 10-20 分钟）
cd desktop && npm install
npm run dev                # = tauri dev
```

Python 探测顺序（要求 `import yaml/flask/httpx/openai` 可用）：
项目 `.venv` → homebrew 3.13/3.12/3.11 → PATH `python3`。
可用 `ASTRION_DESKTOP_REPO=<仓库根>` 显式覆盖仓库路径。

## 打包路线（TODO）

- [ ] python-build-standalone + uv 锁依赖，作为 sidecar 打进安装包
      （不能用 PyInstaller 冻结：skills 需要完整可 pip install 的 Python 环境）
- [ ] 打包时排除 `config/custom_models.json`（开发者私有配置，
      分发回退链应为：部署目录 → `.example` 种子）
- [ ] 图标全套生成：`npx tauri icon <source.png>`
- [ ] 签名 / 公证（macOS Developer ID + Windows 签名证书）
- [ ] 自动更新：tauri-plugin-updater
- [ ] 首启向导（创建首个工作区 + 配置首个提供商）
- [ ] 单实例：tauri-plugin-single-instance
