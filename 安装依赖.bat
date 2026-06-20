@echo off
chcp 65001 >nul
echo ============================================
echo   微信求购监控系统 - 安装依赖
echo ============================================
echo.

where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    echo 下载地址: https://www.python.org/downloads/
    echo 安装时请勾选 "Add Python to PATH"
    pause
    exit /b 1
)

echo [1/2] 正在安装 Python 依赖...
python -m pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo [错误] pip 安装失败，请检查网络或尝试: python -m pip install --upgrade pip
    pause
    exit /b 1
)

echo.
echo [2/2] 检查配置文件...
if not exist config.py (
    if exist config.example.py (
        copy config.example.py config.py >nul
        echo 已从模板创建 config.py，请编辑填入你的配置！
        notepad config.py
    ) else (
        echo [错误] 未找到 config.example.py
    )
) else (
    echo config.py 已存在，跳过
)

echo.
echo ============================================
echo   安装完成！
echo.
echo   下一步:
echo     1. 编辑 config.py 填入你的配置
echo     2. 安装 wechat-decrypt (见 README)
echo     3. 双击「启动Bot.bat」开始监控
echo ============================================
pause
