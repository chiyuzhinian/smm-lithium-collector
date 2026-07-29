@echo off
cd /d "%~dp0.."
start /b "" "ngrok.exe" http 8888
timeout /t 2 /nobreak > nul
start /b "" ".venv\Scripts\python.exe" "scripts\file_server.py"
