# 浏览器默认搜索引擎与新标签页快捷方式回归

`check.mjs` 在 Electron 主进程（不开窗口）里检查：

- `policy.js`：关键词按所选引擎转成搜索地址，网址 / `localhost:端口` 不会被当成搜索，不允许搜索时关键词会报错。
- `profile.js`：默认搜索引擎的未设置回退、设置与非法值拒绝；快捷方式去重、清洗、`javascript:` 与超过 10 个时整体拒绝；重启后数据还在。
- `static/src/components/browser/newTabShortcuts.ts`（用 esbuild 转译）：最常访问的排序、`www.` 合并、排除搜索结果页和登录跳转域名、固定项在前、隐藏项不出现、上限 10 个、手动输入网址的补全与拒绝。

运行（仓库根目录）：

```bash
desktop-electron/node_modules/.bin/electron test/2026-11-09_浏览器搜索与快捷方式/check.mjs
```

Windows 端的 `policy.rs` / `model.rs` 有同样的契约，需在 Windows 上编译后实机确认。界面效果（设置页按钮、新标签页布局、菜单与编辑框）由用户实机验收。
