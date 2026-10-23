# 桌面导航重构回归

## 运行

```bash
/opt/homebrew/bin/python3.11 -B test/2026-10-23_桌面导航重构/run_regressions.py
```

35 项 Python 测试（26 项状态/持久化，9 项 Flask handler 行为），17 组前端回归。临时文件在本目录内，使用合成工作区、受控网络和真实 Vue/Pinia；不启动应用，不读取真实对话或凭据，不请求模型。handler 测试提取当前源码并替代认证装饰器，验证路由业务装配，不替代认证测试或原生壳端到端测试。

覆盖稳定 key、绑定后关闭、关闭后迟到绑定、同命令网络重试、并发写入、旧版本拒绝、加载中关闭、B→C 只提交 C、旧 renderer 失效、刷新保留 pending、启动旧地址不抢占目标、删除与工作区隔离、确认 missing 清理、读取异常保留、存储损坏不覆盖、失败可重试、空页失败恢复、独立页返回、attach 自动重连与卸载取消、可见视图发送守卫。

配套回归：

```bash
/opt/homebrew/bin/python3.11 -B test/2026-10-07_运行对话快照/run_regressions.py
/opt/homebrew/bin/python3.11 -B test/2026-10-09_对话标签运行态判据/run_regressions.py
/opt/homebrew/bin/python3.11 -B test/2026-10-12_工作区切换重构/backend.py
node test/2026-10-12_工作区切换重构/frontend.cjs
node test/2026-10-12_工作区切换重构/startup.cjs
/opt/homebrew/bin/python3.11 -B test/2026-10-23_对话草稿与运行引导/test_backend.py
node test/2026-10-23_对话草稿与运行引导/frontend.cjs
```

2026-10-23 已执行上述回归及生产 `npm run build`（tsc、全量 stylelint、vite）。本次修改前端文件的定向 ESLint 通过。全仓库 `npm run lint` 的 i18n 审计通过，ESLint 0 errors、171 条本次未修改文件的 Prettier warnings，因 max-warnings=0 未通过。未清洗无关文件。

## 实机验收

前后端与壳一起更新后，macOS Electron 和 Windows Tauri 分别检查：

1. 连续新建多个空标签，分别发送首消息；每个对话独立显示，绑定后可关闭。
2. 在 A→B→C 快速切换，B 加载中关闭 B，最终内容与激活标签一致；迟到 B 不顶回。
3. 关闭最后一个实际对话，进入可用的新对话；关闭标签不停止后台工作。
4. 删除当前、非当前和加载中的对话，标签同步移除，重开应用后不复活。
5. 两个工作区同 ID 对话分别打开，不串标题、运行状态或草稿。
6. 设置、工作流、Git 页面返回对话；切换途中刷新主页面，最新目标保留。
7. 断网后恢复，标签同步与 attach 能重连；失败目标显示错误，可关闭或重新激活。
8. 原生窗口拖拽、macOS 红绿灯及 Windows 三大键、快捷键和关闭动画正常。

本轮没有启动第二个后端、操作真实对话或自动化视觉验收，没有构建或替换安装包；Rust 工具链在当前 PATH 不可用，Windows 原生编译未执行。离线回归通过不等于已安装应用完成更新或所有现场问题已实机验证。
