# Rust 桌面快捷对话和 CLI 回归

```bash
app="$PWD/desktop-electron/dist-rust/mac-arm64/Astrion.app"
ASTRION_TEST_APP="$app" node --test 'test/2026-11-27_Rust快捷对话/quick_app.test.mjs'
python3.11 -B 'test/2026-11-27_Rust快捷对话/cli_entry.py' "$app"
```

快捷对话使用真实 Electron 窗口、独立资料目录以及回环模拟模型，检查签名、窗口打开和隐藏、认证读取、发送、呈现与快捷会话归档。CLI 通过普通 `astrion` 命令进入 TUI，并确认自启动的签名 Rust 进程使用临时资料目录。

本地候选 `f796ea20-3dfa-4d56-a8e0-c4311af0109c` 两项均通过。快捷窗复用桌面 Rust PID 84578，CLI 独立启动 PID 83146，验证后均回收。
