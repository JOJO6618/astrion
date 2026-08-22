# AGENTS.md 模板集

## 模板 1：通用项目模板（最常用）

适用于大多数单体项目。

```markdown
# AGENTS.md

## 项目概述
<!-- 简要描述项目做什么、面向谁、技术栈概览 -->
[项目名称] 是一个 [一句话描述]。技术栈：[列出主要技术]。

## 开发环境设置

### 前置要求
- Node.js >= [版本]
- [数据库/运行时/其他依赖]

### 安装依赖
```bash
[安装命令，如 pnpm install / npm install / pip install -r requirements.txt]
```

### 常用命令
```bash
# 启动开发服务器
[dev command]

# 运行测试
[test command]

# 代码检查
[lint command]

# 构建生产版本
[build command]
```

## 项目结构
```
[简要目录树，标注关键目录用途]
```

## 编码规范

### 命名约定
- 组件：PascalCase
- 函数/变量：camelCase
- 常量：UPPER_SNAKE_CASE
- 文件名：[规则]

### 代码风格
- [规则1，如：使用 TypeScript 严格模式]
- [规则2，如：单引号，无分号]
- [规则3，如：优先使用函数式模式]

## 测试说明

### 测试结构
- 单元测试：[说明位置和命名规范]
- 集成测试：[说明位置]
- E2E 测试：[说明位置]

### 运行测试
```bash
# 运行所有测试
[command]

# 运行特定测试
[command --filter "..."]
```

## PR 规范
- 标题格式：`[scope] 简短描述`
- 提交前必须通过 lint 和 test
- [其他规则]

## 注意事项
- [已知陷阱/特殊约定]
- [安全敏感的操作]
```

---

## 模板 2：Monorepo 根级模板

适用于 monorepo 根目录，给子包留有指导空间。

```markdown
# AGENTS.md (Root)

## 仓库概述
这是一个 [技术栈] monorepo，包含 [N] 个子包。

## Monorepo 管理工具
- 包管理器：[pnpm workspaces / yarn workspaces / nx / turborepo]
- 构建工具：[工具名]

## 子包概览
| 包名 | 路径 | 用途 |
|------|------|------|
| [name] | packages/[name] | [说明] |
| [name] | packages/[name] | [说明] |

## 全局命令
```bash
# 安装所有依赖
[command]

# 运行所有测试
[command]

# 全局 lint
[command]
```

## 子包操作
AI 代理会自动使用离当前编辑文件最近的 AGENTS.md。
各子包可在自己目录下放置独立的 AGENTS.md 覆盖本配置。

## 通用规范
- [跨包通用规则]
- [代码提交规范]
```

---

## 模板 3：前端项目模板

适用于 React/Vue/Next.js 等前端项目。

```markdown
# AGENTS.md

## 项目概述
[项目名] 是一个基于 [框架] 的前端应用。

## 技术栈
- 框架：[Next.js 14 / React 18 / Vue 3 / ...]
- 语言：TypeScript (strict mode)
- 样式：[TailwindCSS / CSS Modules / styled-components]
- 状态管理：[Zustand / Redux / Pinia]
- 包管理：[pnpm / npm / yarn]

## 常用命令
```bash
# 开发
pnpm dev

# 构建
pnpm build

# 测试
pnpm test

# 检查
pnpm lint
pnpm type-check
```

## 目录结构
```
src/
├── components/    # 可复用 UI 组件
├── pages/         # 页面 / 路由组件
├── hooks/         # 自定义 React Hooks
├── utils/         # 工具函数
├── types/         # TypeScript 类型定义
├── stores/        # 状态管理
└── styles/        # 全局样式
```

## 组件规范

### 文件结构
```
ComponentName/
├── index.ts           # 导出
├── ComponentName.tsx  # 组件实现
├── ComponentName.test.tsx  # 测试
└── types.ts           # Props 类型
```

### 编码约定
- 组件用 PascalCase，文件同名
- 一个文件一个组件（除非紧密关联的小组件）
- Props 使用 TypeScript interface 定义
- 优先使用函数组件 + Hooks
- 使用 TailwindCSS 工具类（如已采用）

## 状态管理
- 服务端状态：[React Query / SWR / ...]
- 客户端状态：[Zustand / Redux / ...]
- 表单状态：[React Hook Form / Formik / ...]

## 测试
```bash
# 单元测试（Vitest）
pnpm test

# 组件测试（Testing Library）
pnpm test:component

# E2E（Playwright / Cypress）
pnpm test:e2e
```
```

---

## 模板 4：后端项目模板

适用于 Node.js/Python/Go 等后端项目。

```markdown
# AGENTS.md

## 项目概述
[项目名] 是一个 [语言/框架] 后端服务。

## 技术栈
- 运行时/语言：[Node.js 20 / Python 3.12 / Go 1.22]
- 框架：[Express / FastAPI / Gin / ...]
- 数据库：[PostgreSQL / MongoDB / Redis]
- ORM：[Prisma / SQLAlchemy / GORM]

## 常用命令
```bash
# 安装依赖
[command]

# 启动开发服务器
[command]

# 运行测试
[command]

# 数据库迁移
[command]

# 代码检查
[command]
```

## 项目结构
```
src/
├── controllers/   # 请求处理
├── services/      # 业务逻辑
├── models/        # 数据模型
├── middleware/     # 中间件
├── routes/        # 路由定义
├── utils/         # 工具函数
└── config/        # 配置
tests/
├── unit/
├── integration/
└── e2e/
```

## API 规范
- RESTful 风格
- 响应格式：`{ success: boolean, data: any, error?: string }`
- 错误处理：统一错误中间件
- 请求验证：[Joi / Zod / Pydantic]

## 数据库

### 迁移
[说明数据库迁移的流程和命令]

### 查询规范
- 使用 ORM 而非原生 SQL（除非必要）
- 避免 N+1 查询
- 大查询加分页

## 测试
```bash
# 所有测试
[command]

# 特定测试
[command --filter="auth"]
```

## 安全
- [认证方式说明]
- [授权模型说明]
- 敏感配置使用环境变量
```

---

## 模板 5：轻量级模板（小项目/库）

适用于小型项目或库，极简风格。

```markdown
# AGENTS.md

## Setup
```bash
pnpm install
```

## Commands
- `pnpm dev` - Start dev server
- `pnpm test` - Run tests
- `pnpm build` - Build for production
- `pnpm lint` - Lint and format

## Code Style
- TypeScript strict mode
- Single quotes, no semicolons
- Functional patterns preferred
- Export named, not default

## Testing
- Unit tests alongside source files (`*.test.ts`)
- Run `pnpm test -- --coverage` for coverage report
```
