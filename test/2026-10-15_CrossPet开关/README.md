# CrossPet 单一开关回归

仅 Astrion 设置里的开关控制输出。默认关闭；开关存部署配置目录，所有工作区共用。CrossPet 配置只可提供自定义目录，不是第二道启用条件。

```sh
/opt/homebrew/bin/python3.11 -B -m unittest discover -s test/2026-10-15_CrossPet开关 -p 'test_*.py' -v
/opt/homebrew/bin/python3.11 -B test/2026-10-15_CrossPet开关/settings_api_check.py
/opt/homebrew/bin/python3.11 -B -m unittest discover -s test/2026-10-14_CrossPet接入 -p 'test_*.py'
/opt/homebrew/bin/python3.11 -B test/2026-10-14_CrossPet接入/runtime_check.py
```

- 12 个模块回归：缺配置默认关闭、Astrion 单独开启、CrossPet 标记不能开关输出、平台默认目录和覆盖优先级、持久化与严格布尔值、坏配置/符号链接拒绝、关闭后零写入、重新开启即时写入、关闭与正在写入线程的互斥。
- 6 个真实 Flask API 检查：默认与保存、启停、无效请求、匿名/非管理员拒绝、非 host 拒绝、Web/headless 路由注册。使用合成桌面标记和登录 nonce，不创建执行终端或启动服务器。
- 原 26 个状态回归和真实 TaskManager → 文件 → CrossPet JS 投影继续验证状态转换；真实链路不创建 CrossPet 接入配置，只打开 Astrion 开关。

全部使用本目录内临时数据，ASTRION_IGNORE_DOTENV=1；不读取真实凭据，不修改用户开关、接入目录或当前运行服务。UI 视觉由用户验收。
