# 网页提取与搜索多服务商（完整）

> 本文由 `AGENTS.md` §14 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 14) 网页提取（白名单直提 + 可选提取商）

> 实现唯一权威：`modules/webpage_extractor.py`。

### 14.1 机制一句话

`extract_webpage` / `save_webpage` 的提取链为**两层**：白名单直提（本机、免费、**唯一自动回退层**）→ **用户在设置页选定的提取商**。可选提取商四家（2026-09-26 从「直提→Jina→Tavily 三级降级链」改为可选模式）：**Jina Reader**（默认，无 key 匿名限速可用）/ **Tavily** / **Exa**（`POST /contents`）/ **Parallel**（`POST /v1/extract`）。选定商失败即报错，**不再向其他家回退**（避免在用户不知情时消耗多家付费额度）。**个性化存储已拆分（2026-09-27）**：`personalization.json` 只存 UI/行为偏好；搜索/提取服务配置（直提开关 `webpage_direct_extract_enabled`、追加域名 `webpage_direct_extract_domains`、提取商 `webpage_extract_provider`、搜索商 `search_provider`、`searxng_base_url`）存 `service_config.json`；所有 API 密钥（tavily/bocha/exa/parallel/jina）存 `secrets.json`（0600 权限、原子写）。三文件由 `modules/personalization_manager.py` 统一读写（load/save/sanitize 对外签名与返回结构不变，调用方无感知），旧格式首次加载自动迁移并抹除；文件落在 personalization.json 符号链接解析后的用户级共享目录，跨工作区共享语义不变。提取商解析入口 `resolve_extract_provider_config()`。

### 14.2 硬约束（改代码必须知道）

1. **统一入口是 `extract_single_url()`**：白名单判定 → `_direct_extract()` → 按 `extract_config.provider` 分发到 `jina_extract` / `tavily_extract` / `exa_extract` / `parallel_extract`。两个工具调用点都走它，不要绕过单写某一层路径。
2. **直提层内部降级链**：GitHub blob 页 → jsDelivr CDN（`cdn.jsdelivr.net/gh/{owner}/{repo}@{branch}/{path}`，国内可用、无限速）→ GitHub API contents（备用，匿名限 60 次/时）→ trafilatura 通用提取 → 失败转交选定提取商。**不要用 raw.githubusercontent.com**（国内被 DNS 污染，实测不可达）。
3. **trafilatura 是可选依赖**：`import` 失败时通用直提静默失效（GitHub 直链不依赖它仍可用），白名单不会报错；已进 `requirements.txt`，正式部署应装上。
4. **blob URL 的 branch 按单段解析**（`[^/?#]+`）；含斜杠的分支名解析失败会自然降级 trafilatura → 选定提取商，属预期行为。
5. **域名匹配 = 精确或子域名后缀**：`github.com` 命中 `gist.github.com`；用户输入经 `_normalize_domain` 清洗（容忍粘贴完整 URL）。
6. **提取商凭证复用搜索同源解析**：tavily/exa/parallel 的 key 走 `resolve_search_credential()`（UI 配置优先、环境变量兜底），零新增配置项；jina 的 key 单独解析（`jina_api_key` → `AGENT_JINA_API_KEY`）。选定 tavily/exa/parallel 而无 key 时返回明确错误（`webpage.provider_key_missing`），不静默换家。
7. **Exa/Parallel 提取契约**：Exa `POST https://api.exa.ai/contents`（body `{"urls":[url],"text":{"maxCharacters":N}}`，取 `results[0].text`）；Parallel `POST https://api.parallel.ai/v1/extract`（`advanced_settings.full_content` 需显式开启，响应优先 `full_content`、否则拼接 `excerpts`，HTTP 200 可能部分成功——目标 URL 进 `errors` 视为失败）。两家均为付费端点（约 $1/1k），x-api-key 认证。
8. **新增 personalization 键必须双注册**（`DEFAULT_PERSONALIZATION_CONFIG` + `sanitize_personalization_payload`），否则保存后被静默丢弃（见 .astrion/memory/personalization_config_whitelist.md）。
9. **返回格式**沿用 `🌐 网页内容 (N 字符)` 骨架，附 `[提取方式: ...]` 标注（i18n key `webpage.method_label` / `webpage.method_*`），模型侧无感知。
10. **read-before-edit 已读白名单**含 `recall_project_memory`（recall 返回记忆文件全文，视为已读）；`search_project_memory` 只回片段，刻意不标记。

### 14.3 搜索多服务商（2026-09-26 新增）

**机制一句话**：`web_search` 从仅 Tavily 扩展为可插拔多服务商——`modules/search_engine.py` 只做参数校验/归一化、凭证解析、结果包装（`_validate_params` / `_wrap_results`），各服务商的请求构造与响应解析在 **`modules/search_providers/` 适配器包**（base/tavily/bocha/exa/parallel/searxng）。

**硬约束（改代码必须知道）：**

1. **适配器契约**：入参为归一化 params（`max_results/topic/time_range/days/start_date/end_date/country/include_domains`），出参 `{"ok", "results": [{title,url,content,published_date,score}], "answer", "dropped"}`；`dropped`=该服务商不支持而被忽略的参数（按基类的 `supports_*` 能力声明自动判定），透传到 `filters.provider_dropped` 展示，禁止静默吞掉。
2. **凭证解析**：`resolve_search_provider_name()` / `resolve_search_credential()`（`search_providers/__init__.py`）——personalization UI 配置优先、环境变量兜底（`config/search.py` 的 `AGENT_*_API_KEY` / `AGENT_SEARXNG_BASE_URL`）；searxng 的 credential 是实例地址而非密钥（`auth_kind="base_url"`）。
3. **前端契约不变**：`filters` 与 `results[]` 结构对所有服务商保持一致（前端 `renderWebSearch` 直接消费）；仅新增 `filters.provider`（服务商 id）与 `provider_dropped`，前端经 `toolResults.searchProviders.*` 显示名称。
4. **新增服务商 checklist** 见 `modules/search_providers/__init__.py` 文件头注释（适配器 → config env → personalization 双注册 + stores 三处 → SearchTab + locales 双语 → i18n 文案）。
5. **密钥权限线**：设置页「网络搜索」分区的搜索引擎选择、网页提取方式选择与各家密钥（含 Jina 密钥）均仅 host/管理员可见（对齐提供商分区）。
6. **Tavily key 同时是提取层凭证**：选定提取商为 tavily 时经 `resolve_search_credential("tavily")` 取 key（与搜索同源）；`resolve_tavily_api_key`（`modules/search_engine.py`）为其环境变量兜底实现，不要删。

---
