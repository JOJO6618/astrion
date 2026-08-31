@echo off
rem start-sandbox-test-instance.cmd — 启动「沙箱安装向导」的隔离测试实例（Windows）
rem
rem 用途：验证「未安装沙箱时的居中弹窗 + 一键安装流程」。
rem 本机 astrion-sandbox 已就绪时，正常实例不会弹窗；本脚本通过
rem HOST_SANDBOX_WSL_DISTRO 指向一个不存在的测试发行版，使检测判定为缺失。
rem
rem 可选参数：
rem   wsl-missing    额外模拟「WSL 未启用」分支：在 PATH 最前置入一个含同名
rem                  0 字节假 wsl.exe 的目录（E:\astrion\sandbox-test-fake），
rem                  which/Start-Process 优先命中它而失败，等效于挪走系统
rem                  wsl.exe，但不碰系统文件，删目录即恢复。
rem
rem 隔离手段（主程序 8091/8092 完全不受影响）：
rem   1. setlocal —— 环境变量仅存在于本脚本进程及其子进程，
rem      脚本退出即失效，外部 cmd 窗口与其他服务实例读不到；
rem   2. ASTRION_DATA_ROOT —— 数据目录独立（全新初始化，不动现有对话）；
rem   3. --port 8093 —— 端口独立。
rem
rem 测试结束后清理：
rem   wsl --unregister astrion-sandbox-test
rem   rmdir /s /q E:\astrion\sandbox-test-data
rem   rmdir /s /q E:\astrion\sandbox-test-fake
rem   （若存在 %USERPROFILE%\.astrion\wsl-sandbox-astrion-sandbox-test 残留目录一并删除）

setlocal
set ASTRION_DATA_ROOT=E:\astrion\sandbox-test-data
set HOST_SANDBOX_WSL_DISTRO=astrion-sandbox-test

if /i "%~1"=="wsl-missing" (
    if not exist E:\astrion\sandbox-test-fake mkdir E:\astrion\sandbox-test-fake
    if not exist E:\astrion\sandbox-test-fake\wsl.exe type nul > E:\astrion\sandbox-test-fake\wsl.exe
    set "PATH=E:\astrion\sandbox-test-fake;%PATH%"
    echo [测试模式] 已模拟 wsl.exe 缺失：PATH 优先命中 0 字节假文件
)

python -m server.app --port 8093
endlocal
