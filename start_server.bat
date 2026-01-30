@echo off
REM File: start_server.bat
REM Start the stock quote web server in background

echo ============================================================
echo Starting Stock Quote Server (Background)
echo ============================================================
echo.

REM Check if server is already running
python stock_system.py --server 2>&1 | findstr /C:"already running" >nul
if %errorlevel% equ 0 (
    echo Server is already running.
    echo To stop: python stock_system.py --stopserver
    timeout /t 15 /nobreak >nul
    exit /b 1
)

REM Start server in background (new window, minimized)
start "Stock Quote Server" /MIN python stock_system.py --server

REM Wait a moment for server to start
timeout /t 2 >nul

REM Check if it started successfully
python stock_system.py --stats >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo Server started successfully!
    echo.
    echo Server running at: http://localhost:5000
    echo.
    echo Test: http://localhost:5000/quote/AAPL
    echo Use in Calc: =WEBSERVICE("http://localhost:5000/quote/AAPL")
    echo.
    echo To stop server: python stock_system.py --stopserver
    echo Or: stop_server.bat
    echo.
) else (
    echo.
    echo Warning: Server may have failed to start.
    echo Check the log: type %USERPROFILE%\stock_system.log
    echo.
)

echo Window will close in 15 seconds...
timeout /t 15 /nobreak >nul