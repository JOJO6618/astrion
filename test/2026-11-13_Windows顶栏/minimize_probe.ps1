param([Parameter(Mandatory=$true)][int]$ShellPid)
$ErrorActionPreference = 'Stop'
Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class OwnWindowState {
    public delegate bool EnumWindowProc(IntPtr hwnd, IntPtr parameter);
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct WINDOWPLACEMENT {
        public int Length, Flags, ShowCmd;
        public POINT MinPosition, MaxPosition;
        public RECT NormalPosition;
    }
    public class Candidate {
        public long Handle;
        public int Width, Height;
        public bool Visible, Minimized;
    }
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowProc callback, IntPtr parameter);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hwnd, int command);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
    [DllImport("user32.dll")] public static extern bool GetWindowPlacement(IntPtr hwnd, ref WINDOWPLACEMENT placement);
    public static Candidate[] WindowsFor(uint processId) {
        var windows = new List<Candidate>();
        EnumWindows((hwnd, unused) => {
            uint owner;
            GetWindowThreadProcessId(hwnd, out owner);
            if (owner != processId) return true;
            var placement = new WINDOWPLACEMENT();
            placement.Length = Marshal.SizeOf(typeof(WINDOWPLACEMENT));
            if (GetWindowPlacement(hwnd, ref placement)) {
                windows.Add(new Candidate {
                    Handle = hwnd.ToInt64(),
                    Width = placement.NormalPosition.Right - placement.NormalPosition.Left,
                    Height = placement.NormalPosition.Bottom - placement.NormalPosition.Top,
                    Visible = IsWindowVisible(hwnd), Minimized = IsIconic(hwnd)
                });
            }
            return true;
        }, IntPtr.Zero);
        return windows.ToArray();
    }
}
'@
$expected = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\output\windows-electron\build\win-unpacked\astrion-desktop.exe'))
$process = Get-Process -Id $ShellPid
if (-not $process.Path.Equals($expected, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected test executable' }
$deadline = [DateTime]::UtcNow.AddSeconds(10)
$minimized = $false
# MainWindowHandle can point at a hidden quick-chat window after it was opened.
# Normal placement retains the main window's size even while minimized.
while ([DateTime]::UtcNow -lt $deadline) {
    $candidates = @([OwnWindowState]::WindowsFor([uint32]$ShellPid))
    $mainWindows = @($candidates | Where-Object { $_.Visible -and $_.Width -ge 960 -and $_.Height -ge 600 })
    if ($mainWindows.Count -eq 1 -and $mainWindows[0].Minimized) {
        $handle = [IntPtr]$mainWindows[0].Handle
        $minimized = $true
        break
    }
    Start-Sleep -Milliseconds 100
}
if (-not $minimized) {
    $diagnostic = ConvertTo-Json -InputObject $candidates -Compress
    throw "Own test main window did not minimize; PID-local windows: $diagnostic"
}
$owner = [uint32]0
[OwnWindowState]::GetWindowThreadProcessId($handle, [ref]$owner) | Out-Null
if ($owner -ne $ShellPid -or -not [OwnWindowState]::IsWindow($handle)) { throw 'Unexpected window owner' }
[OwnWindowState]::ShowWindowAsync($handle, 9) | Out-Null
$deadline = [DateTime]::UtcNow.AddSeconds(5)
while ([OwnWindowState]::IsIconic($handle) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
$restored = [OwnWindowState]::IsWindow($handle) -and -not [OwnWindowState]::IsIconic($handle)
@{ minimized = $minimized; restored = $restored; handle = $handle.ToInt64(); windows = $candidates } | ConvertTo-Json -Compress -Depth 4
