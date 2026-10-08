# Host scrypt 解释器兼容回归（第二批）

触发问题：用户通过本机默认 Apple Python 3.9.6 运行密码脚本，Werkzeug 调用 hashlib.scrypt 时抛 AttributeError。缺失的是解释器内置能力，不是可由 pip 补装的 hashlib 模块。

修复：`modules/host_auth_hash.py` 统一通过已在 requirements.txt 中的 cryptography.Scrypt 执行密码生成和校验。参数、随机盐与哈希格式保持与原 Werkzeug 一致，不更换算法、不迁移已有哈希、不改变默认免登录行为。

运行：

```bash
/opt/homebrew/bin/python3.11 -B test/2026-10-08_Host密码保护_02/run_regressions.py
```

本机 6 项全部通过：

- 同一密码与盐的结果和 Werkzeug 完全一致。
- 新哈希可由原 Werkzeug 校验，原哈希可由新实现校验。
- 缺少 hashlib.scrypt 时依旧生成/校验成功，错误密码拒绝。
- 未授权算法参数与损坏哈希在派生之前拒绝。
- 默认 Python 3.9 运行实际脚本生成，3.11 读取配置校验。
- 3.11 运行实际脚本生成，默认 Python 3.9 读取配置校验。

原 `test/2026-10-08_Host密码保护/` 的 28 项回归也全部通过。入口禁用真实 .env，使用临时运行目录与合成密码，不操作实际密码配置，不启动服务。

这仅验证密码模块和源码脚本的解释器兼容，不声明整个项目支持 Python 3.9。
