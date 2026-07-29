$TaskName = "SMM_Ngrok_FileServer"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

# 先启动 ngrok
$ngrokExe = Join-Path $projectRoot "ngrok.exe"
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$serverScript = Join-Path $projectRoot "scripts\file_server.py"

# 用 cmd 脚本同时启动两个服务
$cmdScript = @"
@echo off
cd /d "$projectRoot"
start /b "" "$ngrokExe" http 8888
timeout /t 2 /nobreak > nul
start /b "" "$pythonExe" "$serverScript"
"@

$scriptPath = Join-Path $projectRoot "scripts\_start_services.bat"
$cmdScript | Out-File -FilePath $scriptPath -Encoding ASCII

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$scriptPath`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Days 365)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null

# 立即启动
Start-ScheduledTask -TaskName $TaskName

Write-Host "Ngrok + File server startup task installed: $TaskName"
Write-Host "Both services will auto-start on Windows startup."
