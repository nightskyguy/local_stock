# Stock Quote Database System for LibreOffice Calc

A complete system for managing stock market data locally with LibreOffice Calc integration.

## Overview

This system:
- Stores stock quotes in a local SQLite database
- Supports multiple data sources (yfinance, Alpha Vantage, FMP, Finnhub)
- Automatically fetches 3 years of historical data
- Integrates with LibreOffice Calc for portfolio tracking
- Avoids rate limits by caching data locally
- Allows on-demand fetching of missing data

## Data Sources Comparison

### Free Options (No API Key Required)
1. **yfinance** (Default, Recommended)
   - No API key needed
   - 2000+ requests/day
   - Historical data back to IPO
   - Real-time 15-min delayed
   - Best for: Personal use, historical analysis

### Free Options (API Key Required)
2. **Alpha Vantage**
   - Free tier: 25 requests/day
   - Get key: https://www.alphavantage.co/support/#api-key
   - 20+ years historical data
   
3. **Financial Modeling Prep (FMP)**
   - Free tier: 250 requests/day
   - Get key: https://site.financialmodelingprep.com/developer/docs/
   - 30+ years historical data
   - Best for: Fundamental data needs

4. **Finnhub**
   - Free tier: 60 calls/minute
   - Get key: https://finnhub.io/register
   - Real-time data
   - Best for: Frequent updates

## Installation

### Prerequisites
```bash
# Install Python 3.8 or higher
# Install required packages
pip install yfinance pandas numpy

# Optional: For Alpha Vantage support
pip install alpha-vantage

# Optional: For other data sources
pip install requests
```

### Setup Steps

1. **Create the Database**
```bash
python stock_db_setup.py
```
This creates `stock_quotes.db` in your home directory.

2. **Initialize Your Portfolio**
```bash
python daily_update.py --init
```
This adds your portfolio symbols to the database.

3. **Fetch Historical Data (3 years)**
```bash
python daily_update.py --update --years 3
```
This will take a few minutes depending on the number of symbols.

4. **Check Statistics**
```bash
python daily_update.py --stats
```

## Your Portfolio Symbols

The system is pre-configured with your symbols:
```
BND, BOXX, AAPL, CSCO, CSOAX, EFA, FNILX, FZROX,
HYDB, HYGH, IBIT, IVV, QQQ, SCHD, SMYX, STAYX,
SUSTX, FDEV, FISOX, VIG, VOO, VTI, VWEHX, VWO
```

## LibreOffice Calc Integration

### Method 1: Python-UNO (Recommended)

