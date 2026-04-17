@echo off
REM ===============================================================
REM  MachReach — local development launcher
REM  Double-click to run the app with debug + hot reload enabled.
REM ===============================================================

cd /d "%~dp0"

REM Load environment variables from .env if present (simple parser)
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        if not "%%A"=="" if not "%%A:~0,1%"=="#" set "%%A=%%B"
    )
)

REM Enable Flask debug mode (auto-reloads on file save)
set FLASK_DEBUG=1

REM Default port (change here if 5000 is busy)
if "%PORT%"=="" set PORT=5000

echo.
echo =================================================
echo   MachReach local dev server
echo   http://127.0.0.1:%PORT%
echo   Debug + hot reload: ON  (edits auto-restart)
echo   Press Ctrl+C to stop
echo =================================================
echo.

python app.py

pause
