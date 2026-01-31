"""
File: stock_system.py
Unified Stock Quote Management System

Features:
- Local SQLite database for historical quotes
- Multi-source data fetching (yfinance, Alpha Vantage, FMP, Finnhub)
- Web server for LibreOffice Calc integration
- Live quote caching with configurable duration
- Automatic daily updates at scheduled time
- Smart incremental updates (only fetch new dates)
- Market closure detection and tracking
- CLI interface for all operations

Usage:
    # Server mode
    python stock_system.py --server [--db path]
    
    # Symbol management
    python stock_system.py --init
    python stock_system.py --add AAPL --add CSCO --add TSLA
    python stock_system.py --remove SYMBOL
    
    # Updates
    python stock_system.py --update [--years N]
    
    # Configuration
    python stock_system.py --config list
    python stock_system.py --config set KEY VALUE
    python stock_system.py --config set apikey SOURCE KEY
    
    # Info
    python stock_system.py --stats
    python stock_system.py --dbpath

Web API:
    http://localhost:5000/quote/AAPL              - Latest close
    http://localhost:5000/quote/AAPL/now          - Live quote (cached 15min)
    http://localhost:5000/quote/AAPL/2025-01-15   - Historical quote
    http://localhost:5000/quote/AAPL/field/high   - Specific field
    http://localhost:5000/latest_date/AAPL        - Most recent date
"""

import sqlite3
import argparse
import os
import sys
import logging
from datetime import datetime, timedelta
import time
import yfinance as yf
import pandas as pd
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
import threading
import socket
import atexit

# Get Lock File with PID (if it exists)
LOCK_FILE = os.path.join(os.environ.get('USERPROFILE', os.path.expanduser('~')), 'stock_system.lock')
SERVER_PORT_FILE = os.path.join(os.environ.get('USERPROFILE', os.path.expanduser('~')), 'stock_system.port')

# ============================================================================
# CONFIGURATION
# ============================================================================

DEFAULT_DB_PATH = os.path.join(os.environ.get('USERPROFILE', os.path.expanduser('~')), 'stock_quotes.db')
LOG_PATH = os.path.join(os.environ.get('USERPROFILE', os.path.expanduser('~')), 'stock_system.log')

# Reference symbols for market closure detection (must have 4+ years of data)
REFERENCE_SYMBOLS = ['QQQ', 'CSCO']

