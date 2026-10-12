@echo off
chcp 65001 >nul
title TK投流监控
cd /d "%~dp0"
python main.py
if errorlevel 1 (
    echo.
    echo 启动失败。可能原因：未安装依赖（先跑 INSTALL_PYTHON.bat）
    echo 或 Python 未加入 PATH。
    pause
)
