# 工作区文件 / Chat 文件读取夹具交接

状态：**暂停开发与测试**。只完成了夹具源码的部分修改，当前 GUI 夹具不能作为通过结论使用；没有重建共享 target。

## 已有产出

### 1. GUI 文件 / `/api/files` / `/api/focused` / 搜索夹具

路径：`test/2026-11-26_Rust工作区文件/file_api_roundtrip.py`

当前实现方式：

- Python：真实 `server.app` + `server.host_startup.initialize_host`，Werkzeug `make_server` 独立监听器；真实 Host 登录、Cookie、CSRF。
- Rust：真实 `astrion-headless --web`，同一 `DATA_DIR` / workspace。
- 同一个浏览器请求序列分别打 Python 和 Rust，规范化时间戳后逐字段严格比较。
- 登录后的写操作都带 CSRF；未登录全部断言 `401`，无 CSRF 的写断言 `403`。
- Python 侧显式断言 `/api/files` Host 契约：
  - `path` 等于 workspace；
  - `files=[]`、`folders=[]`、`tree={}`、`total_files=0`、`total_size=0`；
  - `unavailable=True`，`message` 非空。
- `/api/focused` 严格断言 `{}`。
- `/api/project/files/search` 覆盖：空查询根目录浅扫、大小写、目录前缀、中文、max-depth 6 外的文件、无结果、limit 下限/上限。
- `/api/gui/files/entries` 覆盖：根目录、子目录、绝对 workspace 内路径、隐藏目录、文件可编辑性、目录/文件类型与大小。
- `/api/gui/files/text` 覆盖：读取、UTF-8、绝对 workspace 内路径、workspace 内符号链接、写后读、2 MiB 上限文件、>2 MiB 拒绝、目录/缺失/非 UTF-8、写缺失字段。
- `/api/gui/files/download` 覆盖：普通二进制下载、`Content-Disposition`、workspace 内符号链接、目录 `410`、缺失文件。
- 路径边界：`../`、绝对 workspace 外路径、workspace 外符号链接、workspace 内符号链接。
- 写后下载：代码改动未完成，见“未完成/已知问题”。

### 2. 早期已验证证据

在较早版本（没有 `exact-limit.txt`、链接专项、严格错误文本恢复）上：

- `--python-only` 曾通过：`Python Host file oracle collected.`
- 使用 `backend-rust/target-master/debug/astrion-headless`（12:55 构建）跑完整 Python/Rust：
  - 在把错误文本临时归一化为“非空错误”后，**唯一剩余结构差异**是：
    - Rust `/api/gui/files/entries` 根目录没有 `escape-link`；
    - Python 根目录有 `escape-link`，且旧 Python 跟随外部目标读取 metadata。
  - 当时采用严格错误文本时还出现过大量中文错误文案差异，例如：
    - Rust `路径超出工作区` vs Python `路径越界`
    - Rust `文件不存在或无法访问` vs Python `文件不存在` / `目录不存在`
    - Rust `文件超过 2 MiB` vs Python `文件过大，暂不支持直接编辑`
    - Rust `文件不是 UTF-8 文本` vs Python 带 Python codec 细节的 `文件不是 UTF-8 编码: ...`
- 用户在之后明确授权：外部链接列表行为按安全修正验证，Rust 显示链接自身条目，`size` 用 `lstat`，`is_editable=false`；打开/下载/保存继续拒绝。**该专项断言已写好但尚未接入运行流程。**

## 当前源码实际状态（重要）

`file_api_roundtrip.py` **当前不完整**：

1. `exercise()` 已改成 `return result, outside_link`，但 `run()` 两处仍写成：
   - `python_result = exercise(...)`
   - `rust_result = exercise(...)`
   还没有解包，直接运行会报 tuple/attribute 类型错误。
