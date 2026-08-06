@echo off
setlocal enabledelayedexpansion
echo ============================================
echo   SMM 锂电采集 - 服务器一键部署
echo ============================================
echo.

cd /d "%~dp0.."
set ROOT=%CD%

REM 1. Python 环境
echo [1/5] 检查 Python...
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] 未找到 Python，请先安装 Python 3.11+
    echo 下载地址: https://www.python.org/downloads/
    pause && exit /b 1
)
python --version
echo.

REM 2. 虚拟环境
echo [2/5] 安装依赖...
if not exist .venv (
    python -m venv .venv
)
call .venv\Scripts\activate
pip install -r requirements.txt -q
echo.

REM 3. Playwright
echo [3/5] 安装 Chromium...
playwright install chromium
echo.

REM 4. 登录态
echo [4/5] 配置登录态...
if exist data\auth\storage_state.json (
    echo 登录态文件已存在: data\auth\storage_state.json
) else (
    echo [WARNING] 未找到登录态文件！
    echo 请先在本机运行一次 manual_login.py 登录，然后把 data\auth\storage_state.json
    echo 复制到服务器 %ROOT%\data\auth\ 目录
    echo.
)

REM 5. .env 配置
if not exist .env (
    copy .env.example .env >nul
    echo 已创建 .env 模板，请编辑填入配置
    echo 必填: SMM_LOGIN_URL, SMM_TARGET_URL, DINGTALK_WEBHOOK, DINGTALK_SECRET
    echo.
)

REM 6. 安装定时任务
echo [5/5] 安装定时任务...
powershell -ExecutionPolicy Bypass -File "%ROOT%\scripts\install_daily_task.ps1" 2>nul
powershell -ExecutionPolicy Bypass -File "%ROOT%\scripts\install_metals_retry.ps1" 2>nul

echo.
echo ============================================
echo   部署完成！
echo ============================================
echo.
echo 定时任务:
echo   周一至周五 9:00  - 全量采集 + 报表 + 钉钉
echo   周一至周五 11:00 - 铜铝镍延迟补采
echo.
echo 测试运行:
echo   %ROOT%\.venv\Scripts\python.exe %ROOT%\scripts\run_daily.py --dry-run
echo.
echo 登录态文件:
echo   %ROOT%\data\auth\storage_state.json
echo.
echo 输出文件:
echo   %ROOT%\data\exports\
echo.
pause
