@echo off
REM ============================================
REM  AI Research Report Generator - Run
REM ============================================

if not exist venv (
    echo.
    echo  [NOTE] Environment not found.
    echo  Please double-click  setup.bat  FIRST to install.
    echo.
    echo  Press any key to close...
    pause >nul
    exit /b 1
)

call venv\Scripts\activate.bat
python gui.py

echo.
echo  Program closed. Press any key to exit...
pause >nul
