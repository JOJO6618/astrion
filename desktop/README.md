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
- **macOS**：项目 `.venv` → homebrew 3.13/3.12/3.11 → PATH `python3`
- **Windows**：项目 `.venv/Scripts` → py launcher（3.13→3.12→3.11→3，解析真实解释器路径）→ PATH `python.exe`

可用 `ASTRION_DESKTOP_REPO=<仓库根>` 显式覆盖仓库路径。

若 `desktop/src-tauri/runtime/` 存在（python-build-standalone + staging 后端），
dev 也会直接使用内嵌运行时（与生产形态一致）；不存在才回退系统 Python 开发形态。

## 打包

```bash
npm run build   # = prepare-backend（staging）+ tauri build（MSI + NSIS / dmg）
```

- staging 脚本：`scripts/prepare_backend.py`（跨平台，排除清单单一来源；
  `prepare-backend.sh` 仅为 macOS 兼容入口的薄壳）
- 内嵌运行时：`desktop/src-tauri/runtime/python`（python-build-standalone，
  已 gitignore）+ `runtime/backend`（staging 产物）。Windows 放 `python.exe`，
  macOS 放 `bin/python3.12`——`backend.rs` 按平台解析
- 依赖安装：`runtime/python` 就位后，用它 `pip install -r requirements.lock.txt`

### Windows 特有坑（已踩过）

1. **WiX / NSIS 工具链下载超时**（GitHub 直连）：手动下载放到
   `%LOCALAPPDATA%\tauri\WixTools314`（wix314-binaries.zip 解开）与
   `%LOCALAPPDATA%\tauri\NSIS`（nsis-3.11.zip 解开；
   `Plugins/x86-unicode/additional/nsis_tauri_utils.dll` 需单独下载，
   SHA1 与 bundler 常量匹配——用错版本会被判 mis-hashed 并删目录重下）
2. **icon.ico 必须**：MSI/NSIS bundle 需要 .ico，`tauri.conf.json` 的
   `bundle.icon` 要同时列出 `icons/icon.png` 与 `icons/icon.ico`
3. **后端黑框**：release 是 GUI 子系统，spawn python 必须加
   `CREATE_NO_WINDOW`（`backend.rs` 已处理）
4. **GBK 代码页崩溃**：后端 print 含 Unicode 符号时 GBK stdout 直接
   UnicodeEncodeError——spawn 时注入 `PYTHONUTF8=1`（PEP 540）根治

### 打包路线（TODO）

- [x] python-build-standalone 内嵌运行时（Windows/macOS）+ 锁依赖打进安装包
      （不能用 PyInstaller 冻结：skills 需要完整可 pip install 的 Python 环境）
- [x] 打包时排除 `config/custom_models.json` 及其 `.example`（开发者私有配置，
      全新用户模型库为干净空态）
- [ ] 图标全套生成：`npx tauri icon <source.png>`（当前 180x180 源图，ico 为多尺寸转换）
- [ ] 签名 / 公证（macOS Developer ID + Windows 签名证书；未签名时 SmartScreen 拦截）
- [x] 自动更新：tauri-plugin-updater（见下「自动更新与发布」）
- [ ] 首启向导（创建首个工作区 + 配置首个提供商）
- [ ] 单实例：tauri-plugin-single-instance

### 自动更新与发布（2026-09-26 上线）

**链路**：应用内检查 `https://astrion.cyjai.com/downloads/latest-{{target}}-{{arch}}.json`
→ 发现新版本侧边栏「软件更新」亮红点 → 弹窗确认 → 壳侧 updater 后台下载 →
minisign 验签 → 原地安装 → 自动重启（macOS 替换 .app / Windows NSIS passive 覆盖）。

**架构关键点**：WebView 是 External URL，拿不到 Tauri JS API——壳侧开 localhost
控制桥（`src/bridge.rs`：`GET /version`、`POST /update/install`、`GET /update/progress`，
手写最小 HTTP），端口与应用版本经 `ASTRION_DESKTOP_BRIDGE_PORT` /
`ASTRION_DESKTOP_VERSION` 环境变量注入后端；前端只同源调后端代理
（`server/status/desktop_update.py`），无 CORS、鉴权沿用会话。

**密钥**：minisign 签名私钥 `~/.astrion-desktop-keys/updater.key`（**不进 git、
不能丢失**，丢失则永远无法再签名更新包）；公钥写死在 `tauri.conf.json`
`plugins.updater.pubkey`。构建需环境变量 `TAURI_SIGNING_PRIVATE_KEY` +
`TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""`（密钥为空密码，不显式置空会交互式卡死）。

**发布（macOS）**：`bash desktop/scripts/release_mac.sh [版本号]`——同步版本号
（tauri.conf.json / Cargo.toml / package.json）→ 构建前端 → tauri build → 上传
服务器 `downloads/` → 远端 `regen_manifest.py` 重新生成清单（`latest-*.json` 供
updater、`downloads.json` 供官网下载页）。更新说明唯一来源 =
`DESKTOP_CHANGELOG.md` 顶部小节（发布脚本自动提取进清单）。

**发布（Windows）**：Windows 机上同版本号 `tauri build`（需同一私钥的两个环境变量）
→ 把 `Astrion_<ver>_x64-setup.exe` 与 `.sig` 传到服务器 `/var/www/astrion/downloads/`
→ 服务器上跑 `python3 /var/www/astrion/downloads/regen_manifest.py`。
mac/win 清单互相独立（模板端点），两端可以不同步发布。

**坑（已踩过）**：bundle targets 必须含 `app`（macOS updater 包 .app.tar.gz 是 app
目标产出的，只写 dmg 不出包）；mac updater 产物名恒为 `Astrion.app.tar.gz`
（无版本号），发布脚本上传时重命名为 `Astrion_<ver>_aarch64.app.tar.gz`。
