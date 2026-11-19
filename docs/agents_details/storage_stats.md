# 存储空间统计与清理（storage stats）

> 来源：`AGENTS.md` §28（2026-11-19 下沉，原文保留）。仅在 host 模式开放，涉及数据根遍历与删除，改动前逐条核对。

## 入口与前端

- 设置页「账户 → 存储空间」（`/settings/storage`，与用量统计同宽列）。
- 前端：`settings/tabs/StorageTab.vue` + `tabs/storage/`；文案命名空间 `storageStats`；分类色为新增 `--storage-1..8`（三主题＋首屏回退已补齐）。
- 分区登记三处同步：`settingsNav.ts` 的 NAV_GROUPS／SECTION_TITLE_KEYS、`SettingsShell` 的 SECTION_COMPONENTS、`stores/settings.ts` 的 `SettingsSection`。

## 统计范围与口径

- **范围 = 数据根内**：`modules/storage_stats/scanner.py` 一次遍历 `DATA_DIR` + `LOGS_DIR`，按 `catalog.py` 的最长前缀规则归桶。
- 8 类：备份与快照 / 日志 / 浏览器MCP / Agent 对话 / 对话媒体 / 子智能体 / Chat 对话 / 其他；未命中归「其他」，**保证总和等于实际占用**。
- **不遍历工作区项目目录里的 `.astrion/`**；沙箱不计（host 无持久目录，Windows WSL vhdx 属系统层）。
- 符号链接跳过；字节数优先 `st_blocks`（与 `du` 同口径），Windows 回退 `st_size`。

## 缓存与刷新

- 缓存文件 `<DATA_DIR>/storage_stats.json`（version 守卫）。
- `GET /api/storage-stats` 只读缓存（毫秒级）；无缓存时触发后台扫描。
- `POST /api/storage-stats/refresh` 启动单飞后台扫描（进程内锁，不排队）。
- 前端进入页面先渲染缓存，再触发重扫，并以 `scanned_at` 变化为轮询终点（30s 超时收口）。

## 可用范围与鉴权

- **仅 host 模式开放**：统计口径是整个数据根，多用户部署会读到他人数据、清理会删他人文件；非 host 统一返回 `available=false`（前端显示不可用提示）。
- 接口沿用 `@api_login_required`；前端 POST 依赖 `static/security.js` 的全局 CSRF 注入。

## 清理：只覆盖两类

- `POST /api/storage-stats/clean/logs`：活动 `*.log` **截断为 0 字节**、归档与非日志文件删除、递归子目录。用截断而非 unlink——活动句柄会让空间无法及时释放。
- `POST /api/storage-stats/clean/backups`：`keep_days` 1–3650，按 `save/<conv_*>/meta.json.updated_at` 判定，缺失回退目录 mtime，整份删除。只处理 `save` 根的直接 `conv_*` 子目录并复校验父目录身份，符号链接与非 `conv_` 目录跳过。
- 其余类别**只统计不清理**。

## 前端确认与反馈

- 清理前走 `uiStore.requestConfirm`（danger 变体），显示份数与预计释放量。
- 成功后 `pushToast` 并强制重载（先收口旧轮询，避免被 busy 拦截）。
- 「保留最近」下拉为 7/30/90/自定义（自定义为行内数字输入，Enter 严格校验、Esc／失焦静默退出）；预计删除量由扫描缓存里的 `backups` 明细在前端实时计算。

## 回归与验收边界

- 回归目录 `test/2026-11-15_存储空间统计/`（11 个用例，全部临时目录）。
- Windows 数据根、三主题视觉与真实清理后的后端连续性仍需实机验收。
