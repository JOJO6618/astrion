# 内置浏览器「跟随智能体」开关与后台操作闪现修复

日期：2026-11-13

## 需求（用户拍板）

- 工具栏下载按钮**左侧**新增一个按钮：跟随智能体。
- **默认开启**；开启时严格跟随——AI 操作哪个标签、看到什么，用户视图就显示哪个。任何操作都跟随，不做“手动点标签就退出跟随”的例外。
- 点击关闭后，AI 怎么切换、显示什么，全部由用户自己点的标签决定。
- 跟随关闭时**不需要**任何“AI 正在别的标签工作”的提示。
- 与既有「自动打开浏览器面板」(`browser_auto_open`) **相互独立**，不联动。

## 实现

### 主进程（`desktop-electron/src/browser`）

| 文件 | 改动 |
|------|------|
| `follow.js` | 新增。跟随判定的唯一来源：`followAiTarget(tab, selectedId)` 返回应切换到的标签 id，跟随关闭/已是同一标签/无 profile 时返回 `null`。 |
| `index.js` | 新增 `follow(owner, tab)`：拿到判定结果才写 `selected`，并 `persist + layout + publish`。新增 `follow-ai` 命令（列入 `PROFILE_ACTIONS`，独立窗口也可用）。快照 `settings` 增加 `followAi`。 |
| `profile.js` | `settings.followAi` 默认 `true`；旧资料缺字段按开启处理，不做迁移写回。`followAi()` / `setFollowAi()`。 |
| `ai/controller.js` | 四处入口统一跟随：首次建标签、每次操作的目标标签、`tabs new`、`tabs select`。AI 新建标签一律 `select: false`，不再抢用户可见标签。 |
| `tabs.js` | `create()` 不再重复隐藏，统一由 `attach()` 负责。 |
| `index.js` 的 `attach()` | `addChildView` 之后立即 `setVisible(false)`：未挂载时的 setVisible 对独立视图不生效，不藏就会在 layout 前露出一帧（用户报告的“跳到标签1又回来”）。 |

跟随关闭时整条 AI 链路不写 `selected`，标签可见性仍然只由用户动作决定。

### 前端（`static/src`）

- `stores/nativeBrowser.ts`：`followAi` getter（快照缺字段视为开启）、`toggleFollow()`。
- `components/browser/BrowserToolbar.vue`：下载按钮左侧新增 `crosshair` 按钮，`aria-pressed` + 高亮态，文案随状态切换。
- `components/browser/BrowserButton.vue`：新增可选 `active` 属性与激活态样式（`--accent` tint）。
- `types.ts`：快照 `settings.followAi`。
- `utils/icons.ts` + `static/icons/crosshair.svg`：新图标。
- 文案：`locales/{zh-CN,en-US}/browser.ts` 的 `followAiOn` / `followAiOff`。
- 能力标记：`preload-main.cjs` / `preload-browser-window.cjs` 声明 `followAi: true`，工具栏据此显示按钮。

## 平台范围

只实现了 macOS Electron 壳。Windows Tauri 壳（`desktop/src-tauri`）的浏览器是独立 Rust 实现，本轮未同步；它的 bridge 不声明 `followAi`，所以界面上不会出现按不动的开关。Windows 端需要单独实现（settings、`follow-ai` 命令、AI 操作处切 selected）。

## 验证

```bash
node test/2026-11-13_浏览器跟随智能体/follow.test.mjs
```

覆盖：跟随开/关的纯逻辑、无 profile 兜底、controller 四处接线、AI 建标签不抢焦点、快照字段、挂载后隐藏、store 默认值与切换动作、按钮位于下载左侧。

已执行：

- `node --check` 主进程四个改动文件，通过。
- `npm run build`（含 stylelint、i18n 审计与 vite 构建），通过。
- 上述回归脚本 9 项通过。

**未覆盖（需实机验收）**：Electron 真机里跟随切换的视觉效果、被回收标签恢复时是否还有一帧闪现、跟随关闭后 AI 后台操作是否完全不干扰用户视图。这些都要启动桌面壳配合真实模型操作确认。
