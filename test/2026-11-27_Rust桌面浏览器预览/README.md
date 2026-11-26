# Rust 桌面浏览器、内联内容与预览回归

使用本地 macOS arm64 Astrion 1.0.0 目录包。构建标识为 `611b370d-b0b7-4268-81ad-296d2a7c5256`，内嵌 Rust 二进制的 SHA256 为 `5ec9c9c7984803221812999acc4354a272b8e548363aa510b9d20eff81cd5c54`。实际验收使用独立 Electron 资料、运行数据、工作区和回环模型替身。

```bash
ASTRION_TEST_APP="$PWD/desktop-electron/dist-rust/mac-arm64/Astrion.app" \
  node --test 'test/2026-11-27_Rust桌面浏览器预览/desktop_tools.test.mjs'
```

- 确认 Rust 是 Electron 子进程，工具定义包含 24 项。模拟模型实调原生导航、站点点击、截图和写入预览文件。
- 站点点击在批准前没有副作用。批准后在 Chromium 执行，截图回执可定位工作区真实 PNG。
- 浏览器关闭后工具定义保持稳定，后续调用返回关闭状态。
- 模型输出进入 `show_ui` 与 `show_html` 的真实渲染节点。工作区 HTML 文件和模型正文的回环地址都登记为预览。使用隔离预览服务读取文件。
- 通过桌面渲染器的实际 Host API 提交定时任务并手动触发，运行记录保存模型摘要。
- 执行用的是本地回环页面。Windows 守护管道和外部站点的登录、下载仍需对应环境的实机验证。
