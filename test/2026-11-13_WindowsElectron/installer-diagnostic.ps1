$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$all = @(Get-CimInstance Win32_Process)
$rows = @($all | Where-Object {
    $_.Name -eq 'Astrion-Electron-Test-0.10.0.exe' -or $_.Name -eq 'Astrion-Electron-Test-0.10.1.exe' -or
    $_.Name -eq 'astrion-electron-migration-test.exe' -or
    $_.CommandLine -like '*installer-test\installed-app*' -or $_.CommandLine -like '*Astrion-Electron-Test-0.10*'
})
do {
    $ids = @($rows | ForEach-Object { $_.ProcessId })
    $children = @($all | Where-Object { $ids -contains $_.ParentProcessId -and $ids -notcontains $_.ProcessId })
    $rows += $children
} while ($children.Count -gt 0)
foreach ($row in $rows) {
    Write-Output ('TEST PROCESS: ' + $row.ProcessId + ' parent=' + $row.ParentProcessId + ' path=' + $row.ExecutablePath + ' cpu=' + $row.UserModeTime + ' read=' + $row.ReadTransferCount + ' write=' + $row.WriteTransferCount)
    $condition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ProcessIdProperty, [int]$row.ProcessId)
    $windows = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
        [System.Windows.Automation.TreeScope]::Children, $condition)
    foreach ($window in $windows) {
        Write-Output ('TEST WINDOW: ' + $window.Current.Name)
        $children = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition)
        foreach ($child in $children) {
            Write-Output ('  ' + $child.Current.ControlType.ProgrammaticName + ' id=' + $child.Current.AutomationId + ' enabled=' + $child.Current.IsEnabled + ' text=' + $child.Current.Name)
        }
    }
}
