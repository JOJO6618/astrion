param([switch]$Clean)
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '..\..\desktop-electron\build\stop-installed-backends.ps1'
$expectedHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
$processes = @(Get-CimInstance Win32_Process)
$owned = @()
Get-ChildItem -LiteralPath $env:TEMP -Directory -Filter 'ns*.tmp' | ForEach-Object {
    $marker = Join-Path $_.FullName 'astrion-stop-backends.ps1'
    if ((Test-Path -LiteralPath $marker) -and (Get-FileHash -LiteralPath $marker -Algorithm SHA256).Hash -eq $expectedHash) {
        $prefix = $_.FullName.TrimEnd('\') + '\'
        $active = @($processes | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) })
        if ($active.Count -gt 0) { Write-Output ('Kept active owned NSIS temp: ' + $_.Name); return }
        $owned += [PSCustomObject]@{ name = $_.Name }
        if ($Clean) {
            Write-Output ('Cleaning verified owned NSIS temp: ' + $_.Name)
            $python = if ($env:ASTRION_TEST_PYTHON) { $env:ASTRION_TEST_PYTHON } else { 'python' }
            & $python -c 'import shutil, sys; shutil.rmtree(sys.argv[1])' $_.FullName
            if ($LASTEXITCODE -ne 0) { throw 'Owned temp cleanup failed' }
        }
    }
}
$owned | ConvertTo-Json
'Cleaned=' + $Clean.IsPresent
'FreeC=' + (Get-PSDrive -Name C).Free
