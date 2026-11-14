<#
dialog-monitor.ps1 - read-only Win32 dialog observer for the 2026-11-14 real update test.

Run this AS THE TEST USER, in the same interactive desktop session that runs the
legacy Astrion 0.9.3 installer / the in-app update:

    powershell -NoProfile -ExecutionPolicy Bypass -File "<repo>\test\2026-11-14_Windows更新实机\dialog-monitor.ps1"

Early stop: create  <repo>\output\windows-update-real-20261114\stop-dialog-monitor.txt
(or override with -StopFile <path>).

WHAT IT DOES
  * reads the current process SID and refuses to run unless it equals -ExpectedSid
  * builds a PID whitelist of processes that BOTH match an Astrion / setup /
    install / uninstall image name AND are owned by -ExpectedSid. PowerShell,
    conhost, WmiPrvSE and this process itself are excluded, and every foreign
    owner is dropped, so windows of the main user's running Astrion app are
    never enumerated.
  * every -IntervalMs (default 80 ms) enumerates top-level windows, keeps the
    visible class "#32770" dialogs owned by whitelisted PIDs and reads the
    caption plus every non-empty Static text with
    SendMessageTimeout(WM_GETTEXT, SMTO_ABORTIFHUNG|SMTO_BLOCK, 150 ms) so a
    hung dialog can never block this monitor.
  * appends one JSON object per line to
    output\windows-update-real-20261114\dialogs.jsonl
    and only writes when a dialog appears, its text changes, or it disappears
    (dedupe key = PID + HWND). Console output is ASCII counters only.

WHAT IT NEVER DOES
  * never closes, clicks, focuses, moves or posts messages to a dialog
  * never starts, suspends or terminates a process
  * never touches registry, ACLs or any system state
  * the only write is the jsonl log (plus the console)

Why 80 ms: the installer guard dismisses a blocking failure MessageBox after
roughly 2 s. Do not raise -IntervalMs above 200.
#>

[CmdletBinding()]
param(
    [string]$ExpectedSid = 'S-1-5-21-1133143854-1543063607-2493749742-1010',
    [string]$OutputPath = '',
    [string]$StopFile = '',
    [int]$IntervalMs = 80,
    [int]$MaxMinutes = 20,
    [int]$WhitelistRefreshMs = 500
)

$ErrorActionPreference = 'Continue'

$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (-not $OutputPath) { $OutputPath = Join-Path $repo 'output\windows-update-real-20261114' }
if (-not $StopFile) { $StopFile = Join-Path $OutputPath 'stop-dialog-monitor.txt' }
if ($IntervalMs -lt 50) { $IntervalMs = 50 }
if ($IntervalMs -gt 200) { $IntervalMs = 200 }
if ($WhitelistRefreshMs -lt 250) { $WhitelistRefreshMs = 250 }
if ($MaxMinutes -lt 1) { $MaxMinutes = 1 }

$logPath = Join-Path $OutputPath 'dialogs.jsonl'
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Write-JsonLine {
    param([hashtable]$Record)
    try {
        $line = ($Record | ConvertTo-Json -Compress -Depth 4)
        [IO.File]::AppendAllText($logPath, $line + "`r`n", $utf8)
    } catch {
        # Logging must never abort monitoring.
    }
}

# ---- identity gate -----------------------------------------------------------
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$sid = $identity.User.Value

try { [IO.Directory]::CreateDirectory($OutputPath) | Out-Null } catch { }
Write-JsonLine @{
    ts         = (Get-Date).ToString('o')
    event      = 'start'
    sid        = $sid
    user       = $identity.Name
    pid        = $PID
    interval   = $IntervalMs
    maxMinutes = $MaxMinutes
    log        = $logPath
}

if ($sid -ne $ExpectedSid) {
    Write-JsonLine @{ ts = (Get-Date).ToString('o'); event = 'abort'; reason = 'sid-mismatch'; sid = $sid; expected = $ExpectedSid }
    Write-Host "dialog-monitor: refusing to run, process SID is not the test user SID."
    Write-Host ("dialog-monitor: current=" + $sid)
    Write-Host ("dialog-monitor: expected=" + $ExpectedSid)
    exit 2
}

# ---- Win32 reader (read-only P/Invoke, ASCII source) -------------------------
$typeSource = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public class AstrionDlgInfo
{
    public int Pid;
    public long Handle;
    public string ClassName;
    public string Caption;
    public string Texts;
}

public static class AstrionDlgProbe
{
    private const uint WM_GETTEXT = 0x000D;
    private const uint SMTO_BLOCK = 0x0001;
    private const uint SMTO_ABORTIFHUNG = 0x0002;
    private const int TEXT_CAPACITY = 2048;

