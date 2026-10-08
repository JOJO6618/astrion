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

**本机访问（localhost/127.0.0.1/[::1]）= 独立预览服务器**（`server/preview_server.py`）：主应用懒启动只绑 127.0.0.1 的随机高位端口迷你 Flask（werkzeug make_server，daemon 线程）；路径带每对话随机 token（`metadata.preview_token` 持久化，重启不变）；两道关卡=Host 头白名单（防 DNS rebinding）+ token 注册表反查（注册表存会话身份三元组而非 terminal 引用——对话级 terminal 会被 24h 回收，身份可随时经 `get_user_resources(update_session=False)` 重建工作区级 terminal，容器句柄工作区级共享）。cookie 侧：会话 cookie 已是 SameSite=Strict+HttpOnly，且预览源与主应用不同 host=不同站，跨站请求不带 cookie；同 host 访问场景由 CSRF token+无 CORS 头兜底。下发链路：`build_preview_runtime()`（preview.py）在 targets/bootstrap/messages 三处响应里带 `preview_base`+`preview_token`，前端 store（previewBase/previewToken）经 `previewUrlFor()` 推导 iframe 地址；任务事件流只推数组，base/token 由最近一次拉取保持。

**docker/web 模式 = 预览整体禁用（2026-09-27 用户拍板）**：红队演练确认 docker 是唯一把端口暴露到公网的部署形态，远端回退的同源端点=会话级注入缺口（漏洞详情与重启前提见项目记忆 `preview_security_redteam`）。唯一判定=`modules/preview_targets.py::is_preview_enabled()`（=IS_HOST_MODE），四处门控：①记录入口（`record_file_target`/`record_url_targets` 直接返回）；②端点（targets 返回空/remove·file·proxy 403，`build_preview_runtime` 回 empty）；③序列化（bootstrap/messages 端点 `preview_targets` 强制置空，老对话残留数据不下发）；④prompt（冻结段为空）。前端无目标自然隐藏，docker 老对话数据残留无害。

**远端域名访问（host 模式经反向代理/域名访问的边角场景）= 回退旧同源端点**（/api/preview/file|proxy 保留，仅 host 模式可用）：浏览器到不了 127.0.0.1 预览服务器，`build_preview_runtime` 对非回环 Host 返回 None，前端回退旧端点——残余风险已知（有 CSRF+无 CORS 兜底），彻底解决需泛子域名（本地无 DNS/证书不可用）。

- **host 模式**：服务器目标 iframe **直连 localhost URL**（保真 + HMR 可用，与主应用天然跨源）；文件目标走预览服务器 `/<token>/file/<path>`。
- **docker 代理架构（代码保留，docker 禁用后实际不可达）**：服务器目标走预览服务器 `/<token>/proxy/<port>/…`。**关键架构**：docker 模式下智能体的命令经 `docker exec` 跑在独立的工具箱容器里（独立网络命名空间），后端进程的 127.0.0.1 根本到不了容器内的服务器（2026-09-27 实测 502 根因）——代理按 `terminal.container_session.mode == "docker"` 分流，docker 时经 `_docker_exec_fetch`（`docker exec <容器> curl -sS -i`，容器句柄自带 container_name/sandbox_bin）在容器内取数，host 时 httpx 直连。curl 路径不跟随重定向，3xx 透传且 Location 经 `_rewrite_location` 改道代理前缀；两条链路统一剥离 X-Frame-Options/CSP、HTML 重写绝对路径 src/href/action + 注入 base（base/前缀都按请求路径推导，兼容两种挂载点）、请求头剔除 accept-encoding。**SSRF 防护：仅允许代理当前对话已登记的端口**。
- **已知限制（MVP）**：代理不转发 WebSocket（HMR 在代理预览中失效，需手动刷新）；页面内 JS 动态 import/fetch 的绝对路径不重写；docker 路径每请求一次 docker exec（约 50-150ms 开销），资源多的页面加载偏慢。

### 16.3 前端

- store：`stores/preview.ts`（targets/activeKey/自动展开抑制）；dock 第六窗 `PreviewWindow.vue`；展开面板 `PreviewPanel.vue`（与 FilePreviewPanel 同位、共享宽度存储键）。
- 自动展开：personalization `preview_auto_open`（默认 false）；规则=最新目标优先、同目标更新不重弹、用户手动关闭后本次任务内不再自动展开（`sendMessage` 时重置抑制）。
- 事件处理：`taskPolling/lifecycle.ts` 的 `preview_targets_updated` 分支；切换对话清空走 `watchers.ts` + `bootstrap.ts` 回填。
- **桌面端 localhost 链接拦截（2026-09-27）**：桌面壳的 `on_navigation` 会把一切非后端同源导航拦去系统浏览器，聊天里的 localhost 链接也不例外——与「窗口内预览」语义冲突。App.vue 注册 document 级 capture 点击拦截（仅 `__ASTRION_DESKTOP__`）：回环 http(s) 链接 `preventDefault` 后走 `preview store 的 openServerUrl()` 开面板；已登记目标直接激活，未登记的进 `ephemeralTargets`（不写后端、不进 dock 列表、随对话切换清除，`setTargets` 的关面板检查对它豁免）。同源（后端端口）链接必须放行，否则会断应用内导航。PreviewPanel 的「外部打开」按钮走 `utils/openExternal`（桌面壳 window.open 静默失败）。

### 16.4 Prompt

- `prompts/preview_panel.txt`：冻结段注入（`messages.py` `frozen_preview_panel_prompt`，与 tool_loading 目录同模式）。核心要求：启动服务器后在回复中写完整地址、不要引导用户手动开浏览器、自我验证仍走 Playwright。

---

注：本节按「现有架构 + 多智能体分支」方案描述；如与代码冲突，以代码为准并同步修订本节。

---
