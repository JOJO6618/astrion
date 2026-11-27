# Windows Electron 手动导入回归

测试使用独立的合成 Chrome 浏览记录、密码 CSV 和 Cookie JSON。

## 运行

Windows原生环境，在工作区根目录：

```cmd
npm --prefix desktop-electron run build:quick-native
node test\2026-11-13_WindowsElectron导入\run.mjs
```

脚本用 Node SQLite 在独立输出目录生成合成的 Chrome 资料列表和浏览记录。辅助程序的 `LOCALAPPDATA` 指向该合成目录，Electron userData、sessionData 与浏览器元数据同样隔离。

真实Electron加载生产preload、NativeBrowser、凭据服务与导入模块，经IPC执行preview/preview-file/import；随后启动第二个Electron进程验证DPAPI密码与持久Cookie恢复。

构建新Windows包后，再运行：

```cmd
node test\2026-11-13_WindowsElectron导入\run.mjs --packaged
```

此模式调用包内的 `resources/astrion-windows-helper.exe`，生产 JS 由测试 Electron 加载。用 `test/2026-11-13_WindowsElectron/verify_package.mjs` 核对包中的辅助程序、前端、JS 和扩展。

## 检查范围

覆盖主窗口和独立窗口的导入能力、旧导入请求拒绝、浏览记录排除原因、CSV BOM/引号/换行、冲突处理、vault 变更、目标密码库加密、JSON 格式/过期/分区、Cookie 写入回读、取消与过期 lease、来源哈希、重启保留及 macOS 读取适配器。

结果位于每轮 `import-<timestamp>/results.json`，便捷指针为 `output/windows-electron/import-latest.json`。日志只记录测试动作结果，不输出密码/Cookie值。

Rust 解析和 SQLite 历史读取的离线回归支持非 Windows 环境。

```sh
cargo test --locked --lib --manifest-path desktop-electron/native/windows-helper/Cargo.toml
```

用户实际验收：使用Chrome主动导出的密码CSV和扩展导出的Cookie JSON，确认预览、选择冲突与导入结果；不同网站可能还依赖localStorage、设备绑定或服务端状态，Cookie写入成功不保证登录保留。导出文件包含敏感资料，由用户保管并在迁移后删除，不上传给模型。
