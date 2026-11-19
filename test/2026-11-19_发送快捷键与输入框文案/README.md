# 发送快捷键与输入框占位文案（2026-11-19）

- **发送快捷键**：用户级 `personalization.send_shortcut`（`mod_enter` 默认 / `enter`），设置页「通用 → 快捷键 → 发送消息」。主输入框（Chat / Agent 共用 InputComposer）与桌面快捷窗口共用，判定逻辑唯一实现 `static/src/utils/sendShortcut.ts`。
  - `mod_enter`：Ctrl/⌘+Enter 发送；Enter、Shift+Enter 换行
  - `enter`：Enter 发送；Shift+Enter、Ctrl/⌘+Enter 换行
  - 输入法组字中的 Enter 不处理；触屏设备上 `enter` 模式不生效（回车换行，按钮发送）
- **占位文案**：16 条 Astrion 口吻文案池（`static/src/utils/composerPlaceholders.ts`），切换对话、发送后输入框清空时随机换一句，不定时轮换。

## 离线回归

需要 Node 24（直接加载 TypeScript）：

```bash
node --test test/2026-11-19_发送快捷键与输入框文案/send_shortcut.test.mjs
python3 -m py_compile modules/personalization_manager.py
npm run build --silent 2>&1 | tail -n 5
```

真实按键手感、输入法组字、视觉效果由用户实机验收。