1. Copy `calc_functions.py` to LibreOffice Python scripts folder:
   - Windows: `%APPDATA%\LibreOffice\4\user\Scripts\python\`
   - Location may vary by version

2. In Calc, use the PYUNO function:
```
=PYUNO("get_quote", "AAPL")                     -> Latest close for AAPL
=PYUNO("get_quote", "AAPL", "2025-01-15")       -> Specific date
=PYUNO("get_quote", "AAPL", "", "high")         -> Latest high
=PYUNO("get_latest_date", "AAPL")               -> Last update date
=PYUNO("refresh_symbol", "AAPL")                -> Force refresh
```

### Method 2: LibreOffice Basic Macro (Alternative)

1. Copy these files to your home directory:
   - `stock_fetcher.py`
   - `calc_quote_lookup.py`

2. In Calc: Tools → Macros → Edit Macros → Standard → Module1

3. Paste this macro:
```vb
Function STOCKQUOTE(symbol As String, Optional quoteDate As String, Optional field As String) As Variant
    Dim pythonPath As String
    Dim scriptPath As String
    Dim cmd As String
    Dim oShell As Object
    Dim result As String
    
    ' Set defaults
    If IsMissing(quoteDate) Then quoteDate = ""
    If IsMissing(field) Then field = "close"
    
    ' Build command
    pythonPath = "python"  ' or full path: "C:\Python39\python.exe"
    scriptPath = Environ("USERPROFILE") & "\calc_quote_lookup.py"
    cmd = pythonPath & " """ & scriptPath & """ " & symbol & " " & quoteDate & " " & field
    
    ' Execute and capture output
    oShell = CreateObject("WScript.Shell")
    Set oExec = oShell.Exec(cmd)
    
    ' Wait for result
    Do While oExec.Status = 0
        Wait 10
    Loop
    
    result = oExec.StdOut.ReadAll()
    
    ' Parse result
    If InStr(result, "ERROR") > 0 Then
        STOCKQUOTE = CVErr(xlErrNA)
    Else
        STOCKQUOTE = CDbl(Trim(result))
    End If
End Function

Function LATESTDATE(symbol As String) As String
    Dim cmd As String
    Dim oShell As Object
    Dim result As String
    
    cmd = "python " & Environ("USERPROFILE") & "\calc_quote_lookup.py " & symbol & " latest_date"
    oShell = CreateObject("WScript.Shell")
    Set oExec = oShell.Exec(cmd)
    
    Do While oExec.Status = 0
        Wait 10
    Loop
    
    LATESTDATE = Trim(oExec.StdOut.ReadAll())
End Function
```

4. Use in spreadsheet:
```
=STOCKQUOTE("AAPL")                    -> Latest close
=STOCKQUOTE("AAPL", "2025-01-15")      -> Specific date
=STOCKQUOTE("AAPL", "", "high")        -> Latest high
=LATESTDATE("AAPL")                    -> Last update
```

## Daily Maintenance

### Update All Symbols
```bash
python daily_update.py --update
```
Run this daily (or schedule it) to fetch the latest quotes.

### Add New Symbol
```bash
python daily_update.py --add NVDA
python daily_update.py --update
```

### Change Data Source

Edit the database to enable different sources:
```python
import sqlite3
conn = sqlite3.connect('stock_quotes.db')
cursor = conn.cursor()

# View current sources
cursor.execute('SELECT * FROM data_sources ORDER BY priority')
for row in cursor.fetchall():
    print(row)

# Change to Alpha Vantage (need API key)
cursor.execute('UPDATE data_sources SET enabled=0')  # Disable all
cursor.execute('''
    UPDATE data_sources 
    SET enabled=1, api_key='YOUR_API_KEY_HERE' 
    WHERE source_name='alphavantage'
''')
conn.commit()
conn.close()
```

## Database Schema

### daily_quotes table
- `symbol`: Stock ticker (e.g., 'AAPL')
- `quote_date`: Trading date (YYYY-MM-DD)
- `open`, `high`, `low`, `close`: Price data
- `volume`: Trading volume
- `dividends`: Dividend amount
- `stock_splits`: Split ratio
- `data_source`: Where data came from
- `last_updated`: Timestamp

### symbols table
- `symbol`: Stock ticker (PRIMARY KEY)
- `name`: Company name
- `first_fetch_date`, `last_fetch_date`: Data range
- `active`: Track this symbol? (1/0)

### data_sources table
- `source_name`: Name of API
- `api_key`: API key (if required)
- `rate_limit`: Requests per day
- `enabled`: Use this source? (1/0)
- `priority`: Lower number = higher priority

## Troubleshooting

### "No module named 'yfinance'"
```bash
pip install yfinance
```

### "No data for symbol"
```bash
# Manually fetch
python daily_update.py --add SYMBOL
python daily_update.py --update
```

### Database locked error
Close any programs accessing the database and try again.

### Python not found in Calc
Ensure Python is in your PATH, or use full path in macro:
```vb
pythonPath = "C:\Python39\python.exe"
```

## Advanced Usage

### Query Database Directly
```python
import sqlite3
import pandas as pd

conn = sqlite3.connect('stock_quotes.db')

# Get all AAPL quotes
df = pd.read_sql_query('''
    SELECT * FROM daily_quotes 
    WHERE symbol = 'AAPL' 
    ORDER BY quote_date DESC
''', conn)

conn.close()
```

### Batch Export to CSV
```python
import sqlite3
import pandas as pd

conn = sqlite3.connect('stock_quotes.db')
symbols = ['AAPL', 'CSCO', 'MSFT']

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

## Performance Tips

1. **Index Usage**: Queries on `(symbol, quote_date)` are optimized
2. **Batch Updates**: Update all symbols at once rather than individually
3. **Cache Locally**: The whole point! Avoid re-fetching the same data
4. **Schedule Updates**: Run daily updates during off-hours

## License and Disclaimers

This system uses:
- **yfinance**: Not affiliated with Yahoo. For personal use only.
- **Stock data**: For informational purposes only, not investment advice
- **No warranty**: Use at your own risk

## Support

For issues with:
- yfinance: https://github.com/ranaroussi/yfinance
- Alpha Vantage: https://www.alphavantage.co/support/
- This system: Check database stats and logs

## Future Enhancements

Potential additions:
- Support for more data sources (IEX Cloud, Polygon, etc.)
- Intraday data (minute/hourly bars)
- Fundamental data (P/E ratios, earnings, etc.)
- Automatic anomaly detection
- Web dashboard for visualization
- Excel integration (similar to Calc)

---

**Database Location**: `%USERPROFILE%\stock_quotes.db`
**Last Updated**: January 2025
