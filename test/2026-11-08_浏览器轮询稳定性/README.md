# 内置浏览器轮询闪烁与菜单消失回归

用户在 fix1 试用中报告页面不断闪烁，“…”菜单打开后闪一下消失；另明确要求去掉该菜单阴影。

## 已复现根因

- NativeBrowserHost 用单来源 `watch(() => [open, JSON.stringify(scope)], ...)`。桌面导航每 500ms 返回新快照，scope computed 返回新对象，getter 因此生成新数组；即使数组内字段未变，Vue 仍执行回调，重复 connect/suspend，隐藏 native、换 lease 并重置菜单。
- useBrowserControls 用单来源 `watch(() => [active.id, active.url], ...)`。普通浏览器状态发布替换 active 对象，导致同网址也调用 closePopovers、覆盖地址编辑、清空查找。
- 实测网页本身没有重新加载；闪烁来自视图可见性与界面状态重置，不能归因为网站自己刷新。

修正为 Vue 多来源 watch，分别比较稳定的 primitive 值；不停止桌面轮询，不削弱导航归属/旧 renderer 防护。只在真实归属或标签/网址变化时重连、更新相关状态。BrowserMenu 删除本体 box-shadow。

## 运行

复用上一轮真实 Vue/Pinia/i18n + 生产 preload/NativeBrowser 的隔离夹具，增加本目录 polling.cjs；不会复制另一套生产实现。

```bash
node test/2026-11-08_浏览器无模型渲染/build.mjs
./desktop-electron/node_modules/.bin/electron test/2026-11-08_浏览器无模型渲染/native.cjs --polling-stability
```

原生命令需本机窗口与 loopback 监听，命令沙箱受限时申请单次完全访问。数据、网页、身份均为项目临时合成资料，不访问真实凭据、不截图、不操作用户现有应用，测试 finally 退出本次 Electron。

连续送入 8 次相同归属导航快照（每 500ms 一次），再发布 12 次同网址浏览器状态，检测 lease/身份请求次数、菜单、地址编辑、查找、网页内标记及真实导航事件。最后改变网页 URL，确认页面专属状态仍正确更新；计算样式确认更多菜单无阴影。

## 证据

- 修正前：`_experiments/browser-native-integration/no-model-1794111118550/polling-observations.json`。8 次轮询触发 8 次额外身份连接，菜单/地址/查找保留断言失败；网页标记仍在且无真实重载。更早一轮在网页加载状态发布时就复现菜单消失。
- 修正后：`_experiments/browser-native-integration/no-model-1794111500015/results.json` 与 `polling-observations.json`。共 25 项通过，8 次轮询的额外身份连接为 0；菜单/编辑/查找保持，真实 URL 变化仍更新，网页实例保持，菜单 box-shadow 为 none，原有导航防护及无模型功能仍通过。
- 定向 ESLint、根前端构建、git diff --check 通过。fix2 包内 8 个 Electron 源文件、manifest 和 567 个前端资源引用与当前源码/构建一致。

## 交付

最新目录包 `_experiments/macos-browser-20261108-fix2/mac-arm64/Astrion.app`。入口为 fix2 目录的“启动浏览器修正版.command”，沿用最初 `macos-browser-20261108/validation-profile/`，须先退出旧试用版再启动，避免同 userData 单实例锁只激活旧 app。

仅本地 ARM64 验证包，未正式签名/公证，不是 DMG；未覆盖或重启用户应用。以上有限回归不等同长期运行、真实站点或最终视觉验收。

fix2 打包应用的独立启动冒烟已通过五项检查：`_experiments/browser-native-integration/packaged-1794111931916/results.json`；同目录 `cleanup.json` 记录本次测试进程已回收。该检查使用额外临时资料，不复用用户正在使用的试用目录。
