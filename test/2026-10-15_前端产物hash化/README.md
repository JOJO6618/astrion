# 前端产物哈希化 + 服务端注入（2026-10-15）

## 修的是什么

线上反复出现（刷新缓存后几小时又复发）：

```
main.js:2 Uncaught SyntaxError: The requested module './theme.js' does not provide an export named 'e'
```

**根因**：产物文件名固定（`main.js` / `theme.js`），URL 不体现版本。入口 chunk 与它
import 的 chunk 是**两个独立缓存对象**，各自独立过期、独立回源；只要它们的缓存条目
来自不同批次，就会出现「旧 main.js + 新 theme.js」这种混搭，浏览器直接抛模块导出错误。
刷新缓存只是把当下所有条目对齐一次，**下次部署或任一文件被缓存淘汰就会再次错位**——
所以「刷一次就永久好」在固定文件名下不成立。

**根治**：产物名带内容哈希（URL 即版本），HTML 里的固定名引用在响应时按 manifest 注入。

## 改动清单

| 文件 | 改动 |
|---|---|
| `vite.config.ts` | 输出 `assets/[name]-[hash:8].(js\|css\|ext)`，哈希固定 8 位 |
| `scripts/generate_entry_styles.mjs` | 装配样式由 `static/style.css` 改为 `static/dist/style-<token>.css`（内容寻址），并写出 `static/dist/.vite/entry-styles.json` 供服务端读取 |
| `server/entry_assets.py` | 新增：按 manifest 注入产物路径；HTML 响应 `Cache-Control: no-store` |
| `server/auth.py`、`admin.py`、`project_git.py`、`multi_agent.py`、`workflow_page.py`、`app_legacy.py` | HTML 出口由 `send_static_file` 改为 `serve_entry_html` |
| `static/style.css` | 从 git 移除（改由构建生成到 dist，源码树不再被构建写回） |
| `desktop-electron/src/quick/entry-styles.js` | quick 窗口补 script 的 manifest 注入（原先只替换 CSS） |
| `desktop/src-tauri/src/quick/assets.rs` | 同上（Windows 壳） |

未改动：HTML 源码（`static/*.html` 仍写固定名，由服务端替换）、Android WebView（走远程域名，自动受益）。

## 部署顺序（重要）

新代码对「旧 dist（未哈希产物）」**完全兼容**（引用的文件真实存在就原样保留），
所以下面两种顺序都不会 404；推荐**代码先行**：

```bash
# 推荐：代码先、dist 后
1) git push + 服务器 refresh_agents.sh（git pull + 重启）
   → 此时 dist 还是旧的固定名产物，新代码读旧 manifest，注入结果 = 原样，行为不变
2) 本地 npm run build + 上传 static/dist/
   → 新 manifest 就位后，注入立即切到哈希产物
```

**不要**在服务器上 build（2G 内存会 OOM，见 `cloud_server_ssh_and_directories` 记忆）。

### dist 清理（必须）

哈希化后每次构建都会产生**新文件名**，`static/dist/` 会持续累积（本地同理）。
服务器磁盘长期紧张，部署时应清空后再解压，而不是覆盖叠加：

```bash
ssh root@59.110.19.30 "cd /opt/agent/agents/static && rm -rf dist && tar xzf /tmp/static_dist.tar.gz"
```

（`refresh_agents.sh` 不做 dist 清理，这是部署侧责任。）

## 首次割接后必须做一次

> 下文用 `<域名>` / `<ESA 站点 ID>` 指代环境具体值（不写进仓库，避免公开渠道带出生产标识）；
> 实际取值见项目记忆 `esa_cdn_setup` 与 `frontend_deploy_cdn_cache_stale`。

1. 部署完成后访问应用域名，确认响应 HTML 里的引用带哈希：
   ```bash
   curl -s https://<域名>/new | grep -o '/static/dist/assets/main-[A-Za-z0-9_-]*\.js'
   ```
2. **ESA 控制台刷一次目录缓存**（改缓存头/文件名不会让已有旧条目失效）：
   站点 `<ESA 站点 ID>` →「缓存 > 刷新缓存」→ 目录 `https://<域名>/static/dist/`
3. 老用户浏览器里压着的旧文件：哈希化后新 HTML 引用的是**新 URL**，不会再被旧缓存命中；
   无需再要求用户强刷。

## 回归

```bash
/opt/homebrew/bin/python3.11 -B test/2026-10-15_前端产物hash化/test_entry_assets.py
```

需先 `npm run build`（测试读取真实构建产物与 manifest）。

Rust（Tauri/Windows 壳）改动**未在本机编译验证**，需在 Windows 构建机 `cargo check` 或发布时验证。

## 视觉/端到端验收（留给用户）

- 浏览器：`/new`、`/login`、`/register`、`/settings`、`/workflows`、`/git`、`/admin/*`
- 桌面壳：主窗口、顶部标签条（chrome）、快捷窗口（quick）
- 重点看：页面能正常加载、无 404、样式完整