# Holidays from finnhub
MARKET_CLOSURES = [{'eventName': 'Christmas', 'atDate': '2027-12-24', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Thanksgiving Day', 'atDate': '2027-11-26', 'tradingHour': '09:30-13:00', 'postMarket': '13:00:17:00'}, {'eventName': 'Thanksgiving Day', 'atDate': '2027-11-25', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Labor Day', 'atDate': '2027-09-06', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Independence Day', 'atDate': '2027-07-05', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Juneteenth', 'atDate': '2027-06-18', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Memorial Day', 'atDate': '2027-05-31', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Good Friday', 'atDate': '2027-03-26', 'tradingHour': '', 'postMarket': ''}, {'eventName': "Washington's Birthday", 'atDate': '2027-02-15', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Birthday of Martin Luther King, Jr', 'atDate': '2027-01-18', 'tradingHour': '', 'postMarket': ''}, {'eventName': "New Year's Day", 'atDate': '2027-01-01', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Christmas', 'atDate': '2026-12-25', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Christmas', 'atDate': '2026-12-24', 'tradingHour': '09:30-13:00', 'postMarket': '13:00:17:00'}, {'eventName': 'Thanksgiving Day', 'atDate': '2026-11-27', 'tradingHour': '09:30-13:00', 'postMarket': '13:00:17:00'}, {'eventName': 'Thanksgiving Day', 'atDate': '2026-11-26', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Labor Day', 'atDate': '2026-09-07', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Independence Day', 'atDate': '2026-07-03', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Juneteenth', 'atDate': '2026-06-19', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Memorial Day', 'atDate': '2026-05-25', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Good Friday', 'atDate': '2026-04-03', 'tradingHour': '', 'postMarket': ''}, {'eventName': "Washington's Birthday", 'atDate': '2026-02-16', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Birthday of Martin Luther King, Jr', 'atDate': '2026-01-19', 'tradingHour': '', 'postMarket': ''}, {'eventName': "New Year's Day", 'atDate': '2026-01-01', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Christmas', 'atDate': '2025-12-25', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Christmas', 'atDate': '2025-12-24', 'tradingHour': '09:30-13:00', 'postMarket': '13:00:17:00'}, {'eventName': 'Thanksgiving Day', 'atDate': '2025-11-28', 'tradingHour': '09:30-13:00', 'postMarket': '13:00:17:00'}, {'eventName': 'Thanksgiving Day', 'atDate': '2025-11-27', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Labor Day', 'atDate': '2025-09-01', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Independence Day', 'atDate': '2025-07-04', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Independence Day', 'atDate': '2025-07-03', 'tradingHour': '09:30-13:00', 'postMarket': ''}, {'eventName': 'Juneteenth', 'atDate': '2025-06-19', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Memorial Day', 'atDate': '2025-05-26', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Good Friday', 'atDate': '2025-04-18', 'tradingHour': '', 'postMarket': ''}, {'eventName': "Washington's Birthday", 'atDate': '2025-02-17', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Birthday of Martin Luther King, Jr', 'atDate': '2025-01-20', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'National Mourning Day', 'atDate': '2025-01-09', 'tradingHour': '', 'postMarket': ''}, {'eventName': "New Year's Day", 'atDate': '2025-01-01', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Christmas', 'atDate': '2024-12-25', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Christmas', 'atDate': '2024-12-24', 'tradingHour': '09:30-13:00', 'postMarket': ''}, {'eventName': 'Thanksgiving Day', 'atDate': '2024-11-29', 'tradingHour': '09:30-13:00', 'postMarket': ''}, {'eventName': 'Thanksgiving Day', 'atDate': '2024-11-28', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Labor Day', 'atDate': '2024-09-02', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Independence Day', 'atDate': '2024-07-04', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Independence Day', 'atDate': '2024-07-03', 'tradingHour': '09:30-13:00', 'postMarket': ''}, {'eventName': 'Juneteenth', 'atDate': '2024-06-19', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Memorial Day', 'atDate': '2024-05-27', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Good Friday', 'atDate': '2024-03-29', 'tradingHour': '', 'postMarket': ''}, {'eventName': "Washington's Birthday", 'atDate': '2024-02-19', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Birthday of Martin Luther King, Jr', 'atDate': '2024-01-15', 'tradingHour': '', 'postMarket': ''}, {'eventName': "New Year's Day", 'atDate': '2024-01-01', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Christmas Day', 'atDate': '2023-12-25', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Thanksgiving Day', 'atDate': '2023-11-24', 'tradingHour': '09:30-13:00', 'postMarket': ''}, {'eventName': 'Thanksgiving Day', 'atDate': '2023-11-23', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Labor Day', 'atDate': '2023-09-04', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Independence Day', 'atDate': '2023-07-04', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Independence Day', 'atDate': '2023-07-03', 'tradingHour': '09:30-13:00', 'postMarket': ''}, {'eventName': 'Juneteenth', 'atDate': '2023-06-19', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Memorial Day', 'atDate': '2023-05-29', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Good Friday', 'atDate': '2023-04-07', 'tradingHour': '', 'postMarket': ''}, {'eventName': "Washington's Birthday", 'atDate': '2023-02-20', 'tradingHour': '', 'postMarket': ''}, {'eventName': 'Birthday of Martin Luther King, Jr', 'atDate': '2023-01-16', 'tradingHour': '', 'postMarket': ''}, {'eventName': "New Year's Day", 'atDate': '2023-01-02', 'tradingHour': '', 'postMarket': ''}]


# Default configuration values
DEFAULT_CONFIG = {
    'update_time': '18:30',           # 6:30 PM local time
    'cache_duration': '900',          # 15 minutes in seconds
    'default_years': '3',             # Default history to fetch
    'apikey_alphavantage': '',
    'apikey_fmp': '',
    'apikey_finnhub': '',
}

# For rate limiting alphavantage
_last_alphavantage_call = 0
_alphavantage_delay = 12  # 12 seconds = 5 calls per minute (safe for free tier)


# Setup logging
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global database path (can be overridden by --db argument)
DB_PATH = DEFAULT_DB_PATH

# Live quote cache
live_quote_cache = {}  # {symbol: (price, timestamp)}
cache_lock = threading.Lock()

# ============================================================================
# DATABASE LAYER
# ============================================================================

def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn

def setup_database():
    """Initialize database with all required tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Config table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            description TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Symbols table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS symbols (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            notes TEXT,
            first_fetch_date TEXT,
            last_fetch_date TEXT,
            active INTEGER DEFAULT 1,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Daily quotes table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_quotes (
            symbol TEXT NOT NULL,
            quote_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            dividends REAL DEFAULT 0,
            stock_splits REAL DEFAULT 0,
            data_source TEXT,
            last_updated TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, quote_date)
        )
    ''')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_symbol_date ON daily_quotes(symbol, quote_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_date ON daily_quotes(quote_date)')
    
    # Market closures table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS market_closures (
            date TEXT PRIMARY KEY,
            confirmed_count INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Data sources table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS data_sources (
            source_name TEXT PRIMARY KEY,
            api_key TEXT,
            rate_limit INTEGER,
            priority INTEGER DEFAULT 99,
            notes TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info(f"Database initialized: {DB_PATH}")

def initialize_default_config():
    """Insert default configuration values"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    for key, value in DEFAULT_CONFIG.items():
        cursor.execute('''
            INSERT OR IGNORE INTO config (key, value, description)
            VALUES (?, ?, ?)
        ''', (key, value, f'Default: {value}'))
    
    conn.commit()
    conn.close()
    logger.info("Default configuration initialized")

def initialize_data_sources():
    """Initialize data sources table"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    sources = [
        ('yfinance', '', 2000, 1, 1, 'Yahoo Finance via yfinance - Free, no API key'),
        ('finnhub', '', 60, 0, 2, 'Finnhub - Free tier: 60 calls/min'),
        ('alphavantage', '', 25, 0, 3, 'Alpha Vantage - Free tier: 25 req/day'),
        ('fmp', '', 250, 0, 4, 'Financial Modeling Prep - Free tier: 250 req/day'),
    ]
    
    for source in sources:
        cursor.execute('''
            INSERT OR IGNORE INTO data_sources (source_name, api_key, rate_limit, priority, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', source)
    
    conn.commit()
    conn.close()
    logger.info("Data sources initialized")

# ============================================================================
# CONFIGURATION MANAGEMENT
# ============================================================================

def get_config(key):
    """Get configuration value"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM config WHERE key = ?', (key,))
    result = cursor.fetchone()
    conn.close()
    return result['value'] if result else None

def set_config(key, value):
    """Set configuration value"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO config (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
    ''', (key, value))
    conn.commit()
    conn.close()
    logger.info(f"Config updated: {key} = {value}")

def list_config():
    """List all configuration including data sources"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get config table
    cursor.execute('SELECT key, value, description FROM config ORDER BY key')
    config_results = cursor.fetchall()
    
    # Get data sources table
    cursor.execute('''
        SELECT source_name, api_key, rate_limit, priority 
        FROM data_sources 
        ORDER BY priority ASC
    ''')
    sources_results = cursor.fetchall()
    
    conn.close()
    
    return {
        'config': config_results,
        'data_sources': sources_results
    }

def set_source_priority(source, priority):
    """Set data source priority """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE data_sources SET priority = ? WHERE source_name = ?', (source, priority))
    conn.commit()
    conn.close()

def set_api_key(source, key):
    """Set API key for data source"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE data_sources SET api_key = ? WHERE source_name = ?', (key, source))
    conn.commit()
    conn.close()
    
    # Also update config table for compatibility
    set_config(f'apikey_{source}', key)
    logger.info(f"API key set for {source}")

# ============================================================================
# SYMBOL MANAGEMENT
# ============================================================================

def add_symbol(symbol):
    """Add symbol to tracked list"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT INTO symbols (symbol, active)
            VALUES (?, 1)
        ''', (symbol.upper(),))
        conn.commit()
        logger.info(f"Symbol added: {symbol.upper()}")
        print(f"Added symbol: {symbol.upper()}")
        return True
    except sqlite3.IntegrityError:
        logger.warning(f"Symbol already exists: {symbol.upper()}")
        print(f"Symbol already exists: {symbol.upper()}")
        return False
    finally:
        conn.close()

def remove_symbol(symbol):
    """Remove symbol and all its data"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if symbol exists
    cursor.execute('SELECT COUNT(*) as count FROM symbols WHERE symbol = ?', (symbol.upper(),))
    if cursor.fetchone()['count'] == 0:
        print(f"Symbol not found: {symbol.upper()}")
        conn.close()
        return
    
    # Confirm deletion
    cursor.execute('SELECT COUNT(*) as count FROM daily_quotes WHERE symbol = ?', (symbol.upper(),))
    quote_count = cursor.fetchone()['count']
    
    #// response = input(f"Delete {symbol.upper()} and {quote_count} quotes? (yes/no): ")
    #// if response.lower() != 'yes':
    #//     print("Cancelled")
    #//     conn.close()
    #//    return
    
    # Delete data
    cursor.execute('DELETE FROM daily_quotes WHERE symbol = ?', (symbol.upper(),))
    cursor.execute('DELETE FROM symbols WHERE symbol = ?', (symbol.upper(),))
    conn.commit()
    conn.close()
    
    logger.info(f"Symbol removed: {symbol.upper()} ({quote_count} quotes deleted)")
    print(f"Removed {symbol.upper()} and {quote_count} quotes")

def get_tracked_symbols():
    """Get list of active symbols"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT symbol FROM symbols WHERE active = 1 ORDER BY symbol')
    symbols = [row['symbol'] for row in cursor.fetchall()]
    conn.close()
    return symbols

def update_symbol_tracking(symbol):
    """Update first/last fetch dates for symbol"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT MIN(quote_date) as first, MAX(quote_date) as last
        FROM daily_quotes
        WHERE symbol = ?
    ''', (symbol,))
    
    result = cursor.fetchone()
    if result and result['first']:
        cursor.execute('''
            UPDATE symbols
            SET first_fetch_date = ?, last_fetch_date = ?
            WHERE symbol = ?
        ''', (result['first'], result['last'], symbol))
        conn.commit()
    
    conn.close()

# ============================================================================
# MARKET CLOSURE DETECTION
# ============================================================================

def get_market_closures():
    """Get list of known market closure dates with metadata"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT date
        FROM market_closures
        ORDER BY date DESC
    ''')
    closures = cursor.fetchall()
    conn.close()
    
    # Format with day names
    result = []
    for row in closures:
        date_str = row['date']
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        day_name = date_obj.strftime('%A')
        
        result.append({
            'day': day_name,
            'date': date_str
        })
    
    return result

def mark_market_closure(date):
    """Mark a date as market closure"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO market_closures (date, confirmed_count, created_at)
        VALUES (?, 1, CURRENT_TIMESTAMP)
    ''', (date,))
    conn.commit()
    conn.close()
    logger.info(f"Marked market closure: {date}")

def detect_market_closures(symbol, failed_dates):
    """
    Detect market closures based on failed fetch attempts
    Mark as closure if:
    - Symbol is a reference symbol (QQQ or CSCO) and fetch failed
    """
    if not failed_dates:
        return
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    is_reference = symbol in REFERENCE_SYMBOLS
    
    for date_str in failed_dates:
        # Skip weekends
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        if date_obj.weekday() >= 5:
            continue
        
        if is_reference:
            # Reference symbol failed - mark immediately
            mark_market_closure(date_str)

    conn.close()

# ============================================================================
# DATA FETCHING
# ============================================================================

def fetch_yfinance(symbol, start_date, end_date):
    """Fetch data from Yahoo Finance via yfinance"""
    try:
        ticker = yf.Ticker(symbol)

        # yfinance end date is EXCLUSIVE - add 1 day to include end_date
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
        end_date_inclusive = end_dt.strftime('%Y-%m-%d')
        
        df = ticker.history(start=start_date, end=end_date_inclusive)        
        
        if df.empty:
            return None
        
        quotes = []
        for date, row in df.iterrows():
            quotes.append({
                'symbol': symbol,
                'quote_date': date.strftime('%Y-%m-%d'),
                'open': float(row['Open']) if pd.notna(row['Open']) else None,
                'high': float(row['High']) if pd.notna(row['High']) else None,
                'low': float(row['Low']) if pd.notna(row['Low']) else None,
                'close': float(row['Close']) if pd.notna(row['Close']) else None,
                'volume': int(row['Volume']) if pd.notna(row['Volume']) else 0,
                'dividends': float(row.get('Dividends', 0)) if pd.notna(row.get('Dividends', 0)) else 0,
                'stock_splits': float(row.get('Stock Splits', 0)) if pd.notna(row.get('Stock Splits', 0)) else 0,
                'data_source': 'yfinance'
            })
        
        return quotes
    
    except Exception as e:
        logger.error(f"yfinance: fetch failed for {symbol}: {e}")
        return None

def fetch_live_quote_yfinance(symbol):
    """Fetch current live quote from yfinance"""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        
        # Try different price fields
        price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose')
        
        if price:
            return float(price)
        return None
    
    except Exception as e:
        logger.error(f"Live quote fetch failed for {symbol}: {e}")
        return None

def get_enabled_sources():
    """
    Get list of data sources in priority order that are ready to use.
    
    Returns sources that either:
    - Don't require an API key (yfinance), OR
    - Have an API key configured
    
    Returns:
        List of tuples: [(source_name, api_key), ...]
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT source_name, api_key, rate_limit
        FROM data_sources
        WHERE source_name = 'yfinance' 
           OR (api_key IS NOT NULL AND api_key != '' and priority < 98)
        ORDER BY priority ASC
    ''')
    sources = cursor.fetchall()
    conn.close()
    return [(row['source_name'], row['api_key']) for row in sources]

