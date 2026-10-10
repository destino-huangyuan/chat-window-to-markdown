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
echo Please install Python 3.10+ and run:
echo   pip install -r requirements.txt
goto done

:found_python
echo Python command: %PYTHON_CMD%
%PYTHON_CMD% -c "import PIL" >nul 2>nul
if errorlevel 1 goto missing_pillow

%PYTHON_CMD% "%~dp0wechat_chat_exporter.py" --ocr-engine tesseract --max-lines 100000 --max-pages 22000 --max-empty-pages 6 --scroll 0 --scroll-ratio 0.55 --wheel-pixels-per-notch 24 --scroll-pulse -5 --scroll-pulse-delay 0.04 --delay 0.45 --content-inset 0,140,0,260 --avatar-strip 125 --edge-margin 110 --ocr-scale 1.8 --text-threshold 150 --dedupe-threshold 0.80 %*
goto done

:missing_pillow
echo 缺少截图组件 Pillow。
echo.
echo 请先运行：
echo   %PYTHON_CMD% -m pip install -r requirements.txt
echo.
echo 如果你使用的是国内网络，也可以运行：
echo   %PYTHON_CMD% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
goto done

:done
pause
