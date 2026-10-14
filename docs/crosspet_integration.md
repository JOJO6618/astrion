# CrossPet 本机接入（协议 v1）

Astrion 在 `host` 模式提供可选的 CrossPet 桌宠状态输出。Web、CLI、Electron、Tauri 发起的任务共用后端事件入口；Docker/web 服务不会导出本机状态。默认关闭。

## 启用与撤销

需要本次 Astrion 代码和支持 Astrion 协议的 CrossPet。当前配套 CrossPet 源码在 `_experiments/crosspet/`；该目录不纳入 Astrion Git，原始基线在 `_experiments/crosspet-upstream/`。不是未经修改的 CrossPet 1.3.4 即可接入。

在 Astrion 的 **设置 → 通用 → CrossPet 联动** 打开开关即可，不需要在 CrossPet 那边安装接入或启用第二个开关。默认关闭，所有工作区共用同一开关，仅 host 管理员可修改；Web 本机、CLI、Electron、Tauri 后端共用该配置。

开关持久保存在 `DEPLOY_CONFIG_DIR/crosspet.json`，默认是 `~/.astrion/astrion/config/crosspet.json`，沿用 `ASTRION_DATA_ROOT` 和部署配置目录覆盖规则：

```json
{"version":1,"enabled":true}
```

开启会启动或唤醒观察器，即使尚未发起任务，也可以输出空快照；之后任务事件自动更新状态。关闭后停止三个状态文件的所有写入，不删除、不补写清空快照；CrossPet 在最多约 45 秒后按过期规则清理 Astrion 状态。开关保存与状态写入共用锁，关闭响应返回后不能继续按旧开关写入。更新后端代码仍需正常重启一次，开关本身的变化无需重启。

默认状态目录：macOS 为 `/tmp/crosspet/`，Windows 为 `%LOCALAPPDATA%\CrossPet\state`；**三个联动文件不在 Astrion 的运行数据目录内**，桌面版和本机网页版使用相同协议。

输出目录优先级：`CROSSPET_STATE_DIR` → 可选 CrossPet 配置的 `state_dir` → 平台默认目录。CrossPet 配置在 macOS 的 `~/Library/Application Support/CrossPet/astrion.json`、Windows 的 `%LOCALAPPDATA%\CrossPet\astrion.json`；其 `enabled` 标记不再控制 Astrion 输出。没有该文件也能联动。

`CROSSPET_DATA_DIR` 可覆盖 CrossPet 配置目录，`ASTRION_CROSSPET_CONFIG` 只覆盖 Astrion 读取的可选 CrossPet 配置路径。自定义状态目录必须是绝对路径，两个进程应使用同一目录。CrossPet 原有 install/uninstall 命令只登记或取消目录配置，不开关 Astrion 的输出，也不修改 Astrion 配置或凭据。

## 状态协议

Astrion 独占 `<state_dir>/astrion-state.json`，使用唯一临时文件和原子替换。心跳约 10 秒，事件短时合并约 150 ms，活动查询约 2 秒。读取端最多接受 1 MB、256 个会话，45 秒无快照即清理 Astrion 来源。输出端最多序列化 256 个会话，依次优先前台、活动、等待和最近更新的会话；不会因为同时活动会话过多而输出读取端拒收的快照。

```json
{
  "version":1,"source":"astrion","instance":"runtime-uuid","sequence":12,"ts":1791980000,
  "foreground":{"session":"hashed-identity","model":"gpt-example","character":"gpt","ts":1791980000},
  "sessions":{
    "hashed-identity":{
      "character":"gpt","model":"gpt-example","pose":"reading","event":"PreToolUse","tool":"read_file",
      "ts":1791980000,"event_ts":1791979999,"revision":3,"active":true,"tools":1
    }
  }
}
```

- 会话键是用户名、工作区和会话身份的 SHA-256 摘要，不输出原始用户名、路径或会话 ID。不同工作区同名会话隔离。
- `instance` 标识本次后端进程，重启后更换；不承诺多后端共享同一输出文件。仍遵守 Astrion 的单后端进程约束。
- `ts` 是活动心跳，`event_ts` 是最后一个逻辑动作时间，`revision` 只在逻辑动作变化时递增。心跳不重演动作、不增加工具次数。
- 工具次数按会话、任务轮次独立累计，通过工具调用 ID 防重。来源不写到其他 AI 的 `<角色>-turn.json` 或 `<角色>-sessions.json`。
- 只输出角色、模型名、工具名、动作和状态等字段，不输出消息、思考正文、参数、命令、结果、异常文本、凭据。失败仅用状态/布尔值表示。
- `astrion-host.json` 是兼容两平台前台跟随的最小模型记录；`seen-astrion` 是接入诊断心跳。

## GPT 订阅额度（2026-10-15）

联动开启后，`modules/crosspet/quota.py` 在独立后台线程中使用 Astrion 自己的 Codex OAuth 登录态和已有代理配置查询 `wham/usage`，无需本机 Codex 另行登录。此查询不发起模型对话；不读取或复制 Codex CLI 凭据。没有订阅登录时不向 OpenAI 发起用量请求。网络和 token 刷新期间不持有任务、联动开关或文件写入锁。