def fetch_data_multi_source(symbol, start_date, end_date):
    """Fetch data trying multiple sources with fallback"""
    sources = get_enabled_sources()
    
    if not sources:
        logger.error("No enabled data sources configured")
        return None
    
    for source_name, api_key in sources:       
        try:
            if source_name == 'yfinance':
                quotes = fetch_yfinance(symbol, start_date, end_date)
            else:
                if not api_key:
                    logger.warning(f"{source_name}: Skipping - no API key configured")
                    continue
                   
                if source_name == 'alphavantage':
                    quotes = fetch_alphavantage(symbol, start_date, end_date, api_key)
                        
                elif source_name == 'fmp':
                    quotes = fetch_fmp(symbol, start_date, end_date, api_key)
                        
                elif source_name == 'finnhub':
                    quotes = fetch_finnhub(symbol, start_date, end_date, api_key)
                        
                else:
                    logger.warning(f"{source_name}: Unknown source: '{source_name}'")
                    continue

            if quotes:
                logger.info(f"{source_name}: Successfully fetched {len(quotes)} quotes ")
                return quotes

        except Exception as e:
            logger.error(f"{source_name}: Error with {symbol}: {e}")
            continue  # Try next source
    
    logger.error(f"All enabled sources failed for {symbol}")
    return None


