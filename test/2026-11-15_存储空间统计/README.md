# 存储空间统计（设置 → 账户 → 存储空间）

## 覆盖范围

只统计数据根与日志根，**不遍历工作区项目目录里的 `.astrion/`**：

| 类别 | 路径 |
|------|------|
| 备份与快照 | `<DATA_DIR>/save/` |
| 日志 | `<LOGS_DIR>` |
| 浏览器 / MCP | `<DATA_DIR>/mcp_profiles/` |
| Agent 对话 | `<DATA_DIR>/conversations/`（不含 media_store） |
| 对话媒体 | `<DATA_DIR>/conversations/media_store/` |
| 子智能体 | `<DATA_DIR>/sub_agent_tasks/` + `sub_agent_runtime.json` + `sub_agents.json` |
| Chat 对话 | `<DATA_DIR>/chat/` |
| 其他 | 其余（配置、`api_costs`、草稿、模型缓存…） |

沙箱未纳入统计（host 模式无持久沙箱目录；Windows WSL 沙箱的 vhdx 属于系统层）。

## 运行回归

```bash
python3 -B test/2026-11-15_存储空间统计/test_storage_stats.py
```

用例只使用临时目录（在 `cache/` 下创建并清理），不会读取或删除真实数据根，覆盖：

- 分类规则（`media` 前缀优先于 `conversations`、单文件规则）
- 扫描归桶与「总和 = 各类之和」、符号链接跳过
- 日志清理（活动 `.log` 截断、归档删除、子目录递归）
- 备份清理（只删 `conv_*`、`meta.json.updated_at` 优先/回退 mtime、符号链接跳过）
- 缓存读写与版本守卫

## 手工验证（需重启后端）

1. 打开 设置 → 账户 → 存储空间，应先用缓存瞬间渲染，按钮显示「统计中…」并在约 1–2 秒后恢复。
2. 刷新页面后再次进入，应直接显示上次结果并后台重扫。
3. 清理区选「保留最近 30 天」，确认弹窗中的份数与大小应与列表一致。
4. 清空日志后确认 `<LOGS_DIR>` 只剩 0 字节的 `*.log` 文件，且后端未中断。

## 待实机验收

- Windows 数据根（`%USERPROFILE%\.astrion\astrion-desktop`）下的路径与 `st_blocks` 回退（Windows 无该字段，自动用 `st_size`）。
- 三主题配色（`--storage-1..8`）与宽列布局的视觉效果。
