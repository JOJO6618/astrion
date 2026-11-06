# 无限制模式单次完全访问自动审批（2026-11-07）

复用用户级 `allow_auto_approve_full_access`，默认关闭，只影响沙箱中显式申请的单次 `full_access`。

| 权限模式 | 开关关闭 | 开关开启 |
| --- | --- | --- |
| auto_approval | 自动与人工均通过 | 自动审核通过即可执行 |
| unrestricted | 人工审批 | 自动审核通过即可执行 |
| approval | 人工审批 | 人工审批 |

普通 unrestricted 沙箱调用和已在 direct 环境的调用继续免审。审批条件在创建时保存；后续派发读取请求快照，不在等待期间重新读取开关。拒绝、故障、输入变化均不得执行待审命令。

## 验证范围

`test_unrestricted.py` 使用真实生产权限判定方法、工具循环、ToolApprovalManager、自动审核服务、输入指纹与一次性授权。模型响应和工具体使用合成替身，验证模式／开关矩阵、默认人工入口、自动批准、单次 grant 只消费一次、拒绝／故障后同批后续调用继续、持续输入变化拒绝执行、审批条件快照、普通调用免审及只读失败后的普通审批重试。临时数据目录，无真实模型调用、凭据或宿主机命令执行。

`frontend.cjs` 加载生产 TS 状态逻辑，验证 unrestricted 请求显式字段不被模式兜底覆盖、审核进度保留、关闭开关时人工动作保留，以及批准／拒绝／error 的真实终态与清理。

## 已执行

```bash
/opt/homebrew/bin/python3.11 -B -m unittest discover -s 'test/2026-11-07_无限制单次完全访问' -p 'test_*.py' < /dev/null
/opt/homebrew/bin/python3.11 -B -m unittest discover -s 'test/2026-11-06_自动审核上下文' -p 'test_*.py' < /dev/null
/opt/homebrew/bin/python3.11 -B -m unittest discover -s 'test/2026-11-05_完全访问审批' -p 'test_*.py' < /dev/null
/opt/homebrew/bin/python3.11 -B -m unittest discover -s 'test/2026-10-07_子智能体权限与单次完全访问' -p 'test_*.py' < /dev/null
/opt/homebrew/bin/python3.11 -B -m unittest discover -s 'test/2026-10-23_运行中压缩与状态边界' -p 'test_*.py' < /dev/null
node --test 'test/2026-11-07_无限制单次完全访问/frontend.cjs' 'test/2026-11-06_自动审核上下文/frontend.cjs' 'test/2026-11-05_完全访问审批/frontend.cjs'
./node_modules/.bin/tsc --noEmit
npm run lint:text --silent
./node_modules/.bin/eslint static/src/locales/zh-CN/personalization.ts static/src/locales/en-US/personalization.ts
set -o pipefail
npm run build --silent 2>&1 | tail -n 5
```

结果：Python 11＋76＋41＋35＋13＝176 项通过；Node 3＋3＋5＝11 项通过。类型检查、文案审计、定向 ESLint、相关 py_compile、git diff --check 通过。标准生产构建成功（15.42 秒），保留已有大 chunk 提示。

第一轮新增测试因终端桩漏配 `_APPROVAL_REQUIRED_TOOLS` 导致五个子场景报错，补齐桩后完整重跑通过；未为通过测试修改生产权限判定规则。

未重启后端、未提交或推送；真实审核模型效果、原生平台执行和视觉仍需用户实机验收。此次未扩展“隐藏工具审核面板”设置的模式范围，无限制模式的自动审核按现有规则展开面板。
