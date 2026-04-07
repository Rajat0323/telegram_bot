$projectPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = (Get-Command python).Source
$taskName = "IPLTelegramAutoPoster"
$scriptPath = Join-Path $projectPath "autoposter.py"

$action = New-ScheduledTaskAction -Execute $python -Argument "`"$scriptPath`"" -WorkingDirectory $projectPath
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Output "Scheduled task '$taskName' registered. It will run every 15 minutes."
