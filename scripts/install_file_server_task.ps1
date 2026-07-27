$TaskName = "SMM_File_Server"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$serverScript = Join-Path $projectRoot "scripts\file_server.py"

$action = New-ScheduledTaskAction -Execute $pythonExe -Argument "`"$serverScript`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Days 365)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "File server auto-start task installed: $TaskName"
Write-Host "The file server will start automatically on next reboot."
Write-Host "To start now, run: Start-ScheduledTask -TaskName '$TaskName'"
