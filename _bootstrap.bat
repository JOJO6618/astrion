@echo off
rem 共享引导逻辑：被 setup.bat / start.bat 通过 call 调用（对应 bash 版 _bootstrap.sh 的 source）。
rem 负责定位项目根、准备 Python venv 与依赖、准备 Node 依赖。
rem
rem 用法: call _bootstrap.bat ^<函数名^>
rem   ensure_python_env  准备虚拟环境与 Python 依赖
rem   ensure_node_env    准备 Node 依赖（easyagent / 前端构建，可选）
rem   has_env_file       .env 存在返回 0，否则返回 1
rem
rem 说明：本脚本故意不使用 setlocal，使 ROOT/VENV_DIR/VENV_PY 等变量
rem       保留在调用者环境中（与 bash source 语义一致）。

rem 项目根目录 = 本脚本所在目录（%~dp0 自带末尾反斜杠，去掉）。
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "VENV_DIR=%ROOT%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

if /i "%~1"=="ensure_python_env" goto :ensure_python_env
if /i "%~1"=="ensure_node_env" goto :ensure_node_env
if /i "%~1"=="has_env_file" goto :has_env_file
echo [error] _bootstrap.bat: 未知命令 "%~1" 1>&2
exit /b 1

rem ---------------------------------------------------------------
rem 准备虚拟环境与 Python 依赖（依赖系统已装 Python，不内置解释器）。
rem 已存在 venv 则复用；缺失则创建并安装 requirements.lock.txt（优先）或 requirements.txt。
:ensure_python_env
if exist "%VENV_PY%" goto :venv_ready

rem 选择系统 Python：python -> python3 -> py -3。
rem 用 --version 探测而不是 where，可跳过 WindowsApps 的商店占位 stub。
set "SYS_PY="
python --version >nul 2>&1 && set "SYS_PY=python"
if not defined SYS_PY ( python3 --version >nul 2>&1 && set "SYS_PY=python3" )
if not defined SYS_PY ( py -3 --version >nul 2>&1 && set "SYS_PY=py -3" )
if not defined SYS_PY (
    echo [error] 未找到系统 Python（需要 python 或 python3）。请先安装 Python 3.9+。 1>&2
    exit /b 1
)
echo [setup] 使用 %SYS_PY% 创建虚拟环境：%VENV_DIR%
%SYS_PY% -m venv "%VENV_DIR%"
if errorlevel 1 exit /b 1

:venv_ready
echo [setup] 升级 pip 并安装依赖...
"%VENV_PY%" -m pip install --upgrade pip >nul
if errorlevel 1 exit /b 1
if exist "%ROOT%\requirements.lock.txt" (
    "%VENV_PY%" -m pip install -r "%ROOT%\requirements.lock.txt"
) else if exist "%ROOT%\requirements.txt" (
    "%VENV_PY%" -m pip install -r "%ROOT%\requirements.txt"
) else (
    echo [error] 找不到 requirements.lock.txt 或 requirements.txt 1>&2
    exit /b 1
)
if errorlevel 1 exit /b 1
exit /b 0

rem ---------------------------------------------------------------
rem 准备 Node 依赖（依赖系统已装 Node；不内置 Node）。
rem easyagent 仅需运行时依赖；前端需要构建产物（vite build）。
:ensure_node_env
node --version >nul 2>&1
if errorlevel 1 (
    echo [warn] 未找到系统 Node。子智能体（easyagent）与前端构建将不可用。 1>&2
    echo        如需完整功能，请安装 Node.js 18+ 后重跑。 1>&2
    exit /b 0
)
call npm --version >nul 2>&1
if errorlevel 1 (
    echo [warn] 找到 node 但未找到 npm，跳过 Node 依赖安装。 1>&2
    exit /b 0
)

rem easyagent 运行时依赖
if not exist "%ROOT%\easyagent\package.json" goto :easyagent_done
if exist "%ROOT%\easyagent\node_modules" goto :easyagent_done
echo [setup] 安装 easyagent 依赖（npm ci）...
pushd "%ROOT%\easyagent"
call npm ci
if errorlevel 1 goto :npm_fail
popd
:easyagent_done

rem 前端构建产物。仅在缺失时构建，避免每次启动都跑。
if not exist "%ROOT%\package.json" goto :frontend_done
if exist "%ROOT%\node_modules" goto :frontend_done
echo [setup] 安装前端依赖并构建（npm ci ^&^& npm run build）...
pushd "%ROOT%"
call npm ci
if errorlevel 1 goto :npm_fail
call npm run build
if errorlevel 1 goto :npm_fail
popd
:frontend_done
exit /b 0

:npm_fail
popd
exit /b 1

rem ---------------------------------------------------------------
rem .env 是否存在（首次启动判断依据）。
:has_env_file
if exist "%ROOT%\.env" exit /b 0
exit /b 1
