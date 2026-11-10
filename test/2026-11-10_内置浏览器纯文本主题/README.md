# 内置浏览器：纯文本类文档的主题可读性

## 问题

深色主题下在内置浏览器打开纯文本类响应（`text/plain`、`application/json`、脚本源码如
`cyjai.com/deck-mail.js?v=...`）会出现**深底黑字**，完全不可读。

真实 Electron 35 探针结果：

| 项目 | 值 |
|------|-----|
| `document.contentType` | `text/plain` / `application/javascript` / `application/json` |
| `body` 子元素 | 单个 `PRE`（JSON 多一个提示节点） |
| `html`/`body` 背景 | `rgba(0, 0, 0, 0)`（透明） |
| 默认文字色 | `rgb(0, 0, 0)` |
| `prefers-color-scheme` | `light`（跟随系统，不跟应用主题） |
| 标签视图底色 | 应用主题 `--surface-base`（深色 `#1a1a1a`） |

即：透明背景透出深色视图底色，文字保持黑色。

## 修复

`dom-ready`（Windows：`NavigationCompleted`）后执行自守卫脚本，命中才注入样式：

- 判定：`document.contentType` 不属于 `text/html` / `application/xhtml+xml` / `image/svg+xml` / `application/pdf`，
  且 `body` 子元素含 `<pre>`。
- 注入：`<style id="__astrion_text_document_theme">`，
  `html{background:<--surface-base> !important;color-scheme:dark|light}` + `body{color:<--text-primary> !important}`。
- 主题切换时对已命中的标签重新执行；普通网页、`prefers-color-scheme`、页面自身样式不受影响。

实现位置：

- macOS：`desktop-electron/src/browser/text-documents.js`（`tabs.js` 的 `dom-ready` 接入，
  `geometry.js` 在主题变化时刷新）
- Windows：`desktop/src-tauri/src/browser/text_document.rs`（`views.rs::loaded` 注入，
  `layout.rs::apply_theme` 变化时刷新）

两端的判定条件与 CSS 必须同步修改。主题上报载荷已由 `surfaceColor` 字符串扩展为
`{ surfaceColor, textColor }`（`static/src/components/browser/useBrowserSurface.ts`）。

## 运行

```bash
cd <仓库根>
./desktop-electron/node_modules/.bin/electron \
  test/2026-11-10_内置浏览器纯文本主题/text_document_theme.mjs
```

macOS 沙箱内启动 Electron 会被 Seatbelt 拒绝，需要完全访问或直接在宿主机执行。

覆盖 7 项：`text/plain`、`application/javascript`、`application/json` 命中并注入；
`text/html` 完全不受影响；浅色主题跟随；缺文字色时按背景明暗兜底；脚本模板完整。

Windows 侧本机无 Rust 工具链，**未编译、未实机验证**，需在 Windows 机器上构建确认。
