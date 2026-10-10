@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist "%~dp0导出结果" mkdir "%~dp0导出结果"
start "" explorer.exe "%~dp0导出结果"
