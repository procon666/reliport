@echo off
REM ============================================
REM  AI Research - Diagnose and Run
REM  Writes all output to error.log so you can
REM  share it with me even if the window closes.
REM ============================================

cd /d "%~dp0"
set LOGFILE=error.log
echo ===== AI Research diagnose log ===== > "%LOGFILE%"
echo Time: %date% %time% >> "%LOGFILE%"
echo. >> "%LOGFILE%"

echo [1/5] Checking Python...
python --version >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo [FAIL] Python not found >> "%LOGFILE%"
    echo Python not found. See error.log
    pause
    exit /b 1
)

echo [2/5] Checking venv...
if exist venv\Scripts\activate.bat (
    echo venv exists >> "%LOGFILE%"
) else (
    echo [INFO] venv missing, creating... >> "%LOGFILE%"
    python -m venv venv >> "%LOGFILE%" 2>&1
)

echo [3/5] Activating venv...
call venv\Scripts\activate.bat >> "%LOGFILE%" 2>&1

echo [4/5] Checking PyQt5...
python -c "import PyQt5" >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo [INFO] PyQt5 not installed, installing... >> "%LOGFILE%"
    pip install -r requirements.txt >> "%LOGFILE%" 2>&1
    echo [INFO] pip install done >> "%LOGFILE%"
)

echo [5/5] Launching GUI...
python gui.py >> "%LOGFILE%" 2>&1
echo.
echo  Done. Log saved to error.log
echo  If the window closed, please send me error.log
pause