def fetch_alphavantage(symbol, start_date, end_date, api_key):
    """Fetch data from Alpha Vantage with rate limiting"""
    global _last_alphavantage_call
    
    try:
        import requests
        from datetime import datetime
        
        # Rate limiting: ensure at least 12 seconds between calls
        now = time.time()
        time_since_last = now - _last_alphavantage_call
        if time_since_last < _alphavantage_delay:
            sleep_time = _alphavantage_delay - time_since_last
            logger.info(f"Alpha Vantage rate limit: sleeping {sleep_time:.1f}s")
            time.sleep(sleep_time)
        
        _last_alphavantage_call = time.time()
        
        # Alpha Vantage TIME_SERIES_DAILY endpoint
        url = 'https://www.alphavantage.co/query'
        params = {
            'function': 'TIME_SERIES_DAILY',
            'symbol': symbol,
            'apikey': api_key,
            'outputsize': 'compact',
            'datatype': 'json'
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        # Check for error messages
        if 'Error Message' in data:
            logger.error(f"AlphaVantage: error {data['Error Message']}")
            return None
        
        if 'Note' in data:
            # Daily rate limit hit (25 requests/day)
            logger.warning(f"AlphaVantage: daily rate limit hit")
            return None
        
        if 'Information' in data:
            # Burst rate limit message
            logger.warning(f"AlphaVantage: burst rate limit (ignoring - we have rate limiting)")
            # Don't return None here - might still have data
        
        if 'Time Series (Daily)' not in data:
            logger.error(f"AlphaVantage: no time series data for {symbol}")
            return None
        
        time_series = data['Time Series (Daily)']
        
        # Filter by date range
        start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
        
        quotes = []
        for date_str, daily_data in time_series.items():
            date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
            
            if start_dt <= date_obj <= end_dt:
                try:
                    quotes.append({
                        'symbol': symbol,
                        'quote_date': date_str,
                        'open': round(float(daily_data['1. open']), 2),
                        'high': round(float(daily_data['2. high']), 2),
                        'low': round(float(daily_data['3. low']), 2),
                        'close': round(float(daily_data['4. close']), 2),
                        'volume': int(float(daily_data['5. volume'])),
                        'dividends': 0,
                        'stock_splits': 0,
                        'data_source': 'alphavantage'
                    })
                except (KeyError, ValueError) as e:
                    logger.warning(f"Skipping {date_str} for {symbol}: {e}")
                    continue
        
        if quotes:
            quotes.sort(key=lambda x: x['quote_date'])
            logger.info(f"Alpha Vantage: retrieved {len(quotes)} quotes for {symbol}")
        
        return quotes if quotes else None
    
    except requests.exceptions.RequestException as e:
        logger.error(f"Alpha Vantage request error for {symbol}: {e}")
        return None
    except Exception as e:
        logger.error(f"Alpha Vantage error for {symbol}: {e}")
        return None


# ////////////////////

def fetch_fmp(symbol, start_date, end_date, api_key):
    """Fetch data from Financial Modeling Prep"""
    # TODO: Implement FMP fetching
    logger.warning("FMP: fetching not yet implemented")
    return None


def fetch_finnhub(symbol, start_date, end_date, api_key):
    """Fetch data from Finnhub"""
    try:
        import requests
        from datetime import datetime
        
        # Convert dates to Unix timestamps (Finnhub requires this)
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date, '%Y-%m-%d')
        
        start_timestamp = int(start_dt.timestamp())
        end_timestamp = int(end_dt.timestamp())
        
        # Finnhub stock candles endpoint
        url = 'https://finnhub.io/api/v1/stock/candle'
        params = {
            'symbol': symbol,
            'resolution': 'D',  # Daily data
            'from': start_timestamp,
            'to': end_timestamp,
            'token': api_key
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        # Check for errors
        if 's' not in data:
            logger.error(f"Finnhub: unexpected response format for {symbol}")
            logger.debug(f"Finnhub Response: {data}")
            return None
        
        # Check status
        if data['s'] == 'no_data':
            logger.warning(f"Finnhub: no data available for {symbol}")
            return None
        
        if data['s'] != 'ok':
            logger.error(f"Finnhub: error status: {data['s']}")
            return None
        
        # Finnhub returns parallel arrays
        timestamps = data.get('t', [])
        opens = data.get('o', [])
        highs = data.get('h', [])
        lows = data.get('l', [])
        closes = data.get('c', [])
        volumes = data.get('v', [])
        
        if not timestamps:
            logger.warning(f"Finnhub: empty data for {symbol}")
            return None
        
        # Convert to our quote format
        quotes = []
        for i in range(len(timestamps)):
            try:
                # Convert Unix timestamp to date string
                date_obj = datetime.fromtimestamp(timestamps[i])
                date_str = date_obj.strftime('%Y-%m-%d')
                
                quotes.append({
                    'symbol': symbol,
                    'quote_date': date_str,
                    'open': round(opens[i], 2) if i < len(opens) else None,
                    'high': round(highs[i], 2) if i < len(highs) else None,
                    'low': round(lows[i], 2) if i < len(lows) else None,
                    'close': round(closes[i], 2) if i < len(closes) else None,
                    'volume': int(volumes[i]) if i < len(volumes) else 0,
                    'dividends': 0,  # Finnhub doesn't include dividends in candle data
                    'stock_splits': 0,
                    'data_source': 'finnhub'
                })
            except (IndexError, ValueError, OSError) as e:
                logger.warning(f"Skipping timestamp {timestamps[i]} for {symbol}: {e}")
                continue
        
        if quotes:
            quotes.sort(key=lambda x: x['quote_date'])
            logger.info(f"Finnhub: retrieved {len(quotes)} quotes for {symbol}")
        
        return quotes if quotes else None

    except requests.exceptions.HTTPError as e:
        # Handle HTTP errors separately
        if e.response.status_code == 403:
            logger.info(f"Finnhub: {symbol} not available (possibly mutual fund)")
        else:
            logger.error(f"Finnhub HTTP error for {symbol}: {e}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Finnhub request error for {symbol}: {e}")
        return None
    except Exception as e:
        logger.error(f"Finnhub error for {symbol}: {e}")
        return None

def save_quotes(quotes):
    """Save quotes to database"""
    if not quotes:
        return 0
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    saved = 0
    for quote in quotes:
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO daily_quotes 
                (symbol, quote_date, open, high, low, close, volume, dividends, stock_splits, data_source, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (
                quote['symbol'], quote['quote_date'],
                round(quote.get('open'), 2) if quote.get('open') is not None else None,
                round(quote.get('high'), 2) if quote.get('high') is not None else None,
                round(quote.get('low'), 2) if quote.get('low') is not None else None,
                round(quote.get('close'), 2) if quote.get('close') is not None else None,
                int(quote.get('volume', 0)),
                round(quote.get('dividends', 0), 2),
                round(quote.get('stock_splits', 0), 4),  # Stock splits can be fractional
                quote.get('data_source', 'unknown')
            ))
            saved += 1
        except Exception as e:
            logger.error(f"Error saving quote for {quote['symbol']} on {quote['quote_date']}: {e}")
    
    conn.commit()
    conn.close()
    
    return saved

def get_last_date_for_symbol(symbol):
    """Get the most recent date we have data for"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT MAX(quote_date) as last_date
        FROM daily_quotes
        WHERE symbol = ?
    ''', (symbol,))
    result = cursor.fetchone()
    conn.close()
    
    if result and result['last_date']:
        return datetime.strptime(result['last_date'], '%Y-%m-%d').date()
    return None

def get_missing_dates(symbol, start_date, end_date):
    """Get list of missing dates (weekdays only, excluding known closures)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT quote_date
        FROM daily_quotes
        WHERE symbol = ? AND quote_date BETWEEN ? AND ?
    ''', (symbol, start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
    
    existing = {row['quote_date'] for row in cursor.fetchall()}
    conn.close()
    
    # Get known market closures
    closures = get_market_closures()
    
    # Generate expected dates
    expected = set()
    current = start_date
    while current <= end_date:
        # Only weekdays
        if current.weekday() < 5:
            date_str = current.strftime('%Y-%m-%d')
            # Skip known closures
            if date_str not in closures:
                expected.add(date_str)
        current += timedelta(days=1)
    
    missing = expected - existing
    return sorted(list(missing))

# ============================================================================
# SMART UPDATE LOGIC
# ============================================================================

def smart_update_symbol(symbol, years=None):
    """
    Smart update that only fetches missing data
    - New symbols: fetch full history (years parameter or default)
    - Existing symbols: fetch from last date forward
    """
    last_date = get_last_date_for_symbol(symbol)
    today = datetime.now().date()
    
    if last_date is None:
        # New symbol: fetch full history
        if years is None:
            years = int(get_config('default_years') or 3)
        start_date = today - timedelta(days=365 * years)
        logger.info(f"New symbol {symbol}: fetching {years} years of history")
    else:
        # Existing symbol: fetch from last date + 1
        start_date = last_date + timedelta(days=1)
        
        # If already current, skip
        if start_date > today:
            logger.info(f"{symbol} already up to date (last: {last_date})")
            return True
        
        logger.info(f"Updating {symbol} from {start_date} to {today}")
    
    # Fetch data
    quotes = fetch_data_multi_source(symbol, start_date.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
    
    if quotes:
        saved = save_quotes(quotes)
        update_symbol_tracking(symbol)
        logger.info(f"Saved {saved} quotes for {symbol}")
        
        # Detect market closures based on missing dates
        missing = get_missing_dates(symbol, start_date, today)
        if missing:
            detect_market_closures(symbol, missing[:10])
        
        return True
    else:
        # No data available yet - not necessarily an error
        if start_date == today:
            logger.info(f"No new data yet for {symbol} (checking for today {today})")
        else:
            logger.warning(f"No data fetched for {symbol} from {start_date} to {today}")
        return False

def update_all_symbols(years=None):
    """Update all active symbols"""
    symbols = get_tracked_symbols()
    
    if not symbols:
        print("No symbols to update")
        logger.warning("No symbols to update")
        return
    
    print(f"Updating {len(symbols)} symbols...")
    logger.info(f"Starting update for {len(symbols)} symbols")
    
    success_count = 0
    for i, symbol in enumerate(symbols, 1):
        print(f"[{i}/{len(symbols)}] Updating {symbol}...")
        if smart_update_symbol(symbol, years):
            success_count += 1
    
    print(f"\nUpdate complete: {success_count}/{len(symbols)} symbols updated successfully")
    logger.info(f"Update complete: {success_count}/{len(symbols)} successful")

def auto_fetch_symbol(symbol):
    """Auto-fetch a new symbol (30 days of data)"""
    logger.info(f"Auto-fetching new symbol: {symbol}")
    
    # Add symbol if not exists
    add_symbol(symbol)
    
    # Fetch 30 days
    today = datetime.now().date()
    start_date = today - timedelta(days=30)
    
    quotes = fetch_data_multi_source(symbol, start_date.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
    
    if quotes:
        saved = save_quotes(quotes)
        update_symbol_tracking(symbol)
        logger.info(f"Auto-fetched {saved} quotes for {symbol}")
        return True
    else:
        logger.warning(f"Auto-fetch failed for {symbol}")
        return False

# ============================================================================
# LIVE QUOTE CACHING
# ============================================================================

def is_market_open():
    """Check if market is open (9:30 AM - 4:00 PM local time, Mon-Fri)"""
    now = datetime.now()
    
    # Weekend check
    if now.weekday() >= 5:
        return False
    
    # Time check (local timezone)
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    
    return market_open <= now <= market_close

def get_live_quote(symbol):
    """Get live quote with caching"""
    with cache_lock:
        now = time.time()
        cache_duration = int(get_config('cache_duration') or 900)
        
        # Check cache
        if symbol in live_quote_cache:
            price, timestamp = live_quote_cache[symbol]
            if now - timestamp < cache_duration:
                logger.debug(f"Cache hit for {symbol}")
                return price
        
        # Fetch live quote if market open
        if is_market_open():
            logger.info(f"Fetching live quote for {symbol}")
            price = fetch_live_quote_yfinance(symbol)
            
            if price:
                live_quote_cache[symbol] = (price, now)
                return price
        
        # Fallback: get last close from database
        logger.info(f"Market closed or fetch failed, returning last close for {symbol}")
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT close FROM daily_quotes
            WHERE symbol = ?
            ORDER BY quote_date DESC
            LIMIT 1
        ''', (symbol,))
        result = cursor.fetchone()
        conn.close()
        
        if result and result['close']:
            return result['close']
        
        return None

