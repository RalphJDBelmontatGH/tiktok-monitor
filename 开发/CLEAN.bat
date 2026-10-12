@echo off
chcp 65001 >nul
title 快速清理（保留程序）
echo 将删除：数据(data.json)、配置(config.json)、报告(reports)
echo 程序 tiktok_monitor_v10.pyw 本身保留。
set /p ok=确认清理？(y/n):
if /i not "%ok%"=="y" exit /b 0
if exist "%~dp0reports" rmdir /s /q "%~dp0reports"
if exist "%~dp0data.json" del /q "%~dp0data.json"
if exist "%~dp0config.json" del /q "%~dp0config.json"
if exist "%~dp0state.json" del /q "%~dp0state.json"
echo 清理完成。
pause
