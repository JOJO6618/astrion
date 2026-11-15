# 外部 / 非托管工作树的进入与授权

2026-11-15。对应提交前的改动：`enter_worktree` 的 `enter` 支持 `directory` 键，允许进入其它工具（Claude Code、Codex、手工 `git worktree add`）创建的工作树；工作区外的目标需要落在沙箱路径授权内，用户手动切换则由对话级临时放行覆盖。

契约见 `docs/git_worktree.md` 与 `AGENTS.md` §27。

## 覆盖内容

- 键互斥：`branch_name` 与 `directory` 只能给一个，两个都给或都不给都报错；`directory` 必须是绝对路径。
- 工作区内任意已注册工作树直接进入（`.claude/worktrees/*`、非托管目录），不产生临时授权。
- 工作区外：模型侧需要可读写授权或 `execution_mode=direct`；用户手动切换（API / 新对话预选）不受限，但必须仍是本仓库已注册工作树。
- detached HEAD 工作树只能用 `directory` 进入，进入后 `branch` 为空、`head` 记录提交；状态栏与提示词显示 `detached @ <sha7>`。
- 拒绝：主工作树、符号链接、未注册目录、相对路径。
- 临时放行：绑定记录 `grant={path, added}`；`grants.ContextVar` 并入 `get_macos_writable_paths` / `get_macos_readable_paths`，离开上下文即失效，不写策略文件。
- 退出前置：执行目录下有运行中子智能体、后台命令或终端进程时拒绝退出；退出清除绑定与放行。
- 失效校验：`checked_root` / `query.directory_for` 在没有 grant 记录时拒绝工作区外的绑定。
- 路径校验只用本机 `lstat`，不走 `FileManager.inspect_path`（砂箱可读边界会误拦用户手动切换）；本仓库归属用 `.git` 工作树指针 + `git rev-parse --git-common-dir` 判定，不依赖 `worktree list` 路径字符串；普通目录、其它仓库克隆、主工作目录分别报不同错误。
- 测试的 `FileManager` 固定在 **sandbox** 模式，覆盖「用户切换到未授权外部目录不应被可读授权拦住」的回归。

## 执行

```bash
/opt/homebrew/bin/python3.11 -B test/2026-11-15_工作树外部路径/test_external_worktrees.py
```

工作树副本没有 `node_modules`，跑前端回归/构建前先 `ln -sfn <主仓库>/node_modules node_modules`。

## 本次结果

| 验证 | 结果 |
| --- | --- |
| 本目录 14 项 | 全部通过 |
| `test/2026-11-14_Git工作树/run_regressions.py worktree execution permissions new-conversation system children tool-loading snapshot` | 全部 exit=0（snapshot 首次因缺 `node_modules` 失败，补链接后通过） |
| `node test/2026-11-14_Git工作树/frontend.cjs` | 11 项通过 |
| `npm run lint:text` | 通过 |
| `npm run build --silent`（含 `tsc --noEmit`、stylelint） | 通过 |
| 改动前端文件 `eslint --max-warnings=0` | 通过 |
| `git diff --check` | 通过 |

## 未验证

- 真实模型自主进入外部工作树、三主题与窄屏下的弹窗和路径输入视觉，需要用户在实机确认。
- Windows / WSL2 / Linux host 沙箱 / Docker 的原生执行未验证；Docker 下 Git 工作树本来也只支持 Local 后端。
- 修改留痕与浅备份对外部路径依赖「跟踪键退化为绝对路径」的既有实现，代码路径已确认，但没有在真实外部工作树里跑过一次完整编辑回合。
