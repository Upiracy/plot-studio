@echo off
setlocal
cd /d "%~dp0"

echo Starting Plot Studio local server...
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 server.py %*
    goto :end
)

where python >nul 2>nul
if %errorlevel%==0 (
    python server.py %*
    goto :end
)

echo [ERROR] Python not found.
echo Please install Python 3.8+ and make sure it is on PATH.
echo https://www.python.org/downloads/
echo.

:end
echo.
echo Server stopped. Press any key to close this window.
pause >nul
