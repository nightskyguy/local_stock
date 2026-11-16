REM ==========================================================================
REM  SAMPLE FILE - the API keys below are FAKE placeholders, not working keys.
REM
REM  Do not put real keys in this file: it is tracked in git (public repo).
REM  1. Copy it to api_keys_donotdisclose.bat (that name is git-ignored).
REM  2. Replace the fake keys in the COPY with your own.
REM  3. Run the copy from this folder:  api_keys_donotdisclose.bat
REM ==========================================================================
if /i "%~nx0"=="my_api_keys.bat" (
    echo This is the SAMPLE file and its keys are fake. Copy it to api_keys_donotdisclose.bat, edit the copy, and run that instead.
    exit /b 1
)
python stock_system.py --config set apikey alphavantage QAMFHO0HAKE294Q5
python stock_system.py --config set apikey fmp SpslZJLq5nagjeuCl0VKXhliO4DpNCjt
python stock_system.py --config set apikey finnhub jf91djp91j9nf8ucoomja0tcm9592ge1cstsdkiz
python stock_system.py --config set source_priority_1 yfinance
python stock_system.py --config set source_priority_2 finnhub
python stock_system.py --config set source_priority_3 alphavantage
python stock_system.py --config set source_priority_4 NONE