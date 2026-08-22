# AGENTS.md 规范参考

## 什么是 AGENTS.md？

AGENTS.md 是一个简单、开放的 Markdown 格式文件，专为 AI 编程代理（Coding Agents）设计。它是 AI 助手的"README"——与 README.md（面向人类开发者）互补，提供 AI 代理处理项目时所需的上下文和指令。

目前已超过 6 万个开源项目采用，由 Linux 基金会旗下 Agentic AI Foundation 管理，获得 OpenAI、Google、Cursor、Cognition 等主流厂商支持。

## 核心设计理念

1. **面向 AI 代理**：为 AI 编码工具提供结构化的操作手册
2. **跨工具兼容**：同一份文件可被 Codex、Jules、Cursor、Windsurf、Devin、Aider、Gemini CLI、VS Code、Zed 等 20+ 工具识别
3. **就近原则**：AI 代理使用离当前编辑文件最近的 AGENTS.md
4. **纯 Markdown**：无需特殊格式，AI 直接解析

## 文件位置与嵌套

### 根目录放置
在项目根目录放置一个 AGENTS.md，覆盖整个项目。

### Monorepo 嵌套
在子包中放置独立的 AGENTS.md：

```
my-monorepo/
├── AGENTS.md              # 根级通用配置
├── packages/
│   ├── web/
│   │   └── AGENTS.md      # 前端特定配置
│   ├── api/
│   │   └── AGENTS.md      # 后端特定配置
│   └── shared/
│       └── AGENTS.md      # 共享库配置
```

AI 代理处理 `packages/web/src/App.tsx` 时会使用 `packages/web/AGENTS.md`，而非根目录的。

## 优先级规则

1. **最近的 AGENTS.md**（最靠近被编辑文件的）优先级最高
2. **用户聊天中的显式指令** 覆盖一切
3. 父目录的 AGENTS.md 作为后备

## 典型内容板块

| 板块 | 说明 | 是否必要 |
|------|------|---------|
| 项目概述 | 技术栈、架构简介、项目目标 | 推荐 |
| 构建/测试命令 | 安装依赖、启动、构建、测试的具体命令 | 强烈推荐 |
| 代码风格 | 命名约定、格式化规则、语言特性约束 | 推荐 |
| 测试说明 | 测试策略、覆盖率要求、测试命令 | 推荐 |
| 安全注意事项 | 敏感配置、授权边界、禁止操作 | 按需 |
| PR 规范 | 标题格式、提交前检查清单 | 按需 |
| 部署步骤 | 发布流程、环境变量 | 按需 |
| 已知问题/陷阱 | 常见坑点、注意事项 | 按需 |

## 支持的 AI 工具

完整兼容（原生支持）：
- OpenAI Codex
- Google Jules
- Cursor
- Windsurf (Cognition)
- Devin (Cognition)
- Aider
- Gemini CLI
- VS Code (Copilot)
- Goose (Block)
- opencode
- Zed
- Warp
- Factory
- Amp
- RooCode
- Kilo Code
- Phoenix
- Semgrep
- UiPath Autopilot
- JetBrains Junie
- Augment Code
- Ona

## 与其他配置文件的区别

| 文件 | 面向对象 | 用途 |
|------|---------|------|
| README.md | 人类开发者 | 项目介绍、快速上手、贡献指南 |
| AGENTS.md | AI 编程代理 | 构建命令、测试、代码规范、工具链细节 |
| .cursorrules | Cursor 专属 | 已逐步被 AGENTS.md 取代 |
| CLAUDE.md | Claude Code 专属 | 正在向 AGENTS.md 靠拢 |
| .aider.conf.yml | Aider 专属 | Aider 通过 `read: AGENTS.md` 接入 |

## 迁移指南

如果项目已有工具专属配置文件，可以这样迁移：

```bash
# 将旧配置重命名为 AGENTS.md
mv CLAUDE.md AGENTS.md
# 或
mv .cursorrules AGENTS.md

# 为兼容旧工具，创建软链接
ln -s AGENTS.md CLAUDE.md
```

Aider 配置（`.aider.conf.yml`）：
```yaml
read: AGENTS.md
```

Gemini CLI 配置（`.gemini/settings.json`）：
```json
{
  "context": {
    "fileName": "AGENTS.md"
  }
}
```
