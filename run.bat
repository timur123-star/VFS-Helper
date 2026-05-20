@echo off
REM ============================================================
REM  VFS Helper - one-click launcher for Windows
REM ============================================================
REM  Activates the virtual environment created by setup.bat and
REM  runs the helper.
REM
REM  Run setup.bat first if you haven't yet.
REM ============================================================
setlocal

cd /d "%~dp0"

if not exist .venv\Scripts\activate.bat (
    echo.
    echo Virtual environment not found. Please run setup.bat first.
    echo.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

if not exist config.json (
    echo.
    echo config.json not found. Launching setup wizard ...
    echo.
    python vfs_helper.py --setup
    echo.
)

python vfs_helper.py %*

echo.
pause
endlocal
