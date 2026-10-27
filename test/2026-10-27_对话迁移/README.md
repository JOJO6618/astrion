# 对话迁移、跨机器 ZIP 和默认交接回归

全部使用临时工作区、合成对话和隔离数据根，不读取真实对话、凭据或 `.env`。测试不启动现有服务，不调用真实模型，不进行浏览器视觉验收。

```bash
set -o pipefail
ASTRION_IGNORE_DOTENV=1 \
ASTRION_DATA_ROOT="$PWD/cache/conversation-transfer-test-runtime" \
/opt/homebrew/bin/python3.11 -B -m unittest discover \
  -s test/2026-10-27_对话迁移 -p 'test_*.py'
```

覆盖：

- ZIP 原始 JSON、历史消息／工具配对／加密思考／冻结提示词不变，末尾只追加一次 user 通知。
- 跨数据根媒体恢复，并通过真实 MediaMixin 重建相同内容；旧媒体路径映射及缺资源拒绝。
- 内部迁移不打 ZIP、同库不读／复制 blob、保留对话 ID、源恢复副本及源运行实例解绑。
- Chat 全树、旧分支、媒体、完整交接目录和再次导出；材料发现只读可信工作区。
- 目标已写入后回执失败／源退役失败的重试；目标被替换、历史被编辑或源产生新进度时保留源。
- 桌面标签保留 key 和数量，撤销旧导航代次。
- 认证入口、活动任务拒绝、无效 ZIP 不发布、缺模型报错、显式模型选择的归档协议约束。
- ZIP 校验和／穿越、目标符号链接、未携带的旧本地媒体路径、下载目录符号链接和临时导出过期范围。
- 默认交接工作区复用，pending 直接 accept，忽略前端指定路径／工作区。

配套验证：

```bash
ASTRION_IGNORE_DOTENV=1 \
ASTRION_DATA_ROOT="$PWD/cache/conversation-transfer-chat-regression" \
/opt/homebrew/bin/python3.11 -B -m unittest discover \
  -s test/2026-10-25_Chat模式 -p 'test_*.py'

ASTRION_IGNORE_DOTENV=1 \
ASTRION_DATA_ROOT="$PWD/cache/conversation-transfer-navigation-regression" \
/opt/homebrew/bin/python3.11 -B test/2026-10-23_桌面导航重构/run_regressions.py

node test/2026-10-25_Chat模式/shared_ui.cjs
./node_modules/.bin/tsc --noEmit
node scripts/i18n_audit.mjs
npm run build --silent 2>&1 | tail -n 5
```

2026-10-27 实测：本目录迁移 31 项通过；Chat 51 项通过；桌面导航 35 项 Python 用例及 17 项前端导航用例通过；共享 UI 的树投影、媒体、工具状态、6 个 SFC 编译及空／有消息 Shell 渲染通过。类型检查、i18n 审计、改动文件 ESLint／stylelint 和生产构建通过。

顺手修正旧测试夹具与现行契约的差异：压缩测试显式配置阈值、preferences_dir 和合成 api_client，摘要 mock 接收关键字参数；共享 UI fixture 使用真实 settings.settings 结构。未修改压缩产品逻辑。Chat 停止测试日志仍可能输出 pending task／ContextVar 回收警告，断言通过不代表该异步回收警告已解决。

仍需实机验收：真实机器间传输、模型续聊、桌面原生另存、Android 下载及用户视觉。构建只有常规的大 chunk 体积提示，不据此宣称原生安装包或服务器已更新。
