# Host 网页可选密码回归

使用已安装项目依赖的 Python，从仓库根执行：

```bash
python3 -B test/2026-10-08_Host密码保护/run_regressions.py
python3 -B test/2026-10-08_Host密码保护/run_compatibility.py
```

入口在子进程 import config 前清除相关路径/桌面环境、禁用真实 .env，使用临时数据根。测试中仅有合成密码、nonce、token与工作区；不启动服务器、不读取真实登录配置、不修改现有服务。

- `test_storage_script.py`：缺配置默认关闭、scrypt 随机盐、0600、密码空白、输入边界、损坏配置、符号链接拒绝、原子写失败保留旧配置、直接参数脚本与实际 DATA_DIR 解析、Docker 模式拒绝。
- `test_web_auth.py`：免登录基线、远程密码登录、错误输入、nonce/generation失效、关闭/重启保护、旧普通账户拒绝、损坏配置、桌面后端豁免与网页标记无效、CSRF、限流、无网页开启接口、并发同密码重设与迟到关闭冲突、不清理运行终端。
- `test_bearer.py`：密码/损坏配置不影响 CLI、full/headless 共用策略、有效 Bearer 不发 Cookie且不替换已有Cookie、不发行nonce、无效Bearer不回退Cookie、远程Bearer拒绝。

本机 `/opt/homebrew/bin/python3.11`：新增 28 项全部通过；兼容入口后端冒烟 6 项通过、Linux 沙箱 18 项通过/3项平台跳过。前端 build（tsc/stylelint/Vite）、i18n audit、本次文件 ESLint通过。

这组离线检查不等同真实桌面启动、Windows 文件锁或界面视觉验收。
