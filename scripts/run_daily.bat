@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found: .venv
  exit /b 1
)
REM 启动文件服务器（端口8888，已运行则跳过）
start /b "" ".venv\Scripts\python.exe" "scripts\file_server.py" > nul 2>&1
REM 执行采集
".venv\Scripts\python.exe" "scripts\run_daily.py"
set "SMM_EXIT_CODE=%ERRORLEVEL%"
if not exist "logs" mkdir "logs"
echo %date% %time% scheduled_daily exit_code=%SMM_EXIT_CODE%>>"logs\task_scheduler_exit.log"
exit /b %SMM_EXIT_CODE%
