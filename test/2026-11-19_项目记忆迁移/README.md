# 索引式项目记忆迁移回归

```bash
ASTRION_IGNORE_DOTENV=1 TERMINAL_SANDBOX_MODE=host ASTRION_DATA_ROOT="$PWD/.astrion/test-project-memory" \
  python3 -B -m unittest discover -s 'test/2026-11-19_项目记忆迁移' -p 'test_*.py'
```

测试使用临时项目和合成记忆。覆盖 Host 启动提醒和设置重开、实际旧文件清理和说明、时间戳、同名冲突、文件变动保护、Docker 管理权限、Web/API 工作区枚举和忙碌工作区保护。Docker 用例检查挂载命令，并在独立目录运行真实文件代理脚本模拟容器视图。

原生 Docker 挂载和容器回收需要运行中的 Docker daemon。Windows 原生文件操作和桌面弹窗视觉需在相应产品环境验收。
