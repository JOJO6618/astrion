# CEF 混合 Views 宿主实验（第二批）

实验日期：2026-11-07。该目录只包含隔离原型，不是正式桌面壳。

完整报告：`output/native-browser-mixed-views-20261107.md`。

## 当前结果

- 确认真正同一个 CefWindow 中包含左 Alloy、右 Chrome BrowserView。
- 确认原生 Chrome toolbar 可按官方 cefclient 的 reparent 方法挂入右侧 Panel 顶部；原生状态记录 visible=1、宽933、高46，页面在其下方。
- 一次直接挂载运行完整通过两侧页面填表点击、中文DOM文本、独立上下文存储、尺寸、收起恢复、焦点、全屏、popup创建关闭和主窗口关闭。
- 该完整成功发生在工具栏未重挂显示的直接挂载模式。工具栏可见的后续运行页面未加载，不能合并宣称完整版本通过。
- 重复启动仍间歇出现空URL，尚未定位根因。当前原型不满足生产集成条件。
- 固定CEF156公开Views路径没有直接保留原生Chrome tab strip的入口；工具栏可复用不等于整套多标签浏览器UI可复用。

## 文件职责

| 文件 | 职责 |
|---|---|
| `prepare-sdk.cjs` | 读取第一批已保存的官方版本索引，下载并校验同版本minimal SDK，仅提取开发文件 |
| `lab-cef.h` | 实验级C API回调、引用计数、字符串与布局辅助，不是生产封装 |
| `mixed-host.mm` | Cocoa/CEF启动，RequestContext初始化，同窗混合视图，原生状态与文件命令 |
| `build-host.cjs` | clang++ C++20编译，官方runtime loader，自包含实验bundle |
| `run-mixed.cjs` | 本机HTTP夹具、独立profile、原版Playwright测试与结果记录 |
| `summarize-results.cjs` | 只读历史逐次结果并生成机器可读证据总表 |

## 环境与构建

在项目根目录执行，需要macOS ARM64、Xcode Command Line Tools、Node、项目已有的playwright-core。无需安装另一份Playwright Chromium。

第一批官方client包应已存在：
`_experiments/native-browser-feasibility-20261107/cef-client/`。

```bash
node test/2026-11-07_内置浏览器可行性_02/prepare-sdk.cjs
node test/2026-11-07_内置浏览器可行性_02/build-host.cjs
node --check test/2026-11-07_内置浏览器可行性_02/run-mixed.cjs
```

SDK固定版本为 `156.0.15+gcf671c0+chromium-156.0.8078.80`；minimal归档SHA1为 `bcdd1877edb300b0fb91d8f71c0b14aae256cd0f`。

构建保持CEF sandbox开启，使用官方loader和独立Helper。实验bundle标识沿用官方Helper对应的`org.cef.cefclient`，不是产品标识决定。Frameworks使用APFS clone复制，保留包内相对链接，不用跨bundle链接。

## 运行

以下命令需要本机HTTP监听和GUI能力。当前工作区沙箱禁止相关操作，智能体运行时需走合法的单次完全访问申请，不能绕过。

```bash
# 当前默认：右Panel包含可见toolbar和Chrome BrowserView
node test/2026-11-07_内置浏览器可行性_02/run-mixed.cjs

# 对照：右Chrome BrowserView直接挂Window；不重挂显示toolbar
node test/2026-11-07_内置浏览器可行性_02/run-mixed.cjs --direct

# 先等待原生URL状态，再连接Playwright，用来隔离连接时机影响
node test/2026-11-07_内置浏览器可行性_02/run-mixed.cjs --late-attach
```

诊断选项，不代表修复：

- `--explicit-navigation`：原生poll第一次执行时调用Frame.load_url。
- `--finish-launch`：cef_initialize后显式调用NSApp.finishLaunching。
- `--sample`：初始页面未就绪时，各采样本实验NetworkService/Renderer一秒。
- `--global-context`：用共享全局RequestContext排除自定义上下文因素；只用于诊断，不满足会话隔离要求，也不应按独立会话测试的成功方案使用。

runner没有截图/视频传输通道。HTML/CSS/JS夹具来自第一批实验目录，不是正式Astrion应用。不会启动生产后端或修改现有运行数据。

## 结果定位与复核

每轮有独立的：
`_experiments/native-browser-feasibility-20261107/mixed-views/run-<timestamp>/`。

- `state.json`：最后一份原生view tree、窗口/视图/toolbar几何与归属。
- `results.json`：观测、状态、异常，以及较新runner记录的HTTP请求。
- `process.log`：仅本实验主进程与Helper输出。
- `cef.log`：CEF日志。
- `network-sample.txt` / `renderer-sample.txt`：仅诊断采样执行时存在。
- `profile/`：独立合成运行数据。

`latest-run.json`只指最近一次，**不代表最近成功版本**。生成全部证据索引：

```bash
node test/2026-11-07_内置浏览器可行性_02/summarize-results.cjs
```

输出：`mixed-views/evidence-summary.json`。历史尝试源码和选项不同，20次尝试中1次完整成功不能被统计成产品成功率；没有执行记录的审批故障也不计为运行失败。

完整成功证据：`run-1794059220848/results.json`（14条观测、10条HTTP请求、正常关闭）。

可见toolbar结构证据：`run-1794060088706/results.json`、`run-1794060408530/results.json`。

## 必须保留的解释边界

1. 先等两个`OnRequestContextInitialized`，再创建BrowserView；单纯创建RequestContext返回不代表已经可用。
2. 原型创建视图、挂窗口、正常导航是不同阶段；日志必须检查返回值，不能只靠“调用已返回”就写成功。
3. 原生toolbar可以reparent。官方`ViewsWindow::AddControls`取`GetChromeToolbar`后加到外部Window布局。右Panel布局是本原型扩展，不能再写“CEF禁止重挂”。
4. Views-hosted把TYPE_NORMAL改为TYPE_POPUP；`CEF_CTT_NORMAL`只提供toolbar，不提供原生标签栏。不能按完整Chrome UI已经内嵌来汇报。
5. 空URL在直接挂载、Panel、晚连接、全局上下文对照均出现，不能把问题仅归因其中一个因素。SIGTERM后的Helper日志不等于原始故障根因。
6. 页面成功一次不等于重复稳定。popup创建不等于留在右栏。焦点/DOM测试不等于人工输入法、菜单、下载弹层和视觉验收。
7. 本轮没有Windows原生验证、真实登录或公网Docker验证。第一批本机Docker控制通道不能推出纯网页客户端可获得原生视图。
8. 原型C API封装未做完整泄漏审计；正式实现不应直接照抄。

收尾核查没有残留本实验MixedViewsLab或官方cefclient主程序。没有长期HTTP预览服务，没有提交/推送，没有修改正式业务代码。
