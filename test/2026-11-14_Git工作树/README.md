# 对话级 Git worktree 验证

2026-11-14，macOS arm64，Python 3.11.13、Node 24。契约见 `docs/git_worktree.md`。

## 已执行结果

| 验证 | 结果 |
| --- | --- |
| 本目录 Python 专项 | 54 项通过 |
| 本目录前端状态与并发回归 | 11 项通过 |
| 公共执行层 `test/2026-11-07_公共执行层` | 198 项，189 通过、9 项平台条件跳过 |
| 子固定权限与单次完全访问 | 35 项通过 |
| 新对话初始化 | 10 项通过 |
| 完整系统提示词统一冻结 | 23 项通过 |
| 统一子智能体 | 23 项通过 |
| 工具动态加载 | 23 项通过 |
| 对话显示快照完整入口 | Python 27 + 12 + 6 项、Node 54 项通过 |
| 本轮修改 Python 文件 `py_compile` | 58 个文件通过 |
| 本轮修改前端定向 ESLint | 19 个文件，0 errors / 0 warnings |
| `npm run build --silent` | 通过，末次 Vite 构建 10.32 秒；仍有大 chunk 提醒 |
| `git diff --check` | 通过 |
| 全量 `npm run lint` | 未通过：0 errors / 287 条存量 Prettier warnings，配置要求最大警告数为 0 |

全量 lint 的剩余警告位于本轮未修改文件；没有为通过 lint 对全仓做格式清洗。现有回归中的已删除 `python_env` 参数、旧双主对话入口和新增资源接口 mock 已按当前生产接口更新，未恢复过时生产 API。

## 专项覆盖

- 使用真实临时 Git 仓库验证创建、进入、退出、分支校验、明确 start_ref 和 include_uncommitted；从其它兼容起点携带及冲突后保留、再次进入。
- staged / unstaged 同文件分层、二进制、删除、重命名、新文件、0755 权限、忽略文件不复制；长未跟踪清单不依赖终端输出上限；临时补丁清理失败保留原始错误。复制时并发出现不同内容的目标文件会报错，源和目标均不被覆盖。
- 默认关闭、老对话不启用、Chat 无快照、工具定义与动态目录可见性、执行守门、创建审批、主任务门闸。
- 主执行根绑定不改变工作区身份；相对读写、已读守门、审批作用域下的前台与后台命令、新终端，以及无审批作用域的沙箱终端显式相对目录使用正确基准；已有进程不迁移。
- 主初始提示、后续 user 通知持久化、落盘失败保留待通知、确认失败重试去重、冻结 system 不变。
- 子实例完整工具批次后同步，idle 下次工作前同步；无效执行根暂停并保留消息，固定写根不扩大，通知和 registry 写入失败可重试。
- 技能与项目记忆归属原工作区；工具返回给模型的路径指向实际物理文件，编辑记录和预览仍按工作区相对路径保存。
- 失效工作树保持实际绑定，拒绝默认执行，不回主目录写入；状态栏保留分支与返回主目录入口。
- 导航及工作区切换、旧请求乱序、同对话切树、旧请求 finally、不创建空页工作树等前端行为。
- 模拟执行端 `/workspace` 绝对 Git 路径及自定义容器挂载点映射：真实 Git 验证宿主侧 branch / diff / list 查询及越界管理目录拒绝。Windows EncodedCommand 仅做引用与封装结构验证。

## 可复现命令

```bash
/opt/homebrew/bin/python3.11 -B test/2026-11-14_Git工作树/run_regressions.py worktree execution permissions new-conversation system children tool-loading snapshot
node test/2026-11-14_Git工作树/frontend.cjs
set -o pipefail
npm run build --silent 2>&1 | tail -n 5
npm run lint
git diff --check
```

`run_regressions.py` 为每组设置临时数据、日志和部署目录，禁用 dotenv 和用户级 Git 配置。日志写入未追踪的 `_experiments/worktree-implementation/<group>.log`。测试不读取真实对话或凭据，不启动另一个后端、不调用真实模型。

## 验收范围

已验证的是 macOS 上的真实临时 Git、文件与命令实现，以及离线 API / 模型循环 / 前端状态回归。没有把模拟目录映射当作 Docker 或 WSL 原生验收。

仍需实际使用验收：三主题和窄屏下的设置开关、分支菜单与弹窗；真实模型自主创建/切换；Windows direct / WSL2、Linux host 沙箱和 Docker 原生执行。当前不支持生产远端执行后端，遇未适配后端明确拒绝，不使用控制端目录替代。

操作入口：个人空间 → 外观中的 Git worktree 开关（只影响新 Agent 对话），再从新对话 Git 状态栏的分支菜单进入。明确指定起始版本和是否携带未提交修改。退出保留分支、文件和进程，不自动提交、合并或清理。
