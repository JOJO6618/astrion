# 系统提示词统一冻结回归

所有对话、目录、记忆和提供商指令均为合成数据；不读取真实 `.env`、账号、对话或凭据，不请求外部模型，不启动服务。

## 本目录

```bash
/opt/homebrew/bin/python3.11 -B -m unittest discover -s test/2026-11-12_系统提示词统一冻结 -p 'test_*.py'
```

23 项，覆盖：

- 新对话的完整 system（含提供商前置指令和桥接）只保存到一个字段。
- 同配置的新对话共享相同静态前缀，动态资料在后。
- 普通发送与恢复不重读模板、不刷新模式或提供商指令。
- 旧分段按历史顺序合并，普通读取不迁移；压缩后切新版布局。
- 原子写失败不发送未持久化快照；并发只生成一份。
- 图片、图片＋视频和纯文本能力说明；动态资料中的字面大括号保持原样。
- Responses 原样发送冻结文本、不重复基础指令；内部标记不外发。
- 运行期间通知保持现有角色和内容。
- 自动浅压缩有实际内容时失效完整快照，空操作不失效。
- 私有 MEMORY.md 正文与版本数据一起提交，免预读识别不依赖旧正文 key。
- 普通历史保存不覆盖快照；检查点回溯保留目标对话的当前快照，新分叉不强行继承源快照。
- 新模板缺失时明确失败，避免冻结成一句通用兜底提示。

## 相关现有回归

私有记忆 20 项、环境显示 11 项：

```bash
/opt/homebrew/bin/python3.11 -B -m unittest discover -s test/2026-11-07_私有项目记忆 -p 'test_*.py'
/opt/homebrew/bin/python3.11 -B -m unittest discover -s test/2026-11-07_公共执行层 -p test_environment_routing.py
```

深压缩提交／取消／失败的 10 项，在本目录的隔离配置下运行：

```bash
/opt/homebrew/bin/python3.11 -B - <<'PY'
import sys
import unittest
sys.path.insert(0, 'test/2026-11-12_系统提示词统一冻结')
import support
suite = unittest.defaultTestLoader.discover('test/2026-10-04_对话压缩', pattern='test_compression_lifecycle.py')
result = unittest.TextTestRunner().run(suite)
raise SystemExit(not result.wasSuccessful())
PY
```

共 64 项通过。另对本轮 17 个 Python 模块执行 `compile(...)` 语法检查（不生成字节码）；`git diff --check` 通过。

这些结果验证冻结、迁移和请求结构；真实上游的 `cached_tokens` 命中情况尚未实测。工具列表、缓存键、Chat 与子智能体的独立提示词机制不属于本轮修改范围。
