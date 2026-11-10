# 浏览器深色绿色背景：复现与根因

2026-11-10 用户确认：外框缩放正常；按住浏览器内部左右分隔条（即使不移动）、加载网页和主界面重绘会出现绿色。用户截图 `.astrion/user_upload/截屏2026-11-10 13.08.05.png` 的大面积背景为 `#305244`。

## 已验证的根因

`static/src/components/input/InputComposer.vue` 的三组深色目标模式横幅规则使用：

```css
:global(:root[data-theme='dark']) .goal-mode-banner--done,
:global(body[data-theme='dark']) .goal-mode-banner--done { /* ... */ }
```

在当前 `@vue/compiler-sfc` 的 scoped CSS 编译中，`:global(...)` 后的横幅类被丢弃，实际生成：

```css
:root[data-theme='dark'], body[data-theme='dark'] {
  background: color-mix(in srgb, var(--theme-surface-muted) 72%, var(--state-success) 28%);
}
```

因此根元素被无条件染绿，是否启用目标模式不影响。根背景污染覆盖浏览器透明叠层；内部拖动期间主视图抬到原生网页上方，绿色持续覆盖。浅色不匹配这组规则。网站本身、窗口底色与 GPU 默认背景不是本次绿色的来源。

## 证据

- 完整当前应用复现：`_experiments/browser-green/1794288227563/results.json`，三主题/21个采样点。深色内部按住时主视图截图有1,586,314个绿色像素，网页视图为0；主视图主体绿色为 `#305244`，根元素 computed background 为 `color(srgb 0.13898 0.324549 0.263059)`。
- 背景CSS匹配来源：`_experiments/browser-green/1794288460088/results.json` 的 `dark-internal-held.rootRules`；三条直接命中 `:root` 的背景规则均来自包含 InputComposer 的入口CSS。
- 同进程 A/B：`_experiments/browser-green/1794288699445/results.json` 和 `css-ab.json`。保持内部拖动按住，仅在诊断 renderer CSSOM 内将三组规则重新限定到各自横幅类。根背景立即变回 `rgba(0, 0, 0, 0)`，大面积绿色消失；剩余162个主视图绿色像素为界面原有的小图标等，与浏览器区域无关。继续拖动和局部重绘均保持正常。
- 直接调用当前 `@vue/compiler-sfc.compileStyle` 已验证：去除错误的 `:global(...)` 包裹后，生成正确的 `:root[data-theme='dark'] .goal-mode-banner--done[data-v-...]`。

首次定位阶段仅进行测试，CSSOM A/B 只修改独立测试进程。随后用户明确授权“修复并构建测试版，记录在文档里，先不发正式版”，源码修复与测试包更新结果见下节。

## 源码修复与测试版交付（2026-11-10）

- 修正 `InputComposer.vue` 三组共六个深色选择器，去掉根选择器的 `:global(...)` 包裹，保留提示条类，由 scoped 编译器把 scope 加在提示条上。提示条原有配色与原生透明叠层机制不变。
- `scoped-style.test.mjs` 使用真实 `@vue/compiler-sfc` 编译当前源码中的三组规则，断言类名和 scope 均保留；另用原始错误写法验证守护确实拒绝根背景污染，4 项通过。
- `npm run build --silent 2>&1 | tail -n 5`（启用 pipefail）通过，包含 TypeScript、Stylelint 与 Vite。构建仍有既有的大于 500 kB chunk 提示。
- 完整源码应用、未启用 CSSOM A/B：`_experiments/browser-green/1794289421349/results.json`，三主题 24 个采样点通过；连续录制 532 帧，绿色覆盖帧 0。
- 更新原测试 App 的包内 `runtime/backend/static/dist`；3,700 个文件 SHA-256 与构建产物逐一一致，`app.asar` 哈希未变。旧前端备份在 `_experiments/macos-browser-20261109-ai/green-fix-1794289568966/backup/dist`。
- 使用测试 App 内 `app.asar` 原生模块和包内后端/前端，再跑完整应用且不启用 CSSOM A/B：`_experiments/browser-green/1794289601930/results.json`，三主题 24 个采样点通过；连续录制 450 帧，绿色覆盖帧 0。
- `verify-results.mjs` 对两轮结果断言：网页持续可见，根背景透明，内部按住/重绘/移动时 `raised=true` 与 `resizing=true`，释放后取消 resizing；网页自身绿色像素 0，主界面仅余 154–156 个原有绿色小图标像素。

测试 App：`_experiments/macos-browser-20261109-ai/mac-arm64/Astrion.app`。继续使用 `_experiments/macos-browser-20261109-ai/启动AI浏览器操作版.command`，保留原 `validation-profile`；若旧测试版仍运行，退出后再启动才能加载新资源。更新记录：同目录 `green-fix-update.json`。

此次没有运行正式发布脚本、没有调整正式版本号或更新远端发布清单，没有 commit/push，也没有覆盖 `/Applications`。包内验证由隔离诊断入口装配同一原生窗口和完整应用，不等于从 Finder 启动后的全部功能验收；局部重绘使用诊断节点触发，未发送真实消息或调用模型，真实会话与外站交互仍由用户试用。

## 运行

