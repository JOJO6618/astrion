# 完全访问审批（2026-11-05）

## 行为

- sandbox 的命令预审批／只读试运行／权限拒绝重试维持原逻辑。
- direct 可搭配 approval / auto_approval / unrestricted。前两者每次 run_command / terminal_input 均先审批完整参数，再直接执行；只读／plan 保持沙箱锁。
- 显式切入 direct 的风险确认提供「不再提示」「返回沙箱模式」「我明白，切换」（红色）。full_access_warning_dismissed 为用户级，默认 false，仅确认后保存勾选；取消不切换、不保存。恢复已处于 direct 的对话不重复提示。
- 设置 → 审核智能体 → 自动审批新增 allow_auto_approve_full_access，默认 false。仅 auto_approval 的新建单次 full_access 请求读取它，开启后自动审核通过直接执行；关闭时仍需自动与人工均通过。人工审批模式不受影响。
- 审批记录绑定环境、任务及完整参数，单次原子领取；direct_execution 与单次 full_access grant 区分。
- 自动审批及工作流审核的取证命令独立绑定只读沙箱。
- 桌面标签等待人工审批时显示黄点；纯自动审核、人工已批准后仅等待自动审核不显示。状态按用户／工作区／对话匹配，沿用现有快照轮询。

## 本次通过

```bash
/opt/homebrew/bin/python3.11 -B -m unittest discover -s test/2026-11-05_完全访问审批 -p 'test_*.py'
node --test test/2026-11-05_完全访问审批/frontend.cjs
/opt/homebrew/bin/python3.11 -B -m unittest discover -s test/2026-10-07_子智能体权限与单次完全访问 -p 'test_*.py'
/opt/homebrew/bin/python3.11 -B test/2026-10-12_工作区切换重构/backend.py
/opt/homebrew/bin/python3.11 -B test/2026-10-17_目标模式重构/test_state.py
npm run lint:text
npm run build --silent 2>&1 | tail -n 5
```

- 新增 Python 41 项、前端状态 5 项通过。
- 已有单次授权 Python 35 项、工作区后端 11 项、目标模式 9 项通过。
- 桌面导航 runner 中 Python 41 项通过；完整 runner 未全部通过，见下文。
- 修改的前端 TS/Vue 定向 ESLint（含 InputComposer 和中英文文案）通过；TypeScript、stylelint、i18n 审计及生产构建通过。
- 后端修改及新增测试的语法检查、git diff --check 通过。

测试使用临时目录、合成审批和真实 ContextVar／权限判断分支，不请求模型、不读真实凭据、不在宿主机执行任意修改命令。离线通过不等于原生平台或视觉已验收。

## 旧回归限制

- `test/2026-10-23_桌面导航重构/frontend.cjs:177` 在前九项通过后出现 `0 !== 1`。以 HEAD 原版 conversationTabs.ts 替换内存中的加载内容后，同样位置仍失败，未修改此套件或导航功能来绕过。
- `test/2026-10-07_子智能体权限与单次完全访问/test_approval_state.mjs` 的旧加载桩缺少 `./aiStream` 依赖，套件加载失败；本次新增前端回归单独覆盖人工等待和确认框状态。该旧加载桩未改。
- 项目标准构建使用 `tsc --noEmit`。额外尝试 `vue-tsc --noEmit` 时，已安装的 vue-tsc 报 `Search string not found: supportedTSExtensions`，未调整依赖；标准 tsc/stylelint/Vite 构建通过。

## 用户视觉和原生验收

重启后端以加载 Python 变更，使用已构建前端：

1. 在 sandbox 下分别切 approval、auto_approval，确认原有流程不变。
2. 切换 direct，确认警告、勾选框及红色确认按钮；勾选后取消不保存，确认后跨对话不再提示。
3. direct + approval 下运行只读命令／持久终端输入，确认均先出现人工审批；拒绝时不执行。
4. direct + auto_approval 下确认每次命令均由审核智能体审核后直接执行。
5. sandbox + auto_approval 申请单次完全访问：开关关闭时双重批准，打开后自动通过即执行；审核上下文明确说明权限与执行后果。
6. 对话等待人工审批后切到另一标签或设置页，确认原标签为黄点；人工批准、拒绝、停止或过期后按实际状态更新。纯自动审核不显示黄点。
7. 检查经典／浅色／深色主题、窄屏、长文案和键盘操作。macOS/Windows 原生执行及桌面标签视觉由用户实机验收。
