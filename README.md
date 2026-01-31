# Stock Quote System

A unified, self-contained stock quote management system with local database, web API, and LibreOffice Calc integration.

## Features

- **Local SQLite database** for historical quotes
- **Multi-source data fetching** with automatic fallback (yfinance, Finnhub, Alpha Vantage, FMP)
- **Web server** for LibreOffice Calc integration via WEBSERVICE()
- **Live quote caching** (15-minute default)
- **Automatic daily updates** at scheduled time (6:30 PM default)
- **Smart incremental updates** - only fetch new dates
- **Market closure detection** - no requesting data that corresponds to market closures.
- **Single command installation**
- **All-in-one CLI tool** for management

## Quick Start

### Installation (One Command!)

```bash
install.bat
```

This will:
1. Install Python dependencies (Flask, yfinance, pandas, APScheduler)
2. Initialize database with a specific portfolio (and 2 reference symbols) - see [setup.bat](setup.bat)
3. Fetch and keep 3 years of historical data
4. Start the web server

### Using in LibreOffice Calc

Make sure the server is running (`start_server.bat`), then from your local spreadsheet you can use:

```
=WEBSERVICE("http://localhost:5000/quote/AAPL")
=WEBSERVICE("http://localhost:5000/quote/AAPL/now")
=WEBSERVICE("http://localhost:5000/quote/AAPL/2025-01-15")
=WEBSERVICE("http://localhost:5000/quote/AAPL/field/high")
=WEBSERVICE("http://localhost:5000/latest_date/AAPL")
```

## Command Line Interface

### Server Mode
```bash
# Start web server with automatic scheduled updates
python stock_system.py --server

# Use custom database location
python stock_system.py --db C:\path\to\database.db --server
```
Default location is %USERPROFILE%\stock_quotes.db

### Symbol Management
```bash
# Initialize database (includes QQQ and CSCO as reference symbols)
python stock_system.py --init

# Add symbols (can add multiple at once)
python stock_system.py --add AAPL --add NVDA --add TSLA

# Remove symbol (no confirmation - be careful!)
python stock_system.py --remove FISOX
```

### Updates
```bash
# Force an update of all symbols (smart incremental update)
python stock_system.py --update

# Update with specific history for new symbols
python stock_system.py --update --years 5
```

### Configuration
```bash
# List all configuration information
python stock_system.py --config list

# List specific configuration value
python stock_system.py --config get update_time

# Set configuration
python stock_system.py --config set update_time "17:30"
python stock_system.py --config set cache_duration 600
python stock_system.py --config set default_years 5

# Set API keys and fetch priority (lower number is higher priority)
python stock_system.py --config set apikey alphavantage YOUR_KEY_HERE
python stock_system.py --config set apikey fmp YOUR_KEY_HERE
python stock_system.py --config set priority alphavantage 4
```

### Information
```bash
# Show database statistics
python stock_system.py --stats

# Show database file location
python stock_system.py --dbpath
```

## Configuration Options

All configuration is stored in the database:

| Key | Default | Description |
|-----|---------|-------------|
| `update_time` | `15:30` | Time for automatic updates (local time, Mon-Fri) |
| `cache_duration` | `900` | Live quote cache duration in seconds (15 minutes) |
| `default_years` | `3` | Years of history to fetch for new symbols |


## How It Works

### Smart Updates

**New Symbols:**
- Fetches full history (default: 3 years)
- Stores permanently in database

**Existing Symbols:**
- Only fetches from last known date forward
- Skips known market closures
- Detects new market closures automatically

**Auto-Fetch Unknown Symbols:**
- When you request `/quote/UNKNOWN_SYMBOL`
- Automatically fetches 30 days of data
- Stores in database for future use

### Market Closure Detection

The system automatically detects market closures:
- Uses QQQ and CSCO as reference symbols (known to have 4+ years of data)
- If both reference symbols fail on a date → market closure
- If 4+ regular symbols fail on same date → market closure
- Stops requesting data for detected closure dates

### Live Quote Caching

`/quote/SYMBOL/now` endpoint:
- During market hours (9:30 AM - 4:00 PM local, Mon-Fri): fetches live quote
- Caches for 15 minutes (configurable)
- Outside market hours: returns last close from database

