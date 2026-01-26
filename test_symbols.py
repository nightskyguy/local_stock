"""
Test Symbol Availability on Yahoo Finance
Tests all portfolio symbols to see which ones have data
"""
import yfinance as yf

symbols = ['BND', 'BOXX', 'AAPL', 'CSCO', 'CSOAX', 'EFA', 'FNILX', 'FZROX',
           'HYDB', 'HYGH', 'IBIT', 'IVV', 'QQQ', 'SCHD', 'SIYYX', 'STAYX',
           'SUSYX', 'TSLA', 'VASGX', 'VIOO', 'VOO', 'VTI', 'VWEHX', 'VWO']

print("Testing symbol availability on Yahoo Finance:")
print("=" * 60)
print()

working = []
failed = []

for symbol in symbols:
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d")
        if hist.empty:
            print(f"❌ {symbol:8} - NO DATA AVAILABLE")
            failed.append(symbol)
        else:
            print(f"✓  {symbol:8} - OK ({len(hist)} days of data)")
            working.append(symbol)
    except Exception as e:
        error_msg = str(e)[:50]
        print(f"❌ {symbol:8} - ERROR: {error_msg}")
        failed.append(symbol)

print()
print("=" * 60)
print(f"\nSummary:")
print(f"  Working: {len(working)} symbols")
print(f"  Failed:  {len(failed)} symbols")

if failed:
    print(f"\nSymbols that need replacement:")
    for symbol in failed:
        print(f"  - {symbol}")