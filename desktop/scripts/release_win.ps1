# Astrion 桌面端 Windows 一键发布脚本（macOS 半见 release_mac.sh）
#   版本号同步 -> 前端构建 -> prepare-backend + tauri build -> 手动补 updater 签名
#   -> keynum 校验 -> changelog 提取更新说明 -> 上传 exe/sig/notes -> 服务器 regen -> 公网验证
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File desktop/scripts/release_win.ps1 [版本号]
#   缺省读 tauri.conf.json 的 version；显式传入时会同步写回 tauri.conf.json / Cargo.toml / package.json。
#
# 前置条件：
#   1. 更新签名私钥 %USERPROFILE%\.astrion-desktop-keys\updater.key（同 mac 半，一次生成）
#   2. 服务器 SSH 免密 59.110.19.30
#   3. desktop/DESKTOP_CHANGELOG.md 顶部已写好本版本小节（发布脚本提取为 release-notes-windows.txt）
#   4. 内嵌运行时就位（desktop/src-tauri/runtime/python/python.exe）
#
# 更新说明按平台分文件（2026-09-29 起 win/mac 两套版本号与日志分开维护）：
#   Windows → release-notes-windows.txt；macOS → release-notes-macos.txt
#   服务器 regen_manifest.py 各取各的，平台文件缺失时才回退共享的 release-notes.txt。
#   （此前两边共用 release-notes.txt，Windows 更新弹窗会显示 mac 的文案）
#
# 与 mac 半的关键差异（Windows 特有坑，勿改）：
#   - Windows 不允许空值环境变量（$env:X="" 是删除语义），TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""
#     无效会让 bundler 停在密码 prompt 无限等待。因此本脚本【不设私钥环境变量】，tauri build
#     结束时 bundler 因缺私钥报错退出（exe 已正常产出），随后用 tauri signer sign -p "" 手动补签。
#   - 脚本全程用 $LASTEXITCODE 判断退出码（不要用 cmd 批处理的 %ERRORLEVEL%——整行解析时预展开，
#     失败也会读到旧值 0，0.3.1 发布曾因此误判构建成功）。
#   - 路径一律基于 $PSScriptRoot 计算绝对路径，避免"当前目录已切到 desktop 又写 desktop\xxx"的重复前缀错。

param([string]$Version = "")

$ErrorActionPreference = "Stop"
$RepoRoot   = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$DesktopDir = Join-Path $RepoRoot "desktop"
$TauriConf  = Join-Path $DesktopDir "src-tauri\tauri.conf.json"
$CargoToml  = Join-Path $DesktopDir "src-tauri\Cargo.toml"
$PkgJson    = Join-Path $DesktopDir "package.json"
$Changelog  = Join-Path $DesktopDir "DESKTOP_CHANGELOG.md"
$KeyFile    = Join-Path $env:USERPROFILE ".astrion-desktop-keys\updater.key"
$LogDir     = Join-Path $RepoRoot "cache"
$Server     = "59.110.19.30"
$DestDir    = "/var/www/astrion/downloads/"
$BundleDir  = Join-Path $DesktopDir "src-tauri\target\release\bundle\nsis"

# ── 版本号：显式参数 > tauri.conf.json；参数时同步写回三处 ──
function Set-VersionText($File, [string]$Pattern, [string]$Replacement) {
  # 编码：显式 UTF8 读取（无 BOM 的 UTF-8 文件被 Get-Content 默认按 ANSI/GBK
  # 解码会乱码，0.3.2 踩实）；写回无 BOM UTF-8（JSON/TOML 解析器对 BOM 敏感）。
  # 读入统一转 LF 再匹配（编辑器可能写成 CRLF，原 pattern 行尾 ` *$` 不匹配 \r）。
  # Replacement 不得用 [regex]::Escape——替换串里的 ${1} 是组引用语义，
  # Escape 会把组引用变成字面文本写入文件（0.3.2 踩实）。
  $text = (Get-Content $File -Raw -Encoding UTF8) -replace "`r`n", "`n"
  $newText = [regex]::Replace($text, $Pattern, $Replacement, 1)
  if ($newText -eq $text) {
    # 幂等：匹配成功但替换后无变化 = 当前版本已是目标值（重跑场景），跳过；
    # 只有匹配失败才是真的"未找到版本号行"。
    if ([regex]::IsMatch($text, $Pattern)) {
      Write-Host "    $File 版本号已是目标值，跳过"
      return
    }
    throw "在 $File 中未找到版本号行（pattern: $Pattern）"
  }
  [System.IO.File]::WriteAllText($File, $newText, (New-Object System.Text.UTF8Encoding($false)))
}