额度附加在已有 `astrion-state.json` 的可选 `quota.gpt` 字段，不增加状态文件。只序列化经过数值校验的用量窗口；不导出完整接口响应、账号 ID、套餐资料、令牌、错误正文或 credits：

```json
{
  "connected": true,
  "status": "live",
  "epoch": "opaque-quota-generation",
  "at": 1791980000,
  "rateLimits": {
    "primary": {"usedPercent": 30, "windowDurationMins": 300, "resetsAt": 1791990000},
    "secondary": {"usedPercent": 45, "windowDurationMins": 10080, "resetsAt": 1792500000}
  }
}
```

`status` 为 checking/live/stale/unavailable，`at` 仅在成功查询时更新，不能用快照心跳伪装额度新鲜度；`epoch` 是不含账号信息的随机代次，用于隔离登录变更。正常活动时约 60 秒查询一次，空闲时约 120 秒；失败后也遵守间隔。登出或凭据变更会清缓存，旧请求结果不会跨代次提交。关闭联动后不再启动用量查询，已发起的请求可能结束，但不能继续写状态文件。

CrossPet 保留原有「显示 GPT 额度」显示选项，并新增独立的「显示 Astrion 的 GPT 额度」，两者互斥（打开一个自动关闭另一个）。打开 Astrion 选项时读取快照里的额度并标注来源；数据不可用时保留上次成功数值、标记旧数据，不用本机 Codex 的另一个账号顶替，也不自动切回 Codex。超过 150 秒未成功查询、或已到窗口重置时间均标为旧数据；到期窗口显示「待更新」，不凭时间推算剩余 100%。超过 15 分钟的额度不再显示。切换来源/登录代次、重复心跳和失败不能误触发额度恢复庆祝；新鲜真实额度仍沿用现有低额度和恢复反应。

## 生命周期与实现位置

`modules/crosspet/state.py` 负责纯状态转换，`observer.py` 负责单写者后台队列、活动探测和原子文件输出，`settings.py` 保存 Astrion 的独立总开关。`server/crosspet.py` 是 host 门控及实际模型身份的薄适配层；`server/crosspet_settings.py` 提供 `/api/crosspet` GET/POST，Web 与 headless 两个入口都注册，不为设置查询创建执行实例。通用设置组件为 `CrossPetSettings.vue`，文案中英文同步。

`server/tasks/models.py` 在任务受理、执行资源装配、已有任务事件出口与线程结束时派发白名单元数据。观察器不改写历史、事件索引、显示快照、门闸或任务状态；任务锁及显示锁内不执行配置读取、文件 IO、模型查询、后台状态查询或外部进程。辅助失败不阻塞任务。

`task_complete` 只表示回复边界。成功、停止、失败由真实任务终态确定；业务 `error` 标记禁止庆祝。主线程结束但后台命令、传统子智能体、多智能体仍活动时保持 delegating，后台活动解除后再显示终态。未知活动查询不视为空闲。多智能体 idle 本身不算活动，待派发消息算活动。

审批、提问、计划确认必须收到明确的 resolved 事件才解除等待；等待期间其他工具和模型请求不能覆盖它。手动与自动压缩使用现有 compression_state/compression_finished 事件，压缩迁移会话身份时转移同一个任务的投影。

桌面前台模型由已提交的标签选择写入和成功的模型切换补充，不额外创建 WebTerminal，不使用查询请求去改变工作区选择。标签模型查询延后到启用后的观察器线程；关闭接入时不读对话来获取模型，也不探测后台任务。初次创建会话和压缩迁移都保留实际装配后的探测器。

CrossPet 的 `app/web/astrion.js` 提供独立来源合并和协议校验，`astrion-view.js` 将它接入原角色动画。已有钩子和 Astrion 的会话取并集；其他来源仍在工作时，Astrion 的完成/撤销不能将其清空。Mac/Windows 分别通过 App+Astrion.swift/Pet.Astrion.cs 读取同一协议；原生置顶与贴边判断单独保存 Astrion 贡献，过期不再使用，也不清除其他来源状态。

## 验证

```sh
python3 -B -m unittest discover -s test/2026-10-14_CrossPet接入 -p 'test_*.py'
python3 -B test/2026-10-14_CrossPet接入/runtime_check.py
python3 -B -m unittest discover -s test/2026-10-15_CrossPet开关 -p 'test_*.py'
python3 -B test/2026-10-15_CrossPet开关/settings_api_check.py
```

CrossPet 源码目录：

```sh
npm test
python3 -B -m unittest discover -s tests -p '*_test.py'
swiftc -typecheck -module-cache-path .astrion/clang-module-cache app/mac/*.swift
```

以上命令和前轮结果覆盖工作状态与联动开关。2026-10-15 新增订阅额度接入按用户要求仅修改文件，不执行运行测试、真实额度请求或原生构建，需由用户实测；前轮测试结果不能作为本轮额度接入已经通过的证据。真实角色动画、窗口切换、macOS 权限及 Windows 原生运行仍需实机验收。API 余额和费用不在本次额度接入范围，不将累计消费当成剩余额度。不修改素材授权，不将角色素材复制到 Astrion 产品中。
