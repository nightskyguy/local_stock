@echo off
REM File: stop_server.bat
REM Stop the stock quote web server

echo ============================================================
echo Stopping Stock Quote Server
echo ============================================================
echo.

python stock_system.py --stopserver

echo.
echo Window will close in 15 seconds...
timeout /t 15 /nobreak >nul