# Rust Agent 设置回归

`settings_http.py` 启动隔离的 Rust Web 进程，用 HTTP 设置、创建普通 Agent 对话、发送消息并检查本地模型捕获的请求及工具加载回包。测试不访问外部服务或真实对话。需提供已构建的 Rust 可执行文件：

```sh
ASTRION_HEADLESS_BIN=backend-rust/target/debug/astrion-headless \
  python3 -B test/2026-11-27_RustAgent设置生效/settings_http.py
```

Rust 单元回归：
- `preference_contract`：关闭意图保留准备事件；原生 schema 保留真参数和必填，MCP 远端定义不变。
- `skill_hints::contract`：中英文大小写、同一规则重复命中、无命中。
- `legacy::media::image_compression`：三档等比缩放、PNG MIME、原图和无法解码的输入、源文件不改。
- `system_prompt::contract` 与 `mode_notice::contract`：原有冻结及切换通知契约。

图片设置恢复旧路径图片投影：存在有效媒体存档引用时仍优先使用存档原件；动画、不支持的格式、超过解码预算或不需缩小的文件继续原样发送。验证不涉及实际厂商缓存命中或 macOS／Windows 原生隔离。