# ============================================================================
# STATISTICS
# ============================================================================

def show_statistics():
    """Show database statistics"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Total quotes
    cursor.execute('SELECT COUNT(*) as count FROM daily_quotes')
    total_quotes = cursor.fetchone()['count']
    
    # Symbol breakdown
    cursor.execute('''
        SELECT s.symbol, COUNT(dq.quote_date) as quotes,
               MIN(dq.quote_date) as first_date,
               MAX(dq.quote_date) as last_date
        FROM symbols s
        LEFT JOIN daily_quotes dq ON s.symbol = dq.symbol
        WHERE s.active = 1
        GROUP BY s.symbol
        ORDER BY s.symbol
    ''')
    symbols = cursor.fetchall()
    
    # Market closures
    cursor.execute('SELECT COUNT(*) as count FROM market_closures')
    closure_count = cursor.fetchone()['count']
    
    conn.close()
    
    print(f"\n{'='*70}")
    print(f"Database: {DB_PATH}")
    print(f"Total Quotes: {total_quotes:,}")
    print(f"Market Closures Detected: {closure_count}")
    print(f"{'='*70}")
    print(f"{'Symbol':<10} {'Quotes':>10} {'First Date':<12} {'Last Date':<12}")
    print(f"{'-'*70}")
    
    for row in symbols:
        print(f"{row['symbol']:<10} {row['quotes']:>10,} {row['first_date'] or 'N/A':<12} {row['last_date'] or 'N/A':<12}")
    
    print(f"{'='*70}\n")

def list_market_closures():
    """List all detected market closure dates"""
    closures = get_market_closures()
    
    if not closures:
        print("No market closures detected yet")
        return
    
    print(f"\n{'='*60}")
    print(f"Detected Market Closures: {len(closures)}")
    print(f"{'='*60}")
    print(f"{'Day':<12} {'Date':<15} ")
    print(f"{'-'*60}")
    
    for row in closures:
        date_str = row['date']
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        day_name = date_obj.strftime('%A')
        
        print(f"{day_name:<12} {date_str:<15} ")
    
    print(f"{'='*60}\n")    

# ============================================================================
# WEB SERVER
# ============================================================================

app = Flask(__name__)

@app.route('/shutdown', methods=['POST', 'GET'])
@app.route('/serverstop', methods=['POST', 'GET'])
def shutdown():
    """Graceful shutdown endpoint"""
    logger.info("Shutdown requested via HTTP")
    
    def shutdown_server():
        # Give time to send response
        import time
        time.sleep(1)
        # Force exit
        os._exit(0)
    
    # Start shutdown in background thread
    import threading
    threading.Thread(target=shutdown_server, daemon=True).start()
    
    return jsonify({'message': 'Server shutting down...'}), 200

@app.route('/')
def home():
    """Show API documentation"""
    return """
    <h1>Stock Quote Server</h1>
    <p>Server is running!</p>
    <h2>API Endpoints:</h2>
    <ul>
        <li><code>/quote/&lt;symbol&gt;</code> - Latest close price</li>
        <li><code>/quote/&lt;symbol&gt;/now</code> - Live quote (15min cache)</li>
        <li><code>/quote/&lt;symbol&gt;/&lt;date&gt;</code> - Price on specific date (YYYY-MM-DD)</li>
        <li><code>/quote/&lt;symbol&gt;/field/&lt;field&gt;</code> - Latest value for field (open, high, low, close, volume)</li>
        <li><code>/latest_date/&lt;symbol&gt;</code> - Most recent date with data</li>
        <li><code>/closures</code> - List all detected market closure dates</li>
        <li><code>/config</code> - View configuration (read-only)</li>
        <li><code>/health</code> - Server health check</li>
        <li><code>/shutdown</code> - Stop the server</li>
    </ul>
    <h2>Examples:</h2>
    <ul>
        <li><a href="/quote/AAPL">/quote/AAPL</a></li>
        <li><a href="/quote/AAPL/now">/quote/AAPL/now</a></li>
        <li><a href="/quote/CSCO/2025-01-15">/quote/CSCO/2025-01-15</a></li>
        <li><a href="/quote/AAPL/field/high">/quote/AAPL/field/high</a></li>
        <li><a href="/latest_date/AAPL">/latest_date/AAPL</a></li>
        <li><a href="/config">/config</a></li>
        <li><a href="/health">/health</a></li>
        <li><a href="/closures">/closures</a></li>
    </ul>
    """

@app.route('/health')
def health():
    """Health check"""
    db_exists = os.path.exists(DB_PATH)
    
    if not db_exists:
        return jsonify({'status': 'error', 'message': 'Database not found'}), 500
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) as count FROM daily_quotes')
        quote_count = cursor.fetchone()['count']
        
        cursor.execute('SELECT MAX(last_updated) as last_update FROM daily_quotes')
        last_update = cursor.fetchone()['last_update']
        
        conn.close()
        
        return jsonify({
            'status': 'ok',
            'database': DB_PATH,
            'quotes': quote_count,
            'last_update': last_update,
            'pid': os.getpid()
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/config')
@app.route('/config')
def view_config():
    """View current configuration"""
    try:
        all_config = list_config()
        
        # Convert config results to dict
        config_dict = {item['key']: item['value'] for item in all_config['config']}
        
        # Convert data sources to list of dicts
        sources_list = []
        for item in all_config['data_sources']:
            sources_list.append({
                'source_name': item['source_name'],
                'api_key': item['api_key'] if item['api_key'] else 'Not Set',  
                'rate_limit': item['rate_limit'],
                'priority': item['priority']
            })
        
        return jsonify({
            'config': config_dict,
            'data_sources': sources_list
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/quote/<symbol>')
def get_quote_endpoint(symbol):
    """Get latest close price"""
    return get_quote_helper(symbol.upper(), field='close')


@app.route('/closures')
def list_closures():
    """List market closures"""
    return jsonify(get_market_closures())


@app.route('/quote/<symbol>/now')
def get_live_quote_endpoint(symbol):
    """Get live quote with caching"""
    try:
        price = get_live_quote(symbol.upper())
        
        if price is not None:
            return str(price)
        else:
            # Try auto-fetch if not in database
            if auto_fetch_symbol(symbol.upper()):
                price = get_live_quote(symbol.upper())
                if price:
                    return str(price)
            
            return jsonify({'error': f'No data for {symbol.upper()}'}), 404
    
    except Exception as e:
        logger.error(f"Error getting live quote for {symbol}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/quote/<symbol>/<date>')
def get_quote_by_date_endpoint(symbol, date):
    """Get quote for specific date"""
    return get_quote_helper(symbol.upper(), date=date, field='close')

@app.route('/quote/<symbol>/field/<field>')
def get_quote_by_field_endpoint(symbol, field):
    """Get latest value for specific field"""
    valid_fields = ['open', 'high', 'low', 'close', 'volume']
    if field.lower() not in valid_fields:
        return jsonify({'error': f'Invalid field. Must be one of: {", ".join(valid_fields)}'}), 400
    return get_quote_helper(symbol.upper(), field=field.lower())

@app.route('/latest_date/<symbol>')
def get_latest_date_endpoint(symbol):
    """Get most recent date for symbol"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT MAX(quote_date) as latest
            FROM daily_quotes
            WHERE symbol = ?
        ''', (symbol.upper(),))
        result = cursor.fetchone()
        conn.close()
        
        if result and result['latest']:
            return str(result['latest'])
        else:
            # Try auto-fetch
            if auto_fetch_symbol(symbol.upper()):
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT MAX(quote_date) as latest
                    FROM daily_quotes
                    WHERE symbol = ?
                ''', (symbol.upper(),))
                result = cursor.fetchone()
                conn.close()
                
                if result and result['latest']:
                    return str(result['latest'])
            
            return jsonify({'error': f'No data for {symbol.upper()}'}), 404
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def get_quote_helper(symbol, date=None, field='close'):
    """Helper function to get quote data"""
    try:
        if not os.path.exists(DB_PATH):
            return jsonify({'error': 'Database not found'}), 500
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Validate field
        valid_fields = ['open', 'high', 'low', 'close', 'volume']
        if field not in valid_fields:
            field = 'close'
        
        if date:
            # Try exact date first
            cursor.execute(f'''
                SELECT {field}
                FROM daily_quotes
                WHERE symbol = ? AND quote_date = ?
            ''', (symbol, date))
            
            result = cursor.fetchone()
            
            # If no exact match, find nearest date before
            if not result or result[field] is None:
                cursor.execute(f'''
                    SELECT {field}
                    FROM daily_quotes
                    WHERE symbol = ? AND quote_date <= ?
                    ORDER BY quote_date DESC
                    LIMIT 1
                ''', (symbol, date))
                result = cursor.fetchone()
        else:
            # Get most recent
            cursor.execute(f'''
                SELECT {field}
                FROM daily_quotes
                WHERE symbol = ?
                ORDER BY quote_date DESC
                LIMIT 1
            ''', (symbol,))
            result = cursor.fetchone()
        
        conn.close()
        
        if result and result[field] is not None:
            return str(result[field])
        else:
            # Try auto-fetch for unknown symbols
            if auto_fetch_symbol(symbol):
                # Retry query after fetch
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(f'''
                    SELECT {field}
                    FROM daily_quotes
                    WHERE symbol = ?
                    ORDER BY quote_date DESC
                    LIMIT 1
                ''', (symbol,))
                result = cursor.fetchone()
                conn.close()
                
                if result and result[field] is not None:
                    return str(result[field])
            
            return jsonify({'error': f'No data for {symbol}'}), 404
    
    except Exception as e:
        logger.error(f"Error in get_quote_helper: {e}")
        return jsonify({'error': str(e)}), 500

