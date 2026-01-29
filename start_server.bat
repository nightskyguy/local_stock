@echo off
REM File: start_server.bat
REM Start the stock quote web server

echo Starting Stock Quote Server...
echo.
echo Server will run at http://localhost:5000
echo.
echo Keep this window open while using LibreOffice Calc.
echo Press Ctrl+C to stop the server.
echo.

python stock_server.py

pause