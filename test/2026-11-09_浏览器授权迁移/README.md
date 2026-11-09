# Windows 授权迁移与双平台 file 回归 · 2026-11-09

用户真实 Chrome 数据在旧版测试中失败：密码 107 / Cookie 220 条为不支持的 v20；另外 14 条分区 Cookie 被跳过、100 条历史记录未细分原因。用户明确批准密码 CSV + Chrome 扩展主动授权导出 Cookie，并追加要求 macOS/Windows 都支持手动 file 网页。本目录只使用合成数据，不读取真实资料。

## 已执行结果

- `test_exports.py`：9 项通过，含 UTF-8 BOM、CSV 引号/逗号/换行/中文、坏表头与截断输入、大小和条数限制、Cookie 分区/host-only/session/过期及非法记录、历史原因细分。
- `extension_test.mjs`：7 项通过。独立 Chromium 149 实际加载生产扩展脚本，使用 Chrome Cookie API 设置并导出合成普通/分区 Cookie，验证完整分区、hostOnly、HttpOnly、domain 和限定网站权限参数。测试目录副本仅预授权 example.test 合成域；生产扩展仍须用户点击并在 Chrome 确认权限，未自动验收真实权限弹窗。
- `file_policy_test.mjs`：9 项通过。Electron 生产标签代码配原生视图替身，验证本机手动 URL/相对导航/popup、远程 file 导航与重定向拒绝、密码适配器不安装、AI 不读取 file。**没有 Mac 原生运行环境，不等同 Mac 实机通过。**
- 生产 WebView2 探针写入/重启两进程：39 / 43 项通过。新增 CSV 原子入库与特殊字符、取消预览、Cookie 分区键身份/覆盖/原生回读/重启恢复、本地中文与空格路径、相对 JS/CSS/链接、本地与 HTTP 历史前进后退、远程 file 导航/popup 拒绝。直接引用生产模块，没有复制浏览器实现。
- Rust 12 项、AI 工具原生 29 项、旧导入 4 项、UI/桥 22 项、前端定向 ESLint/tsc/stylelint/Vite 均通过。测试包 r2 的实际包内启动与关闭回收 11 项通过。

证据在 `_experiments/windows-browser-20261109/`：`production-latest.json`、`ai-latest.json`、`extension-migration.json`、`file-browser-policy.json`、`migration-tests.log`、各原生/前端日志。r2 交付包附不含凭据的报告，不附测试 Chromium 资料或合成导出文件。

## 重跑（Windows CMD，仓库根目录）

```cmd
python -B -m unittest discover -s test\2026-11-09_浏览器授权迁移 -p test_*.py
node test\2026-11-09_浏览器授权迁移\file_policy_test.mjs
set "PLAYWRIGHT_BROWSERS_PATH=E:\astrion\agent-Specialization\_experiments\windows-browser-20261109\playwright-browsers"
node test\2026-11-09_浏览器授权迁移\extension_test.mjs
python test\2026-11-09_WindowsBrowser\host.py build-production-probe
python test\2026-11-09_WindowsBrowser\host.py run-production-probe
```

测试 Chromium 存在时直接重跑；缺失可使用 `prepare_chromium.py` 下载同一官方 Windows 测试构建。该脚本使用系统默认 TLS 信任链，未关闭验证；最初 Node 下载失败于本机证书链，换到系统信任链后成功。

## 明确限制

- v20 不通过破解/注入/提权解密。用户主动导出文件含登录凭据，不传给模型或日志，导入后由用户删除。
- 不支持远程 file 主机/UNC，不关闭 Chromium sandbox 或 webSecurity。file 页面没有可信桥，不安装密码适配器；AI 仍限 HTTP(S) 网页。本地页面依赖跨源 fetch、ES modules 或服务端路由时，按 Chromium 原有规则可能需要 HTTP 服务。
- Cookie 不包含网站 localStorage/IndexedDB/设备密钥；写入成功不保证所有真实网站登录保持。
- 扩展首次授权、真实网站、Mac 原生、UI 视觉、IME、多 DPI 和系统对话框由用户实机验收。
