# Android App 发布联动要求（完整）

> 本文由 `AGENTS.md` §8 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 8) Android App 发布联动要求（重要）

当修改前端并需要发布 Android WebView App 时，必须同步执行以下步骤（依据 `doc/android_app_release_and_update.md`，本地文档未随仓库发布）：

1. 同时更新版本信息  
   - `android-webview-app/app/build.gradle.kts`：递增 `versionCode`，更新 `versionName`
2. 同时更新更新说明  
   - `android-webview-app/APP_CHANGELOG.md`：在顶部新增当前版本说明
3. 完成修改后提醒用户运行上传脚本  
   - 在发布流程中运行：`bash ./upload_android_apk.sh`（本地私有脚本，未随仓库发布）
   - 脚本除上传 APK 外，还会同步 `app/build.gradle.kts` 与 `APP_CHANGELOG.md` 到服务器（2026-09-02 修复）

> 禁止只改前端代码而不更新版本号/更新说明，否则会导致客户端更新提示与分发信息不一致。
> 服务端 `/api/app/version` 的版本号是从服务器文件系统上的 `build.gradle.kts` 解析的（实现见 `server/status/docker.py`），**不是从 APK 包解析**——只传 APK 不同步元数据文件会导致 App 检测不到新版本。
