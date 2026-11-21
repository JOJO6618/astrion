# 预览面板（完整）

> 本文由 `AGENTS.md` §16 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 16) 预览面板（Preview Panel，2026-09-27 新增）

> 类 Claude Code Browser 面板的 MVP 实现：智能体创建的 HTML 文件、启动的 dev server、输出的 localhost 链接，自动进入右侧快捷 dock 的「预览」窗口，点击在预览面板内嵌 iframe 中打开。

### 16.1 检测与存储

- **检测模块唯一权威**：`modules/preview_targets.py`。**两类捕获源**（2026-09-29 简化，原为三类）：
  1. `write_file`/`edit_file` 的 `.html`/`.htm` 文件（挂钩 `tools_execution._record_edited_file`）
  2. 模型流式输出的 localhost/127.0.0.1/[::1] 链接（`chat_flow_stream_loop` 滚动缓冲 120 字符增量扫描，防 URL 跨 chunk 断裂；每次回复内去重）
  - **run_command/terminal_input/terminal_snapshot 输出检测已删除**（2026-09-29 用户拍板）：实测命令输出是脏数据主来源（浏览器控制台 `@ url:行号` 后缀、代码模板 `f"http://127.0.0.1:{port}/"` 假地址），且放行条件「输出非空」几乎恒真形同虚设；服务器地址改由 prompt（`prompts/preview_panel.txt`）要求模型在回复中主动写出完整 URL
- **URL 识别规则**（2026-09-29 重写，修复 Markdown/中文粘连）：路径为 ASCII 白名单字符集（`* ' " ( ) [ ] :` 与非 ASCII 一概不粘）；裸主机（无端口无路径）不记录；favicon.ico 等静态资源噪音不记录；尾部 `/` 归一后去重。`_normalize` 对存量条目按新规则自愈清洗（脏 URL 截回干净形态重新去重、无效条目剔除、label 统一重算），所有读取出口（preview.py / conversation.py / conversation_bootstrap.py）均走 `_normalize`，老对话读出自愈无需迁移。测试：`test/历史测试_日期不明/test_preview_targets.py`。
- **聊天消息裸 URL 渲染截断**（2026-09-29）：remark-gfm autolink-literal 按 GFM 规范只修剪尾随 ASCII 标点，中文/全角符号/Markdown 残余会被整段粘进链接；`useMarkdownRenderer.ts` 的 `cleanAutolinkLiteralsPlugin`（rehype 层）把 autolink 生成的 <a> 在第一个「URL 不可能字符」处截断（Markdown 定界符 + 全角/CJK 标点 + 弯引号），显示文字不变只修链接范围；刻意保留 CJK 表意文字（维基百科类合法 URL）。
- **0.0.0.0 归一化**：`0.0.0.0` 是监听地址不是访问地址（Chrome 已禁访问），扫描时统一映射为 127.0.0.1 再记录。
- **存储**：对话 `metadata.preview_targets`（按对话隔离，跟随压缩/重启）；服务器按**完整 URL** 去重——同 origin 不同 path 保留多条（用户拍板：根地址与具体页面是独立预览目标），同 URL 重复命中不写不广播（防流式刷屏）；label 格式 `:端口[/路径] · 框架`（同端口多条靠路径区分）。
- **广播**：与 edited_files 同一链路——`context_manager._web_terminal_callback("preview_targets_updated", {preview_targets})` 进任务事件流；bootstrap 回填走对话 messages 接口的 `data.preview_targets`（文件目标过滤已删除文件）。

### 16.2 预览渲染与安全隔离（2026-09-27 换源改造）

**安全模型**：预览内容（智能体写的 HTML / 代理的 dev server）若以主应用同源端点提供，iframe `sandbox="allow-scripts allow-same-origin"` 对同源内容等于无沙箱（预览页 JS 可读父 DOM/localStorage、带会话 cookie 调应用 API）。调研结论（`.astrion/sub_agent_results/agent_2_4/preview_isolation_research.md`）：业界全部用「换源」隔离，无一靠 sandbox flag/CSP。

**本机访问（localhost/127.0.0.1/[::1]）= 独立预览服务器**（`server/preview_server.py`）：主应用懒启动只绑 127.0.0.1 的随机高位端口迷你 Flask（werkzeug make_server，daemon 线程）；路径带每对话 token（优先用 `metadata.preview_token`；写盘被主任务整文件回写冲掉时由 `_derived_preview_token()` 按对话 ID 进程内派生兜底，重启后前端重新拉取即换新）；两道关卡=Host 头白名单（防 DNS rebinding）+ token 注册表反查（注册表存会话身份四元组而非 terminal 引用——对话级 terminal 会被 24h 回收，身份可随时经 `get_user_resources(update_session=False)` 重建工作区级 terminal）。cookie 侧：会话 cookie 已是 SameSite=Strict+HttpOnly，且预览源与主应用不同 host=不同站，跨站请求不带 cookie；同 host 访问场景由 CSRF token+无 CORS 头兜底。下发链路：`build_preview_runtime()`（preview.py）在 targets/bootstrap/messages 三处响应里带 `preview_base`+`preview_token`，前端 store（previewBase/previewToken）经 `previewUrlFor()` 推导 iframe 地址；任务事件流只推数组，base/token 由最近一次拉取保持。

