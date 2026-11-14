param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$InstallerPath,
    [Parameter(Mandatory=$true)][int]$InstallerPid,
    [Parameter(Mandatory=$true)][string]$ProgressDirectory,
    [string]$WindowTitle = 'Astrion',
    [string]$AppExe = 'astrion-desktop.exe'
)

# Lives outside the application and NSIS plugin directory. Never terminates
# processes or dismisses installer errors; the user can inspect those errors.
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
if ($root -eq [IO.Path]::GetPathRoot($root).TrimEnd('\')) { exit 1 }
$phasePath = Join-Path $ProgressDirectory 'phase.txt'
$logPath = Join-Path $ProgressDirectory 'install.log'
# Use the installer-created session marker, not delayed PowerShell startup.
$startedFileTime = [IO.File]::GetCreationTimeUtc((Join-Path $ProgressDirectory 'install-directory.txt')).ToFileTimeUtc()

function Write-GuardLog([string]$Message) {
    Add-Content -LiteralPath $logPath -Value ('[{0:o}] {1}' -f (Get-Date), $Message) -Encoding UTF8
}
function Set-InstallPhase([string]$Phase, [string]$Detail = '') {
    [IO.File]::WriteAllText((Join-Path $ProgressDirectory 'detail.txt'), $Detail, [Text.Encoding]::UTF8)
    [IO.File]::WriteAllText($phasePath, $Phase, [Text.Encoding]::ASCII)
    Write-GuardLog ($Phase + ' ' + $Detail)
}
function Get-MissingFile {
    foreach ($relative in @($AppExe, 'resources.pak', 'snapshot_blob.bin', 'v8_context_snapshot.bin', 'resources\app.asar')) {
        if (-not [IO.File]::Exists((Join-Path $root $relative))) { return $relative }
    }
    if ([IO.Directory]::Exists((Join-Path $root 'resources\runtime')) -and -not [IO.File]::Exists((Join-Path $root 'resources\runtime\python\python.exe'))) { return 'resources\runtime\python\python.exe' }
    return $null
}

try {
    [IO.File]::WriteAllText((Join-Path $ProgressDirectory 'controller.pid'), [string]$PID)
    $shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $view = Join-Path $ProgressDirectory 'astrion-install-progress.ps1'
    $viewArgs = '-NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $view + '" -ProgressDirectory "' + $ProgressDirectory + '" -WindowTitle "' + $WindowTitle + '"'
    $viewStart = New-Object Diagnostics.ProcessStartInfo
    $viewStart.FileName = $shell
    $viewStart.Arguments = $viewArgs
    $viewStart.UseShellExecute = $false
    $viewStart.CreateNoWindow = $true
    $viewProcess = [Diagnostics.Process]::Start($viewStart)
    $viewProcess.Dispose()
    Write-GuardLog ('installer=' + $InstallerPid + ' installDir=' + $root)
    Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class AstrionInstallWindows {
    private delegate bool EnumProc(IntPtr hwnd, IntPtr arg);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumProc callback, IntPtr arg);
    [DllImport("user32.dll")] private static extern bool EnumChildWindows(IntPtr hwnd, EnumProc callback, IntPtr arg);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out int pid);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetClassName(IntPtr hwnd, StringBuilder value, int count);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr hwnd, StringBuilder value, int count);
    [DllImport("kernel32.dll", SetLastError=true)] private static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
    [DllImport("kernel32.dll", SetLastError=true)] private static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] private static extern bool QueryFullProcessImageName(IntPtr handle, uint flags, StringBuilder path, ref int size);
    [DllImport("kernel32.dll", SetLastError=true)] private static extern bool GetProcessTimes(IntPtr handle, out long created, out long exited, out long kernel, out long user);
    public static int FindApplicationWindow(string expectedPath, long notBefore) {
        int found = 0;
        EnumWindows((hwnd, arg) => {
            if (!IsWindowVisible(hwnd)) return true;
            var kind = new StringBuilder(64); GetClassName(hwnd, kind, kind.Capacity);
            if (!kind.ToString().StartsWith("Chrome_WidgetWin_", StringComparison.Ordinal)) return true;
            int pid; GetWindowThreadProcessId(hwnd, out pid);
            // PROCESS_QUERY_LIMITED_INFORMATION works across x86/x64 without
            // reading the other process's modules. BaseWindow may have no title.
            IntPtr process = OpenProcess(0x1000, false, pid);
            if (process == IntPtr.Zero) return true;
            try {
                var image = new StringBuilder(32768);
                int size = image.Capacity;
                long created, exited, kernel, user;
                if (QueryFullProcessImageName(process, 0, image, ref size) &&
                    GetProcessTimes(process, out created, out exited, out kernel, out user) &&
                    created >= notBefore &&
                    String.Equals(image.ToString(), expectedPath, StringComparison.OrdinalIgnoreCase)) {
                    found = pid;
                    return false;
                }
            } finally { CloseHandle(process); }
            return true;
        }, IntPtr.Zero);
        return found;
    }
    public static string ReadDialog(int owner) {
        var result = new StringBuilder();
        EnumWindows((hwnd, arg) => {
            int pid; GetWindowThreadProcessId(hwnd, out pid);
            if (pid != owner || !IsWindowVisible(hwnd)) return true;
            var kind = new StringBuilder(64); GetClassName(hwnd, kind, kind.Capacity);
            if (kind.ToString() != "#32770") return true;
            EnumChildWindows(hwnd, (child, unused) => {
                var text = new StringBuilder(2048); GetWindowText(child, text, text.Capacity);
                if (text.Length > 0) result.AppendLine(text.ToString());
                return true;
            }, IntPtr.Zero);
            return true;
        }, IntPtr.Zero);
        return result.ToString().Trim();
    }
}
'@
    $originalArgs = [IO.File]::ReadAllText((Join-Path $ProgressDirectory 'arguments.txt'), [Text.Encoding]::Unicode)
    $expectRestart = $originalArgs -match '(?i)(?:^|\s)(?:/R|--force-run)(?:\s|$)'
    $installer = Get-Process -Id $InstallerPid -ErrorAction SilentlyContinue
    if ($installer) { $null = $installer.Handle }
    for ($attempt = 0; $attempt -le 1; $attempt++) {
        $deadline = (Get-Date).AddMinutes(20)
        $lastDialog = ''
        while ($installer -and -not $installer.HasExited) {
            if ((Get-Date) -gt $deadline) {
                Set-InstallPhase 'error' 'The installer is still running. Inspect the installation log and any installer message before trying again.'
                exit 3
            }
            $dialogText = [AstrionInstallWindows]::ReadDialog($installer.Id)
            if ($dialogText -and $dialogText -ne $lastDialog) {
                Write-GuardLog ('installer dialog: ' + $dialogText)
                [IO.File]::WriteAllText((Join-Path $ProgressDirectory 'attention.txt'), $dialogText, [Text.Encoding]::UTF8)
            } elseif (-not $dialogText -and $lastDialog) {
                Remove-Item -LiteralPath (Join-Path $ProgressDirectory 'attention.txt') -ErrorAction SilentlyContinue
            }
            $lastDialog = $dialogText
            Start-Sleep -Milliseconds 750
        }
        Remove-Item -LiteralPath (Join-Path $ProgressDirectory 'attention.txt') -ErrorAction SilentlyContinue
        $exitCode = if ($installer) { $installer.ExitCode } else { $null }
        $missing = Get-MissingFile
        $committed = [IO.File]::Exists((Join-Path $ProgressDirectory 'installed.txt'))
        Write-GuardLog ('installer exited=' + $exitCode + ' committed=' + $committed + ' missing=' + $missing)
        if ($committed -and -not $missing -and ($null -eq $exitCode -or $exitCode -eq 0)) { break }
        if ($missing -and $attempt -eq 0 -and [IO.File]::Exists($InstallerPath)) {
            Set-InstallPhase 'retrying' ('Missing file: ' + $missing)
            # NSIS /D= must be the final argument, unquoted even with spaces.
            $retryArgs = [regex]::Replace($originalArgs, '(?i)(?:^|\s)/D=.*$', '').Trim() + ' /S /D=' + $root
            $env:ASTRION_INSTALL_PROGRESS_DIR = $ProgressDirectory
            Remove-Item -LiteralPath (Join-Path $ProgressDirectory 'installed.txt') -ErrorAction SilentlyContinue
            $installer = Start-Process -FilePath $InstallerPath -ArgumentList $retryArgs -PassThru
            $null = $installer.Handle
            Write-GuardLog ('retry installer=' + $installer.Id)
            continue
        }
        Set-InstallPhase 'error' ('Installation did not complete. Exit code: ' + $exitCode + '; missing file: ' + $missing)
        exit 3
    }
    if (-not $expectRestart) {
        Set-InstallPhase 'complete'
        exit 0
    }
    Set-InstallPhase 'starting'
    $appPath = Join-Path $root $AppExe
    $deadline = (Get-Date).AddMinutes(3)
    while ((Get-Date) -lt $deadline) {
        $windowPid = [AstrionInstallWindows]::FindApplicationWindow($appPath, $startedFileTime)
        if ($windowPid -gt 0) {
            Write-GuardLog ('new application window detected pid=' + $windowPid + ' path=' + $appPath)
            Set-InstallPhase 'ready'
            exit 0
        }
        Start-Sleep -Milliseconds 350
    }
    Set-InstallPhase 'launch-error' 'Installation completed, but the new application window has not appeared. See the log before retrying.'
    exit 4
} catch {
    try { Set-InstallPhase 'error' $_.Exception.Message } catch { }
    exit 3
}
