param(
    [Parameter(Mandatory=$true)][string]$ProgressDirectory,
    [string]$WindowTitle = 'Astrion'
)
$ErrorActionPreference = 'Stop'
$logPath = Join-Path $ProgressDirectory 'progress-window.log'
try {
    Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
    $allText = [IO.File]::ReadAllText((Join-Path $ProgressDirectory 'astrion-install-progress.strings.json'), [Text.Encoding]::UTF8) | ConvertFrom-Json
    $text = if ([Globalization.CultureInfo]::CurrentUICulture.Name -like 'zh*') { $allText.zh } else { $allText.en }
    # Native system semantic brushes follow Windows/high-contrast settings.
    # No browser, application backend or files being replaced are required.
    [xml]$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Width="510" MinWidth="510" MaxWidth="510" SizeToContent="Height"
        ResizeMode="CanMinimize" WindowStartupLocation="CenterScreen"
        Background="{DynamicResource {x:Static SystemColors.WindowBrushKey}}"
        Foreground="{DynamicResource {x:Static SystemColors.WindowTextBrushKey}}"
        FontFamily="Segoe UI" FontSize="14">
  <Grid Margin="28,24,28,24">
    <Grid.RowDefinitions>
      <RowDefinition Height="38"/><RowDefinition Height="36"/><RowDefinition Height="12"/>
      <RowDefinition Height="28"/><RowDefinition Height="132"/><RowDefinition Height="Auto"/>
      <RowDefinition Height="36"/>
    </Grid.RowDefinitions>
    <TextBlock x:Name="Heading" FontSize="23" FontWeight="SemiBold" TextTrimming="CharacterEllipsis"/>
    <TextBlock x:Name="Status" Grid.Row="1" VerticalAlignment="Center" TextTrimming="CharacterEllipsis"/>
    <ProgressBar x:Name="Activity" Grid.Row="2" Height="4" IsIndeterminate="True"/>
    <TextBlock x:Name="Elapsed" Grid.Row="3" FontSize="12" VerticalAlignment="Center"
               Foreground="{DynamicResource {x:Static SystemColors.GrayTextBrushKey}}"/>
    <StackPanel Grid.Row="4" Margin="0,6,0,0">
      <TextBlock x:Name="Stage0" Height="29"/><TextBlock x:Name="Stage1" Height="29"/>
      <TextBlock x:Name="Stage2" Height="29"/><TextBlock x:Name="Stage3" Height="29"/>
    </StackPanel>
    <TextBox x:Name="Details" Grid.Row="5" Height="100" Margin="0,4,0,12" Visibility="Collapsed"
             IsReadOnly="True" TextWrapping="Wrap" VerticalScrollBarVisibility="Auto"
             BorderThickness="0" Background="Transparent"
             Foreground="{DynamicResource {x:Static SystemColors.WindowTextBrushKey}}"/>
    <Grid Grid.Row="6">
      <TextBlock x:Name="Hint" FontSize="12" VerticalAlignment="Center" TextWrapping="Wrap"
                 Foreground="{DynamicResource {x:Static SystemColors.GrayTextBrushKey}}"/>
      <StackPanel x:Name="Actions" Orientation="Horizontal" HorizontalAlignment="Right" Visibility="Collapsed">
        <Button x:Name="Logs" Height="32" MinWidth="132" Padding="12,0" Margin="0,0,8,0"/>
        <Button x:Name="Close" Height="32" MinWidth="72" Padding="12,0"/>
      </StackPanel>
    </Grid>
  </Grid>
</Window>
'@
    $window = [Windows.Markup.XamlReader]::Load((New-Object Xml.XmlNodeReader $xaml))
    $window.Title = $text.title
    $window.MaxHeight = [Math]::Max(350, [Windows.SystemParameters]::WorkArea.Height - 48)
    $parts = @{}
    foreach ($name in @('Heading','Status','Activity','Elapsed','Details','Hint','Actions','Logs','Close','Stage0','Stage1','Stage2','Stage3')) { $parts[$name] = $window.FindName($name) }
    $parts.Heading.Text = $text.heading
    $parts.Hint.Text = $text.hint
    $parts.Logs.Content = $text.logs
    $parts.Close.Content = $text.close
    $script:terminal = $false
    $script:successAt = $null
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $phaseOrder = @{ preparing = 0; closing = 0; installing = 1; retrying = 1; checking = 2; starting = 3; ready = 4; complete = 4 }
    $script:lastPhase = 'preparing'
    $parts.Logs.Add_Click({ Start-Process -FilePath 'explorer.exe' -ArgumentList ('"' + $ProgressDirectory + '"') })
    $parts.Close.Add_Click({ $window.Close() })
    $window.Add_Closing({
        param($sender, $eventArgs)
        if (-not $script:terminal) {
            $eventArgs.Cancel = $true
            $window.WindowState = [Windows.WindowState]::Minimized
        }
    })
    $timer = New-Object Windows.Threading.DispatcherTimer
    $timer.Interval = [TimeSpan]::FromMilliseconds(350)
    $timer.Add_Tick({
        try {
            $phaseFile = Join-Path $ProgressDirectory 'phase.txt'
            if ([IO.File]::Exists($phaseFile)) {
                $value = [IO.File]::ReadAllText($phaseFile).Trim()
                if ($text.PSObject.Properties[$value]) { $script:lastPhase = $value }
            }
            $phase = $script:lastPhase
            $detail = ''
            $attentionFile = Join-Path $ProgressDirectory 'attention.txt'
            if ([IO.File]::Exists($attentionFile) -and $phase -notin @('error','launch-error','ready','complete')) {
                $phase = 'attention'
                $detail = [IO.File]::ReadAllText($attentionFile, [Text.Encoding]::UTF8)
            }
            $controllerFile = Join-Path $ProgressDirectory 'controller.pid'
            if ($clock.Elapsed.TotalSeconds -gt 15 -and $phase -notin @('error','launch-error','ready','complete')) {
                $controllerId = if ([IO.File]::Exists($controllerFile)) { [int][IO.File]::ReadAllText($controllerFile) } else { 0 }
                if (-not $controllerId -or -not (Get-Process -Id $controllerId -ErrorAction SilentlyContinue)) {
                    $phase = 'error'
                    $detail = $text.controllerStopped
                }
            }
            $parts.Status.Text = $text.PSObject.Properties[$phase].Value
            $parts.Elapsed.Text = $text.elapsed -f [int][Math]::Floor($clock.Elapsed.TotalMinutes), $clock.Elapsed.Seconds
            $index = if ($phaseOrder.ContainsKey($phase)) { $phaseOrder[$phase] } else { 0 }
            for ($i = 0; $i -lt 4; $i++) {
                $prefix = if ($i -lt $index) { $text.done } elseif ($i -eq $index) { $text.current } else { '  ' }
                $parts[('Stage' + $i)].Text = $prefix + $text.stages[$i]
                $parts[('Stage' + $i)].FontWeight = if ($i -eq $index) { [Windows.FontWeights]::SemiBold } else { [Windows.FontWeights]::Normal }
            }
            if ($phase -in @('error','launch-error')) {
                if (-not $detail) { $detail = [IO.File]::ReadAllText((Join-Path $ProgressDirectory 'detail.txt'), [Text.Encoding]::UTF8) }
                $script:terminal = $true
                $parts.Heading.Text = $text.PSObject.Properties[$phase].Value
                $parts.Actions.Visibility = [Windows.Visibility]::Visible
                $parts.Hint.Visibility = [Windows.Visibility]::Collapsed
                $parts.Activity.Visibility = [Windows.Visibility]::Hidden
            } elseif ($phase -in @('ready','complete')) {
                $script:terminal = $true
                $parts.Activity.Visibility = [Windows.Visibility]::Hidden
                if ($null -eq $script:successAt) { $script:successAt = $clock.Elapsed.TotalSeconds }
                if ($clock.Elapsed.TotalSeconds - $script:successAt -ge 0.8) { $window.Close() }
            }
            $parts.Details.Text = $detail
            $parts.Details.Visibility = if ($detail) { [Windows.Visibility]::Visible } else { [Windows.Visibility]::Collapsed }
        } catch {
            Add-Content -LiteralPath $logPath -Value $_.Exception.Message -Encoding UTF8
        }
    })
    $window.Add_Closed({ $timer.Stop() })
    $timer.Start()
    [void]$window.ShowDialog()
} catch {
    Add-Content -LiteralPath $logPath -Value $_.Exception.ToString() -Encoding UTF8
    exit 1
}