**预览服务器解析工作区必须用显式身份**。独立 Flask app 没有主应用 session，`_resolve_context()` 必须传 `RuntimeIdentity(host_mode=True, host_workspace_id=<注册时工作区>, role=<注册时角色>)`。Host 资源解析要求此身份，且结果的 `workspace.workspace_id` 必须与注册值一致。

- 本机浏览器预览服务器目标时直连 localhost URL，文件目标走独立预览服务器的 `/<token>/file/<path>`。
- 通过反向代理或域名访问时，`build_preview_runtime()` 对非回环 Host 返回空值，浏览器回退到 `/api/preview/file` 或 `/api/preview/proxy`。同源回退沿用现有 CSRF 与 CORS 约束。
- 代理经 HTTP 客户端连接本机已登记的端口，限制为当前对话的预览目标。代理重写 HTML 资源路径与重定向。WebSocket 和 JavaScript 动态 import／fetch 的绝对路径仍受现有代理限制。

### 16.3 前端

- store：`stores/preview.ts`（targets/activeKey/自动展开抑制）；dock 第六窗 `PreviewWindow.vue`；展开面板 `PreviewPanel.vue`（与 FilePreviewPanel 同位、共享宽度存储键）。
- 自动展开：personalization `preview_auto_open`（默认 false）；规则=最新目标优先、同目标更新不重弹、用户手动关闭后本次任务内不再自动展开（`sendMessage` 时重置抑制）；对话区放不下（<300px）时不自动展开。
- **窄布局联动（2026-11-12）**：预览窗口与快捷面板绑定，快捷面板折叠（手动或窄布局自动）时一并关闭（联动关闭不抑制后续自动展开）；对话区<300px 时自动折叠，手动在空间不足时打开则强制保留至关闭。见 `test/2026-11-12_窄布局自动折叠/README.md`。
- 事件处理：`taskPolling/lifecycle.ts` 的 `preview_targets_updated` 分支；切换对话清空走 `watchers.ts` + `bootstrap.ts` 回填。
- **桌面端预览窗口停用、链接改走内置浏览器（2026-11-17，取代原 localhost 拦截）**：桌面壳内不渲染 dock 的 `PreviewWindow.vue` 与展开面板 `PreviewPanel.vue`，`preview_auto_open` 不生效，预览目标不计入快捷面板内容判定；后端照常记录 `metadata.preview_targets`（只记录不展示）。正文内 http(s) 链接（含 localhost）由 App.vue 的 document 级 capture 点击拦截（仅 `__ASTRION_DESKTOP__`）改交 Astrion 内置浏览器（`nativeBrowserStore.open = true` → 等 `NativeBrowserHost` 连接拿 lease → `navigate`），无作用域或连接失败回退 `openExternal` 系统浏览器；同源（后端端口）链接必须放行，否则会断应用内导航。原 `openServerUrl()` + `ephemeralTargets` 链路保留代码，但桌面端已无入口。PreviewPanel 的「外部打开」按钮仍走 `utils/openExternal`（桌面壳 window.open 静默失败）。
  - **文件目标 = 打开真实文件（2026-10-29）**：`openWorkspaceFile()` → `POST /api/system/open-file`（仅 host 模式，`file_manager._validate_path` 校验后由宿主 `open`/`startfile`/`xdg-open` 打开，即 `file://` 真身），不再打开预览服务器代理地址（随机端口 + 对话 token，复制出去无意义）。该接口失败时才退回 `openExternal(iframeSrc)`，兼顾远端域名访问等宿主通道缺失场景。
  - **拖拽调宽（2026-10-29）**：手柄贴面板左缘，向右拖会立刻压到 iframe 上；iframe 是独立文档，会吞掉 `mousemove`/`pointermove`，父文档收不到事件 → 表现为「向左拉大容易、向右拉小完全拉不动」。修法 = `pointerdown` + `setPointerCapture` + 拖拽期间给 iframe 加 `pointer-events: none`（组件内 `is-resizing` class）。

### 16.4 Prompt

- 主 Agent 的固定预览规则集中 `prompts/main_agent/static.txt`，明确仅在初始化功能状态表明可用时适用；`main_agent/dynamic/features.txt` 记录初始可用性，整体保存进 `frozen_system_prompt`。旧 `frozen_preview_panel_prompt` 只用于原序迁移。核心要求仍为：启动服务器后写完整地址、不要引导用户手动开浏览器。

---

注：本节按「现有架构 + 多智能体分支」方案描述；如与代码冲突，以代码为准并同步修订本节。

---
