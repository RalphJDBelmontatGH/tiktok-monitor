@echo off
chcp 936 >nul 2>&1
cd /d "%~dp0"
title 安装 TikTok 广告监控 v10 依赖
echo 正在安装依赖（pillow requests），如果网络较慢请耐心等待...
echo.

rem ---------- 探测可用 Python（用命令名调用，兼容微软商店版别名） ----------
set "PY_CMD="
python --version >nul 2>&1 && set "PY_CMD=python"
if not defined PY_CMD (
    py --version >nul 2>&1 && set "PY_CMD=py"
)

if not defined PY_CMD (
    echo.
    echo [错误] 未找到 Python。
    echo 请先安装 Python 3.8 或更高版本，安装时勾选 "Add Python to PATH"。
    echo.
    pause
    exit /b 1
)

"%PY_CMD%" -m pip install --upgrade pip
"%PY_CMD%" -m pip install pillow requests
if errorlevel 1 (
    echo.
    echo [错误] 依赖安装失败，请检查网络后重试。
    echo.
    pause
    exit /b 1
)

echo.
echo 依赖安装完成！现在可以双击 START.bat 启动工具。
pause
