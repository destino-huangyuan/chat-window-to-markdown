@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
echo Checking OCR environment...
echo.

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"
if defined PYTHON_CMD goto check_python

where python >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=python"

:check_python

if "%PYTHON_CMD%"=="" (
  echo Python was not found.
  echo Please install Python 3.10+.
  echo.
) else (
  echo Python:
  %PYTHON_CMD% --version
  echo.
  %PYTHON_CMD% -c "import PIL; print('Pillow: OK')" >nul 2>nul
  if errorlevel 1 (
    echo Pillow: missing
    echo Please run:
    echo   %PYTHON_CMD% -m pip install -r requirements.txt
    echo.
  ) else (
    %PYTHON_CMD% -c "import PIL; print('Pillow: OK')"
    echo.
  )
)

where tesseract >nul 2>nul
if %errorlevel%==0 (
  echo Found tesseract in PATH:
  where tesseract
  echo.
  tesseract --version
  echo.
  echo Installed languages:
  tesseract --list-langs
  goto done
)

if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
  echo Found: C:\Program Files\Tesseract-OCR\tesseract.exe
  "C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs
  goto done
)

echo Tesseract was not found.
echo Please install Tesseract OCR with Chinese Simplified language data ^(chi_sim^).

:done
echo.
pause
