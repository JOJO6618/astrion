# 旧多智能体主对话迁移

更新：2026-10-22。

新版本主对话统一存储，不再查询旧 mutiagents 目录。历史数据由 `scripts/migrate_multi_agent_conversations.py` 独立迁移；应用不提供运行时双目录兼容，旧传统子任务不需要恢复。

## 范围与前提

迁移对象是现行 `conv_*.json` 主对话文件及对应索引，保留 conversations 下的工作区相对路径。保留对话ID、消息、待办、用量和其余metadata；移除 multi_agent_mode、frozen_available_agents_prompt、frozen_main_system_prompt_multi_agent 三个旧字段。旧工具消息保持原文，不转换旧子实例记录。

正式 apply 前停止会写入这两个存储目录的 Astrion 后端；脚本不会自己停服务。不要同时启动 full/headless 两个后端。服务停止是避免并发修改的操作前提，脚本仍会校验源文件和索引是否发生变化。

## 本机已核对的目录

```text
源：~/.astrion/astrion/host/mutiagents/conversations
目标：~/.astrion/astrion/host/data/conversations
```

这是本机现行host数据布局，不是所有部署的硬编码默认值。其它数据根需传入实际 conversations 目录，不要传数据根或某个单工作区目录。另一套早期 ma_*.json 数据不在本脚本迁移范围。

## 预览

默认只读，不创建目标文件或备份。项目根目录执行：

```bash
/opt/homebrew/bin/python3.11 scripts/migrate_multi_agent_conversations.py \
  --source-dir "$HOME/.astrion/astrion/host/mutiagents/conversations" \
  --target-dir "$HOME/.astrion/astrion/host/data/conversations"
```

报告包含文件数、工作区和目标路径。任何重复ID、已有目标、坏JSON／坏索引或符号链接都会使整批预检失败，不覆盖原文件。

## 正式迁移

停服务并核对预览后，在同一命令增加 --apply；建议明确指定一个全新备份目录，例如：

```bash
/opt/homebrew/bin/python3.11 scripts/migrate_multi_agent_conversations.py \
  --source-dir "$HOME/.astrion/astrion/host/mutiagents/conversations" \
  --target-dir "$HOME/.astrion/astrion/host/data/conversations" \
  --backup-dir "$HOME/.astrion/astrion/sub-agent-merge-backup" \
  --apply
```

备份目录必须不存在，且位于两棵 conversations 树之外。省略时脚本会生成新的备份位置，以报告中的路径为准。

执行顺序：完整预检 → 备份源原文、全部受影响索引和manifest → 独占创建目标文件 → bytes核验 → 再次确认源／索引未变化 → 删除已迁移源 → 更新索引。任何一步异常会删除本轮新增目标，恢复源文件及旧索引。源目录本身保留；不递归删除运行态目录。

## 备份与恢复

备份保存原始bytes、hash和manifest。脚本支持执行失败时自动回滚，**没有独立的 --rollback 命令**。成功迁移后需要人工撤回时，应先停服务并按manifest恢复源文件和原索引，只删除manifest记录的本轮目标文件；已有新数据时先另行备份，不应整目录覆盖。

成功后按正常方式启动新后端，确认同一主列表能看到原有普通与迁入对话、打开原ID历史、切换工作区及继续新建子任务。原多智能体子实例不能按旧记录续接，新的子实例才使用新恢复机制。

## 已验证的本机数据

2026-10-22读取本机真实数据进行只读预览：217条旧多智能体主对话、7个工作区，无目标ID冲突。

在 `.astrion/local_data_subagent_merge/` 隔离副本中执行迁移并核对：217条迁入、源副本余0条；全部消息／待办／用量保持；备份bytes一致；已有主对话文件保持；真实源／目标树前后hash相同。使用生产 ConversationManager 的普通列表和读取接口，217条均可加载。

本机真实原目录尚未执行 --apply。副本和报告包含用户私有数据，不应提交或发布。离线副本通过不等于真实后端／界面已验收。
