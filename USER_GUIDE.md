# Stock Quote Database System - User Guide

## Quick Start (5 Minutes)

```bash
# 1. Install everything
python quick_start.py

# 2. Fetch 3 years of historical data
python daily_update.py --update --years 3

# 3. View your data
python daily_update.py --stats
```

That's it! Your database now has 3 years of closing prices for all 24 symbols in your portfolio.

## Detailed Walkthrough

### Day 1: Initial Setup

#### Step 1: Install Dependencies
```bash
pip install yfinance pandas numpy
```

#### Step 2: Create Database
```bash
python stock_db_setup.py
```
Creates `stock_quotes.db` in your home directory (`C:\Users\YourName\`)

#### Step 3: Initialize Portfolio
```bash
python daily_update.py --init
```
Adds these 24 symbols to your database:
- **Bonds**: BND, BOXX, HYDB, HYGH
- **Large Cap**: AAPL, CSCO, IVV, VOO, VTI
- **Growth/Tech**: QQQ, IBIT, FNILX
- **Dividend**: SCHD, VIG
- **International**: EFA, VWO, VWEHX
- **Sector**: FZROX, FDEV, FISOX
- **Theme**: CSOAX, SMYX, STAYX, SUSTX

#### Step 4: Fetch Historical Data
```bash
python daily_update.py --update --years 3
```
Downloads ~750 trading days per symbol = ~18,000 total quotes

**Time estimate**: 5-10 minutes for 24 symbols

#### Step 5: Verify
```bash
python daily_update.py --stats
```

Example output:
```
=== Database Statistics ===
Total quotes: 18,247
Unique symbols: 24
Date range: 2022-01-25 to 2025-01-25

=== Per-Symbol Statistics ===
AAPL       759 quotes  2022-01-25 to 2025-01-25
CSCO       759 quotes  2022-01-25 to 2025-01-25
...
```

### Daily Use

#### Morning Routine: Update Yesterday's Closes
```bash
python daily_update.py --update
```
Fetches only missing dates (typically just yesterday's close)

#### View Latest Prices
```python
import sqlite3
import pandas as pd

conn = sqlite3.connect('C:/Users/YourName/stock_quotes.db')

# Latest prices for all symbols
df = pd.read_sql_query('''
    SELECT 
        symbol,
        close as price,
        quote_date as date,
        volume
    FROM daily_quotes d1
    WHERE quote_date = (
        SELECT MAX(quote_date)
        FROM daily_quotes d2
        WHERE d2.symbol = d1.symbol
    )
    ORDER BY symbol
''', conn)

print(df)
conn.close()
```

#### Add New Symbol
```bash
python daily_update.py --add NVDA
python daily_update.py --update
```

### LibreOffice Calc Integration

#### Setup (One-Time)

**Option A: Python-UNO** (Recommended)
1. Locate LibreOffice Python scripts folder:
   ```
   C:\Users\YourName\AppData\Roaming\LibreOffice\4\user\Scripts\python\
   ```
2. Copy `calc_functions.py` to this folder
3. Restart LibreOffice Calc

**Option B: Basic Macro** (Simpler)
1. Copy `stock_fetcher.py` and `calc_quote_lookup.py` to:
   ```
   C:\Users\YourName\
   ```
2. Open LibreOffice Calc
3. Tools → Macros → Edit Macros
4. Copy the macro from README.md into Module1
5. Save and close Basic IDE

#### Using in Spreadsheet

##### Method 1: PYUNO Function (if using Python-UNO)
```
Cell A1: AAPL
Cell B1: =PYUNO("get_quote", A1)                -> Latest close
Cell C1: =PYUNO("get_latest_date", A1)          -> Last update date
Cell D1: =PYUNO("get_quote", A1, "", "high")    -> Latest high
Cell E1: =PYUNO("get_quote", A1, "2025-01-15")  -> Specific date
```

##### Method 2: STOCKQUOTE Macro (if using Basic)
```
Cell A1: AAPL
Cell B1: =STOCKQUOTE(A1)                        -> Latest close
Cell C1: =LATESTDATE(A1)                        -> Last update date
Cell D1: =STOCKQUOTE(A1, "", "high")            -> Latest high
Cell E1: =STOCKQUOTE(A1, "2025-01-15")          -> Specific date
```

##### Complete Portfolio Sheet
```
     A       B          C            D              E           F
1  Symbol  Shares  Latest Price  Latest Date    Value      Change %
2  AAPL    100     =PYUNO...     =PYUNO...      =B2*C2     =...
3  CSCO    500     =PYUNO...     =PYUNO...      =B3*C3     =...
4  VTI     200     =PYUNO...     =PYUNO...      =B4*C4     =...
...
25         Total:                               =SUM(E2:E25)
```

##### Performance Tracking
```
     A       B           C             D                E
1  Symbol  Current   30-Day Ago   Change $         Change %
2  AAPL    =PYUNO... =PYUNO...    =B2-C2          =(B2-C2)/C2*100
```

Calculate 30-day ago date: `=TODAY()-30` then format as "2025-01-15"

### Advanced Usage

#### Custom Queries

##### Top Performers (Last 30 Days)
```python
import sqlite3
import pandas as pd

conn = sqlite3.connect('stock_quotes.db')

query = '''
WITH recent AS (
    SELECT symbol, close as current_price
    FROM daily_quotes
    WHERE quote_date = (SELECT MAX(quote_date) FROM daily_quotes)
),
month_ago AS (
    SELECT symbol, close as month_price
    FROM daily_quotes
    WHERE quote_date = date('now', '-30 days')
)
SELECT 
    r.symbol,
    r.current_price,
    m.month_price,
    (r.current_price - m.month_price) / m.month_price * 100 as pct_change
FROM recent r
JOIN month_ago m ON r.symbol = m.symbol
ORDER BY pct_change DESC
'''

df = pd.read_sql_query(query, conn)
print(df.to_string())
conn.close()
```

##### Volatility Analysis
```python
query = '''
SELECT 
    symbol,
    AVG(high - low) as avg_range,
    MAX(high - low) as max_range,
    MIN(high - low) as min_range,
    STDEV(close) as price_stdev
FROM daily_quotes
WHERE quote_date >= date('now', '-90 days')
GROUP BY symbol
ORDER BY price_stdev DESC
'''

df = pd.read_sql_query(query, conn)
```

##### Export to CSV
```python
import sqlite3
import pandas as pd

conn = sqlite3.connect('stock_quotes.db')

# Export all data for AAPL
df = pd.read_sql_query('''
    SELECT * FROM daily_quotes
    WHERE symbol = 'AAPL'
    ORDER BY quote_date
''', conn)

df.to_csv('AAPL_history.csv', index=False)

# Export entire portfolio
symbols = ['AAPL', 'CSCO', 'VTI']  # etc.
for symbol in symbols:
    df = pd.read_sql_query(f'''
        SELECT quote_date, open, high, low, close, volume
        FROM daily_quotes
        WHERE symbol = '{symbol}'
        ORDER BY quote_date
    ''', conn)
    df.to_csv(f'{symbol}_quotes.csv', index=False)

conn.close()
```

#### Switching Data Sources

##### View Available Sources
```bash
python config.py list
```

Output:
```
=== Configured Data Sources ===
Source          Enabled  Priority  Rate Limit  API Key    Notes
----------------------------------------------------------------------------------------------------
yfinance        YES      1         2000        Not Set    Yahoo Finance via yfinance library - Free
alphavantage    NO       2         25          Not Set    Alpha Vantage - Free tier: 25 requests/day
fmp             NO       3         250         Not Set    Financial Modeling Prep
finnhub         NO       4         60          Not Set    Finnhub - Free tier
```

##### Switch to Alpha Vantage
```bash
# 1. Get API key from https://www.alphavantage.co/support/#api-key
# 2. Configure it
python config.py setkey alphavantage YOUR_API_KEY_HERE
python config.py default alphavantage

# 3. Verify
python config.py active
```

##### Switch Back to yfinance
```bash
python config.py default yfinance
```

### Troubleshooting

#### Problem: "No module named 'yfinance'"
```bash
pip install yfinance
```

#### Problem: "No data for symbol XXXX"
```bash
# Try manually adding and updating
python daily_update.py --add XXXX
python daily_update.py --update
```

#### Problem: "Database is locked"
- Close any programs accessing the database
- Check for Python scripts still running
- Restart and try again

#### Problem: Calc function returns #N/A
1. Check if data exists:
   ```bash
   python daily_update.py --stats
   ```
2. If symbol missing, add it:
   ```bash
   python daily_update.py --add SYMBOL
   python daily_update.py --update
   ```
3. Verify Python path in macro (for Basic method)

#### Problem: Rate limit exceeded
Using yfinance, you're unlikely to hit limits. For other sources:
```bash
python config.py list  # Check rate limits
# Wait or switch to different source
python config.py default yfinance
```

### Performance Tips

#### Faster Updates
```python
# Batch update in single script
from stock_fetcher import StockDataFetcher
import sqlite3

symbols = ['AAPL', 'CSCO', 'VTI']  # Your list
fetcher = StockDataFetcher()

for symbol in symbols:
    quotes = fetcher.fetch_data(symbol, '2025-01-01')
    if quotes:
        fetcher.save_quotes(quotes)
```

#### Database Optimization
```python
import sqlite3

conn = sqlite3.connect('stock_quotes.db')
conn.execute('VACUUM')  # Reclaim space
conn.execute('ANALYZE')  # Update statistics
conn.close()
```

#### Bulk Export
```bash
# Export entire database to CSV files
python -c "
import sqlite3, pandas as pd
conn = sqlite3.connect('stock_quotes.db')
symbols = pd.read_sql_query('SELECT DISTINCT symbol FROM daily_quotes', conn)
for sym in symbols['symbol']:
    df = pd.read_sql_query(f\"SELECT * FROM daily_quotes WHERE symbol='{sym}' ORDER BY quote_date\", conn)
    df.to_csv(f'{sym}.csv', index=False)
conn.close()
"
```

### Automation

#### Windows Task Scheduler
1. Open Task Scheduler
2. Create Basic Task
3. Name: "Stock Quote Daily Update"
4. Trigger: Daily at 6 PM (after market close)
5. Action: Start a program
   - Program: `C:\Python39\python.exe`
   - Arguments: `C:\Users\YourName\daily_update.py --update`
6. Finish

#### Batch Script
Create `update_stocks.bat`:
```batch
@echo off
cd C:\Users\YourName
python daily_update.py --update >> stock_update.log 2>&1
```

Schedule this batch file instead.

### Data Sources Comparison

| Source | Free Tier | API Key | Rate Limit | Best For |
|--------|-----------|---------|------------|----------|
| yfinance | Yes | No | 2000+/day | Most users, default choice |
| Alpha Vantage | Yes | Yes | 25/day | Academic research |
| FMP | Yes | Yes | 250/day | Fundamental data needs |
| Finnhub | Yes | Yes | 60/min | Real-time needs |

**Recommendation**: Start with yfinance. Only switch if you need:
- Fundamental data → FMP
- More frequent updates → Finnhub
- Specific data coverage → Alpha Vantage

### Best Practices

1. **Daily Updates**: Run once per day after market close (6 PM ET)
2. **Backups**: Periodically backup `stock_quotes.db`
3. **Validation**: Check stats weekly to ensure data completeness
4. **New Symbols**: Add immediately, update gets historical automatically
5. **LibreOffice**: Use Calc file templates for consistency

### Your Portfolio Summary

You're tracking these 24 symbols across 6 categories:

**Fixed Income (4)**
- BND, BOXX, HYDB, HYGH

**Large Cap Core (5)**
- AAPL, CSCO, IVV, VOO, VTI

**Growth/Tech (3)**
- QQQ, IBIT, FNILX

**Dividend Focus (2)**
- SCHD, VIG

**International (3)**
- EFA, VWO, VWEHX

**Sector/Theme (7)**
- FZROX, FDEV, FISOX, CSOAX, SMYX, STAYX, SUSTX

At 750 days/symbol × 24 symbols ≈ **18,000 data points** for 3 years.

---

## Need Help?

1. Check README.md for setup instructions
2. Run `python daily_update.py --stats` to diagnose data issues
3. Run `python config.py list` to check configuration
4. Review error logs in console output
