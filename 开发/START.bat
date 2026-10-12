@echo off
chcp 936 >nul 2>&1
cd /d "%~dp0"
title TikTok 广告监控 v10 启动器

rem ---------- 探测可用 Python（用命令名调用，兼容微软商店版别名） ----------
set "PY_CMD="
pythonw --version >nul 2>&1 && set "PY_CMD=pythonw"
if not defined PY_CMD (
    python --version >nul 2>&1 && set "PY_CMD=python"
)
if not defined PY_CMD (
    py --version >nul 2>&1 && set "PY_CMD=py"
)

if not defined PY_CMD (
    echo.
    echo [错误] 未找到可用的 Python 解释器。
    echo.
    echo 请打开 https://www.python.org/downloads/ 下载并安装 Python 3.8 或更高版本，
    echo 安装时务必勾选 "Add Python to PATH"。
    echo 安装完成后，先双击 INSTALL_DEP.bat 安装依赖，再重新双击本文件。
    echo.
    pause
    exit /b 1
)

rem ---------- 检查依赖是否安装 ----------
"%PY_CMD%" -c "import PIL, requests" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [错误] 缺少依赖库 pillow / requests。
    echo 请先双击 INSTALL_DEP.bat 安装依赖，再重新启动本工具。
    echo.
    pause
    exit /b 1
)

rem ---------- 无控制台启动 ----------
if exist error_log.txt del error_log.txt >nul 2>&1
echo 正在启动 TikTok 广告监控 v10 面板，请稍候...
start "" "%PY_CMD%" tiktok_monitor_v10.pyw
timeout /t 6 /nobreak >nul 2>&1
if exist error_log.txt (
    echo.
    echo [提示] 程序启动时出错，错误已保存到 error_log.txt。
    echo 请把 error_log.txt 文件发给技术人员排查。
    echo.
    pause
)
exit /b 0
