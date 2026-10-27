# Chat 交接标签持久状态

按用户确认：每个有效标签有独立身份及处理状态；重载隐藏已处理标签，保留待处理选择但取消计时；直接发送新消息受理即算继续。

仅使用临时数据、合成模型身份和可控请求，不调用真实模型、不创建真实工作区、不启动或重启现有服务。

```bash
ASTRION_IGNORE_DOTENV=1 \
ASTRION_DATA_ROOT="$PWD/cache/chat-handoff-regression" \
/opt/homebrew/bin/python3.11 -B -m unittest discover \
  -s 'test/2026-10-27_Chat交接标签' -p 'test_*.py'
node 'test/2026-10-27_Chat交接标签/frontend.cjs'
```

后端覆盖独立标签 ID／累计数、重复登记、历史升级、刷新取消计时、旧页面迟到心跳与释放、到期后存活确认、重启、显式暂停、直接发送及失败事务、切换重试与互斥、删除／重新编辑、重生成分支、复制和真实 Flask 交接接口。

前端执行生产 HandoffCard 脚本与模板，覆盖逐条映射、已处理隐藏、待处理无倒计时、首次展示心跳、暂停、切换与旧轮询竞争、失败重试。原有 `test/2026-10-25_Chat模式/frontend.cjs` 的 11 组状态检查也通过。

相关回归：`test/2026-10-25_Chat模式/` 的 51 项后端用例及 `test/2026-10-27_对话迁移/test_chat_transfer.py` 的 2 项通过。Chat 停止用例仍输出既有 pending task／ContextVar 回收告警；本任务不将这些告警视为已修复。

静态验证：修改文件 py_compile、定向 ESLint 和前端构建（含 TypeScript／stylelint）通过。构建保留常规 chunk 体积提示。

真实模型生成标签、实际 30 秒等待、多窗口网络竞争、原生桌面／移动端视觉仍需用户运行验收；本次未使用浏览器自动化代替视觉验收。
