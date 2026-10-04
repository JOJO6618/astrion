# 快捷模式：源码调试

macOS 先行。快捷窗是独立的 Gateway 客户端，不需要构建或安装 DMG，也不需要打开完整桌面主窗口。

在仓库根目录运行：

```bash
npm --prefix desktop-electron run dev:quick
```

该命令先编译前端，再直接运行 Electron 源码入口 `--quick-debug`。初次运行会打开快捷窗；默认双击 Option 唤起/隐藏，Cmd+Shift+Space 是备用组合快捷键。退出快捷窗用 Esc；窗外空白单击关闭，拖动框选截图，点击应用窗口上的截图按钮则截取对应窗口。退出调试进程可在菜单栏的 Astrion 菜单选择退出。

如需连接指定端口（请先启动含本次改动的后端）：

```bash
ASTRION_API_PORT=8092 npm --prefix desktop-electron run dev:quick
```

如需选择特定运行数据根目录，同时设置 `ASTRION_DATA_ROOT`。显式配置优先于自动发现。

未指定端口/数据根时优先探测新版桌面端发布的本地 Gateway 记录；没有可用记录时使用 host/CLI 默认 8091 和 `~/.astrion/astrion`。兼容服务已在运行时复用；没有服务时自启动 `server.headless_app`。不终止或重启已有服务，退出快捷客户端也不终止共享 headless 服务。

## 首次权限

- 输入监控：双击修饰键由原生只读监听辅助程序识别，系统可能要求为 Terminal、Electron 或监听程序授权，以系统实际列出的进程为准。授权后重新运行调试命令。
- 屏幕录制：快捷窗初始设置区域有截图权限入口。权限检查与申请统一调用实际执行截图的原生辅助程序，使用 CGPreflightScreenCaptureAccess / CGRequestScreenCaptureAccess，不用 Electron 自身权限代表辅助程序。授权后重新唤起窗口；如系统提示重启，则退出并重新运行调试命令。
- 不申请辅助功能权限来模拟键盘输入，不把消息转交完整桌面窗口。

## 已接入的行为

