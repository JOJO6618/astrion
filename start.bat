@echo off
rem 启动入口（Windows 版 start.sh）：
rem   1. 确保 Python venv 与依赖就绪（缺失才安装）
rem   2. 确保 Node 依赖就绪（缺失才安装）
rem   3. 若没有 .env（首次启动），自动运行配置向导
rem   4. 启动 python -m server.app
rem
rem 端口/监听地址/模式等由 .env 决定（见 config/server.py、config/paths.py）。
rem 透传的命令行参数会传给 server.app（如 --port / --path / --thinking-mode）。
rem
rem 用法：
rem   start.bat                       正常启动（首次会自动初始化）
rem   start.bat --thinking-mode       透传参数给 server.app

rem 控制台切到 UTF-8，保证中文提示与日志正常显示。
chcp 65001 >nul
setlocal

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"

call "%ROOT%\_bootstrap.bat" ensure_python_env
if errorlevel 1 exit /b 1
call "%ROOT%\_bootstrap.bat" ensure_node_env
if errorlevel 1 exit /b 1

call "%ROOT%\_bootstrap.bat" has_env_file
if not errorlevel 1 goto :start_server

echo.
echo [start] 未检测到 .env，进入首次初始化向导...
"%VENV_PY%" -m scripts.setup
call "%ROOT%\_bootstrap.bat" has_env_file
if not errorlevel 1 goto :start_server
echo [start] 初始化未完成（未生成 .env），已退出。 1>&2
exit /b 1

:start_server
echo.
echo [start] 启动 Web 服务...
cd /d "%ROOT%"
"%VENV_PY%" -m server.app %*
exit /b %errorlevel%
