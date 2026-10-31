# ChatGPT 订阅额度面板与查询回归

所有测试使用合成凭证、隔离数据目录与 mock 网络，不读取真实账号，也不请求外网。

```sh
/opt/homebrew/bin/python3.11 -B test/2026-10-31_ChatGPT额度/run_regressions.py
node test/2026-10-31_ChatGPT额度/frontend.cjs
node test/2026-10-31_ChatGPT额度/panels.cjs
node test/2026-10-31_ChatGPT额度/token_interaction.cjs
node test/2026-10-27_宿主机配额/quota_polling.cjs
```

- Python：按账号缓存与连接复用、代理变化、并发查询、只读连接失败重试、不重试兑换 POST、凭证变更与兑换失效缓存、同步刷新竞争，以及既有提供商多账号回归。
- frontend：共享请求去重、首次响应触发 Vue 更新、旧账号响应晚到、缓存/强制刷新/截止、当前订阅模型账号覆盖默认账号、普通模型与未知模型选择首个已登录订阅账号、隐藏停止轮询和管理员边界。
- panels：编译真实 composable 与 SFC，验证宿主机上下结构、容器原四格、账号 hash、两个已用百分比与重置日期、点击大面板进度条、加载与错误状态。模板需顶层解构 computed refs，禁止用纯值 composable stub 掩盖解包问题。
- token_interaction：执行真实 InputComposer 交互块并编译真实卡片 SSR，验证鼠标/触屏/键盘/外部点击/卸载，以及悬停无条、点击有条、错误和加载时保留原 Token 信息。

产品约定：使用 ChatGPT 订阅模型时跟随模型所属账号；使用其他模型时同样显示额度，选择账号列表中第一个已登录订阅。悬停小卡只显示百分比数字与重置时间，点击大面板或手机点击态显示百分比条。

真实验收由用户完成：后端变更加载后，查看宿主机用量面板；切换不同订阅账号模型、普通 API 模型；检查桌面 hover/点击与手机点击；实际检查原先偶发的 TLS 握手超时是否仍发生。离线回归通过不代表代理网络或远端服务已验证稳定。
