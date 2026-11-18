# 桌面更新策略契约

本文约束 Windows / macOS Electron 的更新策略、启动门槛、构建身份和官网策略服务。修改相应机制前必读。2026-11-14 已完成源码接线与阅读校对；按用户要求未运行测试、构建、部署或发布，本文不代表实机验收通过。

## 共用实现与启动顺序

两平台桌面壳均位于 `desktop-electron/`，版本和发布说明仍独立维护。策略客户端为 `src/update-policy.js`，启动门槛为 `src/update-policy-window.js`，启动编排为 `src/lifecycle.js`。`desktop/` 中保留的运行时准备、图标和 Windows 原生模块不因此删除。

正式包启动顺序固定为：启动本机控制桥 → 请求并裁定更新策略 → 必要时只显示独立更新窗口 → 获准后处理数据迁移 → 启动 Python 后端 → 启动快捷对话和主窗口。命中强制规则时，主窗口、迁移、Python 后端和快捷对话都不能提前启动。应用激活和第二实例优先聚焦更新窗口，关闭窗口即退出；策略解除时保护关窗到正常建窗之间的过渡，避免最后一个窗口关闭事件提前退出应用。正式包不可通过 `--quick-debug` 绕过此门槛；未打包开发态不请求远端策略。

启动页为本机控制桥提供的独立 HTML/CSS/JS，不依赖 Python、Vue、Vite manifest 或主应用资源。采用系统语义色和系统语言（中文或英文）；文案复用 `static/src/locales/{zh-CN,en-US}/updatePolicy.json`，打包至 `update-policy-locales/`。窗口隔离上下文、禁用 Node，退出和官网下载操作校验本次窗口随机令牌。

## 请求和响应

客户端只请求固定端点：

```text
GET https://astrion.cyjai.com/downloads/update-policy?current_version=0.10.0&build_id=EXAMPLE_BUILD_ID&platform=win32&arch=x64
```

| 请求字段 | 含义 |
| --- | --- |
| `current_version` | 当前安装版本，完整 SemVer |
| `build_id` | 当前安装包内的独立构建编号；相同版本的不同补包必须可区分 |
| `platform` | `win32` 或 `darwin` |
| `arch` | `x64` 或 `arm64` |

成功响应示例（演示数据，时间不代表线上策略）：

```json
{
  "schema": 1,
  "client": {
    "version": "0.10.0",
    "build_id": "EXAMPLE_BUILD_ID",
    "platform": "win32",
    "arch": "x64"
  },
  "id": "affected-build-manual",
  "required": true,
  "delivery": "manual",
  "latest_version": "0.10.1",
  "reason": "此构建需要从官网下载新版安装。",
  "download_url": "https://astrion.cyjai.com/",
  "issued_at": "2026-11-14T12:00:00+00:00",
  "expires_at": "2026-11-15T12:00:00+00:00"
}
```

响应 `client.version` 对应请求 `current_version`，其余身份逐项原样回传。客户端必须校验完整身份、协议、动作、有效期和官方 URL；拒绝身份不符、已过期或签发时间超过本机时间五分钟的响应，有效期跨度最多七天。网络请求超时五秒，不携带 Cookie，禁止重定向，解析前限制正文为 32,768 字符。`issued_at` / `expires_at` 是 HTTPS 服务返回的时间字段，不是离线密码学签名；本机时钟会影响有效期判断。

`download_url` 仅允许 `https://astrion.cyjai.com`，端口为空或 443，不允许用户名和密码。规则原因以纯文本展示。官网地址既在策略客户端验证，也在普通更新前端验证，不接受任意第三方下载域名。

## 两项策略独立生效

| `required` | `delivery` | 行为 |
| --- | --- | --- |
| `false` | `auto` | 正常使用，可在软件更新中自动更新 |
| `false` | `manual` | 正常使用，更新时只提供官网入口 |
| `true` | `auto` | 启动停在更新页，可自动更新、重新检查、退出 |
| `true` | `manual` | 启动停在更新页，只能前往官网安装、重新检查、退出 |

`manual` 同时约束界面和原生 `/update/install`：界面隐藏自动更新/重试，原生安装前强制刷新策略并拒绝 manual 请求，返回 409。若刷新期间规则变更，页面重新读取本机策略状态并切换按钮，不能仅靠隐藏按钮实施限制。自动更新仍由既有 electron-updater 和安装流程完成，此服务不替代安装包验签。

