Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /c cd /d C:\科研\smm_lithium_collector && start /b ngrok.exe http 8888", 0, False
WScript.Sleep 3000
WshShell.Run "cmd /c cd /d C:\科研\smm_lithium_collector && .venv\Scripts\python.exe scripts\file_server.py", 0, False
