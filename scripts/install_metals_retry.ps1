$TaskName = "SMM_Metals_Retry"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$script = Join-Path $projectRoot "scripts\retry_metals.py"

$action = New-ScheduledTaskAction -Execute $pythonExe -Argument $script
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "11:00"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "Installed: $TaskName, next run: $($info.NextRunTime)"
