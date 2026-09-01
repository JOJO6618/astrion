# Astrion Android WebView 壳应用

这个目录是一个最小可用的 Android APK 壳工程：
- 后端仍运行在你的服务器
- APK 只承载 WebView 前端

## 1) 打开工程

Android Studio -> Open -> 选择 `android-webview-app`。

首次打开会自动下载 Gradle 依赖（需要联网）。

## 2) 配置后端地址（必须）

仓库代码中不包含真实后端域名，构建前需要通过以下任一方式注入（优先级从高到低）：

1. 命令行参数：`./gradlew assembleRelease -PHOME_URL=https://your-server.example.com`
2. `local.properties`（本机文件，不纳入版本控制）：添加一行 `HOME_URL=https://your-server.example.com`
3. 环境变量：`export HOME_URL=https://your-server.example.com`

不配置则使用占位地址 `https://agent.example.com`，App 无法连接真实服务。

## 3) 可改项

- 应用名：`app/src/main/res/values/strings.xml`
- 后端地址：见上文「配置后端地址」，最终编译进 `BuildConfig.HOME_URL`

## 4) 调试打包

- 连接手机/模拟器后，点击 Run
- 生成 release APK：
  - 配置签名环境变量 `ANDROID_KEYSTORE_PATH` / `ANDROID_KEYSTORE_PASSWORD` / `ANDROID_KEY_ALIAS` / `ANDROID_KEY_PASSWORD`
  - `./gradlew assembleRelease`

## 5) 已处理能力

- JS / DOM Storage 已开启
- Cookie 已开启
- 文件上传 (`<input type=file>`) 已支持
- 物理返回键支持网页回退
- 外部链接自动跳转系统浏览器打开

## 6) Nginx 建议

如后续出现 WebSocket 不稳定，可把 `Connection 'upgrade'` 改为官方 map 写法（按 upgrade 条件设置）。
