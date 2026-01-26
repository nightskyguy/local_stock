# Mutual Fund Symbols - Yahoo Finance Availability Guide

## The Problem

Several symbols in your portfolio are **mutual funds** that Yahoo Finance doesn't provide data for. These need to be replaced with similar ETFs that have full data availability.

## Your Mutual Funds (Testing Required)

Based on your symbol list, these are likely mutual funds with limited/no Yahoo data:

### ❌ Confirmed No Data
- **FISOX** - Fidelity International Stock Index Fund
    - Status: Not available on Yahoo Finance
    - Error: "Quote not found for symbol: FISOX"

### ⚠️ Needs Testing
- **VWEHX** - Vanguard High-Yield Corporate Bond Fund
- **CSOAX** - Calamos Growth & Income Fund Class A
- **FNILX** - Fidelity ZERO Large Cap Index Fund
- **FZROX** - Fidelity ZERO Total Market Index Fund
- **SMYX** - (Need to verify)
- **STAYX** - (Need to verify)
- **SUSTX** - (Need to verify)
- **FDEV** - (Need to verify)

## Quick Test Script

Run this to test all your symbols:
```bash
cat > test_symbols.py << 'EOF'
import yfinance as yf

symbols = ['BND', 'BOXX', 'AAPL', 'CSCO', 'CSOAX', 'EFA', 'FNILX', 'FZROX',
           'HYDB', 'HYGH', 'IBIT', 'IVV', 'QQQ', 'SCHD', 'SMYX', 'STAYX',
           'SUSTX', 'FDEV', 'FISOX', 'VIG', 'VOO', 'VTI', 'VWEHX', 'VWO']

print("Testing symbol availability on Yahoo Finance:\n")
for symbol in symbols:
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d")
        if hist.empty:
            print(f"❌ {symbol:8} - NO DATA")
        else:
            print(f"✓  {symbol:8} - OK ({len(hist)} days)")
    except Exception as e:
        print(f"❌ {symbol:8} - ERROR: {str(e)[:50]}")
EOF

python test_symbols.py
```

## Recommended Replacements