if ($Version -eq "") {
  $Version = ((Get-Content $TauriConf -Raw | ConvertFrom-Json).version).Trim()
} else {
  if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "非法版本号: $Version（期望 x.y.z）" }
  Set-VersionText $TauriConf '(?m)^(\s*"version":\s*")[^"]*(",) *$'     ("`${1}" + $Version + "`${2}")
  Set-VersionText $PkgJson   '(?m)^(\s*"version":\s*")[^"]*(",) *$'     ("`${1}" + $Version + "`${2}")
  Set-VersionText $CargoToml '(?m)^(version\s*=\s*")[^"]*(") *$'        ("`${1}" + $Version + "`${2}")
}
Write-Host "==> 发布版本: $Version"

# ── 前置检查 ──
if (-not (Test-Path $KeyFile))   { throw "缺少更新签名私钥: $KeyFile" }
if (-not (Test-Path $Changelog)) { throw "缺少更新日志: $Changelog" }
if (-not (Test-Path (Join-Path $DesktopDir "node_modules"))) {
  Write-Host "!! desktop\node_modules 不存在，先执行 npm install（国内走镜像源）"; exit 1
}

# cargo/rust 工具链（新终端会话 PATH 不带）
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"

# ── 1) 前端构建（壳加载的是后端 serve 的 static/dist） ──
Write-Host "==> 构建 Web 前端"
$fLog = Join-Path $LogDir "build_frontend.log"
Push-Location $RepoRoot
try {
  # cmd /c 包裹：npm.ps1 包装器下 node 向 stderr 写 warning 会被 PS 5.1 包装成
  # ErrorRecord，EAP=Stop 时直接终止（NativeCommandError，0.3.2 发布踩实）。
  # cmd 无此概念；顺带解决 *> 重定向产出 UTF-16 日志的问题（cmd 重定向为原文）。
  cmd /c "npm run build --silent > `"$fLog`" 2>&1"
  if ($LASTEXITCODE -ne 0) { throw "前端构建失败，日志: $fLog" }
  Get-Content $fLog -Tail 5
} finally { Pop-Location }

# ── 2) 桌面端构建（prepare_backend staging + tauri build；bundler 缺私钥报错退出属预期） ──
Write-Host "==> 构建桌面端（tauri build）"
$dLog = Join-Path $LogDir "build_desktop.log"
Push-Location $DesktopDir
try {
  cmd /c "npm run build > `"$dLog`" 2>&1"
  Write-Host "    npm run build 退出码 $LASTEXITCODE（因未设私钥环境变量报错退出属预期，exe 已产出）"
  Get-Content $dLog -Tail 6
} finally { Pop-Location }

# ── 3) 产物校验 + 手动补签（空密码走 argv，绕过 Windows 空值环境变量限制） ──
$Exe = Join-Path $BundleDir "Astrion_${Version}_x64-setup.exe"
if (-not (Test-Path $Exe)) { throw "产物缺失: $Exe" }
$Sig = "$Exe.sig"
Write-Host "==> 手动补 updater 签名"
Push-Location $DesktopDir
try {
  # 空密码参数在 PowerShell->cmd 边界会丢失（PS 5.1 丢弃空串实参，导致 -p
  # 吞掉 exe 路径、FILE 缺失，0.3.2 踩实）。cmd /c 显式传字面 "" 保证 argv 完整。
  cmd /c "node_modules\.bin\tauri.cmd signer sign -f `"$KeyFile`" -p `"`" `"$Exe`" 2>&1" | Select-Object -Last 20
  if ($LASTEXITCODE -ne 0) { throw "补签失败" }
} finally { Pop-Location }
if (-not (Test-Path $Sig)) { throw "签名产物缺失: $Sig" }

