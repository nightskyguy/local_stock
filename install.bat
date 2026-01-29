@echo off
echo Installing Stock Quote System for LibreOffice...
echo.

REM Install packages for system Python
python -m pip install yfinance pandas numpy

REM Copy files to user directory
copy calc_quote_lookup.py %USERPROFILE%\
copy stock_fetcher.py %USERPROFILE%\

REM Initialize database
python stock_db_setup.py
python daily_update.py --init

python daily_update.py --years 3 --update
python daily_update.py --stats

echo.
echo Installation complete!
echo Now open LibreOffice Calc and run Tools > Macros > Edit Macros
echo Copy the macro from README.md into Module1
pause