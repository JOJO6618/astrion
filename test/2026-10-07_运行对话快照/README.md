# 运行对话显示快照回归

最后验证：2026-10-08。100 个 Python 用例／Node 回归组全部通过。生产构建、定向 ESLint 与本轮 18 个修改 Python 模块的无缓存语法检查通过。实际界面与繁忙机器的视觉/性能体验尚待用户验收。

## 运行

在仓库根目录，使用已安装项目依赖的 Python 和当前 Node：

```bash
/opt/homebrew/bin/python3.11 -B test/2026-10-07_运行对话快照/run_regressions.py
```

本机已经确认该 Python 有 yaml/flask/httpx/openai；其他机器可换成具备项目依赖的解释器。Node 测试使用仓库已有 TypeScript 与 Pinia 等依赖，不执行安装。

统一入口先运行两个 Python suite 和五个 Node suite，再以临时运行态目录执行原有 `test_server_refactor_smoke.py` 的 unittest。临时目录位于本分类目录，自动清理；禁用 dotenv，隔离运行态、部署配置、日志与用户空间。

测试不启动真实服务或 TUI，不请求模型，不读取真实用户对话。采用具体分类目录的 unittest discovery，避免 pytest 扩大收集范围枚举受保护的根目录文件。

生产构建：

```bash
set -o pipefail
npm run build --silent 2>&1 | tail -n 5
```

`pipefail` 保留真实失败退出码。本次构建经过 TypeScript、stylelint 和 Vite；仅有较大 chunk 提示。

## 覆盖

| Suite | 数量 | 主要验证 |
| --- | ---: | --- |
| `projector_tests.py` | 27 | 用户/思考/正文/工具/审批/系统通知投影、show_html、重试、终态、超过 25000 个片段 |
| `snapshot_tests.py` | 12 | 显示与绝对游标、截断缓存仍完整、部分落盘不重复、消息身份、工作区隔离、通知迁移、接力、压缩、回溯、只读与共享目录写锁 |
| `conversation_session_tests.cjs` | 9 | A→B→A、JSON 解析期间切换、离开失效、一次消息提交、不等布局/工作流、快照 hydrate 与精确游标 |
| `task_polling_tests.cjs` | 18 | 同 task ID 重接管、旧成功/404/finally、窗口缺口、取消、handler 更换、请求所有权与真实 Pinia |
| `message_identity_tests.cjs` | 10 | 乐观用户/assistant 绑定、同文本不同身份、快照后思考/正文/工具接续、show_html、重试区间、引导后新 assistant、时间转换 |
| `auxiliary_ownership_tests.cjs` | 10 | 空页面 ABA、同类请求倒序、live todo/token 胜过旧 GET、子详情关闭、问题/计划不复活、回答后导航 |
| `entry_ownership_tests.cjs` | 8 | 空页面路由、idle status、工作区响应倒序、旧列表错误、草稿 GET/POST、只剩后台命令的活动状态 |
| 原有应用冒烟 | 6 | 后端 import/runner、chat flow helper、工作区个性化、stats、动态工具刷新与纯 helper |
| **合计** | **100** | **全部通过** |

Node 回归通过 `ts_fixture.cjs` 转译当前真实 TS 方法，以 fake host/store 与受控 IO 构造响应顺序；不是复制一份待测业务逻辑。部分轮询用例直接使用 Pinia。Python 投影使用真实模块，快照/存储测试为隔离依赖按 AST 提取实际实现，不装配生产服务。

计数为 Python 测试方法与 Node 顶层回归组之和；一个组可以有多条断言，不等于 100 次真实 UI 操作。

## 用户视觉验收

在加载此源码与构建产物的运行实例中检查；已安装桌面包不会因修改源码自动更新内嵌后端。

1. 思考或长正文流式输出时切走再切回，确认直接显示完整当前进度，后续片段接在原块上。
2. 快速 A→B→A，以及对话→新对话→对话，确认迟到响应不会覆盖页面、标题、模式或任务状态。
3. 引导、特殊用户通知和两次相同文本输入后切换，确认不同输入保留，单次输出不重复。
4. 工具 preparing→running→completed 时切换，确认仍是一个工具块，结果继续更新。
5. 人工/自动审批、提问或计划审批期间切换，确认已有交互能恢复，已处理请求不会被旧 GET 重新显示。
6. 前台任务结束但后台子智能体/命令仍运行，确认活动状态保留，完成通知能正常续接。
7. 压缩期间切走切回，完成后确认历史/队列/运行显示保持一致，后续增量继续正常。
8. 切换工作区及进入 `/new`，确认旧工作区响应与草稿不会回写当前视图。
9. 在电脑繁忙时重复以上关键切换，观察是否仍闪旧进度、重复输出，并区分完整历史解析耗时与流式续接问题。

这些步骤未由浏览器自动化代替用户验收。长历史仍全量读取，没有首次加载时延承诺。

## 相关文档

- 当前协议：`docs/运行对话显示快照契约.md`。
- 调查证据：`docs/2026-10-07_运行中对话加载调查.md`。
- 旧 `运行对话加载调查/loading_races.cjs` 用于调查时证明旧竞态，断言的是问题存在，不作为当前修复回归入口。
