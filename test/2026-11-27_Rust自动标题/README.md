# Rust 自动标题回归

运行真实 Rust Host 服务。测试使用独立临时数据根、工作区、登录态和本机 SSE 提供商。

```bash
ASTRION_HEADLESS_BIN="$PWD/backend-rust/target-master/debug/astrion-headless" \
  python3.11 -B 'test/2026-11-27_Rust自动标题/title_http.py'
```

脚本覆盖 Agent 默认模型选择和运行中标题事件，Chat 主回复完成后的标题和历史行刷新，显式隐藏模型，编辑分支，手动同名保存，以及关闭、无效模型、已存跳过标记、无注册模型的情形。Codex 目录回归单独覆盖仅订阅模型时选用 Luna。