    private delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool EnumWindows(EnumProc callback, IntPtr lParam);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool EnumChildWindows(IntPtr parent, EnumProc callback, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "GetClassNameW")]
    private static extern int GetClassNameW(IntPtr hWnd, StringBuilder text, int count);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "SendMessageTimeoutW", SetLastError = true)]
    private static extern IntPtr SendMessageTimeoutW(IntPtr hWnd, uint msg, IntPtr wParam, StringBuilder lParam, uint flags, uint timeout, out IntPtr result);

    private static string ReadText(IntPtr hWnd)
    {
        StringBuilder buffer = new StringBuilder(TEXT_CAPACITY);
        IntPtr result;
        IntPtr ok = SendMessageTimeoutW(hWnd, WM_GETTEXT, (IntPtr)TEXT_CAPACITY, buffer, SMTO_ABORTIFHUNG | SMTO_BLOCK, 150, out result);
        if (ok == IntPtr.Zero) { return string.Empty; }
        return buffer.ToString();
    }

    private static string ReadClass(IntPtr hWnd)
    {
        StringBuilder buffer = new StringBuilder(256);
        GetClassNameW(hWnd, buffer, 256);
        return buffer.ToString();
    }

    public static AstrionDlgInfo[] Snapshot(int[] pids)
    {
        List<AstrionDlgInfo> found = new List<AstrionDlgInfo>();
        if (pids == null || pids.Length == 0) { return found.ToArray(); }

        Dictionary<int, bool> allowed = new Dictionary<int, bool>();
        for (int i = 0; i < pids.Length; i++) { allowed[pids[i]] = true; }

        EnumProc outer = delegate(IntPtr hWnd, IntPtr lParam)
        {
            uint pid = 0;
            GetWindowThreadProcessId(hWnd, out pid);
            if (!allowed.ContainsKey((int)pid)) { return true; }
            if (!IsWindowVisible(hWnd)) { return true; }
            if (ReadClass(hWnd) != "#32770") { return true; }

            List<string> texts = new List<string>();
            EnumProc inner = delegate(IntPtr child, IntPtr unused)
            {
                if (ReadClass(child) != "Static") { return true; }
                string text = ReadText(child);
                if (text != null && text.Trim().Length > 0 && !texts.Contains(text)) { texts.Add(text); }
                return true;
            };
            EnumChildWindows(hWnd, inner, IntPtr.Zero);

            AstrionDlgInfo info = new AstrionDlgInfo();
            info.Pid = (int)pid;
            info.Handle = hWnd.ToInt64();
            info.ClassName = "#32770";
            info.Caption = ReadText(hWnd);
            info.Texts = string.Join(" | ", texts.ToArray());
            found.Add(info);
            return true;
        };

        EnumWindows(outer, IntPtr.Zero);
        return found.ToArray();
    }
}
'@

if (-not ('AstrionDlgProbe' -as [type])) {
    try {
        Add-Type -TypeDefinition $typeSource -Language CSharp -ErrorAction Stop
    } catch {
        Write-JsonLine @{ ts = (Get-Date).ToString('o'); event = 'abort'; reason = 'add-type-failed'; message = $_.Exception.Message }
        Write-Host "dialog-monitor: Add-Type failed."
        Write-Host ("dialog-monitor: " + $_.Exception.Message)
        exit 4
    }
}

# ---- whitelist: same owner SID AND an installer-like image name --------------
$namePattern = 'astrion|setup|install|unins'
$nameExclude = @('powershell.exe', 'pwsh.exe', 'conhost.exe', 'wmiprvse.exe', 'mshta.exe', 'cmd.exe', 'explorer.exe')
$ownerCache = @{}

function Get-Whitelist {
    param([int]$SelfPid, [string]$OwnerSid)

    $result = @{}
    $candidates = @()

    try {
        $filtered = Get-CimInstance -ClassName Win32_Process -Filter "Name LIKE '%astrion%' OR Name LIKE '%setup%' OR Name LIKE '%install%'" -ErrorAction Stop
        $candidates = @($filtered)
    } catch {
        try {
            $candidates = @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop |
                Where-Object { $_.Name -and $_.Name -match $namePattern })
        } catch {
            return $result
        }
    }

    foreach ($process in $candidates) {
        if (-not $process -or -not $process.Name) { continue }
        $processId = [int]$process.ProcessId
        if ($processId -le 4) { continue }
        if ($processId -eq $SelfPid) { continue }
        if (-not ($process.Name -match $namePattern)) { continue }
        if ($nameExclude -contains $process.Name.ToLowerInvariant()) { continue }

        # PID reuse guard: the cache key carries the creation time, so a recycled
        # PID always gets a fresh owner lookup instead of a stale verdict.
        $created = ''
        try { if ($process.CreationDate) { $created = ([datetime]$process.CreationDate).Ticks.ToString() } } catch { $created = '' }
        $cacheKey = "$processId|$created"

        if ($ownerCache.ContainsKey($cacheKey)) {
            $owner = $ownerCache[$cacheKey]
        } else {
            $owner = $null
            try { $owner = (Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid -ErrorAction Stop).Sid } catch { $owner = $null }
            if ($ownerCache.Count -gt 512) { $ownerCache.Clear() }
            $ownerCache[$cacheKey] = $owner
        }

        if ($owner -and ($owner -eq $OwnerSid)) {
            $result[$processId] = $process.Name
        }
    }

    return $result
}

