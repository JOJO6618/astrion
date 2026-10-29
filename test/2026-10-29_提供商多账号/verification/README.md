# 第二、三轮修复证据（2026-10-29）

本目录只记录本轮针对最终审查缺陷的修复与定向验证。所有测试使用临时目录合成账号/Key，
未读取真实历史对话、真实凭据或真实模型缓存；未发起真实 OAuth / wham 网络请求。

## 修复清单与对应证据

| 编号 | 问题 | 修复位置 | 复现/回归测试 |
|---|---|---|---|
| P1-A | 自定义连接直接把用户 `provider_id` 当内部路由 id；删除后同 ID 重建可让旧 `{pid}/{model}` 串新 Key；`provider_id=openai-codex` 可劫持已删订阅旧 key | `modules/provider_manager.py`：自定义连接内部 id 改为 `custom-<请求id>-<8位随机hex>`（与目录/订阅 namespace 隔离、永不复用）；用户 id 存入 `requested_id`，只作展示/配置基础标识；活跃期同 id 仍返回 `provider_id_exists`；存量记录保留旧键并在迁移时补 `requested_id` | `test_provider_multi.py::test_custom_connection_uses_opaque_non_reusable_internal_id`、`test_custom_provider_id_cannot_hijack_deleted_subscription_keys`、`test_legacy_custom_record_keeps_identity_until_explicit_delete`、`test_custom_provider_single_identity` |
| P1-B | Chat `build_api_history` 无条件回插 `responses_reasoning_items`，同协议切账号仍带旧加密块 | 新增中性判定 `utils/responses_reasoning.py::same_responses_provider`（Agent `core/main_terminal_parts/context/messages.py` 与 Chat 共用）；`modules/chat_mode/context.py` 按存储消息 `metadata.model_key` 与当前运行时/会话模型比较，同连接/同账号才回插 | `test_chat_reasoning_isolation.py`（7 项：同账号保留、跨 Codex 账号剥离、跨提供商连接剥离、同连接保留、未知/空当前模型剥离、无源 model_key 剥离、深拷贝不改权威历史）；`test_codex_routing.py::test_encrypted_reasoning_isolated_across_accounts`（Agent 侧） |
| P2-A | `_fallback` 未按 `_legacy_cache` 门控，非主账号在线失败会读本机 Codex CLI 账号缓存 | `utils/api_client/codex/models.py`：CLI 缓存仅在 `_legacy_cache` 时参与降级 | `test_codex_routing.py::test_non_legacy_fallback_never_reads_cli_account_cache`、`test_legacy_fallback_can_still_use_cli_cache` |
| P2-B | 索引加载时凭证文件暂缺即删登记；恢复后可能扫描成新 id，破坏旧引用 | `utils/api_client/codex/accounts.py`：正常读取不再删索引；`_public_record` 报 `state=credential_missing` / `credential_missing=true` / `connected=false`；只有显式 `logout` 删登记；恢复/重登沿用原 id | `test_codex_accounts.py::test_missing_credential_keeps_registered_identity_and_restores_same_id`、`test_explicit_logout_removes_registration_without_tombstone` |
| P2-C | exchanging 阶段取消后换 token 仍无条件 `upsert_tokens` | `utils/api_client/codex/oauth.py`：浏览器/设备流共享“取消/提交”互斥边界；`cancel` 与落盘同锁，提交临界区内取消等待；提交前状态非 pending 抛内部 `_LoginCancelled`，不落盘；`_fail` 也不覆盖已完成态 | `test_oauth_cancel_boundary.py`（取消先于提交不写；提交中取消等待且最终 completed 无孤儿；设备流同语义） |
| 小问题 1 | logout 遇 OSError 时 API 未结构化报错、登记风险 | `server/codex_auth.py`：捕获 `OSError` 返回 500 `{success:false,error:"logout_failed",account_id,detail}`；存储层先删凭证再删索引，删除失败登记保留 | `test_codex_api.py::test_logout_failure_returns_structured_error_and_keeps_identity` |
| 小问题 2 | `auth=none` 占位值 `not-required` 被当密钥展示掩码 | `modules/provider_manager.py::_mask_key` 对占位值返回 `None` | `test_provider_multi.py::test_local_provider_does_not_expose_placeholder_key_mask` |
| 小问题 3 | 登出不清理对应账号模型缓存 | `accounts.py::_clear_models_cache_locked`：新账号删 `codex_models/<id>.json`；主账号只清 `models/fetched_at/source/etag`，保留全局 proxy/client_version；失败仅返回 `models_cache_cleared=false`，不影响身份/凭证 | `test_codex_accounts.py::test_logout_clears_account_models_cache`、`test_legacy_logout_clears_models_but_keeps_global_config` |
| 小问题 4 | `get_models_manager(None)` 固定 legacy，而 `get_auth_manager(None)` 用主账号，存在错配 | `models.py`：两者都解析注册表主账号；无登记账号时均明确 `no_codex_account`（`get_models_manager` 不再静默选 legacy）；显式传凭证的旧构造方式仍视为 legacy | `test_codex_routing.py::test_models_manager_default_account_matches_auth_manager` |