# ============================================================================
# Concurrency / Start/Stop Management
# ============================================================================

# Replace the acquire_server_lock() function with this:

def is_process_running(pid):
    """
    Check if a process with given PID is running (Windows-compatible)
    """
    import platform
    
    if platform.system() == 'Windows':
        # Use tasklist on Windows
        import subprocess
        try:
            output = subprocess.check_output(['tasklist', '/FI', f'PID eq {pid}'], 
                                           stderr=subprocess.DEVNULL,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
            return str(pid) in output.decode()
        except:
            return False
    else:
        # Unix/Linux/Mac
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

def acquire_server_lock():
    """
    Acquire exclusive server lock to prevent multiple instances
    Returns True if lock acquired, False if another instance is running
    """
    try:
        # Check if lock file exists
        if os.path.exists(LOCK_FILE):
            # Try to read PID from lock file
            with open(LOCK_FILE, 'r') as f:
                pid = int(f.read().strip())
            
            # Check if process is still running (Windows-compatible)
            if is_process_running(pid):
                # Process exists
                logger.warning(f"Server already running (PID: {pid})")
                print(f"Error: Server is already running (PID: {pid})")
                print(f"Use 'python stock_system.py --stopserver' to stop it")
                return False
            else:
                # Process doesn't exist, stale lock file
                logger.info("Removing stale lock file")
                os.remove(LOCK_FILE)
        
        # Write current PID to lock file
        with open(LOCK_FILE, 'w') as f:
            f.write(str(os.getpid()))
        
        # Register cleanup on exit
        atexit.register(release_server_lock)
        
        logger.info(f"Server lock acquired (PID: {os.getpid()})")
        return True
    
    except Exception as e:
        logger.error(f"Error acquiring server lock: {e}")
        return False

def release_server_lock():
    """Release server lock on exit"""
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
            logger.info("Server lock released")
    except Exception as e:
        logger.error(f"Error releasing lock: {e}")

def save_server_port(port):
    """Save server port for stop command"""
    try:
        with open(SERVER_PORT_FILE, 'w') as f:
            f.write(f"{port}\n{os.getpid()}")
    except Exception as e:
        logger.error(f"Error saving server port: {e}")

def get_server_info():
    """Get running server port and PID"""
    try:
        if os.path.exists(SERVER_PORT_FILE):
            with open(SERVER_PORT_FILE, 'r') as f:
                lines = f.read().strip().split('\n')
                if len(lines) >= 2:
                    return int(lines[0]), int(lines[1])
        return None, None
    except Exception as e:
        logger.error(f"Error reading server info: {e}")
        return None, None

def stop_server():
    """Stop running server instance (cross-platform)"""
    import platform
    import subprocess
    
    port, pid = get_server_info()
    
    if not pid:
        print("No server is currently running")
        logger.info("Stop requested but no server running")
        return
    
    # Check if process exists
    if not is_process_running(pid):
        print("Server process not found (stale PID file)")
        # Clean up stale files
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
        if os.path.exists(SERVER_PORT_FILE):
            os.remove(SERVER_PORT_FILE)
        return
    
    print(f"Stopping server (PID: {pid})...")
    logger.info(f"Stop command issued for server PID: {pid}")
    
    try:
        # Try graceful shutdown via HTTP request
        if port:
            try:
                import urllib.request
                urllib.request.urlopen(f'http://localhost:{port}/shutdown', timeout=2)
                print("Server shutdown signal sent")
                time.sleep(2)
            except:
                pass
        
        # Check if still running after graceful attempt
        if is_process_running(pid):
            print("Force stopping server...")
            
            # Platform-specific force kill
            if platform.system() == 'Windows':
                # Windows: use taskkill
                subprocess.run(['taskkill', '/F', '/PID', str(pid)], 
                             stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                # Unix/Linux/Mac: use kill with SIGKILL
                import signal
                os.kill(pid, signal.SIGKILL)
            
            time.sleep(1)
        
        # Clean up lock files
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
        if os.path.exists(SERVER_PORT_FILE):
            os.remove(SERVER_PORT_FILE)
        
        print("Server stopped")
        logger.info("Server stopped successfully")
    
    except Exception as e:
        print(f"Error stopping server: {e}")
        logger.error(f"Error stopping server: {e}")


# ============================================================================
# SCHEDULED UPDATES
# ============================================================================

def scheduled_update():
    """Function called by scheduler for automatic updates"""
    logger.info("Starting scheduled update")
    update_all_symbols()
    logger.info("Scheduled update complete")

def start_scheduler():
    """Start background scheduler for automatic updates"""
    update_time = get_config('update_time') or '20:30'
    hour, minute = map(int, update_time.split(':'))
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        scheduled_update,
        'cron',
        day_of_week='mon-sat',
        hour=hour,
        minute=minute
    )
    scheduler.start()
    
    logger.info(f"Scheduler started: updates at {update_time} Mon-Sat")
    print(f"Automatic updates scheduled for {update_time} Mon-Sat")
    
    return scheduler

# ============================================================================
# COMMAND LINE INTERFACE
# ============================================================================

def main():
    global DB_PATH
    
    parser = argparse.ArgumentParser(description='Stock Quote Management System')
    
    # Database
    parser.add_argument('--db', type=str, help='Database file path')
    
    # Server
    parser.add_argument('--shutdown', '--stop', '--stopserver', action='store_true', help='Stop running server instance')
    parser.add_argument('--server', '--start', '--startserver', action='store_true', help='Run web server')
    
    # Symbol management
    parser.add_argument('--init', action='store_true', help='Initialize database')
    parser.add_argument('--add', action='append', help='Add symbol(s) - can use multiple times')
    parser.add_argument('--remove', type=str, help='Remove symbol')
    
    # Updates
    parser.add_argument('--update', action='store_true', help='Update all symbols')
    parser.add_argument('--years', type=int, help='Years of history to fetch (for new symbols)')
    parser.add_argument('--trigger-update', action='store_true', help='Trigger immediate update')
    
    # Configuration
    parser.add_argument('--config', nargs='+', help='Config operations: list, get KEY, set KEY VALUE, set apikey SOURCE KEY')
    
    # Info
    parser.add_argument('--stats', '--statistics', action='store_true', help='Show database statistics')
    parser.add_argument('--dbpath', action='store_true', help='Show database path')
    parser.add_argument('--closures', action='store_true', help='List all market closure dates')
    
    args = parser.parse_args()
    
    # Set database path if provided
    if args.db:
        DB_PATH = args.db
    
    # Handle --stopserver FIRST (before other commands)
    if args.shutdown:
        stop_server()
        return

    # Handle commands
    if args.init:
        setup_database()
        initialize_default_config()
        initialize_data_sources()
        # Add reference symbols
        add_symbol('QQQ')
        add_symbol('CSCO')
        print("Database initialized with reference symbols (QQQ, CSCO)")
        return
    
    if args.add:
        for symbol in args.add:
            add_symbol(symbol)
        return
    
    if args.remove:
        remove_symbol(args.remove)
        return
    
    if args.update or args.trigger_update:
        update_all_symbols(args.years)
        return
    
    if args.config:
        if args.config[0] == 'list':
            all_config = list_config()
            
            # Show config table
            print(f"{'='*80}")
            print(f"{'Configuration Key':<30} {'Value':<20} {'Description'}")
            print('-' * 80)
            for item in all_config['config']:
                print(f"{item['key']:<30} {item['value']:<20} {item['description'] or ''}")
            
            # Show data sources table
            print(f"{'='*80}")
            print(f"{'Source':<15} {'Priority':<10} {'Rate Limit':<12} {'API Key':<45} ")
            print('-' * 80)
            for item in all_config['data_sources']:
                api_key_display = item['api_key'] if item['api_key'] else "Not Set"
                print(f"{item['source_name']:<15} {item['priority']:<10} {item['rate_limit']:<12} {api_key_display:<45}" )
            print(f"{'='*80}\n")
            return  
        elif args.config[0] == 'get' and len(args.config) > 1:
            value = get_config(args.config[1])
            print(f"{args.config[1]} = {value}")
        elif args.config[0] == 'set' and len(args.config) > 2:
            if args.config[1] == 'apikey' and len(args.config) > 3:
                set_api_key(args.config[2], args.config[3])
                print(f"API key set for {args.config[2]}")
            elif args.config[1] == 'priority':
                set_source_priority(args.config[2], args.config[3])
                print(f"Priority updated: {args.config[2]} = {args.config[3]}")
                logger.info(f"Priority updated: {args.config[2]} = {args.config[3]}")
            else:
                # TODO This seems useless now.
                set_config(args.config[1], args.config[2])
                print(f"Config updated: {args.config[1]} = {args.config[2]}")
        else:
            print("Usage: --config list | get KEY | set KEY VALUE | set apikey SOURCE KEY | set priority SOURCE RANK")
        return
    
    if args.stats:
        show_statistics()
        return

    if args.closures:
        list_market_closures()
        return
        
    if args.dbpath:
        print(f"Database: {DB_PATH}")
        return
    
    if args.server:
        # Make sure database exists
        if not os.path.exists(DB_PATH):
            print(f"Error: Database not found at {DB_PATH}")
            print("Run with --init to create database")
            return
        
        # Acquire server lock (prevent multiple instances)
        if not acquire_server_lock():
            return

        # Save server info for stop command
        save_server_port(5000)
        
        # Start scheduler
        scheduler = start_scheduler()
        
        # Start server
        print("=" * 70)
        print("Stock Quote Server Starting")
        print("=" * 70)
        print(f"Database: {DB_PATH}")
        print(f"Server: http://localhost:5000")
        print(f"PID: {os.getpid()}")
        print()
        print("Test: http://localhost:5000/quote/AAPL")
        print('Use in Calc: =WEBSERVICE("http://localhost:5000/quote/AAPL")')
        print()
        print("To stop: 'python stock_system.py --stopserver' or 'stop_server.bat'")
        print("Or press Ctrl+C")
        print("=" * 70)
        
        try:
            app.run(host='127.0.0.1', port=5000, debug=False)
        except KeyboardInterrupt:
            print("\nShutting down...")
            scheduler.shutdown()
        
        return
    
    # No arguments - show help
    parser.print_help()

if __name__ == '__main__':
    main()