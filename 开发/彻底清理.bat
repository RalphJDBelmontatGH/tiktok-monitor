@echo off
chcp 65001 >nul
title 彻底清理（自毁）
echo ⚠️⚠️⚠️ 高危操作 ⚠️⚠️⚠️
echo 本操作将删除整个网页监控程序目录（含 tiktok_monitor_v10.pyw、数据、配置）！
echo 删除后无法恢复！
echo.
set /p ok=输入 DELETE 确认彻底清理:
if /i not "%ok%"=="DELETE" (
    echo 已取消。
    pause
    exit /b 0
)
cd /d "%~dp0"
cd ..
rmdir /s /q "%~dp0"
echo 已彻底清理。
pause
