# Important Note About Mutual Funds (VWEHX)

## Issue with VWEHX

VWEHX (Vanguard High-Yield Corporate Fund) is a **mutual fund**, not an ETF or stock. Mutual funds have different characteristics:

### Key Differences

1. **Trading**: Mutual funds trade once per day at NAV (Net Asset Value) after market close
2. **Data Availability**: Yahoo Finance may have limited or delayed data for mutual funds
3. **Pricing**: Only one price per day (NAV), no intraday trading

### Solutions

#### Option 1: Check Data Availability
```python
import yfinance as yf

# Test if VWEHX data is available
ticker = yf.Ticker("VWEHX")
hist = ticker.history(period="1mo")
print(hist)

# If empty, Yahoo Finance doesn't have this data
if hist.empty:
    print("No data available for VWEHX on Yahoo Finance")
```

#### Option 2: Use Alternative Ticker
Some mutual funds require different ticker formats:
- Try: `VWEHX` (standard)
- Try: `VWEHX.MF` (some systems use .MF suffix)
- Check Vanguard directly for ticker symbol

#### Option 3: Replace with Similar ETF
If VWEHX data is unavailable, consider tracking a similar high-yield corporate bond ETF:
- **HYG** - iShares iBoxx High Yield Corporate Bond ETF
- **JNK** - SPDR Bloomberg High Yield Bond ETF
- **USHY** - iShares Broad USD High Yield Corporate Bond ETF

Add to your portfolio:
```bash
python daily_update.py --add HYG
python daily_update.py --update
```

#### Option 4: Manual Entry
For mutual funds without Yahoo data, you can manually add NAV prices:
```python
import sqlite3
from datetime import datetime

conn = sqlite3.connect('stock_quotes.db')
cursor = conn.cursor()

# Insert manual NAV data
cursor.execute('''
    INSERT OR REPLACE INTO daily_quotes 
    (symbol, quote_date, close, data_source)
    VALUES (?, ?, ?, 'manual')
''', ('VWEHX', '2025-01-24', 10.85, ))

conn.commit()
conn.close()
```

#### Option 5: Use Vanguard API (Advanced)
If you need official Vanguard data, you might need to:
1. Scrape Vanguard website (check their terms of service)
2. Use a paid data provider that includes mutual funds
3. Use Financial Modeling Prep (FMP) API which may have better mutual fund coverage

### Quick Test

Run this to see if VWEHX works now (after the pandas fix):
```bash
python -c "import yfinance as yf; print(yf.Ticker('VWEHX').history(period='5d'))"
```

If you see data, the pandas fix resolved it. If not, VWEHX simply isn't available on Yahoo Finance.

### Recommended Action

1. **Try the fixed code** - The pandas import was missing
2. **If still no data** - Replace VWEHX with HYG (similar ETF with guaranteed data)
3. **Update your symbols list** in `daily_update.py`:

```python
PORTFOLIO_SYMBOLS = [
    'BND', 'BOXX', 'AAPL', 'CSCO', 'CSOAX', 'EFA', 'FNILX', 'FZROX',
    'HYDB', 'HYGH', 'IBIT', 'IVV', 'QQQ', 'SCHD', 'SMYX', 'STAYX',
    'SUSTX', 'FDEV', 'FISOX', 'VIG', 'VOO', 'VTI', 'HYG', 'VWO'  # Changed VWEHX to HYG
]
```

### Other Mutual Funds in Your Portfolio

Check these as well - they may have similar issues:
- **CSOAX** - Calamos Growth & Income Fund
- **FNILX** - Fidelity ZERO Large Cap Index (may work, Fidelity is good with data)
- **FZROX** - Fidelity ZERO Total Market Index
- **SMYX** - May be a mutual fund
- **STAYX** - May be a mutual fund
- **SUSTX** - May be a mutual fund
- **FDEV** - May be a mutual fund
- **FISOX** - May be a mutual fund

### General Rule

**ETFs and Stocks**: Almost always available on Yahoo Finance
**Mutual Funds**: Hit or miss, especially smaller/institutional share classes

If a symbol consistently fails, it's likely a mutual fund without Yahoo coverage. Replace with a similar ETF or use manual entry.