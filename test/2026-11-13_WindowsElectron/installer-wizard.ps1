param([Parameter(Mandatory=$true)][string]$AllowedRoot)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class TestInstallerButton {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SendMessageTimeoutW(IntPtr hwnd, uint msg, IntPtr wp, IntPtr lp, uint flags, uint timeout, out IntPtr result);
}
'@
$prefix = [IO.Path]::GetFullPath($AllowedRoot).TrimEnd('\') + '\'
$deadline = [DateTime]::UtcNow.AddSeconds(900)
$clicks = 0
while ([DateTime]::UtcNow -lt $deadline) {
    $candidates = Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
    }
    foreach ($candidate in $candidates) {
        # Only the separate test updater's downloaded installer is eligible.
        $condition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ProcessIdProperty, [int]$candidate.ProcessId)
        $windows = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
            [System.Windows.Automation.TreeScope]::Children, $condition)
        foreach ($window in $windows) {
            if ($window.Current.Name -notlike '*Astrion Electron Migration Test*') { continue }
            $buttonCondition = New-Object System.Windows.Automation.PropertyCondition(
                [System.Windows.Automation.AutomationElement]::AutomationIdProperty, '1')
            $button = $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $buttonCondition)
            if (-not $button -or -not $button.Current.IsEnabled) { continue }
            $pattern = $null
            $label = $button.Current.Name
            if ($button.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
                $pattern.Invoke()
            } elseif ($button.Current.NativeWindowHandle -ne 0) {
                # NSIS custom-drawn controls can expose Pane without InvokePattern.
                # BM_CLICK is restricted to the button in the verified test PID.
                [TestInstallerButton]::SetForegroundWindow([IntPtr]$window.Current.NativeWindowHandle) | Out-Null
                $result = [IntPtr]::Zero
                [TestInstallerButton]::SendMessageTimeoutW([IntPtr]$button.Current.NativeWindowHandle, 0xF5, [IntPtr]::Zero, [IntPtr]::Zero, 2, 1000, [ref]$result) | Out-Null
            } else { continue }
            $clicks++
            Write-Output ('Invoked test installer button: ' + $label)
            Start-Sleep -Milliseconds 1200
        }
    }
    if ($clicks -gt 0 -and @($candidates).Count -eq 0) { exit 0 }
    Start-Sleep -Milliseconds 500
}
Write-Error 'Timed out waiting for the isolated test installer wizard.'
exit 1
