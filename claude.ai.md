# Stock System Regeneration Prompt

Use this prompt to regenerate `stock_system.py` if needed.  (It's what claude.ai spit back to me when I asked it to provide a prompt to build the system that I had just iteratively built with it.)

---

## Stock System Requirements

Create a unified Python system (`stock_system.py`) that manages a local stock quote database with web API and LibreOffice Calc integration.

### Core Features

**Database (SQLite)**
- Tables: config, symbols, daily_quotes, market_closures, data_sources
- Store historical OHLCV data, configuration, and detected market closures
- Default location: `%USERPROFILE%\stock_quotes.db`
- Support custom database path via `--db` parameter

**Data Fetching**
- Multi-source support: yfinance (primary), Alpha Vantage, FMP, Finnhub
- Automatic fallback between sources if one fails
- Smart incremental updates: only fetch dates since last known data
- Auto-fetch unknown symbols when requested (30 days of data)
- Market closure detection using reference symbols (QQQ, CSCO)
- If both reference symbols fail on a date OR 4+ regular symbols fail → mark as market closure
- Skip known market closures in future fetches

**Web Server (Flask)**
- Port 5000, localhost only
- Endpoints:
  - `/quote/SYMBOL` - latest close price
  - `/quote/SYMBOL/now` - live quote with 15-min cache (falls back to last close if market closed)
  - `/quote/SYMBOL/DATE` - historical quote (finds nearest date before if exact match not found)
  - `/quote/SYMBOL/field/FIELD` - specific field (open, high, low, close, volume)
  - `/latest_date/SYMBOL` - most recent date with data
  - `/config` - view configuration (read-only)
  - `/health` - health check
- Return plain numbers for WEBSERVICE() compatibility
- Auto-fetch unknown symbols on first request

**Scheduled Updates**
- Background scheduler using APScheduler
- Default: 3:30 PM local time, Monday-Friday
- Configurable via database config table
- Updates all active symbols automatically
- Runs concurrently with web server

**Smart Update Logic**
- For new symbols: fetch `default_years` (default: 3) of history
- For existing symbols: fetch only from last_date + 1 forward
- Track and skip known market closures
- Use SQL-level locking for concurrent access

**Live Quote Caching**
- Cache duration: configurable (default 900 seconds / 15 minutes)
- Check if market open: 9:30 AM - 4:00 PM local time, Monday-Friday
- If market open: fetch live quote and cache
- If market closed: return last close from database

**Logging**
- Use Python's `logging` module (NOT print statements)
- Log to: `%USERPROFILE%\stock_system.log`
- Level: INFO
- Format: timestamp, level, message

### CLI Commands

```bash
# Server mode (blocking, runs Flask + scheduler)
python stock_system.py --server [--db PATH]

# Initialization
python stock_system.py --init  # Creates DB, tables, adds QQQ and CSCO reference symbols

# Symbol management (multiple --add supported)
python stock_system.py --add AAPL --add CSCO --add TSLA
python stock_system.py --remove SYMBOL  # Asks for confirmation

# Updates
python stock_system.py --update [--years N]  # Smart incremental update
python stock_system.py --trigger-update      # Force immediate update

# Configuration
python stock_system.py --config list
python stock_system.py --config get KEY
python stock_system.py --config set KEY VALUE
python stock_system.py --config set apikey SOURCE KEY  # e.g., alphavantage

# Information
python stock_system.py --stats    # Show symbol breakdown, quote counts
python stock_system.py --dbpath   # Print database location
```

### Configuration (stored in database)

Default values in `config` table:
- `update_time`: "15:30" (3:30 PM local time)
- `cache_duration`: "900" (15 minutes in seconds)
- `default_years`: "3" (years of history for new symbols)
- `apikey_alphavantage`, `apikey_fmp`, `apikey_finnhub`: "" (empty by default)
- `active_source`: "yfinance"
- `source_priority_1` through `source_priority_4`: source names in priority order

### Reference Symbols

Always include QQQ and CSCO as reference symbols because:
- They have 4+ years of reliable data
- Used to detect market closures more accurately
- Auto-added during `--init`

### Dependencies

- flask
- apscheduler
- yfinance
- pandas
- numpy

### Important Design Decisions

1. **Single file**: Everything in `stock_system.py` (~1000 lines)
2. **No hardcoded portfolio**: Use batch files with multiple `--add` commands
3. **SQL locking**: Let SQLite handle concurrent access (timeout: 30 seconds)
4. **Market hours**: Local timezone, no ET assumption
5. **Auto-fetch on first request**: Unknown symbols automatically fetch 30 days
6. **Market closure detection**: Reference symbols OR 4+ regular symbols failing
7. **Nearest-date fallback**: If exact date not found, return closest date before
8. **Server + CLI**: Same file can run as server OR execute CLI commands
9. **All config in database**: No config files, everything in DB

### Error Handling

- Log all errors to file
- Return JSON errors with HTTP status codes from web endpoints
- Graceful fallback: if all data sources fail, log and continue
- Database errors: use SQLite's timeout and retry logic

### Code Structure

Approximate organization (~1000 lines total):

1. **Imports and Configuration** (~50 lines)
   - All imports at top
   - Constants and defaults
   - Logging setup

2. **Database Layer** (~150 lines)
   - `setup_database()` - create all tables
   - `get_db_connection()` - connection helper
   - `initialize_default_config()` - insert defaults
   - `initialize_data_sources()` - setup sources table

3. **Configuration Management** (~100 lines)
   - `get_config(key)` - retrieve config value
   - `set_config(key, value)` - update config
   - `list_config()` - list all config
   - `set_api_key(source, key)` - update API key

4. **Symbol Management** (~100 lines)
   - `add_symbol(symbol)` - add to tracking
   - `remove_symbol(symbol)` - remove with confirmation
   - `get_tracked_symbols()` - list active symbols
   - `update_symbol_tracking(symbol)` - update dates

5. **Market Closure Detection** (~80 lines)
   - `get_market_closures()` - get known closures
   - `mark_market_closure(date)` - mark as closure
   - `detect_market_closures(symbol, failed_dates)` - detection logic

6. **Data Fetching** (~150 lines)
   - `fetch_yfinance(symbol, start, end)` - fetch from Yahoo
   - `fetch_live_quote_yfinance(symbol)` - fetch current price
   - `get_enabled_sources()` - get sources in priority order
   - `fetch_data_multi_source()` - try sources with fallback
   - `save_quotes(quotes)` - save to database
   - `get_last_date_for_symbol(symbol)` - get latest date
   - `get_missing_dates()` - calculate missing dates

7. **Smart Update Logic** (~100 lines)
   - `smart_update_symbol(symbol, years)` - incremental update
   - `update_all_symbols(years)` - update all tracked
   - `auto_fetch_symbol(symbol)` - auto-fetch new symbol (30 days)

8. **Live Quote Caching** (~50 lines)
   - `is_market_open()` - check market hours
   - `get_live_quote(symbol)` - get with caching
   - In-memory cache with threading lock

9. **Statistics** (~50 lines)
   - `show_statistics()` - display formatted stats

10. **Web Server (Flask)** (~200 lines)
    - `@app.route('/')` - documentation page
    - `@app.route('/health')` - health check
    - `@app.route('/config')` - view config
    - `@app.route('/quote/<symbol>')` - latest quote
    - `@app.route('/quote/<symbol>/now')` - live quote
    - `@app.route('/quote/<symbol>/<date>')` - historical
    - `@app.route('/quote/<symbol>/field/<field>')` - specific field
    - `@app.route('/latest_date/<symbol>')` - latest date
    - `get_quote_helper()` - shared quote logic

11. **Scheduled Updates** (~30 lines)
    - `scheduled_update()` - callback for scheduler
    - `start_scheduler()` - initialize APScheduler

12. **Command Line Interface** (~100 lines)
    - `main()` - argparse setup and command routing
    - Handle all CLI commands
    - Start server if `--server` flag

### Key Implementation Notes

**Market Closure Detection Logic:**
```python
if symbol in ['QQQ', 'CSCO']:
    # Reference symbol failed - mark immediately
    mark_market_closure(date)
else:
    # Count how many symbols have no data for this date
    # but have data before and after
    if failed_count >= 4:
        mark_market_closure(date)
```

**Smart Update Logic:**
```python
last_date = get_last_date_for_symbol(symbol)
if last_date is None:
    # New symbol: fetch full history
    start_date = today - timedelta(days=365 * years)
else:
    # Existing: fetch from last_date + 1
    start_date = last_date + timedelta(days=1)
```

**Live Quote Cache:**
```python
with cache_lock:
    if symbol in cache and (now - cache_time < cache_duration):
        return cached_price
    
    if is_market_open():
        fetch_and_cache_live_quote()
    else:
        return_last_close_from_db()
```

**Nearest Date Fallback:**
```python
# Try exact date
cursor.execute('SELECT close FROM daily_quotes WHERE symbol=? AND date=?')
if not found:
    # Find nearest before
    cursor.execute('SELECT close FROM daily_quotes WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT 1')
```

---

## Usage Example After Generation

```bash
# Install dependencies
pip install flask apscheduler yfinance pandas numpy

# Initialize
python stock_system.py --init

# Add symbols
python stock_system.py --add AAPL --add CSCO --add TSLA

# Fetch history
python stock_system.py --update --years 3

# Start server
python stock_system.py --server
```

Then in LibreOffice Calc:
```
=WEBSERVICE("http://localhost:5000/quote/AAPL")
=WEBSERVICE("http://localhost:5000/quote/AAPL/now")
```

---

**End of Regeneration Prompt**