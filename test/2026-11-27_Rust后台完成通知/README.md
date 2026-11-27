# Rust 后台指令完成通知回归

`background_notice_http.py` 启动隔离的 Rust Web Host 和本机模拟模型。实际创建并手动触发两条定时任务：
- 主任务先结束时，不访问对话运行状态接口，后台指令完成后仍会自动续跑，计划写入成功和最终摘要。
- 主任务仍在运行时，在同一轮内注入通知，不新开工作段；正文使用旧版简短格式。
- 随后访问旧状态轮询入口，不会重复派发。在两个分支检查真正进入模型请求的英文通知正文。

运行：

```sh
cargo build --locked --manifest-path backend-rust/Cargo.toml --bin astrion-headless
ASTRION_HEADLESS_BIN=backend-rust/target/debug/astrion-headless \
  python3 -B 'test/2026-11-27_Rust后台完成通知/background_notice_http.py'
cargo test --locked --manifest-path backend-rust/Cargo.toml --lib notice_message_tests
```

HTTP 夹具使用 POSIX 本地指令和临时数据；不需要真实模型账号。不验证原生桌面形态或关机运行。
