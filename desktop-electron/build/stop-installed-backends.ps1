param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$ExecutableName
)
$ErrorActionPreference = 'Stop'
try {
    $root = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
    if ($root -eq [IO.Path]::GetPathRoot($root).TrimEnd('\')) { exit 1 }
    $allowed = @(
        [IO.Path]::Combine($root, 'resources\runtime\python\'),
        [IO.Path]::Combine($root, 'runtime\python\')
    )
    if ([IO.Path]::GetFileName($ExecutableName) -ne $ExecutableName) { exit 1 }
    $shell = [IO.Path]::Combine($root, $ExecutableName)
    $helper = [IO.Path]::Combine($root, 'resources\astrion-windows-helper.exe')
    $processes = Get-CimInstance Win32_Process
    foreach ($process in $processes) {
        if (-not $process.ExecutablePath) { continue }
        $candidate = [IO.Path]::GetFullPath($process.ExecutablePath)
        $matched = $candidate.Equals($shell, [StringComparison]::OrdinalIgnoreCase) -or
            $candidate.Equals($helper, [StringComparison]::OrdinalIgnoreCase)
        foreach ($prefix in $allowed) {
            if ($candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { $matched = $true }
        }
        if ($matched) {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    exit 0
} catch {
    Write-Error 'Unable to release application backend files in the installation directory.'
    exit 1
}
