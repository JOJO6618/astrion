# 测试现状与执行方式（完整）

> 本文由 `AGENTS.md` §3 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 3) 测试现状（不要再写过时命令）

- **测试分类（2026-10-04 用户要求）**：每次新增测试都在 `test/` 下新建 `日期_功能/` 目录，日期用 `YYYY-MM-DD`，例如 `test/2026-10-04_对话压缩/`；禁止把测试文件直接放在 `test/` 根目录或项目根目录。同日同功能的独立测试批次用 `_02`、`_03` 后缀，不能覆盖已有目录。配套辅助文件放在对应测试目录内。
- **历史测试**：无法确认日期的已有测试集中放在 `test/历史测试_日期不明/`，不按文件修改时间猜测日期。移动时必须修正相对路径、辅助文件引用和运行命令。
- **执行方式**：pytest 可以按分类目录收集；unittest 使用具体分类目录作为 `discover` 起点，不能依赖 `discover('test')` 自动递归这些非包目录。本次压缩回归：`python3 -m unittest discover -s test/2026-10-04_对话压缩 -p 'test_*.py'`。

- 当前仓库内可见自动化冒烟：`test/历史测试_日期不明/test_server_refactor_smoke.py`（`unittest`）
  - 运行方式：`python -m pytest test/历史测试_日期不明/test_server_refactor_smoke.py -q`
  - 注意（2026-08-01 实测）：`python -m unittest test.test_server_refactor_smoke` 在标准 Windows Python 上失败——stdlib 自带常规包 `test` 遮蔽本地 `test/` 目录（无 `__init__.py` 的命名空间包优先级更低），报 `No module named 'test.test_server_refactor_smoke'`。无 pytest 时可用：
    `python -c "import sys; sys.path.insert(0, '.'); import unittest; suite = unittest.TestLoader().discover('test/历史测试_日期不明', pattern='test_server_refactor_smoke.py'); r = unittest.TextTestRunner().run(suite); sys.exit(0 if r.wasSuccessful() else 1)"`
- `test_system_message.py` 依赖外部 `MOONSHOT_API_KEY` 与网络，不属于离线稳定 CI 用例。
- 当前仓库未发现 `pytest.ini`/`pyproject.toml`/`tox.ini`；不要默认要求 `pytest` 作为唯一入口（仅作为冒烟测试的便捷运行器）。
- CLI 当前最小可复现验证：
  - `cd cli && ./node_modules/.bin/tsc --noEmit`
  - 无头冒烟：`testRender` 抓帧脚本（写完即用即删，参考 cli-redesign-demo 的验证模式； gateway 指向 127.0.0.1:9 不碰真实服务）
- 若改动 `server/chat/`、`server/status/`、`server/tasks/` 等后端接口适配，补充：
  - `python3 -m py_compile server/chat/*.py server/status/*.py server/tasks/*.py`
  - `python -m pytest test/历史测试_日期不明/test_server_refactor_smoke.py -q`
