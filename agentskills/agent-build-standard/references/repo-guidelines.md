# 项目规范（摘要）

## 结构与职责
- `main.py` 启动 CLI agent；`web_server.py` 提供 Web UI
- `core/` 负责终端编排，尽量保持 I/O 轻量
- `modules/` 放可复用能力，优先扩展这里
- `utils/` 放 API 客户端与日志工具，优先复用
- `test/` 放集成测试脚本与辅助工具

## 代码风格
- Python 3.11+，4 空格缩进，snake_case
- 需要类型标注与简短双语 docstring
- 日志统一使用 `utils.logger.setup_logger`

## 前端注意
- `static/src/components/chat/monitor` 为虚拟显示器动画层
- 新场景需对齐 `MonitorDirector` 与 monitor store

## 测试建议
- 优先 `pytest`，文件名为 `test/test_<feature>.py`
- 涉及网络请使用 `test/api_interceptor_server.py` 代理

