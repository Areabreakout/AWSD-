@echo off
REM WASD+ Clone - one-click launcher (double-click to run)
REM Finds Python and runs launcher.py (which checks deps & starts main.py)

setlocal
cd /d "%~dp0"

REM Try the official py launcher first, then python, then python3
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 -u launcher.py
    goto :end
)

where python >nul 2>&1
if %errorlevel%==0 (
    python -u launcher.py
    goto :end
)

where python3 >nul 2>&1
if %errorlevel%==0 (
    python3 -u launcher.py
    goto :end
)

echo [ERROR] Python not found. Please install Python 3.7+
echo Download: https://www.python.org/downloads/
echo.
pause

:end
endlocal
