@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
where py >nul 2>nul
if not errorlevel 1 (
    py -3 web_app.py --open
) else (
    python web_app.py --open
)
if errorlevel 1 pause
