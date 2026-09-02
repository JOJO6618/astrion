# CLI（开发中）

> ⚠️ CLI 目前处于**重写中的开发状态**，功能与稳定性以 Web 端为准，本文仅介绍当前形态，所列行为后续可能变化。

---

## 1. 是什么

一个终端里的对话客户端：**React 19 + Ink 6 + TypeScript** 实现的 TUI，让你不打开浏览器也能与 Astrion 对话。

关键点：CLI **不是独立的 Agent 运行时**——它连接的是你本地正在运行的 Astrion Web 服务（默认 `127.0.0.1:8091`），所有对话、工具执行、权限控制都走后端，CLI 只是一个更轻的交互界面。

## 2. 启动

```bash
npm --prefix cli install   # 首次安装依赖
npm run cli                # 启动，自动连接本地 8091 服务
```

启动后会清屏、连接本地服务、创建新会话，输入区固定在底部。若当前目录不在任何已授权工作区中，会先询问是否添加为工作区。

## 3. 当前能力与边界

- 基础对话、流式输出、工具调用展示；
- `/` 指令体系（设计中，详见仓库 `docs/cli_slash_commands_spec.md`）；
- 思考内容默认折叠，只显示「思考中 / 思考完成」；
- 多模态、快捷窗口、版本控制等 Web 端能力在 CLI 中**尚未对齐**——需要完整功能时请用 Web 端。

## 4. 面向开发者

```bash
npm run cli:typecheck   # 类型检查
npm run cli:build       # 构建（产物提供 agents / agents-cli 命令）
```

CLI 代码在 `cli/src/`（`App.tsx`、`components.tsx`、`eventMapper.ts`、`api.ts`），欢迎贡献。