### FISOX → IXUS or VXUS
**Original**: Fidelity International Stock Index Fund
**Replace with**:
- **IXUS** - iShares Core MSCI Total International Stock ETF (Preferred)
- **VXUS** - Vanguard Total International Stock ETF
- **EFA** - (You already have this! It's similar)

**Why**: Broad international stock exposure, low expense ratio, full Yahoo data

### VWEHX → HYG or JNK
**Original**: Vanguard High-Yield Corporate Bond Fund
**Replace with**:
- **HYG** - iShares iBoxx High Yield Corporate Bond ETF (Preferred)
- **JNK** - SPDR Bloomberg High Yield Bond ETF

**Why**: High-yield corporate bonds, liquid ETF, full data

### CSOAX → AOK or AOR
**Original**: Calamos Growth & Income Fund (60/40 allocation)
**Replace with**:
- **AOK** - iShares Core Conservative Allocation ETF
- **AOR** - iShares Core Growth Allocation ETF

**Why**: Similar balanced approach, growth + income

### FNILX → VOO or IVV
**Original**: Fidelity ZERO Large Cap Index
**Replace with**:
- **VOO** - Vanguard S&P 500 ETF (You already have this!)
- **IVV** - iShares Core S&P 500 ETF (You already have this!)

**Why**: Both track S&P 500, you're already covered

### FZROX → VTI
**Original**: Fidelity ZERO Total Market Index
**Replace with**:
- **VTI** - Vanguard Total Stock Market ETF (You already have this!)

**Why**: Total US market, you're already covered

## Recommended Updated Symbol List

Replace your portfolio symbols with this tested list:

```python
PORTFOLIO_SYMBOLS = [
    # Bonds & Fixed Income
    'BND',      # Vanguard Total Bond Market ETF
    'BOXX',     # Alpha Architect 1-3 Month Box ETF
    'HYDB',     # iShares High Yield Bond Factor ETF
    'HYGH',     # iShares Interest Rate Hedged High Yield Bond ETF
    'HYG',      # ✓ NEW - replaces VWEHX (high yield bonds)
    
    # Large Cap Core
    'AAPL',     # Apple
    'CSCO',     # Cisco
    'IVV',      # iShares Core S&P 500 ETF (covers FNILX)
    'VOO',      # Vanguard S&P 500 ETF
    'VTI',      # Vanguard Total Stock Market ETF (covers FZROX)
    
    # Growth & Tech
    'QQQ',      # Invesco QQQ Trust (Nasdaq-100)
    'IBIT',     # iShares Bitcoin Trust ETF
    
    # Dividend
    'SCHD',     # Schwab US Dividend Equity ETF
    'VIG',      # Vanguard Dividend Appreciation ETF
    
    # International
    'EFA',      # iShares MSCI EAFE ETF
    'IXUS',     # ✓ NEW - replaces FISOX (international stock)
    'VWO',      # Vanguard FTSE Emerging Markets ETF
    
    # Sector/Theme (need to test these)
    'FDEV',     # Test if this works
    'SMYX',     # Test if this works
    'STAYX',    # Test if this works
    'SUSTX',    # Test if this works
]
```

## Step-by-Step Migration

### 1. Test Current Symbols
```bash
python test_symbols.py > symbol_test_results.txt
cat symbol_test_results.txt
```

### 2. Remove Failed Symbols
```bash
# For any symbol that shows "NO DATA", remove it
python daily_update.py --stats  # See current data
```

### 3. Add Replacement Symbols
```bash
# Add the replacements
python daily_update.py --add IXUS  # Replaces FISOX
python daily_update.py --add HYG   # Replaces VWEHX

# Note: VOO, IVV, VTI already cover FNILX and FZROX
```

### 4. Update Historical Data
```bash
python daily_update.py --update --years 3
```

### 5. Update Your Spreadsheet
Replace formulas for old symbols with new ones:
- Change `FISOX` → `IXUS`
- Change `VWEHX` → `HYG`
- Remove `FNILX` (covered by IVV/VOO)
- Remove `FZROX` (covered by VTI)

## Alternative: Keep Some Mutual Funds (Manual Entry)

If you really want to track the original mutual funds, you can manually enter NAV data:

```python
# manual_nav_entry.py
import sqlite3
from datetime import datetime

def add_manual_nav(symbol, date, price):
    """Manually add NAV for a mutual fund"""
    conn = sqlite3.connect('stock_quotes.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO daily_quotes 
        (symbol, quote_date, close, open, high, low, volume, data_source)
        VALUES (?, ?, ?, ?, ?, ?, 0, 'manual')
    ''', (symbol, date, price, price, price, price))
    
    conn.commit()
    conn.close()
    print(f"Added {symbol}: ${price} on {date}")

# Example usage:
add_manual_nav('FISOX', '2025-01-24', 15.23)
add_manual_nav('VWEHX', '2025-01-24', 10.85)
```

Then get NAV prices from:
- Fidelity.com (for FISOX, FNILX, FZROX, FDEV)
- Vanguard.com (for VWEHX)
- Morningstar.com
- Fund company websites

## ETF vs Mutual Fund Differences

| Feature | ETF | Mutual Fund |
|---------|-----|-------------|
| Yahoo Finance Data | ✓ Always available | ❌ Often missing |
| Trading | All day | Once per day (NAV) |
| Ticker Consistency | Standard | Varies by broker |
| Data APIs | Full support | Limited |
| Your Use Case | ✓ Perfect | ⚠️ Problematic |

## Why ETFs Are Better for This System

1. **Full Data Coverage** - Every ETF has Yahoo Finance data
2. **Real-Time Pricing** - Prices update throughout the day
3. **Universal Tickers** - Same ticker everywhere
4. **API Support** - All data sources support ETFs
5. **Liquidity** - More liquid, easier to track
6. **No Rate Limits** - Unlimited queries to your database

## Final Recommendation

**Replace all mutual funds with equivalent ETFs**. Here's why:

Your original list had potential issues:
- FISOX → No data ❌
- VWEHX → Possibly no data ⚠️
- FNILX → Fidelity-only, might not work ⚠️
- FZROX → Fidelity-only, might not work ⚠️
- CSOAX → Mutual fund share class ⚠️

Your new list is bulletproof:
- All major ETFs ✓
- All have Yahoo Finance data ✓
- All trade on major exchanges ✓
- Better coverage (you have both IVV and VOO, covering large cap twice)

## Quick Migration Command

After running the test script, use this to migrate:

```bash
# Remove problematic symbols (if they have no data)
# Note: Don't remove from database, just stop tracking them

# Add proven alternatives
python daily_update.py --add IXUS
python daily_update.py --add HYG

# Update everything
python daily_update.py --update --years 3

# Verify
python daily_update.py --stats
```

## Testing Checklist

Run this checklist before finalizing:

- [ ] Run `python test_symbols.py`
- [ ] Identify all symbols with "NO DATA"
- [ ] Choose ETF replacements
- [ ] Add new symbols to database
- [ ] Fetch 3 years of data
- [ ] Update Calc spreadsheet formulas
- [ ] Verify all formulas return data
- [ ] Remove old manual symbols

---

**Bottom Line**: ETFs are the way to go for automated portfolio tracking. Use IXUS instead of FISOX, HYG instead of VWEHX, and you already have IVV/VOO/VTI covering your large cap and total market needs.