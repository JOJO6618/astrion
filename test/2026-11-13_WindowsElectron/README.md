# Windows Electron 迁移实验（2026-11-13）

仅测试本分支产生的 App 和合成资料。不要改为正式安装目录，不按名称全局杀进程，不读取真实 providers、密码、Cookie 或浏览器 profile。主侧原生命令用 Windows 工具链运行；WSL 不能执行 Windows Electron/MSVC。

## 模块与原生能力

```cmd
node --experimental-vm-modules --test test\2026-11-13_WindowsElectron\migration_contract.test.mjs
python test\2026-11-13_WindowsElectron\native_smoke.py
desktop-electron\node_modules\electron\dist\electron.exe test\2026-11-13_WindowsElectron\native_capture.mjs
cargo test --manifest-path desktop-electron\native\windows-helper\Cargo.toml --target-dir desktop-electron\.cache\windows-native-target
```

- 模块 VM 测试：共享状态断言必须注入同一个 updater-state synthetic module，不能拿 VM 与 Node 两份模块实例直接比较。
- guardian smoke：只使用合成 token、backend、孙进程与假 shell；保存 token hash/长度，不保存 token。
- WGC fixture：外部窗口必须用第二个 Electron 进程；生产列表故意排除 shell PID。子进程不能设置 `windowsHide:true`，否则窗口会被 Win32 可见性过滤。只保存自己合成窗口的截图。
- Cargo 测试覆盖修饰键识别和裁剪，不模拟真实键盘输入；原生监听器主动忽略注入按键。

## 包内 App

```cmd
node test\2026-11-13_WindowsElectron\packaged_smoke.mjs
node test\2026-11-13_WindowsElectron\verify_package.mjs
```

包内 smoke 自己分配调试端口、资料路径、空白工作区和合成本机模型条目，不调用真实模型。验证网页来自自己的 localhost fixture。密码测试仅保存/查看/删除合成账号，不读取真实凭据。浏览器操作通过已挂载 Pinia store 保持租约所有权；不要额外 connect 抢走 lease。

`verify_package.mjs` 使用宿主 `path.relative` 传给 asar：Windows 该库按本机分隔符拆 dirname，强制转 `/` 会导致嵌套文件误报缺失。生产入口必须为 `src/main.js`，不能混入测试 bootstrap。

## 独立安装/更新/卸载

```cmd
set "ASTRION_INSTALL_TEST_VERSION=0.10.0"
cd desktop-electron
node node_modules\electron-builder\cli.js --config ..\test\2026-11-13_WindowsElectron\installer-test.cjs --win --x64 --publish never
set "ASTRION_INSTALL_TEST_VERSION=0.10.1"
node node_modules\electron-builder\cli.js --config ..\test\2026-11-13_WindowsElectron\installer-test.cjs --win --x64 --publish never
cd ..
node test\2026-11-13_WindowsElectron\installed_update.mjs
```

安装实验使用 `com.astrion.electron-migration-test`、独立 exe 和 output 目录；生产安装宏仅在 PRODUCT_NAME=Astrion 时读取旧 Tauri 登记。测试新版本 bootstrap 会先设置独立资料，再导入真实主入口，确保安装后重启丢失环境变量时也不会用正式资料；固定 CDP 19876 只存在于测试产品 bootstrap。更新源固定 localhost:19875，端口占用即失败，不终止占用者。

Windows NSIS 自绘按钮可能只暴露 UIAutomation Pane、不支持 InvokePattern。驱动仅对已确认测试 exe 路径的 PID、窗口与按钮发 BM_CLICK；不点击系统安全窗口。短时诊断 `installer_probe.mjs` 只运行约35秒，观察阶段后结束自己创建的安装器和驱动；不能据此判断完整安装成功。

**electron-builder 25.1.8 ZIP 配套参数**：`useZip:true` 必须搭配 `differentialPackage:false`。生成器在差分内包启用时仍强制产生 7z，安装脚本却按 useZip 选择解压器，会报 Error opening ZIP file。

小型独立 NSIS 产品使用相同生产钩子但不执行 Electron/helper，只包含少量合成文件，便于先隔离安装逻辑。`nsis_probe.mjs --prepare` 创建两版 payload，然后在 `desktop-electron` 目录分别设置 `ASTRION_NSIS_PROBE_VERSION=0.10.0` / `0.10.1`，执行：

```cmd
node node_modules\electron-builder\cli.js --config ..\test\2026-11-13_WindowsElectron\nsis-probe.cjs --prepackaged ..\output\windows-electron\nsis-probe\0.10.0\payload --win --x64 --publish never
```

第二版相应改为 `0.10.1\payload`，随后回到根目录执行 `node test\2026-11-13_WindowsElectron\nsis_probe.mjs`。输出 `nsis-probe/results.json`，验证首次安装、Win32 拒绝删除共享的真实文件锁、恢复旧文件、更新和普通卸载。锁夹具只允许两个精确的合成标记路径。正式完整包实验也接入了 `rollback_guard.mjs`，在启动 App 前验证忙文件失败后的 exe/asar hash 和合成文件均保持完整。

完整结果看 `output/windows-electron/installer-test/installed-update-results.json` 的 success 和逐项 checks，不能用“安装器 exe 已生成”替代安装/更新/重启/卸载通过。结果记录两版安装器与 NSIS 钩子的 SHA-256。静默全链不运行向导点击驱动，意外弹窗不会被测试自动掩盖。正式旧 Tauri→Electron 升级不在这个独立产品测试中。

完整载荷约 1.65 万个条目、669 MB，独立 7-Zip 解压实测约 215 秒；初始 300 秒预算曾在仍持续读写时杀掉安装器。首次安装和版本替换各允许 1200 秒，并保留实际进度诊断。卸载要等整个测试安装目录消失，不能仅见到 exe 删除便计作通过。

重复实验会留下体积较大的 NSIS 临时目录。`clean_owned_nsis_temp.ps1` 只允许清理含当前 cleanup 脚本且 SHA-256 完全相同、没有进程从该目录运行的 `ns*.tmp`；不得泛删系统 TEMP。`final_matrix.mjs` 将构建 TEMP/TMP 放到工作区 output，运行安装实验时保留原 C 盘 TEMP，以继续验证跨卷条件。初次完整打包会重新扫描运行时的 exe；后续只改 NSIS 时优先 `--prepackaged`。

VM 加载器必须用 `path.isAbsolute` 同时识别 Windows 盘符路径与 POSIX 路径，不能只以 `/` 判断绝对路径。最终模块回归在 Windows 原生 Node 上执行。

## 前端与交付

```cmd
npm run build --silent
npm run lint
node test\2026-11-13_WindowsElectron\frontend_lint.mjs
python desktop-electron\scripts\assemble_windows_delivery.py
```

全库 lint 在当前 Windows CRLF 检出上有大量既有 prettier 警告；不关闭规则、不全仓格式化。单文件检查只在内存归一换行，用于区分本轮 Vue 的实际格式问题。

便携装配脚本要求最新 packaged smoke 成功且 exe/asar hash 完全相符，再复制 App、启动器并做 ZIP CRC 检查。只继续本轮未完成装配时可用 `--resume`，含 validation-profile 的目录会被拒绝。大量小文件复制与压缩建议使用 Windows 原生 Python，避免 WSL 映射文件系统的逐文件开销。

`preflight.py` 是最初沙箱环境探测，后续单次完全访问已解除了原生工具链阻塞；不能把旧 preflight 的 unavailable 状态当作最终失败原因。
