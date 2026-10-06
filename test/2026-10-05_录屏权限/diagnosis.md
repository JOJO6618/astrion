# Astrion 录屏权限诊断（2026-10-05）

## 已验证的直接原因

macOS TCC 日志在用户实际截图失败的 14:00 时段，明确记录：

```text
subject=com.astrion.desktop
Failed to match existing code requirement for subject com.astrion.desktop and service kTCCServiceScreenCapture
```

日志中的旧授权签名要求：

```text
identifier "com.astrion.desktop" and certificate leaf = H"432418fdd9f0086a3fe93a219b6662e57abeb9e0"
```

当前安装的 `/Applications/Astrion.app` 指定签名要求：

```text
identifier "com.astrion.desktop" and anchor apple generic and certificate leaf[subject.CN] = "Apple Development: 8613991190618 (LUX53G96H4)" and certificate 1[field.1.2.840.113635.100.6.2.1]
```

当前应用 `codesign --verify --strict` 成功（退出码 0）。以日志中旧授权要求重新验证当前应用，退出码 3：`code failed to satisfy specified code requirement(s)`。

TCC 将截图 helper 正确归属到正在运行的 `/Applications/Astrion.app`（主进程 PID 88507），请求进程为 `Contents/Resources/quick-entry-capture`。因此已确定当前录屏授权记录不接受当前签名身份；蓝色系统开关不能保证该历史记录对当前版本有效。

## 建议处理（尚未执行）

经用户同意，只重置 Astrion 的 ScreenCapture 授权记录：

```sh
tccutil reset ScreenCapture com.astrion.desktop
```

然后由用户退出并重新打开 Astrion，在设置页重新申请录屏权限，依 macOS 提示重新打开应用后验证。该命令不重置输入监控，也不重置其他应用的权限。

本次未修改截图实现，未执行权限重置；实际截图是否恢复仍待重新授权后验证。