`required + auto` 的目标必须高于客户端版本，并且不能高于官网已发布版本。`required + manual` 允许同版本重装，但目标也必须已经发布；同版本不同构建应使用 manual，electron-updater 不会仅凭构建编号自动安装同版本包。任何匹配条件都不会凭空提供某架构安装包，发布时必须确认清单及目标包实际可用。

普通应用中的更新对话框复用 `server/status/desktop_update.py`、`stores/desktopUpdate.ts` 和 `SoftwareUpdateDialog.vue`。命中 required 时自动显示且不能“稍后”关闭。静态版本清单失败时，仍保留已取得的有效 required/manual 策略；显示版本优先用当次静态清单，清单失败才回退策略目标。清单请求也携带身份，electron-updater 请求附加 `X-Astrion-Version`、`X-Astrion-Build-Id`、`X-Astrion-Platform`、`X-Astrion-Arch` 请求头。

## 首次失败、缓存与有效期

- 首次请求失败，且没有仍有效的策略缓存：允许启动。接口未部署、网络异常或无效响应均不凭空制造强制规则。
- 请求失败，但有完整身份匹配且未过期的缓存：继续执行缓存策略，包括强制更新和仅官网下载限制，不能把服务故障当作解除限制。
- 磁盘与内存均有有效策略时，回退选择 `issued_at` 较新的策略；磁盘写入失败不丢弃本次内存策略。
- 正常取得并通过校验的新响应可覆盖旧策略，包括显式解除强制规则。
- 缓存逐项绑定版本、构建、平台和架构，升级安装后不继承另一身份的限制；旧包缺少编号时为 `legacy`，但此前未实现协议的已发布客户端不会自行开始请求策略。
- 缓存位置为 Electron `userData/update-policy-cache.json`，采用同目录临时文件与 rename 写入。策略日志复用桌面 updater 文件日志。
- 原生每次读取当前策略都检查到期；重新检查失败且缓存已过期则放行。更新页不设置到期自动关窗定时器，用户可点击重新检查。普通前端使用最近检查快照，后端最多缓存 30 秒；前端不靠非响应式的 `Date.now()` getter 假装自动解除，重新检查为界面刷新依据。

默认规则 TTL 为一天（86,400 秒），可配置 60–604,800 秒。需结合最长离线限制期选择 TTL，不能描述为无限期锁定。

## 官网规则配置

独立服务读取 `config/desktop_update_policy.json.example` 同结构的 JSON。示例中的所有规则默认 `enabled: false`，启用前替换真实构建和目标版本，并确认对应包已发布。

```json
{
  "schema": 1,
  "ttl_seconds": 86400,
  "rules": []
}
```

| 规则字段 | 匹配/动作 |
| --- | --- |
| `id` | 非空标识，最多 128 字符 |
| `enabled` | 为 `false` 时跳过，缺省启用 |
| `platforms` / `architectures` | 允许的平台/架构字符串数组 |
| `versions` / `build_ids` | 精确版本/构建字符串数组 |
| `min_version` | 版本下界，包含边界 |
| `max_version_exclusive` | 版本上界，不包含边界 |
| `valid_until` | 带时区的 ISO 时间；到期规则跳过，响应到期时间不会越过此值 |
| `required` / `delivery` | 是否必须更新及 auto/manual，缺省 false/auto |
| `target_version` | 策略目标版本，缺省该平台已发布版本 |
| `reason` / `download_url` | 原因纯文本和官方 HTTPS 地址 |

一个规则内的条件按 AND 组合；按文件顺序选择第一个匹配且未过期的规则，不合并多条规则。精确构建规则应放在宽泛版本范围之前。未匹配任何规则返回正常 false/auto 策略，ID 为 `default`。版本范围按 SemVer 比较，包含预发布优先级，不用字符串大小比较版本。

判定函数位于 `modules/desktop_update_policy.py`，不发网络请求、不启动执行实例。服务逐请求读取配置和平台静态清单：macOS 为 `latest-mac.json`，Windows 为 `latest-windows-x86_64.json`。缺配置、损坏配置或清单、无效匹配规则导致 503，不伪造成功的 allow 响应覆盖客户端的有效强制缓存。规则文件应先写临时文件再原子替换，避免读取半截配置；所有变更仍须通过配置管理和发布授权完成。

