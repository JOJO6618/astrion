# setup-wsl-sandbox.ps1 — 创建 astrion WSL2+bwrap 专用沙箱发行版
#
# 用法（PowerShell，普通用户即可，无需管理员）：
#   powershell -ExecutionPolicy Bypass -File scripts\setup-wsl-sandbox.ps1
# 可选参数：
#   -DistroName   发行版名称（默认 astrion-sandbox，需与 HOST_SANDBOX_WSL_DISTRO 一致）
#   -InstallDir   发行版 VHDX 安装目录（默认 ~\.astrion\wsl-sandbox）
#   -RootfsUrl    Alpine minirootfs 下载地址（默认阿里云镜像）
#   -ApkMirror    apk 软件源镜像（默认阿里云）
#
# 设计依据：wsl2-sandbox-poc-report.md
#   - 必须是专用发行版并关闭 interop，否则沙箱内可经 cmd.exe 逃逸到宿主机；
#   - 固化公共 DNS，规避 localhost 代理导致的 WSL NAT DNS 失效；
#   - 内置国内 apk 镜像，避免官方源大包下载卡死。

param(
    [string]$DistroName = "astrion-sandbox",
    [string]$InstallDir = (Join-Path $env:USERPROFILE ".astrion\wsl-sandbox"),
    [string]$RootfsUrl  = "https://mirrors.aliyun.com/alpine/v3.21/releases/x86_64/alpine-minirootfs-3.21.3-x86_64.tar.gz",
    [string]$ApkMirror  = "https://mirrors.aliyun.com/alpine/v3.21"
)

$ErrorActionPreference = "Stop"
$env:WSL_UTF8 = "1"
# 控制台与输出流统一 UTF-8：后端以管道读取本脚本输出，默认 GBK 会导致中文乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

function Invoke-Wsl {
    # PS 5.1 下 ValueFromRemainingArguments 会把 -d/-e 等 wsl 参数误绑定到函数通用参数
    # （-e 前缀匹配 -ErrorAction/-ErrorVariable 报二义性错误），故改为单个数组传参，
    # 调用处一律：Invoke-Wsl @('-d', $Name, '-e', 'true')
    param([string[]]$WslArgs)
    # PS 5.1 坑：全局 EAP=Stop 时原生命令写 stderr（如 wsl 的 localhost 代理警告）
    # 会直接抛 NativeCommandError 终止脚本，Where 过滤来不及生效。
    # 函数内降为 Continue（函数作用域，不影响全局 Stop 对其它命令的保护）。
    $ErrorActionPreference = 'Continue'
    $output = & wsl.exe @WslArgs 2>&1 | Where-Object { $_ -notmatch "localhost 代理|localhost proxy" }
    return @{ Code = $LASTEXITCODE; Output = ($output -join "`n") }
}

Write-Host "==> [1/6] 检查 WSL 环境"
$wslStatus = Invoke-Wsl @('--status')
if ($wslStatus.Code -ne 0) {
    throw "WSL 不可用。请先启用 WSL2（wsl --install --no-distribution 或安装 Docker Desktop 后重试）。"
}

Write-Host "==> [2/6] 检查发行版 '$DistroName' 是否已存在"
$probe = Invoke-Wsl @('-d', $DistroName, '-e', 'true')
if ($probe.Code -eq 0) {
    Write-Host "    发行版已存在，跳过导入（如需重建：wsl --unregister $DistroName）"
} else {
    Write-Host "==> [3/6] 下载 Alpine rootfs"
    $rootfs = Join-Path $env:TEMP "astrion-alpine-minirootfs.tar.gz"
    if (-not (Test-Path $rootfs)) {
        Invoke-WebRequest -Uri $RootfsUrl -OutFile $rootfs -UseBasicParsing
    }
    Write-Host "    rootfs: $rootfs ($([math]::Round((Get-Item $rootfs).Length/1MB,1)) MB)"

    Write-Host "==> [4/6] 导入为 WSL2 发行版"
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    $null = Invoke-Wsl @('--import', $DistroName, $InstallDir, $rootfs, '--version', '2')
    $check = Invoke-Wsl @('-d', $DistroName, '-e', 'true')
    if ($check.Code -ne 0) { throw "发行版导入失败: $($check.Output)" }
}

