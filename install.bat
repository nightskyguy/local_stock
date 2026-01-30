@echo off
REM File: install.bat
REM One-command installer for Stock Quote System

echo ============================================================
echo Stock Quote System Installer
echo ============================================================
echo.

REM Check Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/3] Installing Python packages...
python -m pip install --upgrade pip
python -m pip install flask apscheduler yfinance pandas numpy

echo.
echo [2/3] Setting up portfolio...
call setup.bat

echo.
echo [3/3] Starting server...
echo The server will open in a new window.
echo Keep that window open while using LibreOffice Calc.
echo.
start start_server.bat

timeout /t 3 >nul
start http://localhost:5000

echo.
echo ============================================================
echo Installation Complete!
echo ============================================================
echo.
echo The web server is running in a separate window.
echo.
echo Use in LibreOffice Calc:
echo   =WEBSERVICE("http://localhost:5000/quote/AAPL")
echo.
echo Daily updates happen automatically at 3:30 PM.
echo.
echo To manually update:
echo   python stock_system.py --update
echo.
echo To configure:
echo   python stock_system.py --config list
echo   python stock_system.py --config set update_time "17:30"
echo.
timeout /t 15 /nobreak >nul