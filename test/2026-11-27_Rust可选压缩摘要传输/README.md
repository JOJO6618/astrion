# Rust Host 对话传输：可选历史压缩摘要

用真正的 Web 导入、导出和工作区迁移接口验证 v1 ZIP。缺失的历史压缩摘要不会阻塞对话传输、原消息与压缩记录保留不变；仍存在的摘要按原逻辑导出。额外验证已还原后被清理的摘要路径，以及必需图片、交接材料、包内声明的文件缺失或校验失败仍拒绝。不调用模型服务。

```sh
cargo build --locked --manifest-path backend-rust/Cargo.toml --bin astrion-headless
ASTRION_HEADLESS_BIN="$PWD/backend-rust/target/debug/astrion-headless" \
  python3 -B 'test/2026-11-27_Rust可选压缩摘要传输/transfer_optional_summary.py'
```
