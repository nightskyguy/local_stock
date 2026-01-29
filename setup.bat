@echo off
REM File: setup_portfolio.bat
REM Initialize database and add all portfolio symbols

echo ============================================================
echo Setting Up Stock Portfolio
echo ============================================================
echo.

REM Initialize database (includes QQQ and CSCO as reference symbols)
echo [1/3] Initializing database...
python stock_system.py --init

echo.
echo [2/3] Adding portfolio symbols...
python stock_system.py --add AAPL --add TSLA --add IVV --add VOO --add VTI --add VIOO --add IBIT --add FNILX --add SCHD --add EFA --add VWO --add VWEHX --add FZROX --add CSOAX --add SIYYX --add STAYX --add SUSYX --add VASGX --add BND --add BOXX --add HYDB --add HYGH

echo.
echo [3/3] Fetching 3 years of historical data...
echo (This will take 5-10 minutes)
python stock_system.py --update --years 3

echo.
echo ============================================================
echo Portfolio Setup Complete!
echo ============================================================
echo.
echo Your portfolio contains 24 symbols plus 2 reference symbols (QQQ, CSCO)
echo.
echo To start the server:
echo   start_server.bat
echo.
echo To update daily:
echo   python stock_system.py --update
echo.
pause