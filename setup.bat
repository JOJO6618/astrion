@echo off
rem 首次初始化入口（Windows 版 setup.sh）：
rem   1. 创建/复用 Python 虚拟环境并安装依赖
rem   2. 准备 Node 依赖（easyagent / 前端，依赖系统已装 Node）
rem   3. 运行 python -m scripts.setup 交互式向导，写出 .env 与模型配置
rem
rem 用法：
rem   setup.bat            首次初始化（已存在 .env 时向导会提示备份后重配）
rem   setup.bat --force    跳过「已存在 .env」确认（仍会备份）

rem 控制台切到 UTF-8，保证中文提示与日志正常显示。
chcp 65001 >nul
setlocal

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"

echo ========================================
echo   AI Agent 初始化
echo ========================================

call "%ROOT%\_bootstrap.bat" ensure_python_env
if errorlevel 1 exit /b 1
call "%ROOT%\_bootstrap.bat" ensure_node_env
if errorlevel 1 exit /b 1

echo.
echo [setup] 启动配置向导...
"%VENV_PY%" -m scripts.setup %*
exit /b %errorlevel%
