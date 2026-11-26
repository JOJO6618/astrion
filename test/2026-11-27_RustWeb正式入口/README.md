# Rust Host Web 启动入口冒烟

```bash
python3 -B 'test/2026-11-27_RustWeb正式入口/launch_smoke.py' \
  --binary backend-rust/target-master/debug/astrion-headless
```

分别运行 `main.py`、`python3 -m server.app`、`python3 server/app.py`、`web_server.py` 和 `start.sh`。验证各入口在临时数据根内采用 `settings.json` 的端口，健康接口的 Rust PID 对应启动进程，HTML 入口返回构建资源和 `Cache-Control: no-store`，绑定工作区的 Bearer 会话列表可用，SIGTERM 正常退出。五个入口均使用默认后端，Rust 构建文件由测试参数指定。
