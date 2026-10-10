@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"
if defined PYTHON_CMD goto found_python

where python >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=python"
if defined PYTHON_CMD goto found_python

echo Python was not found.
echo Please install Python 3.10+ first.
goto done

:found_python
echo Python command: %PYTHON_CMD%
%PYTHON_CMD% -m pip install -r requirements.txt

:done
echo.
pause
