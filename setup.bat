@echo off
REM ============================================
REM  AI Research Report Generator - Setup
REM ============================================

REM --- Step 0: check Python ---
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] Python not found.
    echo  Please install Python 3.9+ from:
    echo     https://www.python.org/downloads/
    echo  IMPORTANT: tick "Add Python to PATH" during install.
    echo.
    echo  Press any key to close...
    pause >nul
    exit /b 1
)

echo [1/3] Creating virtual environment...
if not exist venv (
    python -m venv venv
    if errorlevel 1 goto :fail
)

echo [2/3] Installing dependencies (may take a few minutes)...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul 2>nul
pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [3/3] Installing packaging tool...
pip install pyinstaller >nul 2>nul

echo.
echo  ============================================
echo   DONE! Setup complete.
echo   Now double-click  run.bat  to start.
echo  ============================================
echo.
echo  Press any key to close...
pause >nul
exit /b 0

:fail
echo.
echo  [ERROR] Setup failed. Please check your network
echo  and try again. See the error above.
echo.
echo  Press any key to close...
pause >nul
exit /b 1
