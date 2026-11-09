# AI 浏览器操作工具回归（2026-11-09）

设计与约束见 `docs/native_browser.md`「AI 浏览器操作工具」。

## 后端（不依赖 Electron）

```bash
.venv/bin/python -B test/2026-11-09_AI浏览器工具/backend_regression.py
```

覆盖读取类／动作类划分、站点首次审批与「以后都允许」、`browser_evaluate` 与 `browser_fill_saved_login` 每次审批、只读与无限制模式、浏览器关闭时的错误、`remember_site` 只对人工浏览器审批生效、站点文件校验与结果格式化。

## 真实 Electron

```bash
desktop-electron/node_modules/.bin/electron test/2026-11-09_AI浏览器工具/electron_harness.mjs
```

在隐藏窗口里创建真实 `NativeBrowser`，用本目录 `fixture/` 的本地页面逐项调用：快照与差异、点击／输入／下拉／表单、confirm 与 alert 接管、定时器与 fetch 后的变化、文件上传与选择框、截图、控制台与网络、站点校验、遮挡、失效 ref、跳转、多标签、对话隔离、epoch 重置、取消、保存密码填写（测试替身，不读真实密码库）、后台标签显示后视口恢复。结果写到 `_experiments/ai-browser-harness/<时间戳>/results.json`（2026-11-09 末次 51/51 通过）。

不覆盖：真实窗口里的视觉效果、真实模型调用、真实密码库与钥匙串、真实网站登录。
