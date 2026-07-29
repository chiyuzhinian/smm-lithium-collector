@echo off
cd /d "C:\??\smm_lithium_collector"
start /b "" "C:\??\smm_lithium_collector\ngrok.exe" http 8888
timeout /t 2 /nobreak > nul
start /b "" "C:\??\smm_lithium_collector\.venv\Scripts\python.exe" "C:\??\smm_lithium_collector\scripts\file_server.py"
