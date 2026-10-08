# Commit 共同作者尾注规范（2026-08-29 用户定）

> 本文是 §6 Git 工作流的配套规范，**随仓库追踪**，clone 后即可读到（原先只存在于本地私有项目记忆里）。
> `AGENTS.md` §6 保留「必须带尾注」这条硬约束并指向本文。

## 为什么

每次帮用户执行 `git commit`（含 `--amend`）都必须带共同作者尾注，**commit 时就要写对，不要等 push 才补**——改写已推送历史成本极高（需 `git filter-repo` + 强推多个 remote）。

## 规则

1. **格式必须是 `{智能体名} powered by {具体模型名}`**，整行为标准 trailer：

   ```
   Co-authored-by: {智能体名} powered by {具体模型名} <{邮箱}>
   ```

   - 禁止直接使用工具默认作者行（例如 `Claude Fable 5 <noreply@anthropic.com>` 会把 GitHub 提交页关联到第三方账号）。
   - **模型名取提交时实际使用的模型**，随配置变化而替换，不要写死成某个历史值。

2. **邮箱按智能体取**：
   - `Astrion` → `<astrion-agent@users.noreply.github.com>`
   - `Codex` → `<codex@example.com>`（openai/codex 官方仓库默认 git 身份，用户拍板）
   - 新增智能体时沿用同一模式，并使用不指向外部真实账号的邮箱。

3. **哪些智能体参与就挂哪条**；多个智能体协作可挂**多条** `Co-authored-by`。

4. 我帮用户提交时**主动**按此规范写入 commit message 末尾，不让用户事后提醒。

## 示例

```
feat(desktop): 支持应用内原生更新安装

- 新增 updater-install.js 原生安装链路
- 退出闸门改为等待安装器确认

Co-authored-by: Astrion powered by DeepSeek-V4.1-Flash <astrion-agent@users.noreply.github.com>
```

## 提交后自检

```bash
git log -1 --format='%(trailers:key=Co-authored-by,valueonly)'
```

输出为空或格式不符，立即 `git commit --amend` 补上（未 push 前 amend 无成本）。
