# 残留孤儿 Python 后端：已回收（保留历史依据）

## 状态：已于 2026-11-26 在宿主回收

- 主侧在沙箱外精确核对了该 PID 并终止；`ps` 确认不存在，端口 56568 已关闭。
- 本侧复核：`find_orphan.py` 输出「没有发现 python3.11 进程」；对 127.0.0.1:56568 的 connect 为 `closed`。
- `.tmp/small`（它持有的数据根）已被删除；本目录正式测量改用 `.tmp/biz-small` / `.tmp/biz-large`，不依赖它。

以下内容是回收前的诊断记录，仅作历史依据保留，**当前不存在该进程**。

## 历史结论：原 py_bootstrap 停止文件无法作用于它

- `py_bootstrap.py` 的停止标记按 argv 绑定：`sys.argv[1]` 就是该进程独占的 stop_file 路径，
  后台线程只轮询这一个路径（`py_bootstrap.py:28,33`）；没有环境变量入口，也没有全局广播；
- 该孤儿的 argv 中没有 py_bootstrap，形态是 `python -B -m server.headless_app ...`（旧版 bench 直接 spawn 的结果），
  因此它不监视任何停止文件；
- 基准脚本写入的停止文件名是 `.tmp/stop-<kind>-<port>.flag`，每次运行前 unlink、结束后再 unlink，
  只可能命中本次 spawn 的那一个进程，不会影响任何其它服务——两轮正式测量共 28 次启动全部正常退出、端口全部关闭可证。

## 历史识别项（回收前的记录）

| 项 | 值 |
|---|---|
| PID | 17854（现已不存在） |
| 启动时间 | 2026-11-26 05:51:35（本地，epoch 1795643495.70） |
| RSS | 98 MB → 32 MB（idle 回落） |
| 监听 | 127.0.0.1:56568（当时 open，现 closed） |
| 可执行文件 | /opt/homebrew/Cellar/python@3.11/3.11.13/Frameworks/Python.framework/Versions/3.11/Resources/Python.app/Contents/MacOS/Python |
| 完整 argv | -B -m server.headless_app --port 56568 --path <worktree>/test/2026-11-26_Rust启动性能/.tmp/small/ws |

## 当时的沙箱内尝试（均失败，因此走了提权）

`kill -0 17854` → `Operation not permitted`（连探测信号都被拒）；无停止文件通道；Flask 开发服务器没有远程退出端点。