Write-Host "==> [5/6] 写入沙箱配置（关闭 interop / 固定 DNS / 国内镜像）"
# 关闭 Windows 互操作：防止沙箱内经 cmd.exe 逃逸宿主机（PoC 已验证该逃逸路径）
$conf = Invoke-Wsl @('-d', $DistroName, '--', 'sh', '-c', "printf '[network]\ngenerateResolvConf = false\n\n[interop]\nenabled = false\nappendWindowsPath = false\n' > /etc/wsl.conf")
if ($conf.Code -ne 0) { throw "写入 wsl.conf 失败: $($conf.Output)" }
# 坑：首次导入的发行版内 /etc/resolv.conf 是 WSL 生成的 bind mount，直接 rm 会 EBUSY 失败
# （旧脚本未检查返回码被静默吞掉，terminate 后 generateResolvConf=false 生效，
# 不再自动生成 resolv.conf → 文件缺失 → DNS 完全失效）。必须先 umount 再重建真实文件。
$resolv = Invoke-Wsl @('-d', $DistroName, '--', 'sh', '-c', "umount /etc/resolv.conf 2>/dev/null; rm -f /etc/resolv.conf; printf 'nameserver 223.5.5.5\nnameserver 119.29.29.29\n' > /etc/resolv.conf && cat /etc/resolv.conf")
if ($resolv.Code -ne 0) { throw "写入 resolv.conf 失败: $($resolv.Output)" }
$apkRepo = Invoke-Wsl @('-d', $DistroName, '--', 'sh', '-c', "printf '$ApkMirror/main\n$ApkMirror/community\n' > /etc/apk/repositories")
if ($apkRepo.Code -ne 0) { throw "写入 apk 镜像源失败: $($apkRepo.Output)" }

# 使 wsl.conf 生效
$null = Invoke-Wsl @('--terminate', $DistroName)
Start-Sleep -Seconds 2

Write-Host "==> [6/6] 安装沙箱工具链（bubblewrap / bash / python3 / git）"
$apk = Invoke-Wsl @('-d', $DistroName, '--', 'sh', '-c', "apk update && apk add bubblewrap bash ncurses-libs python3 git")
if ($apk.Code -ne 0) { throw "apk 安装失败: $($apk.Output)" }

# 与 Windows 端 Git for Windows 默认的 core.autocrlf=true 对齐：
# Windows 检出的工作区文件是 CRLF，沙箱 git 不开 autocrlf 会把全部文件误判为已修改
# （全量 +/-），且全量内容读取+xdiff 会让 git 命令慢到触发执行超时。
$null = Invoke-Wsl @('-d', $DistroName, '-e', 'git', 'config', '--system', 'core.autocrlf', 'true')
# 9P 挂载的 inode/ctime 语义与 Windows git 写入的索引不匹配，会导致沙箱 git 对全部
# 文件做内容重读（全树 status/diff >20s）；放宽 stat 校验后只需秒级。
$null = Invoke-Wsl @('-d', $DistroName, '-e', 'git', 'config', '--system', 'core.checkStat', 'minimal')
$null = Invoke-Wsl @('-d', $DistroName, '-e', 'git', 'config', '--system', 'core.trustctime', 'false')

# 验收
$verify = Invoke-Wsl @('-d', $DistroName, '--', 'sh', '-c', "bwrap --version && bash -c 'echo bash-ok' && python3 --version && git --version")
Write-Host $verify.Output
$escape = Invoke-Wsl @('-d', $DistroName, '--', 'bwrap', '--die-with-parent', '--new-session', '--unshare-all', '--ro-bind', '/', '/', '--proc', '/proc', '--dev', '/dev', '--', 'bash', '-c', "cmd.exe /c echo escape 2>&1 || echo interop-blocked")
if ($escape.Output -match "interop-blocked|not found") {
    Write-Host "    interop 逃逸封堵验证: OK"
} else {
    Write-Warning "    interop 可能未关闭，请检查 /etc/wsl.conf 后执行 wsl --terminate $DistroName"
}

Write-Host ""
Write-Host "完成。沙箱发行版 '$DistroName' 就绪。"
Write-Host "若使用了非默认名称，请设置环境变量 HOST_SANDBOX_WSL_DISTRO=$DistroName"
