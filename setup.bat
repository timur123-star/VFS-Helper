@echo off
REM ============================================================
REM  VFS Helper - one-click setup for Windows
REM ============================================================
REM  - Creates a virtual environment in .venv
REM  - Installs Python dependencies
REM  - Installs the Chromium binary for Playwright (as a fallback;
REM    the helper prefers real Google Chrome via channel="chrome")
REM  - Launches the interactive config wizard
REM
REM  Just double-click this file. Re-running it is safe.
REM ============================================================
setlocal

cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo Python 3 launcher 'py' was not found.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo During install tick "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

if not exist .venv (
    echo Creating virtual environment in .venv ...
    py -3 -m venv .venv
    if %errorlevel% neq 0 (
        echo Failed to create venv. Aborting.
        pause
        exit /b 1
    )
)

call .venv\Scripts\activate.bat

echo.
echo Installing Python dependencies ...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo pip install failed. Check your internet connection and retry.
    pause
    exit /b 1
)

echo.
echo Installing Playwright Chromium (fallback browser) ...
python -m playwright install chromium
if %errorlevel% neq 0 (
    echo Playwright browser install failed.
    pause
    exit /b 1
)

echo.
echo Running diagnostics ...
python vfs_helper.py --check

echo.
echo ============================================================
echo  Setup finished.
echo  Next step: enter your data via the wizard.
echo ============================================================
echo.
python vfs_helper.py --setup

echo.
echo Done. To start the helper later just double-click run.bat
echo.
pause
endlocal
