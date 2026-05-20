@echo off
REM ============================================================
REM  VFS Helper - optional screen recording (ffmpeg)
REM
REM  Workflow (on YOUR Windows machine, residential IP):
REM    1. You double-click this .bat
REM    2. ffmpeg starts recording the whole screen
REM    3. Chromium opens your start_url from config.json
REM    4. You do the ONE-TIME manual steps in the browser:
REM         a) tick the Cloudflare 'I'm human' checkbox
REM         b) log in to your VFS account
REM         c) open the FULL application form (not only country dropdowns)
REM    5. Switch back to this terminal, press Enter
REM    6. Helper auto-runs:
REM         inspect -^> scroll -^> fill (x2) -^> fill applicant -^> screenshot -^> quit
REM        (fills ALL keys in config.user — see config.demo-full.json)
REM    7. ffmpeg stops, MP4 is in artifacts\vfs-helper-real-vfs-<ts>.mp4
REM ============================================================
setlocal

cd /d "%~dp0"

if not exist .venv\Scripts\activate.bat (
    echo.
    echo [ERROR] .venv not found. Please run setup.bat first.
    echo.
    pause
    exit /b 1
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] ffmpeg is not in PATH. Install once with:
    echo     winget install Gyan.FFmpeg
    echo     ^(or^)  choco install ffmpeg
    echo Or download from https://www.gyan.dev/ffmpeg/builds/
    echo and add the bin folder to PATH.
    echo.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

if not exist config.json (
    if exist config.demo-full.json (
        echo.
        echo [INFO] Copying config.demo-full.json -^> config.json for the recording.
        echo       Edit config.json with your real data before recording.
        echo.
        copy /Y config.demo-full.json config.json >nul
    ) else (
        echo.
        echo [INFO] config.json not found. Launching setup wizard...
        echo       Use REAL data - it will be typed into the form in the video.
        echo.
        python vfs_helper.py --setup
        if errorlevel 1 (
            echo.
            echo [ERROR] Setup wizard aborted.
            pause
            exit /b 1
        )
    )
)

python scripts\record_real_runner.py --platform win %*

echo.
pause
endlocal