先构建当前浏览器组件夹具（产物只在 `_experiments/`）：

```bash
node test/2026-11-10_浏览器深色绿色诊断/build.mjs
```

完整应用复现使用独立运行数据、工作区和随机端口，用户测试包中的 Python 运行时承载当前后端源码，不读取真实配置或调用模型：

```bash
GREEN_FULL_APP=1 GREEN_INTERNAL=1 desktop-electron/node_modules/.bin/electron test/2026-11-10_浏览器深色绿色诊断/native.cjs
```

同进程深色 A/B：

```bash
GREEN_FULL_APP=1 GREEN_INTERNAL=1 GREEN_ONLY_DARK=1 GREEN_CSS_AB=1 desktop-electron/node_modules/.bin/electron test/2026-11-10_浏览器深色绿色诊断/native.cjs
```

真实Electron需连接WindowServer；OS沙箱内SIGABRT后应申请本次完全访问，不绕过系统保护。画面仅采集测试窗口自身。内部拖动使用 `webContents.sendInputEvent` 触发真实组件的 pointerdown/指针捕获处理，记录 `resizing=true` 与原生 `raised=true`。合成鼠标抬起若未解除捕获，仅在夹具中发送 pointercancel 清理，不是业务修改。

独立 BrowserPanel 夹具未导入 InputComposer，不能覆盖该污染问题。早期外框尺寸测试也不等于内部拖动测试。要复现本问题必须使用 `GREEN_FULL_APP=1`。

修复后的编译回归、结果断言：

```bash
node --test test/2026-11-10_浏览器深色绿色诊断/scoped-style.test.mjs
node test/2026-11-10_浏览器深色绿色诊断/verify-results.mjs _experiments/browser-green/1794289421349
node test/2026-11-10_浏览器深色绿色诊断/verify-results.mjs _experiments/browser-green/1794289601930
```

包内模块及前后端复现（结果输出到独立诊断目录）：

```bash
GREEN_FULL_APP=1 GREEN_INTERNAL=1 \
GREEN_SOURCE="$PWD/_experiments/macos-browser-20261109-ai/mac-arm64/Astrion.app/Contents/Resources/app.asar" \
GREEN_BACKEND_ROOT="$PWD/_experiments/macos-browser-20261109-ai/mac-arm64/Astrion.app/Contents/Resources/runtime/backend" \
desktop-electron/node_modules/.bin/electron test/2026-11-10_浏览器深色绿色诊断/native.cjs
```

## 跟进：切换配色主进程报错（2026-11-10）

用户确认绿色消失后，截图显示 `astrion://browser/settings` 打开时切色报 `Cannot read properties of undefined (reading 'webContents')`，栈在包内 `geometry.js:38`。对照确认：上轮只更新前端，漏同步源码已有的两处 shell 修复。该测试包本身还没有新版 retention 管理器，此次实际触发源是内部设置标签不创建 `view`，不能仅归因于网页回收。

已备份旧 `app.asar` 到 `_experiments/macos-browser-20261109-ai/theme-switch-fix-1794290158572/app.asar`，仅同步源码 `src/browser/geometry.js`（跳过无视图/已销毁视图）与 `src/window.js`（先保存主题，再给网页视图着色）。归档内 314 个文件逐一比对：上述两个文件与当前源码一致，其余保持原包内容；上轮已修复的前端保留。

验证：

- 新增 `packaged-surface.test.mjs`，直接提取包内代码测试。更新前 4 项失败，内部设置页用例复现截图同样的 undefined/webContents 错误；更新后 4 项通过，覆盖内部页无 view、挂起页 null view、存活/已销毁视图，以及缓存落盘先于视图更新的顺序检查。既有源码底色回归 4 项也通过。
- 真实包内完整应用通过 `theme-switch.cjs`：先打开实际浏览器设置页（view 为 undefined），再选中真实网页并保留内部标签；每个场景通过真实 `window:surface` IPC 连续切换浅色、深色、经典、深色，共 8 个采样，主进程异常 0，窗口根 View 底色和主题落盘颜色一致。
- 该旧包不含 retention，实际回收场景未在此包执行；null view 由包内模块回归覆盖。诊断脚本按管理器真实能力选择场景，不给旧包注入新版回收机制。
- 证据 `_experiments/browser-green/1794290534205/theme-switch.json`。同轮绿色回归结果 `results.json` 三主题 24 个采样点全部通过；连续录制 471 帧，绿色覆盖帧 0。
- 诊断过程中修正了脚本对旧包 retention 能力和 View 基类颜色探针覆盖范围的错误假设；此前脚本失败不作为产品通过证据。WebContentsView 覆写着色方法，基类探针只用于窗口根 View，网页画面另行采样。

复现命令沿用上面的包内命令，额外加 `GREEN_THEME_SWITCH=1`；包内模块回归命令：

```bash
node --test test/2026-11-10_浏览器深色绿色诊断/packaged-surface.test.mjs
node test/2026-11-10_浏览器深色绿色诊断/verify-results.mjs _experiments/browser-green/1794290534205
```

原测试版启动脚本和资料目录继续使用。用户需完全退出运行中的旧测试版后再启动，以加载新的主进程代码。本次仍未发布正式版、未改正式版本号、未 commit/push。