## 独立服务部署

官网需提供动态策略接口，给静态清单加字段不足以按请求身份匹配。服务入口是 `scripts/desktop_update_policy_server.py`，仅监听回环地址，默认 8098。部署时使用独立、最小依赖环境（Python + Flask 及上述模块），不要导入桌面完整业务后端、复制真实用户数据或开启第二个 full/headless 后端。

以下仅为部署命令说明，本轮未执行：

```bash
python3 scripts/desktop_update_policy_server.py \
  --downloads /var/www/astrion/downloads \
  --rules /var/lib/astrion-release/update-policy.json \
  --port 8098
```

服务须独立常驻并由服务管理器负责启动和恢复；生产可通过 WSGI 装配 `create_app(downloads, rules_file, log_file)`，不要将 Flask 开发服务器作为公开入口。日志默认 `~/.astrion/release-policy/logs/policy.log`，可用 `--log` 指定，5 MB 轮转并保留三份；日志目录需可写，清单和策略配置只需只读。

Nginx 精确路由示例：

```nginx
location = /downloads/update-policy {
    proxy_pass http://127.0.0.1:8098;
    proxy_cache off;
    add_header Cache-Control "no-store" always;
}
```

精确路由必须先于静态下载目录逻辑生效，原样转发查询参数；公共 HTTPS 和证书仍由官网反向代理负责。服务响应 `Cache-Control: no-store`，不在代理/CDN 缓存按客户端身份变化的响应。

部署顺序：先发布只读服务和空规则配置并配置 HTTPS 路由，再完整构建并发布支持协议的新客户端，最后按实际版本/构建启用规则。旧版桌面客户端没有这套请求与启动门槛，服务端无法追溯赋予其强制更新能力。此次源码修改未获得策略服务部署或新客户端发布授权，现有官网补包不包含这些新增源码。

## 构建编号和完整打包

`desktop-electron/build/build-id.cjs` 为每次构建提供 UUID。可用 `ASTRION_BUILD_ID` 显式指定 1–128 字符标识，字符仅限字母、数字、`.`、`_`、`+`、`-`；不同构建应唯一，并随发布记录保存。

- Windows：`scripts/build_windows.mjs` 生成并透传编号，`electron-builder.win.cjs` 写入打包元数据 `astrionBuildId`，构建回执记录 `build_id`。
- macOS：`npm run build` / `npm run build:dir` 经 `scripts/run_builder.mjs` 生成编号，再以 `-c.extraMetadata.astrionBuildId=<uuid>` 传入 electron-builder 写入打包元数据。**不要写回 `electron-builder.yml` 的 `extraMetadata`**：electron-builder 不展开该字段里的 `${env.X}`，会把这个字面量原样写进包内 `package.json`（0.10.4–0.10.9 的 macOS 包就是这么坏的）。
- **历史包遗留**：已发出的 macOS 0.10.4–0.10.9 包里 `astrionBuildId` 是字面量 `${env.ASTRION_BUILD_ID}`，服务端 `evaluate_policy` 的 build_id 字符校验已额外允许 `$` `{` `}`；否则这些客户端永远拿 503、按「策略不可用」放行，强制更新对它们完全不生效。新构建已用 `--dir` 验证包内为真实 UUID。
- 壳读取已打包元数据，将身份通过 `ASTRION_DESKTOP_BUILD_ID`、`ASTRION_DESKTOP_PLATFORM`、`ASTRION_DESKTOP_ARCH` 交给内嵌后端，以 `ASTRION_DESKTOP_POLICY_PROTOCOL=1` 声明桥端协议支持。

本次修改涉及主进程、共享文案、前端和内嵌后端，必须完整重建相应平台的客户端。不能沿用旧 `win-unpacked` 仅通过 `--prepackaged` 重编 NSIS 来声称包含策略能力；前轮仅安装器补包的交付方式不适用于本次改动。

本轮只进行了源码和文档编辑、阅读校对；测试、构建、服务部署、新客户端发布及 Win/Mac 实机验收均未执行，后续不能把此前旧包更新成功的结果作为本功能已验证的证据。
