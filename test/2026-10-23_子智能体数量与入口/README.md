# 子智能体数量与输入栏入口回归

产品约定：每个主对话默认最多保留20个未被terminate的子实例；idle、running及异常failed等均占名额。terminate释放名额但保留历史，不另设running上限。输入栏显示“X 个子智能体”，点击展开快捷窗口并定位子智能体卡片；形象只将running子实例视为后台工作。

## 覆盖内容

- `test_capacity.py`：6项Python行为回归，使用真实manager、实例、注册表和临时持久化目录，仅省略模型及执行worker。覆盖20个idle阻止新建、20个running可共存、只有terminate释放名额、不同主对话独立计数、20个idle经公开消息工具全部恢复running，以及并发创建不能超占最后名额。
- `frontend.cjs`：执行真实TS逻辑。覆盖按钮数量与运行状态分离、各非running状态不触发形象工作态、后台命令及API等待保持原规则；桌面／移动端展开入口、已打开移动层不反向关闭、跨对话及重复点击的请求归属、仅滚动快捷窗口和减少动态效果偏好。

## 复现命令

在项目根目录执行，配置数据根指向工作区内测试目录，忽略真实`.env`：

```bash
ASTRION_IGNORE_DOTENV=1 ASTRION_DATA_ROOT="$PWD/.astrion/test_runtime/sub_agent_capacity" \
  .venv/bin/python -B -m unittest discover \
  -s 'test/2026-10-23_子智能体数量与入口' -p 'test_*.py' -v

node test/2026-10-23_子智能体数量与入口/frontend.cjs
node test/2026-10-08_主子费用/avatar_tests.cjs
node test/2026-10-23_运行中压缩与状态边界/frontend.cjs

ASTRION_IGNORE_DOTENV=1 ASTRION_DATA_ROOT="$PWD/.astrion/test_runtime/sub_agent_capacity_existing" \
  .venv/bin/python -B -m unittest discover \
  -s 'test/2026-10-22_子智能体统一' -p 'test_*.py' -v

set -o pipefail
npm run build --silent 2>&1 | tail -n 5
```

## 2026-10-23 验证结果

- 本批Python 6项通过，既有统一子智能体23项通过，应用解释器Python 3.9.6。
- 本批前端回归与既有形象／压缩前端回归均通过。
- 前端正式构建通过（含tsc及stylelint），改动TS／Vue文件定向ESLint零warning；i18n审计、Prettier格式检查及`git diff --check`通过。
- 构建有既有大chunk提示，不影响成功结果。

这些是离线逻辑和构建验证，不替代真实模型调用及用户视觉验收。首批修改已提交为`397fbb6a`，未推送；桌面／移动端实际点击、展开与滚动效果由用户验收。后续已新增用户级容量设置`sub_agent_max_instances`，保存后下一次创建即时生效，替代旧环境变量；本批6项回归已按真实用户默认值重新通过，详见`test/2026-10-23_子智能体容量设置/README.md`。