# ---- main scan loop ---------------------------------------------------------
$deadline = (Get-Date).AddMinutes($MaxMinutes)
$lastRefresh = [DateTime]::MinValue
$allowed = @{}
$state = @{}
$stats = @{ ticks = 0; opened = 0; updates = 0; closed = 0; maxLive = 0; lastBeat = [DateTime]::MinValue }
$stopReason = 'timeout'

Write-Host "dialog-monitor: sid ok"
Write-Host ("dialog-monitor: log " + $logPath)
Write-Host ("dialog-monitor: interval " + $IntervalMs + " ms, max " + $MaxMinutes + " min")
Write-Host ("dialog-monitor: stop file " + $StopFile)

try {
    while ($true) {
        $tickStart = [DateTime]::UtcNow
        $now = Get-Date
        if ($now -gt $deadline) { $stopReason = 'timeout'; break }

        if (($stats['ticks'] % 6) -eq 0) {
            if (Test-Path -LiteralPath $StopFile) { $stopReason = 'stop-file'; break }
        }

        if (($now - $lastRefresh).TotalMilliseconds -ge $WhitelistRefreshMs) {
            $allowed = Get-Whitelist -SelfPid $PID -OwnerSid $ExpectedSid
            $lastRefresh = $now
        }

        $found = @()
        if ($allowed.Count -gt 0) {
            $pids = @()
            foreach ($key in $allowed.Keys) { $pids += [int]$key }
            try { $found = @([AstrionDlgProbe]::Snapshot($pids)) } catch { $found = @() }
        }

        $current = @{}
        foreach ($dialog in $found) {
            if (-not $dialog) { continue }
            $windowKey = "$($dialog.Pid):$($dialog.Handle)"
            $signature = [string]$dialog.Caption + [char]1 + [string]$dialog.Texts
            $processName = ''
            if ($allowed.ContainsKey([int]$dialog.Pid)) { $processName = $allowed[[int]$dialog.Pid] }

            if (-not $state.ContainsKey($windowKey)) {
                $state[$windowKey] = $signature
                $stats['opened'] = $stats['opened'] + 1
                Write-JsonLine @{
                    ts      = (Get-Date).ToString('o')
                    event   = 'open'
                    pid     = $dialog.Pid
                    proc    = $processName
                    hwnd    = $dialog.Handle
                    cls     = $dialog.ClassName
                    caption = $dialog.Caption
                    texts   = $dialog.Texts
                }
            } elseif ($state[$windowKey] -ne $signature) {
                $state[$windowKey] = $signature
                $stats['updates'] = $stats['updates'] + 1
                Write-JsonLine @{
                    ts      = (Get-Date).ToString('o')
                    event   = 'update'
                    pid     = $dialog.Pid
                    proc    = $processName
                    hwnd    = $dialog.Handle
                    cls     = $dialog.ClassName
                    caption = $dialog.Caption
                    texts   = $dialog.Texts
                }
            }
            $current[$windowKey] = $true
        }

        if ($state.Count -gt 0) {
            $gone = @()
            foreach ($windowKey in $state.Keys) {
                if (-not $current.ContainsKey($windowKey)) { $gone += $windowKey }
            }
            foreach ($windowKey in $gone) {
                $state.Remove($windowKey)
                $stats['closed'] = $stats['closed'] + 1
                Write-JsonLine @{ ts = (Get-Date).ToString('o'); event = 'close'; key = $windowKey }
            }
        }

        if ($state.Count -gt $stats['maxLive']) { $stats['maxLive'] = $state.Count }
        $stats['ticks'] = $stats['ticks'] + 1

        if (($now - $stats['lastBeat']).TotalSeconds -ge 60) {
            $stats['lastBeat'] = $now
            Write-JsonLine @{
                ts        = $now.ToString('o')
                event     = 'heartbeat'
                ticks     = $stats['ticks']
                opened    = $stats['opened']
                updates   = $stats['updates']
                closed    = $stats['closed']
                live      = $state.Count
                whitelist = $allowed.Count
            }
            Write-Host ("dialog-monitor: ticks=" + $stats['ticks'] + " open=" + $stats['opened'] + " upd=" + $stats['updates'] + " close=" + $stats['closed'] + " live=" + $state.Count)
        }

        $elapsed = ([DateTime]::UtcNow - $tickStart).TotalMilliseconds
        $sleep = $IntervalMs - [int]$elapsed
        if ($sleep -gt 0) { Start-Sleep -Milliseconds $sleep }
    }
} finally {
    Write-JsonLine @{
        ts      = (Get-Date).ToString('o')
        event   = 'summary'
        reason  = $stopReason
        ticks   = $stats['ticks']
        opened  = $stats['opened']
        updates = $stats['updates']
        closed  = $stats['closed']
        maxLive = $stats['maxLive']
    }
}

Write-Host ("dialog-monitor: stopped (" + $stopReason + ")")
Write-Host ("dialog-monitor: ticks=" + $stats['ticks'] + " open=" + $stats['opened'] + " upd=" + $stats['updates'] + " close=" + $stats['closed'])
exit 0
