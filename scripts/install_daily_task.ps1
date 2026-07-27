[CmdletBinding()]
param(
    [string]$TaskName = "SMM_Lithium_Daily_Collector",
    [string]$RunAt = "09:00"
)

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$batchFile = Join-Path $projectRoot "scripts\run_daily.bat"
$pythonFile = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $batchFile)) {
    throw "Daily runner not found: $batchFile"
}
if (-not (Test-Path -LiteralPath $pythonFile)) {
    throw "Virtual environment not found: $pythonFile"
}

$time = [datetime]::ParseExact($RunAt, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/d /c `"`"$batchFile`"`"" `
    -WorkingDirectory $projectRoot

# 仅工作日（周一到周五）
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $time

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Collect SMM lithium spot prices daily at $RunAt (Mon-Fri only)" `
    -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "Scheduled task installed successfully."
Write-Host "Task name: $($task.TaskName)"
Write-Host "Task state: $($task.State)"
Write-Host "Schedule: Mon-Fri at $RunAt"
Write-Host "Next run: $($info.NextRunTime)"
