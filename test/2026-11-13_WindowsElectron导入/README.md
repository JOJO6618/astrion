# Windows Electron 手动导入回归（2026-11-13）

用户授权补齐Windows Electron的Chrome数据库读取、密码CSV及主动授权导出的Cookie JSON。测试禁止使用真实Chrome资料或真实账号；只创建独立合成资料和密钥。

## 运行

Windows原生环境，在工作区根目录：

```cmd
node test\2026-11-13_WindowsElectron导入\run.mjs
```

脚本用内嵌Python生成独立 `output/windows-electron/import-<timestamp>/localapp/Google/Chrome/User Data`，真实DPAPI包装合成AES密钥，并生成旧DPAPI、Windows v10 AES-GCM、v20不支持记录、分区Cookie及历史。worker的LOCALAPPDATA显式指向这个合成根；Electron userData/sessionData/浏览器元数据也全部隔离。

真实Electron加载生产preload、NativeBrowser、凭据服务与导入模块，经IPC执行preview/preview-file/import；随后启动第二个Electron进程验证DPAPI密码与持久Cookie恢复。

构建新Windows包后，再运行：

```cmd
node test\2026-11-13_WindowsElectron导入\run.mjs --packaged
```

此模式使用新包的 `resources/runtime/python/astrion-backend.exe` 和 `resources/chrome-import/windows-worker.py`，其余生产JS由原生Electron加载；另由 `verify_package.mjs` 核对包内JS和worker/前端与源码一致。此项不能称为真实用户资料验收或生产身份安装验证。

## 检查范围

当前35项：主窗口与独立窗口的能力和真实IPC预览/提交、DPAPI/AES-GCM读取、v20明确计数、历史排除、CSV BOM/引号/换行、无秘密的预览、显式冲突选择、vault变更拒绝、Windows safeStorage加密落盘、JSON格式/过期/无效行、Cookie host-only/domain、session/persistent、Secure/HttpOnly/SameSite、两种partition祖先状态、明确覆盖与跳过、私有worker取消/操作锁清理、过期lease拒绝、源文件哈希不变、macOS合成AES-CBC路径、独立进程重启保留。

结果位于每轮 `import-<timestamp>/results.json`，便捷指针为 `output/windows-electron/import-latest.json`。日志只记录测试动作结果，不输出密码/Cookie值。

既有解析黄金回归：

```cmd
python -B -m unittest discover -s test\2026-11-09_浏览器授权迁移 -p test_exports.py
python -B -m unittest discover -s test\2026-11-09_WindowsBrowser -p test_chrome_import.py
```

分别9项与4项，通过后证明既有边界及旧壳默认行为保留。新Electron显式声明preservePartitionCookies，旧壳未声明时继续原行为。

用户实际验收：使用Chrome主动导出的密码CSV和扩展导出的Cookie JSON，确认预览、选择冲突与导入结果；不同网站可能还依赖localStorage、设备绑定或服务端状态，Cookie写入成功不保证登录保留。导出文件包含敏感资料，由用户保管并在迁移后删除，不上传给模型。