# ── 4) keynum 校验（minisign base64 解码取 [2:10]，前 2 字节是算法标识，别用 [:8]） ──
function Get-Keynum([string]$B64) {
  $b = $B64.Trim()
  $b = $b.TrimEnd('=')
  $b = $b + ('=' * ((4 - ($b.Length % 4)) % 4))
  $bytes = [Convert]::FromBase64String($b)
  return (($bytes[2..9] | ForEach-Object { $_.ToString("x2") }) -join "")
}
$SigKn = Get-Keynum ((Get-Content $Sig -Raw).Split("`n")[0])
$PubKn = Get-Keynum ((Get-Content $TauriConf -Raw | ConvertFrom-Json).plugins.updater.pubkey)
Write-Host "    sig keynum: $SigKn"
Write-Host "    pub keynum: $PubKn"
if ($SigKn -ne $PubKn) { throw "keynum 不匹配，签名校验失败" }

# ── 5) 提取 changelog 顶部小节为更新说明（对齐 mac 半 awk 语义） ──
Write-Host "==> 提取 changelog 更新说明"
# 显式 UTF8：changelog 是无 BOM UTF-8，默认 ANSI/GBK 读取会让中文 notes 变乱码（0.3.2 踩实）
$lines    = Get-Content $Changelog -Encoding UTF8
$notes    = New-Object System.Collections.Generic.List[string]
$inFirst  = $false
foreach ($line in $lines) {
  if ($line -match '^##\s') {
    if ($inFirst) { break }
    if ($line -notmatch [regex]::Escape($Version)) { Write-Host "!! 警告：CHANGELOG 顶部小节与版本号 $Version 不符" }
    $inFirst = $true
    continue
  }
  if ($inFirst) { $notes.Add($line) }
}
while ($notes.Count -gt 0 -and $notes[0].Trim() -eq "")            { $notes.RemoveAt(0) }
while ($notes.Count -gt 0 -and $notes[$notes.Count-1].Trim() -eq "") { $notes.RemoveAt($notes.Count-1) }
if ($notes.Count -eq 0) { throw "未能从 CHANGELOG 提取更新说明" }
$NotesFile = Join-Path $env:TEMP "astrion-release-notes-$Version.txt"
[System.IO.File]::WriteAllLines($NotesFile, $notes, (New-Object System.Text.UTF8Encoding($false)))

# ── 6) 上传 exe / sig / release-notes-windows.txt -> 服务器 regen 清单 ──
Write-Host "==> 上传服务器"
& scp $Exe $Sig ($Server + ":" + $DestDir)
if ($LASTEXITCODE -ne 0) { throw "scp 上传安装包/签名失败" }
& scp $NotesFile ($Server + ":" + $DestDir + "release-notes-windows.txt")
if ($LASTEXITCODE -ne 0) { throw "scp 上传更新说明失败" }
Remove-Item $NotesFile -Force

Write-Host "==> 服务器重新生成更新清单"
& ssh $Server "python3 $DestDir/regen_manifest.py"
if ($LASTEXITCODE -ne 0) { throw "regen_manifest 失败" }

# ── 7) 公网验证 ──
Write-Host "==> 公网验证"
# -UseBasicParsing 必须：PS 5.1 在未完成 IE 首次运行配置的机器上，
# 默认引擎解析会直接抛 "Operation cancelled due to security concerns"
$manifest = Invoke-RestMethod -UseBasicParsing "https://astrion.cyjai.com/downloads/latest-windows-x86_64.json"
if ($manifest.version -ne $Version) { throw "清单版本不符: $($manifest.version)（应为 $Version）" }
Write-Host "    清单 version: $($manifest.version)"
Write-Host "    更新说明 notes: $($manifest.notes)"
$head = Invoke-WebRequest -UseBasicParsing -Method Head ("https://astrion.cyjai.com/downloads/Astrion_${Version}_x64-setup.exe")
$srvLen = [long]$head.Headers["Content-Length"]
$locLen = (Get-Item $Exe).Length
Write-Host "    exe 字节数: 服务器 $srvLen / 本地 $locLen"
if ($srvLen -ne $locLen) { throw "exe 字节数不一致" }

Write-Host ""
Write-Host "✔ 发布完成: v$Version"
Write-Host "  下载页:   https://astrion.cyjai.com/download.html"
Write-Host "  更新清单: https://astrion.cyjai.com/downloads/latest-windows-x86_64.json"