# 用户自定义子智能体容量

## 行为约定

- 设置→子智能体新增“允许同时存在的子智能体数量”，默认20，只接受大于等于1的JSON安全整数。
- 用户级`sub_agent_max_instances`存于既有共享`personalization.json`。每个主对话独立统计`status != terminated`的实例；idle与failed等仍占名额。
- 创建时读取最新设置，修改保存后下一次创建立即采用，不固定在实例、对话快照或进程启动时。降低上限保留已有实例和续接能力；高于或等于上限时只拒绝新建。
- 旧`SUB_AGENT_MAX_ACTIVE`环境变量入口已删除，统一由用户设置控制。产品默认与校验集中在`modules/sub_agent_settings.py`。
- `GET/PUT /api/sub_agents/settings`同时处理容量与上下文压缩阈值。保存错误会显示在界面；未成功加载设置时禁止用默认值覆盖未知配置。

## 回归覆盖

`test_settings.py`共7项：

1. 默认20、HTTP保存与GET读回、磁盘重新加载、无关个性化保存不丢字段。
2. 布尔、零、负数、小数、字符串、空值、数组及超出JSON安全整数范围的输入返回400且不改旧配置。
3. 缺失或损坏的持久化字段恢复为默认20。
4. 同一个manager即时采用调低及调高后的容量，保留已有实例并可通过公开工具继续工作。
5. 上限可超过20，每个主对话独立计数。
6. 自定义上限为1时并发创建只能成功一次。
7. 用户级共享配置的符号链接保存后保持链接，manager读取同一份最新容量。

`frontend.cjs`执行真实composable，覆盖加载、保存、重新进入、正整数校验、HTTP及网络错误、重复点击防重、未加载时禁止保存及SFC接线。

## 复现命令

项目根目录执行；全部Python测试用临时合成数据与工作区内测试配置根，忽略真实`.env`，不请求真实模型、不启动第二后端：

```bash
ASTRION_IGNORE_DOTENV=1 ASTRION_DATA_ROOT="$PWD/.astrion/test_runtime/sub_agent_settings" \
  .venv/bin/python -B -m unittest discover \
  -s 'test/2026-10-23_子智能体容量设置' -p 'test_*.py' -v

ASTRION_IGNORE_DOTENV=1 ASTRION_DATA_ROOT="$PWD/.astrion/test_runtime/sub_agent_settings" \
  .venv/bin/python -B -m unittest discover \
  -s 'test/2026-10-23_子智能体数量与入口' -p 'test_*.py' -v

ASTRION_IGNORE_DOTENV=1 ASTRION_DATA_ROOT="$PWD/.astrion/test_runtime/sub_agent_settings_existing" \
  .venv/bin/python -B -m unittest discover \
  -s 'test/2026-10-22_子智能体统一' -p 'test_*.py' -v

node test/2026-10-23_子智能体容量设置/frontend.cjs
node test/2026-10-23_子智能体数量与入口/frontend.cjs

set -o pipefail
npm run build --silent 2>&1 | tail -n 5
```

## 验证结果与边界

2026-10-23，应用Python3.9.6下本批7项、既有容量6项与统一子智能体23项共36项通过。两组前端行为回归通过；正式前端构建（含tsc、stylelint）、变更文件定向ESLint零warning及i18n审计通过。

正式构建有既有大chunk提示。API测试隔离认证和工作区解析装饰器，实际执行Flask请求解析、参数校验与真实配置持久化；没有据此宣称认证链或真实模型端到端已经重新验收。视觉由用户验收，本轮未重启正在运行的服务、未提交或推送。
