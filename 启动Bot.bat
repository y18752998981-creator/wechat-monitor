@echo off
chcp 65001 >nul
title 微信求购监控 Bot
echo ============================================
echo   微信求购监控 Bot
echo   按 Ctrl+C 停止
echo ============================================
echo.

where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [错误] 未找到 Python
    pause
    exit /b 1
)

if not exist config.py (
    echo [错误] 未找到 config.py
    echo 请先运行「安装依赖.bat」或复制 config.example.py 为 config.py 并编辑
    pause
    exit /b 1
)

python telegram_bot.py
pause