2. `assert_outside_link()` 已定义，但还没有对 Python/Rust 结果分别调用。
3. `exact-limit.txt` 已创建，根搜索总数已改为 14；但严格 2 MiB 的读取/写入/写后读断言尚未加入。
4. 写后下载（`download_after_write`）尚未加入。
5. 错误响应已恢复为**保留 Python 原始错误 payload 做严格比较**，不再归一化为非空字符串；这意味着上面列出的错误文案差异会重新出现在 full run 中。
6. `.astrion` 处理：Python 生产 terminal 会反复重建 workspace 的 `.astrion`；夹具在 Python 跑完后复制它，reset 后恢复到 Rust 工作区，保证两边看到相同内部目录。
7. 当前测试临时产物：`test/2026-11-26_Rust工作区文件/run.log`（上一次失败输出，不是契约文件）。

## 建议的下一步（按顺序）

1. 修复 `run()` 的两个 `exercise(...)` 调用，解包 `(result, outside_link)`。
2. 对 Python 和 Rust 分别调用 `assert_outside_link(outside_link, workspace, outside)`：
   - 断言条目 `name/path/type` 为 `escape-link` / `escape-link` / `file`；
   - `size == os.lstat(workspace/'escape-link').st_size`；
   - `size != outside/outside.txt` 的目标文件大小；
   - `extension == ''`、`is_editable is False`；
   - 打开/下载/保存仍由现有 `text_errors.symlink`、`text_write_errors.symlink`、`download_errors.symlink` 验证拒绝。
3. 加入 2 MiB 精确边界：
   - `exact-limit.txt` 读取成功且内容长度为 2 MiB；
   - 写入恰好 2 MiB 成功；
   - 写后读内容/哈希一致；
   - `too-large.txt` 与 >2 MiB 写入继续 400。
4. 加入写后下载：
   - POST 写 `docs/guide.md` 后，下载同一路径必须返回写入字节；
   - Rust 写后由 Python 再次下载同一路径，确认跨进程。
5. 决定错误文案是否作为严格契约：
   - 当前源码会严格比较原文；若不要求文案完全一致，应只归一化 `success=false` + `error` 非空，并明确记录这是唯一放宽项；
   - 若要严格，则需要生产侧统一 Rust 中文文案与 Python `tr()`，包括 UTF-8 decode 细节。
6. 等待主侧通知可用的共享二进制后再跑；**不要自行重建 `backend-rust/target-master` 或共享 target**。

## 三个普通读取入口：尚未开始写夹具

用户新增要求（生产代码由主侧负责，夹具只写测试）：

- `GET /api/download/file`
- `GET /api/file/content`
- `GET /api/conversations/media/{id}`

已确认的 Python 源码位置：

- `server/chat/files.py`：`/api/download/file`、`/api/file/content`
- `server/conversation.py:890`：`/api/conversations/media/<path:media_id>`
- Rust 媒体读取基础：`backend-rust/src/legacy/media.rs`

要求覆盖（尚未实现）：

- Range 请求与 `206 Partial Content`、`Content-Range`、`Accept-Ranges`；
- 条件请求（`If-None-Match` / `If-Modified-Since` / `304`）；
- 会话作用域（当前会话 / 其他会话 / 不存在的会话）；
- 工作树路径（workspace 根内、worktree 根内、跨根拒绝）；
- HTML/SVG 的 Content-Type / inline 展示与安全头；
- 已登记 Chat media 的读取、未知/未登记 media `404`；
- 越界、绝对路径、`..`、指向 workspace 外的链接拒绝；
- 全部合成数据，不触碰真实工作区或账号。

目前**没有**为这三个入口创建任何文件；不要把它们误认为已覆盖。

## 复跑命令

```bash
# 仅 Python oracle（当前源码需先修复上述元组解包）
python3.11 -B test/2026-11-26_Rust工作区文件/file_api_roundtrip.py --python-only

# 主侧通知新二进制可用后
ASTRION_HEADLESS_BIN=backend-rust/target-master/debug/astrion-headless \
python3.11 -B test/2026-11-26_Rust工作区文件/file_api_roundtrip.py
```

测试目录只有：

- `test/2026-11-26_Rust工作区文件/file_api_roundtrip.py`
- `test/2026-11-26_Rust工作区文件/run.log`（临时失败日志）
- 尚未补的 `README.md`