### Scheduled Updates

- Runs automatically at configured time (default: 5:30 PM, Mon-Fri)
- Updates all active symbols
- Detects and marks market closures
- Runs in background (doesn't block web server)

## API Endpoints

### Quote Endpoints
- `GET /quote/<symbol>` - Latest close price
- `GET /quote/<symbol>/now` - Live quote with caching
- `GET /quote/<symbol>/<date>` - Historical quote (YYYY-MM-DD)
- `GET /quote/<symbol>/field/<field>` - Latest value for field (open, high, low, close, volume)

### Info Endpoints
- `GET /latest_date/<symbol>` - Most recent date with data
- `GET /config` - View configuration (read-only)
- `GET /health` - Server health check
- `GET /` - API documentation

## Database Schema

### Tables

**config** - System configuration
- key, value, description, updated_at

**symbols** - Tracked symbols
- symbol, name, notes, first_fetch_date, last_fetch_date, active, added_at

**daily_quotes** - Historical quotes
- symbol, quote_date, open, high, low, close, volume, dividends, stock_splits, data_source, last_updated

**market_closures** - Detected market closure dates
- date, confirmed_count, created_at

**data_sources** - API source configuration
- source_name, api_key, rate_limit, enabled, priority, notes

## Files

- `stock_system.py` - Main unified system (all-in-one)
- `setup_portfolio.bat` - Initialize database with your portfolio
- `install.bat` - One-command installer
- `start_server.bat` - Start web server
- `stock_system.log` - Log file (in %USERPROFILE%)
- `stock_quotes.db` - SQLite database (in %USERPROFILE%)

## Troubleshooting

### LibreOffice WEBSERVICE Error 540

If you get Error 540, LibreOffice is blocking localhost connections. See the main documentation for configuration steps.

### Server Won't Start

```bash
# Check if database exists
python stock_system.py --dbpath

# If not, initialize
python stock_system.py --init
```

### Missing Data for Symbol

```bash
# Manual update
python stock_system.py --update

# Or just request the symbol - it will auto-fetch
# Visit: http://localhost:5000/quote/NEWSYMBOL
```

### Check Logs

Log file location: `%USERPROFILE%\stock_system.log`

```bash
type %USERPROFILE%\stock_system.log
```

## Advanced Usage

### Multiple Databases

```bash
# Work database
python stock_system.py --db C:\work\stocks.db --server

# Personal database
python stock_system.py --db C:\personal\stocks.db --stats
```

### Custom Portfolio

Create your own batch file:

```batch
@echo off
python stock_system.py --init
python stock_system.py --add NVDA --add AMD --add INTC --add MSFT
python stock_system.py --update --years 5
```

### API Key Setup

```bash
# Get free API keys from:
# - Alpha Vantage: https://www.alphavantage.co/support/#api-key
# - FMP: https://financialmodelingprep.com/developer/docs/
# - Finnhub: https://finnhub.io/register

# Configure
python stock_system.py --config set apikey finnhub YOUR_KEY
python stock_system.py --config set apikey alphavantage YOUR_KEY
python stock_system.py --config set apikey fmp YOUR_KEY
```

## Requirements

- Windows 10/11
- Python 3.8+
- Internet connection for data fetching
- ~10MB disk space per year of data per symbol

## Dependencies

Automatically installed by `install.bat`:
- Flask - Web server
- APScheduler - Background task scheduling
- yfinance - Primary data source
- pandas - Data processing
- numpy - Numerical operations

## License

Open source - use freely for personal or commercial use.

## Support

Check the log file for errors: `%USERPROFILE%\stock_system.log`

For issues, check:
1. Is the server running? (`start_server.bat`)
2. Does the database exist? (`python stock_system.py --dbpath`)
3. Are there quotes in the database? (`python stock_system.py --stats`)
4. Check the logs for errors

## Tips

1. **Run server on startup**: Add `start_server.bat` to Windows Startup folder
2. **Backup database**: Copy `%USERPROFILE%\stock_quotes.db` periodically
3. **Performance**: System handles hundreds of symbols efficiently
4. **Data quality**: yfinance provides reliable free data for most stocks
5. **Fallback sources**: Configure API keys for backup data sources