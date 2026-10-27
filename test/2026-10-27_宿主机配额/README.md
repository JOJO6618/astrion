# 宿主机应用配额回归

问题：Chat 引入账号级配额后，宿主机虚拟身份没有普通用户记录，`GET /api/usage` 返回 404。宿主机请求应经过正常认证，再返回 `success: true, applicable: false, data: null`；普通账号继续读取账号级真实统计，不创建执行工作区。

## 执行

在仓库根目录运行：

```bash
ASTRION_IGNORE_DOTENV=1 \
ASTRION_DATA_ROOT="$PWD/cache/host-usage-quota-regression" \
/opt/homebrew/bin/python3.11 -B -m unittest discover \
  -s test/2026-10-27_宿主机配额 -p 'test_*.py'

node test/2026-10-27_宿主机配额/quota_polling.cjs
```

## 已验证

- 4 个 Flask 接口用例：宿主机返回不适用且不获取 tracker/工作区；宿主机标记不能绕过认证；普通账号真实计数与上限跨工作区共享；缺失普通账号仍返回 404。
- 4 组真实 Pinia store 场景：初始化请求去重、宿主机永久停止本页轮询并清除残留账号状态、普通账号继续刷新、停止/重启以及 JSON 解析期间的旧响应隔离。
- 2 种实际 TokenDrawer SFC + Vue SSR 状态：宿主机显示不适用且不调用额度数值格式化；普通账号仍显示三类真实额度。
- 测试使用合成会话和临时数据，不读取真实用户 Cookie、凭据或对话。

页面视觉、现有 8091 进程重新加载后的实际会话行为由用户验收；这些离线回归不等同于对现有运行页面的端到端验证。
