# 子智能体查询工具合并

2026-10-23：删除独立list_active_sub_agents，主、子智能体共用get_sub_agent_status。省略names查询当前主对话全部实例（包括idle和terminated）；传入非空names列表按名称查询。显式空列表、null或非列表返回错误。统一success/results，每项found表示是否找到。

模型可见的创建和查询结果不返回task_id、conversation_id；内部记录以及前端活动／运行视图仍保留两者。旧查询工具的定义、执行分派、类别、动态加载和格式化注册均删除，不增加旧名称别名。

## 本次验证

- Python 3.9.6（项目.venv）：本目录7项通过，覆盖全量／按名、大小写与顺序、缺失名称、参数校验、跨对话隔离、主子一致、模型结果不泄露内部ID、前端实时与持久视图仍保留ID、主子schema及工具注册一致。
- Python 3.9.6：原统一子智能体目录23项通过；公开创建回归更新为按名称查内部记录，并断言返回中没有两个ID。
- Python 3.11：动态加载23项通过，相关后端模块py_compile通过。
- subAgentResults.ts：定向ESLint通过，正式前端构建通过（13.61秒；现有大chunk提示保留）。
- git diff --check通过。

```bash
ASTRION_IGNORE_DOTENV=1 .venv/bin/python -B -m unittest discover \
  -s 'test/2026-10-23_子智能体查询合并' -p 'test_*.py' -v
ASTRION_IGNORE_DOTENV=1 .venv/bin/python -B -m unittest discover \
  -s 'test/2026-10-22_子智能体统一' -p 'test_*.py' -v
/opt/homebrew/bin/python3.11 -B test/历史测试_日期不明/test_tool_loading.py
./node_modules/.bin/eslint static/src/components/chat/actions/subAgentResults.ts --max-warnings=0
set -o pipefail
npm run build --silent 2>&1 | tail -n 5
```

未重启服务、未提交／推送。真实模型和UI仍需运行验收。本批次没有修改stop行为；stop即时取消的新需求仍在讨论。
