# Linux host 适配、安装与 API 回归

本机使用 `/opt/homebrew/bin/python3.11`，不启动服务、不安装系统包、不读取真实 token。

```bash
python3 -B -m unittest discover -s test/2026-10-08_Linux_host沙箱适配 -p 'test_*.py' -v
```

本机：21 项，18 通过；3 个 Linux 专属检查因平台跳过。服务器在独立实验目录运行其中 16 项，全部通过；另外 5 个应用导入/安装 API 测试仅在本机执行。

- `test_linux_sandbox.py`：严格 schema、真实 scope 到请求的映射、环境变量拒绝、只读/最低子档边界、系统/内部伪文件系统源拒绝、private virtual parent chmod、网络属性、服务生命周期绑定、SEQPACKET 与源 FD 校验。
- `test_installation.py`：安装器成功保留备份和已有授权、模拟验收失败恢复旧助手/配置/策略/服务；脚本语法/help；Windows 导入不依赖 Linux-only 模块；导入 Web 状态模块不污染全局 sys.path。
- `test_setup_api.py`：使用合成身份和 Mock 安装器验证共享 status 蓝图，headless Bearer 无 Cookie 可读取/启动/轮询；未认证和非 host 会话不能启动安装。没有使用真实凭证或执行安装。
- `linux_contract_results.json`：服务器 16 项结果；`install_dry_run.json`：独立 Shell 安装入口的服务器预演，正确检测系统尚未安装 bwrap，未调用 apt。
- `run_linux_contracts.py`：仅供复制到隔离目录的 helper 合约入口，避免导入服务器现有业务代码；应用相关测试在本机执行。

原有回归也通过：`test/2026-10-07_子智能体权限与单次完全访问/` 35 项；历史 `test_server_refactor_smoke.py` 6 项。前端 build 包含 tsc/stylelint/Vite，本次文件 eslint 和全局 i18n audit 通过。Vite 仍有体积超过 500kB 的既有 chunk 提示，构建成功。

真实助手、UID、网络和取消回收证据见 `../2026-10-08_Linux_host沙箱助手/README.md`，不是用 Mock 安装回归替代系统运行实测。全局安装、sudo/polkit 提权及 UI 视觉仍待实机验收。