- 快捷入口采用独立银白金属色板，不跟随主应用三主题切换。74px 单行输入栏内放置头像、真实截图缩略图、文字、对话按钮和发送按钮；工作区与模型沿用设置默认值，不再显示底部选择栏。未配置工作区时保留首次准备区。
- 头像复用黑色 StatusAvatar，SVG 尺寸60px，六边形可见高度约为输入栏的60%。文字20px，发送按钮39px，附件34px。发送按钮无悬停变色；附件无悬停描边，悬停或键盘聚焦时仅显示右上角删除按钮。
- QuickPrompt 保留真实 textarea 的编辑、选择、粘贴和中文组合输入语义。淡入淡出光标通过同字体镜像与 selectionStart/scrollLeft 对齐，纵向按31px输入行和21px光标居中计算；组合输入时使用原生光标。
- 快捷新对话显式绑定 `work_mode=execute`、`permission_mode=unrestricted`、`execution_mode=sandbox`，创建时持久化 `metadata.quick_entry=true`。恢复已有对话沿用该对话已经保存的模式；运行中的引导不会切换正在执行的任务模式。
- 快捷对话菜单只显示当前工作区中带快捷来源标记的普通对话。Gateway 列表通过 `quick_entry=1&multi_agent_mode=0` 在后端过滤后分页；索引写入和重建均保留标记。主界面对话列表不传来源筛选，仍显示全部对话。
- 恢复已保存对话时校验历史 metadata 中的来源标记，普通或未标记的旧对话清除绑定，保留草稿和截图；旧对话不会自动补标记。
- 空闲发送创建任务；运行中发送文字/截图直接进入 runtime_guidance，不创建提前输入任务。
- 显示最近操作、并行工具、真实流式回复；思考阶段仅显示“思考中”与三点头像，不显示思考内容。工具完成保留约两秒只影响前端呈现。
- 头像复用项目 StatusAvatar 的眼睛追踪与内部工具图标动画，外框不旋转，不绘制不透明六边形背景以保留输入栏金属渐变。
- 原生窗口使用960px宽的固定透明画布（小屏按workArea收窄），底边锚定 macOS workArea，自动避让可见程序坞与菜单栏。菜单、图片、流式回复和折叠仅改变内部布局，不缩放或移动原生窗口；面板外空白交互由全屏框选层捕获，单击关闭快捷窗，拖动框选截图。
- 快捷窗位于普通 floating 层级，截图层在其后方，不再使用 screen-saver 层级遮挡系统中文输入法候选窗口。中文组合输入期间的 Enter/Esc 不触发发送或隐藏。
- 唤起后在窗外拖动可框选截图。松开后使用 ScreenCaptureKit 获取最新区域画面并排除当前 Electron 客户端窗口；快捷窗与覆盖层保持显示，截图返回后加入输入栏，可继续追加。窗口截图也保留原有覆盖层与按钮节点，截图期间防止重复提交，避免全部按钮销毁重建造成闪动。
- 应用窗口左上角显示银白色“发送 {应用名称} 的截图”按钮。原生辅助程序通过 CGWindowListCopyWindowInfo 获取当前可见桌面窗口的名称、矩形坐标和前后顺序，不依赖辅助功能权限；快捷窗显示期间每600ms串行刷新，隐藏时停止。普通 Astrion 主窗口也参与遮挡与截图，快捷窗和框选浮层排除。
- 按钮在透明覆盖层中按前方窗口矩形边界裁切，Chromium clip-path 同时限制绘制和点击区域；露出的后方窗口按钮可直接点击，不聚焦目标应用。按钮按下/松开独立消费事件，框选期间隐藏按钮。遮挡使用矩形边界，未模拟窗口圆角、半透明表面与窗口形变动画的逐像素遮挡。首次浮现按可见按钮前到后排序，首个延迟50ms，每个更低按钮再增加50ms，淡入160ms；刷新与截图结束不重播。
- 点击窗口按钮通过 ScreenCaptureKit 的 desktopIndependentWindow 过滤器获取完整窗口图像，不混入前方窗口或截图按钮；结果加入现有截图附件，不自动发送消息。截图失败通过回复区保留原生 stderr 与错误 domain/code，不把所有失败都解释成权限问题。前端文案和银白语义色由快捷页通过布局桥传入覆盖层。
- 原生辅助程序的每个命令都在主Actor初始化 NSApplication.shared（prohibited，不显示Dock图标）和 NSScreen.screens，再调用 ScreenCaptureKit。窗口枚举和截图是两个独立进程，不能依赖前一次枚举替下一次截图初始化WindowServer；否则会触发 CGS_REQUIRE_INIT / did_initialize 断言。
- 回复复用项目 MarkdownRenderer；折叠按钮悬浮在滚动区域外，仅鼠标进入右上角按钮附近时显现，点击后保留焦点也不会持续显示。流式回复不再额外预留一行高度。菜单使用窄浮层、SVG、银白表面色与淡入淡出，无位移效果。
- 快捷页按 Vite manifest 递归加载共享依赖 CSS，依赖样式先于 quick.css，保证 StatusAvatar 等共享组件的 scoped 样式实际生效。
- 唤起先提交缩小初始状态并跨两帧再启动动画，从 90% 放大到 101.5% 再回到 100%（360ms），关闭时缩小到 94% 并淡出（160ms）；原生画布不缩放、不移动。收到关闭动画结束确认后才隐藏原生窗口，快速重唤起会取消过期的隐藏动作。
- 头像接收系统鼠标位置（含窗口外坐标），中心由 SVG screenCTM 映射获取；左右共用镜像限幅，退出追踪时清零偏移，使用 SVG translate 与单次逐帧插值，避免 CSS transition 重复缓动。等距镜像计算已通过回归，实际观感仍需本机验收。
- 框选窗口或输入窗口中 Esc 隐藏快捷窗；输入窗口有打开的菜单时先关闭菜单。隐藏不停止后台任务。
- 快捷窗支持图片拖入、粘贴、停止、工具/计划审批和回答问题。
- 草稿、截图与当前对话按工作区保存在固定快捷页来源的 IndexedDB。

正式桌面端「设置 → 快捷对话」可启用快捷模式、选择双击按键、默认工作区与默认图片模型。留空分别继承 `/api/host/workspaces` 的 `default_workspace_id` 与个性化 `default_model`，每次唤起刷新；不可用的默认模型会提示选择，不自动替换。

启用后启动应用保留同一个 `server.app` 后端及菜单栏，主窗口由程序坞、菜单栏或设置入口按需创建。关闭主窗口保留后台客户端，退出应用则停止自有后端。当前仍需先启动应用，登录后台自启动留待后续实现。

## 验证

离线测试（仓库根目录，本机有完整后端依赖的解释器为 Python 3.11）：

```bash
ASTRION_IGNORE_DOTENV=1 ASTRION_DATA_ROOT="$PWD/cache/quick-entry-test-data" /opt/homebrew/bin/python3.11 -m unittest discover -s test/2026-10-04_快捷模式 -p 'test_*.py'
node --test test/2026-10-04_快捷模式/*.test.mjs test/2026-10-04_快捷模式_02/*.test.mjs test/2026-10-04_快捷模式_03/*.test.mjs
```

前端检查使用根目录 `npm run build`。原生辅助程序可单独编译：

```bash
npm --prefix desktop-electron run build:quick-native
```

系统双击键、屏幕权限、多显示器框选、Retina 实际裁切、窗口焦点与动画效果需要在本机手工验收；离线测试和编译通过不等同于这些系统交互已经验证。