## 第三轮窄修（credential_missing 管理入口）

| 问题 | 修复 | 回归测试 |
|---|---|---|
| `catalog()` 过滤 `connected`，导致凭证暂缺的已登记账号在提供商页不可见；`has_connections` 也错误 | `modules/provider_manager.py`：所有已登记 Codex 账号都输出为 `entry_kind="connection"` 行；保留真实 `connected=false`、`state="credential_missing"`、`credential_missing=true`；模型统计归零且不调用模型管理器；`has_connections`/`connection_count` 按全部登记计算，目录按钮仍可添加 | `test_provider_multi.py::test_catalog_includes_credential_missing_codex_registration`、`test_catalog_with_zero_codex_accounts_still_offers_add` |
| 前端按 `connected` 布尔分类/展示；缺凭证行会误显示“已连接”或旧模型数 | `static/src/stores/providers.ts`：`connectedEntries`/`catalogEntries` 按 `entry_kind`（兼容旧 `connection`）分类；`ProvidersTab.vue`：缺凭证显示“凭证缺失”、禁用刷新、移除按钮复用 logout，确认文案为“移除账号”；中英文案已补 | `tsc`、`eslint`、`stylelint`、`vite build`；无前端单测框架 |
| 缺凭证登记无法移除 | `CodexAccountStore.logout` 已容忍 `FileNotFoundError`；补测试确认删除登记、不留墓碑 | `test_codex_accounts.py::test_logout_of_missing_credential_removes_registration`、`test_codex_api.py::test_logout_missing_credential_registration_via_api` |

## 验证命令与结果

```bash
.venv/bin/python -m unittest discover -s "test/2026-10-29_提供商多账号" -p "test_*.py" -v
# Ran 53 tests ... OK        -> verification/unittest_targeted.log

.venv/bin/python -m unittest discover -s "test/2026-10-25_Chat模式" -p "test_*.py"
# Ran 51 tests ... OK        -> verification/unittest_chat_regression.log

.venv/bin/python -m unittest "test/历史测试_日期不明/test_codex_models_discovery.py"
# Ran 2 tests ... OK（确认构造器默认行为未回归）
```

```bash
npx tsc --noEmit                 # exit 0 -> verification/tsc.log
npx eslint <本轮改动的 4 个前端文件> --max-warnings=0  # exit 0 -> verification/eslint_targeted.log
npx stylelint "static/src/**/*.{scss,vue}"             # exit 0 -> verification/stylelint.log
npx vite build                   # exit 0，built in ~10s -> verification/vite_build.log
```

全仓 `eslint` 仍会因**本轮未改动的存量文件**报 203 条 prettier warning（0 error），日志见
`verification/eslint.log`；本轮 4 个改动文件单独 lint 为 0 warning。

另：改动 Python 文件均通过 `ast.parse`；`modules.provider_manager / modules.chat_mode.context /
utils.responses_reasoning / utils.api_client.codex.{accounts,models,oauth} / server.codex_auth`
导入成功。

## 未验证边界

- 未做真实 OAuth 授权、真实 token 交换、真实用量/额度查询；相关验证均为可注入的合成网络/存储。
- 未启动完整 Flask 应用进程（本机 `.venv` 缺 `yaml` 等全量依赖）；API 行为通过蓝图级 Flask test client 验证。
- 未做真实历史对话/真实凭据回放；所有断言使用临时目录合成数据。
