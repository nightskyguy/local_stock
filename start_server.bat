@echo off
REM File: start_server.bat
REM Start the stock quote web server

echo ============================================================
echo Starting Stock Quote Server
echo ============================================================
echo.
echo Server will run at http://localhost:5000
echo.
echo Keep this window open while using LibreOffice Calc.
echo.
echo Automatic updates: 3:30 PM Mon-Fri (configurable)
echo.
echo Press Ctrl+C to stop the server.
echo ============================================================
echo.

python stock_system.py --server

pause