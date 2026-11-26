# Rust Chat media HTTP contract

契约来源：`server/chat_mode/media.py`、`modules/chat_mode/media.py`、`utils/media_store.py`。

## POST /api/chat/media

- Host 登录会话；未登录 GET `401`。POST 与现有 Chat 写接口一样受 CSRF 保护；无会话写请求会被共享 Host guard 以 `403` 拒绝（CSRF 先于会话检查）。
- `multipart/form-data`，文件字段名 `media`。
- 客户端文件名、扩展名、Content-Type 不参与判断；服务端按字节嗅探。
- 成功 `200`：

```json
{"success":true,"data":{"media_id":"sha256:<64hex>","kind":"image|video","mime_type":"image/png","size":68,"sha256":"<64hex>"}}
```

- 不得返回 `store_path` / `source_path`。
- 缺失字段、空文件、伪造扩展名或类型、非图片/视频、超过 100MB、图片超过 25MB 或像素超过 1e8、AVIF/HEIC 图片均返回 `400` JSON。
- 同一字节内容必须产生同一 `media_id`，重复上传幂等。
- 存储布局与 Python `MediaStore` 相同：账号 Chat 根下 `conversations/media_store/index.json` 与 `blobs/<sha 前两位>/<sha>.<ext>`。

## GET /api/chat/media/<media_id>

- Host 登录会话；未登录 `401`。
- 成功 `200` 返回原始字节。
- `Content-Type` 使用索引登记的 `mime_type`。
- `Cache-Control: private, no-store`（严格断言，不再接受被统一改写成 `no-store`）。
- `X-Content-Type-Options: nosniff`。
- `Content-Disposition` 使用 media id（兼容 Python `send_file(download_name=media_id)`）。
- 未知、未登记、坏 ID 或索引缺失返回 `404` JSON，且绝不回退读取任意文件路径。
- 只查询当前 Chat 账号的 `conversations/media_store`；其他账号不可读取。

## Headless

同一契约在 `astrion-headless --web` 的 Host listener 上生效；登录、CSRF、401、读写和持久化行为与生产 Host 一致。

纯 headless（不带 `--web`，只启动 Agent API）不得挂载 Chat media 路由：`GET /api/chat/media/<id>` 必须直接 `404`。

## 运行

```bash
ASTRION_HEADLESS_BIN=backend-rust/target-master/debug/astrion-headless \
python3.11 -B test/2026-11-26_RustChat媒体/media_roundtrip.py
```

脚本按以下顺序验收：

1. Python 生产 Flask `/api/chat/media` 写入，并在索引中注入未知顶层/条目字段；
2. Rust headless 正常登录后 GET 读取，未知索引字段不得影响读取或阻止接管；
3. Rust POST 上传 PNG/MP4/WebM，并覆盖 JPEG/WEBP/GIF/BMP 嗅探、TIFF 与 AVIF/HEIC 拒绝、像素 1e8、图片 25MB 与视频 100MB 上限；
4. Rust 停止后 Python 生产读取接管、未知索引字段保留、账号隔离；
5. Python 建立主分支/非最新分支/草稿/未完成交接 snapshot 引用和孤儿媒体，并执行 `media_gc.collect`：只删除孤儿，两个分支、草稿和未完成 snapshot 引用的媒体保留，索引未知字段保留，并留下一个过期孤儿供 Rust GC 处理；
6. 重启 Rust 后上传触发写后 GC：过期孤儿被删除，非最新分支、草稿和未完成交接 snapshot 引用且同样过期的媒体保留，索引未知字段仍保留；Python 已删除的媒体仍为 `404`；
7. 未登录 GET 401 / 未授权写 403、坏媒体 400/404；纯 headless 不带 `--web` 时 Chat media 路由 `404`。
