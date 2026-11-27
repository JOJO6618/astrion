# Rust 修改留痕回归

- `cargo test --locked --manifest-path backend-rust/Cargo.toml --lib runtime::modify_history`：验证同一文件多次编辑的净变化、停止前已保存的内容、缺失文件恢复、CRLF/无结尾换行/非 ASCII 路径以及对话 UI 元数据与事件。
- `ASTRION_HEADLESS_BIN=/path/to/astrion-headless python3 -B test/2026-11-27_Rust修改留痕/modify_history_http.py`：临时工作区和本地 mock 模型通过真实 Agent Web 路由，分别验证成功、停止、模型失败及仅保留编辑摘要。隔离检查 `git apply` 和 `git apply -R`。

留痕记录成功的主 Agent 工作区原生 `write_file` / `edit_file`；不采集命令输出、子智能体文件写入及工作区外另行授权的文件内容。与版本控制快照分别配置；任务结束时，只检查已记录文件是否仍存在。
