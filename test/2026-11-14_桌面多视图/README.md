# 桌面多视图回归

## 后端（导航 v3）

```bash
/opt/homebrew/bin/python3.11 -B -m unittest discover -s test/2026-11-14_桌面多视图 -p "test_navigation_v3.py" -v
/opt/homebrew/bin/python3.11 -B -m unittest discover -s test/2026-10-23_桌面导航重构 -p "test_*.py"
```

`test_navigation_v3.py` 覆盖：v2→v3 与 v1→v3 迁移、损坏不覆盖、conv/设置/工作流/Git 去重、
关闭继任（右优先、page 回 opener）、detach/dock/reorder、renderer 所有权（attach/bind/fail）、
attach 缺失标签 409、receipt 幂等、focus 不落盘不增 revision、快照核验（page 跳过、Git 工作区）、
浏览器回收前台集合（主窗口 active + detached）。临时文件写在本目录，使用合成 lookup，不启动应用。

## 前端（桌面多视图 P1）

```bash
node test/2026-11-14_桌面多视图/frontend/v3.cjs          # 视图 attach、发送守卫、快照转发、页面返回 opener、隐藏态降频、标签拖拽排序计划等
node test/2026-11-14_桌面多视图/frontend/p2.cjs          # P2：chrome 壳层请求→命令、拖出判定、独立窗口布局切换、focusTab
node test/2026-10-23_桌面导航重构/frontend.cjs           # 事务层（传输/幂等/轮询）与 v3 store 基础
```

两者共用 `frontend/fixture.cjs`（注入伪 `astrionShell` 桥与伪 fetch，真实编译 `static/src` 的 TS）。
