# 索引式项目记忆迁移回归（Host）

```bash
ASTRION_IGNORE_DOTENV=1 TERMINAL_SANDBOX_MODE=host ASTRION_DATA_ROOT="$PWD/.astrion/test-project-memory" \
  python3 -B -m unittest discover -s 'test/2026-11-19_项目记忆迁移' -p 'test_*.py'
```

测试使用临时项目和合成记忆，只覆盖 Host 产品路径：

- 迁移核心：外部专题目录定位、旧文件清理与 `00_READ_ME_项目记忆已迁移.md` 说明、修改时间保留、同名冲突另存、复制后源/目标变动复核、符号链接拒绝。
- 迁移接口：Host 启动提醒至多一次、「设置 → 上下文与记忆」可重开、Host 工作区枚举、单工作区任务进度与完成后的索引归零。
- 旧身份拒绝：绑定 Docker 会话的 owner 在专题目录、索引扫描和记忆快照定位处明确报错，不会被当作 Host 路径；非 Host 身份访问迁移接口返回 403。

本目录不再包含容器挂载、容器内文件代理或管理员跨用户迁移用例；这些属于 Docker fork 的验收范围。Windows 原生文件操作与桌面弹窗视觉需在相应产品环境验收。
