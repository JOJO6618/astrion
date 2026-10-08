# macOS 桌面更新：原生准备与安装退出分阶段（2026-10-08）

## 修复的问题

0.5.4 更新到 0.5.5 时，ZIP 下载正常，随后应用退出但没有自动启动。
最近相关提交 f302f710 放行了快捷对话的关闭闸门，但从 electron-updater 的
quitAndInstall() 返回后即开始 8 秒/11 秒退出兜底。MacUpdater 6.8.9 的此方法在
Squirrel 尚未就绪时只注册稍后安装的事件监听器，并立即返回，原生读取/解压/验签
尚未完成时兜底便可能退出应用。

本机取证：更新缓存 10 月 8 日 23:02 下载到 0.5.5，原生缓存 23:03:31 出现解出的
0.5.5，但 ShipIt 状态文件和安装日志仍停在 10 月 7 日。用户明确说明目前安装的
0.5.5 是随后手动用 DMG 安装，不能作为自动更新成功证据。

确定性：已用发布包内同版本的真实 MacUpdater 代码复现上述计时缺陷；本次现场没有
持久记录兜底触发行，故本次具体退出由兜底触发仍属于高概率判断。

## 当前行为

- ZIP 就绪后进入 installing，保持窗口退出闸门的正常行为。
- 用 Electron 公开的 autoUpdater.checkForUpdates() 准备原生更新，不提前排队退出。
- 原生 update-downloaded 到达后开闸，再请求 quitAndInstall()，进度变为 restarting。
- 仅在原生 before-quit-for-update 后启用 8 秒重试退出和 11 秒强制退出。
- will-quit、错误、销毁都会清除计时器；强制退出前显式收掉内嵌后端。
- 准备超过 120 秒报错并保留应用，迟到的原生就绪事件不会触发退出。
- 原生检查没有取消 API，超时后等待它收尾，期间拒绝重叠重试，防止旧事件混入新流程。
- 关键事件写入 app.getPath('userData')/logs/desktop-update.log（JSON 行，1MB 轮转，3 份备份）。

## 离线回归

```bash
node --test test/2026-10-08_桌面更新原生准备/*.test.mjs
python3 -B -m unittest discover -s test/2026-10-07_桌面端更新安装退出闸门 -p 'test_*.py'
```

新增 19 项行为回归，旧目录 11 项退出闸门回归，均通过。

- fixture.mjs 执行现有依赖的真实 MacUpdater.js，覆盖 updateDownloaded() 本地代理交接和
  quitAndInstall()；HTTP 服务、原生系统更新器、应用退出与时间使用桩，不下载或安装应用。
- updater_install.test.mjs：准备 30 秒不退出、正常安装、退出拦截兜底、准备超时、迟到事件、
  准备/退出错误、同步异常、重复事件、普通退出、重试与后端清理异常。
- bridge_update.test.mjs：构建并执行真实控制桥和安装控制器，验证 HTTP 202/409、进度状态、
  超时后的重试锁和同步检查失败后的恢复。
- updater_log.test.mjs：在独立临时目录验证日志落盘、轮转及不可写路径不会阻断更新。

## 尚待真实安装验收

源码修复不会改变已安装的 0.5.5 或已经发布的 0.5.4/0.5.5 安装包。本次未打包、发布，
没有执行真实 ShipIt 替换安装和自动拉起。

验收需从包含本修复的应用发起更新。若仍以原版 0.5.4/0.5.5 发起更新，执行的是旧壳
代码，即使目标 ZIP 包含修复，也无法改变这一次发起方的退出行为。

1. 手动安装包含修复的测试构建，再让它更新到一个更高版本的测试构建。
2. 准备期间窗口和应用应保留，不在旧的 8/11 秒阈值自行退出。
3. desktop-update.log 应依次出现 zip-downloaded、native-preparation-start、
   native-update-ready、native-before-quit-for-update、app-will-quit。
4. ~/Library/Caches/com.astrion.desktop.ShipIt/ShipIt_stderr.log 应出现本次时间对应的
   Beginning installation、Installation completed successfully、Successfully launched application。
5. 新应用应自动显示窗口，版本应为目标版本。
6. 如退出被拦截，退出兜底应在 native-before-quit-for-update 之后计时；不得在
   native-preparation-start 后直接计时退出。
