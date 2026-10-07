# 固定子权限与单次完全访问回归

本批次只验证代码与状态，不启动服务、真实子智能体、真实 TUI，也不做截图或视觉验收。

## 已验证

- Python 35项：自动/人工两种批准顺序、人工先批准仍等待审核、拒绝/审核超时/过期、原子单次领取、执行者/工作区/任务/工具调用/实参绑定、重复执行拒绝、停止前不进入执行器、普通审批人工接管不冒充自动裁决。
- 本机原生文件 IO：工作区正例与区外拒绝、打开时替换祖先符号链接、硬链接/FIFO、媒体二进制读取、同前缀兄弟交付目录、自动目录外链、重复新建、固定根恢复、实时路径授权、cwd不改变写根。
- 执行分派：自定义工具不能覆盖内置命令；子执行器独立资源与父权限切换；后台取消先于启动、取消与Popen交错、ID分配前取消、可信上下文进入工作线程、取消意图不被工作线程终态覆盖；Docker seccomp拒绝跨进程内存访问的规则安装。
- Node 11项：Web状态归一化、隐藏普通自动审批时仍展示必要的完全访问决定、同ID更新保留收起、部分决定不清除请求、迟到pending恢复、会话隔离、可见进度上限；CLI两项裁决、真实进度、迟到HTTP、提交失败、会话切换。
- 项目 TypeScript、CLI TypeScript、前端构建和 stylelint 已通过；43个本次Python文件通过Python3.12语法检查。本次22个TS/Vue文件的ESLint为0 errors、0 warnings；全仓库lint为0 errors、124 warnings，因max-warnings=0而未通过，警告均位于未修改文件，未对这些文件做无关清洗。

## 命令

```bash
/opt/homebrew/bin/python3.11 -m unittest discover -s test/2026-10-07_子智能体权限与单次完全访问 -p 'test_*.py'
node --test test/2026-10-07_子智能体权限与单次完全访问/test_approval_state.mjs
./node_modules/.bin/tsc --noEmit
./cli/node_modules/.bin/tsc --noEmit -p cli/tsconfig.json
npm run build --silent 2>&1 | tail -n 5
```

本机 Python3.12 缺少运行依赖，因此行为测试使用依赖齐全的 Python3.11；Python3.12 已检查本次后端改动语法。原有 `test_server_refactor_smoke.py` 的 pytest 收集因沙箱禁止扫描根目录 `.env` 而受阻，未进入测试执行，没有尝试绕过。

额外的 `vue-tsc --noEmit` 因已安装版本与TypeScript不兼容而无法启动，报 `Search string not found: /supportedTSExtensions`；没有修改依赖或工具源码。本次通过的是项目正式构建链的 `tsc --noEmit`、stylelint与Vite，不能据此宣称vue-tsc的Vue模板类型检查已通过。

## 尚未实机验证的范围

- 审批位置、间距、收起箭头、Git状态条与移动端布局由用户视觉验收。
- macOS Seatbelt 策略生成与工作区内临时目录已通过代码回归；本批次没有启动真实命令进程验证内核策略。
- Windows句柄IO、WSL2沙箱和cmd.exe完全访问未在Windows实机执行；Docker最低档的Landlock/libseccomp未在容器实机执行。策略结构检查不能替代这些实机验证。
- Docker最低档需要Landlock ABI≥3和libseccomp；缺失时明确失败，不回退到旧只读DAC或宿主机。元数据修改类系统调用受到seccomp限制。
- Linux宿主机沿用项目原有未适配、未支持的口径。

日志位于 `cache/approval-regression.log`、`cache/approval-state-regression.log`、`cache/approval-smoke.log`；这些本机产物不纳入提交。
