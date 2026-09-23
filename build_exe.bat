@echo off
REM ============================================
REM  AI Research Report Generator - Build EXE
REM  WARNING: this bundles your API keys from .env
REM  into the exe. Do NOT distribute to strangers.
REM ============================================

if not exist venv (
    echo.
    echo  [ERROR] Environment not found.
    echo  Please run  setup.bat  first.
    echo.
    echo  Press any key to close...
    pause >nul
    exit /b 1
)

call venv\Scripts\activate.bat

if not exist .env (
    echo [WARN] .env not found. Create a default one.
    echo DEEPSEEK_API_KEY=> .env
    echo SEARCH_PROVIDER=tavily>> .env
    echo TAVILY_API_KEY= >> .env
)

echo [1/2] Building EXE (this can take 1-3 minutes)...
pyinstaller --noconfirm ^
  --name "AI-Research" ^
  --windowed ^
  --onedir ^
  --add-data "research_agent;research_agent" ^
  --add-data ".env;." ^
  gui.py

if errorlevel 1 goto :fail

echo.
echo  ============================================
echo   DONE! EXE is at:
echo   dist\AI-Research\AI-Research.exe
echo  ============================================
echo.
echo  Press any key to close...
pause >nul
exit /b 0

:fail
echo.
echo  [ERROR] Build failed. See error above.
echo.
echo  Press any key to close...
pause >nul
exit /b 1
