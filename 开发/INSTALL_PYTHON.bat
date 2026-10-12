@echo off
chcp 65001 >nul
title TK投流监控 - 依赖安装
echo ============================================
echo   安装 Python 依赖（需要已安装 Python 3.9+）
echo ============================================
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先到 python.org 安装 Python 3.9+
    echo 安装时务必勾选 "Add Python to PATH"
    pause
    exit /b 1
)
python -m pip install --upgrade pip
python -m pip install -r "%~dp0requirements.txt"
echo.
echo 依赖安装完成。双击 START.bat 启动程序。
pause
