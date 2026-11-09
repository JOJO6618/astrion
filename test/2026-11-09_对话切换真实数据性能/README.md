# 对话切换真实数据性能基准

结论、数据与限制见 [REPORT.md](REPORT.md)。本目录只新增测试与汇总结果，没有业务修复。

## 运行顺序

在仓库根目录执行：

```bash
/opt/homebrew/bin/python3.12 -B test/2026-11-09_对话切换真实数据性能/backend_benchmark.py
node test/2026-11-09_对话切换真实数据性能/frontend_benchmark.cjs
node test/2026-11-09_对话切换真实数据性能/browser_benchmark.mjs
BENCH_HIDDEN_ONLY=1 node test/2026-11-09_对话切换真实数据性能/browser_benchmark.mjs
```

前两步在本机沙箱内可运行。浏览器步骤需要读取 Playwright 安装资源及创建临时浏览器进程；本次沙箱拒绝读取 ICU 数据文件，经单次完全访问授权后执行。遇到同类限制时应申请该次执行权限，不修改生产构建配置或移动浏览器资源规避限制。

依赖：现有 Python 项目依赖、Vue、TypeScript、esbuild、@vue/compiler-sfc、Playwright 及已安装 Chromium/WebKit；本轮没有安装依赖，也不需要启动 Web/headless 服务。

## 脚本职责

- `benchmark_support.py`：配置隔离、只读取样、复制、RLock 计时、真实投影语义重建。
- `backend_benchmark.py`：真实存储与快照方法的单线程基准及有限并发保存压力。
- `frontend_benchmark.cjs`：当前历史转换、真实响应式动作计算和浏览器输入生成。
- `display_filter.cjs`：提取当前 ChatArea 的显示过滤表达式并调用真实消息合并方法。
- `browser_entry.ts`：当前组件挂载、nextTick 和两帧计时入口。
- `browser_benchmark.mjs`：编译真实 Vue 组件，隔离非核心设置与网络依赖，运行两个浏览器引擎的普通／隐藏通知案例。

## 数据与产物

样本位置和 ID 在 `benchmark_support.py` 中显式配置。本轮源为桌面数据目录下三份真实对话，原文件只读，测试保存仅写副本。源码导入前设置 `ASTRION_IGNORE_DOTENV=1`，运行数据和日志均隔离。

带真实对话内容的文件只落到已 Git 忽略的 `_experiments/conversation-switch-benchmark-20261109/`。不要将该目录内的输入或副本加入 Git。脚本重复运行时覆盖该实验目录的对应文件，不修改原始对话。

`results/` 是本轮归档的汇总数据，不含消息正文或工具参数。脚本新的输出先写 `_experiments/`，复核后再更新归档，防止将真实输入误归档。

## 解释限制

- 离线重建投影不是生产进程的实时内存状态，无法还原所有监控快照、原始流式分块及未落盘内容。
- 保存线程采用有限连续写入，是压力条件，不代表实际生产保存频率。
- 浏览器只挂载真实内容组件，不是完整应用导航；并发流式更新、整页观察器和真实 Electron 壳未装配。组件计时也不包含输入传递与解析。
- 不把这些结果表述为已复现三四分钟或已修复问题。
