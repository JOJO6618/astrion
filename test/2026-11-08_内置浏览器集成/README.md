# macOS 内置浏览器集成回归

在项目根执行，需已安装 `desktop-electron/node_modules`。这些是本地原生功能回归，不使用 Playwright、不代替视觉验收，不访问真实密码或生产后端。原生 Electron、loopback 监听与打包应用启动在宿主机沙箱内可能被拒；确认限制后对单次命令申请完全访问，不能关闭系统保护绕过。

## 生产模块与 IPC 回归

```bash
./desktop-electron/node_modules/.bin/electron test/2026-11-08_内置浏览器集成/native-smoke.cjs
```

使用真实 BaseWindow / WebContentsView、生产 NativeBrowser 和 preload、本机 HTTP 夹具及合成身份。资料、下载与结果放在 `_experiments/browser-native-integration/<timestamp>/`，结束关闭测试窗口与服务。

最新完整通过：`_experiments/browser-native-integration/1794079811008/results.json`，24 项。覆盖可信桥、沙箱、真实导航/查找/缩放、浮层避让、renderer 缩放坐标、popup/noopener、唯一下载、权限拒绝、跨工作区共享/跨用户隔离、稳定 owner、旧 lease、Cookie 数据库落盘、恢复与清理。`stale_browser_view` / `untrusted_browser_sender` 是故意触发的负向断言，整体 exit 0 才算通过。

## 实际进程重启持久化

```bash
BROWSER_PERSISTENCE_TEST_ROOT="$PWD/_experiments/browser-native-integration/persistence-$(date +%s)"
export BROWSER_PERSISTENCE_TEST_ROOT
./desktop-electron/node_modules/.bin/electron test/2026-11-08_内置浏览器集成/persistence.cjs --write && ./desktop-electron/node_modules/.bin/electron test/2026-11-08_内置浏览器集成/persistence.cjs --read
```

两个独立 Electron 进程写入再恢复持久 Cookie、历史，不能用同进程重建 profile 替代。通过证据：`_experiments/browser-native-integration/persistence-1794080073/results.json`，writer PID 5723、reader PID 5733。

## sessionData 路径诊断

```bash
./desktop-electron/node_modules/.bin/electron test/2026-11-08_内置浏览器集成/session-paths.cjs
```

这是修复过程的对照诊断，包含预期失败的 sessionData 外目录，不作为日常成功回归。证据：`_experiments/browser-native-integration/paths-1794079723767/results.json`。macOS Electron 35 的 Network Service sandbox 只授权 sessionData 父目录；外部 fromPath 可出现内存 Cookie 成功但不落盘、cache 创建失败。生产路径已修正到 sessionData 内。

## 打包应用启动冒烟

```bash
node test/2026-11-08_内置浏览器集成/packaged-startup.cjs
```

先用同版本 Electron 验证 `--user-data-dir` 同时隔离 userData/sessionData，再启动 `_experiments/macos-browser-20261108/mac-arm64/Astrion.app`，显式设置独立 `ASTRION_DESKTOP_DATA_ROOT`。不复制凭据，只请求本次后端 health、HTML 和 hashed entry asset；结束仅清理本次创建并校验路径的进程。日志和结果写入 `packaged-<timestamp>/`。

最终通过证据：`_experiments/browser-native-integration/packaged-1794081857102/results.json`，五项检查通过；同目录 `cleanup.json` 记录测试进程回收完成。此前 `packaged-1794080904669/` 无终态结果，仅为诊断记录，不纳入成功证据。脚本已补同源跳转处理和主进程强制退出兜底，避免测试收尾阻塞。

## 前端与包内容

本轮 `npm run build --silent 2>&1 | tail -n 5` 通过，包含 tsc、stylelint、Vite。定向 ESLint 通过：

```bash
./node_modules/.bin/eslint static/src/components/browser static/src/stores/nativeBrowser.ts static/src/locales/{zh-CN,en-US}/browser.ts static/src/components/shell/AppShell.vue static/src/components/sidebar/ConversationSidebar.vue --max-warnings=0
```

另用 Vue compiler-sfc 检查新组件模板，未绑定引用为 0。全量 lint 仍有无关存量格式警告；额外 vue-tsc 因现有工具版本与 TypeScript 不兼容在启动阶段失败，未更改依赖。

已逐字比对 app.asar 中五个 browser 模块、preload、window、lifecycle，和源码一致；包内 manifest 及其 567 个前端资源引用与本次 dist 一致。

本地目录包的复现命令（先完成根前端构建、`python3 desktop/scripts/prepare_backend.py`，在 desktop-electron 下执行）：

```bash
./node_modules/.bin/electron-builder --dir --mac --arm64 --publish never --config.directories.output=../_experiments/macos-browser-20261108 --config.electronDist=node_modules/electron/dist --config.mac.identity=null
```

这是未签名/未公证、本地 ARM64 验证包，不是发布命令。真实站点登录、输入法、系统媒体权限、打印、长时间运行和界面视觉仍需用户验收。
