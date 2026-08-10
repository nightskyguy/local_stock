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
    python stock_system.py --exportcsv [filename=stock_system_export.csv] [SYMBOL]
    
    # Updates
    python stock_system.py --update [--years N]          # all symbols
    python stock_system.py --update AOA [--years N]       # force-refresh one symbol (repairs missing OHLC)
    
    # Configuration
    python stock_system.py --config list
    python stock_system.py --config set KEY VALUE
    python stock_system.py --config set apikey SOURCE KEY
    
    # Info
    python stock_system.py --stats
    python stock_system.py --dbpath

    # Money market funds
    python stock_system.py --list-funds
    python stock_system.py --rebuild-fund [SYMBOL]   # rebuild from stored metrics + verify
    python stock_system.py --verify-fund [SYMBOL]    # verify only

Web API:
    http://localhost:5000/quote/AAPL              - Latest close
    http://localhost:5000/quote/AAPL/now          - Live quote (cached 15min)
    http://localhost:5000/quote/CSCO,TSLA,AAPL    - Batch latest close -> CSV (e.g. 70.1,421.5,267.6)
    http://localhost:5000/quote/CSCO,TSLA,AAPL/now- Batch live quote -> CSV (missing -> #N/A)
    http://localhost:5000/quote/AAPL/2025-01-15   - Historical quote
    http://localhost:5000/quote/CSCO,TSLA,AAPL/2025-01-15 - Batch historical close -> CSV (missing -> #N/A)
    http://localhost:5000/quote/AAPL/field/high   - Specific field (OHLCV)
    http://localhost:5000/quote/AAPL/field/pe     - Fundamentals: pe, pe_forward, xr, divyield (batch -> CSV)
    http://localhost:5000/yield/FDLXX             - Latest annualized yield (bare, e.g. 0.0363)
    http://localhost:5000/fund_type/AOA,QQQ       - Asset type(s) -> CSV (missing -> #N/A)
    http://localhost:5000/name/AOA,QQQ            - Name(s) -> CSV (commas in name -> _, missing -> #N/A)
    http://localhost:5000/update/AOA              - Force-refresh one symbol (repairs missing OHLC)
    http://localhost:5000/funds                   - List money market (synthetic) funds
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
from flask import Flask, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler
import threading
import socket
import atexit
import csv
import json
from zoneinfo import ZoneInfo

# Get Lock File with PID (if it exists)
LOCK_FILE = os.path.join(os.environ.get('USERPROFILE', os.path.expanduser('~')), 'stock_system.lock')
SERVER_PORT_FILE = os.path.join(os.environ.get('USERPROFILE', os.path.expanduser('~')), 'stock_system.port')

# Separate lock/port files for a --secondary test instance, so it can run
# alongside the primary server and be stopped independently (--stop --secondary).
LOCK_FILE_SECONDARY = os.path.join(os.environ.get('USERPROFILE', os.path.expanduser('~')), 'stock_system_secondary.lock')
SERVER_PORT_FILE_SECONDARY = os.path.join(os.environ.get('USERPROFILE', os.path.expanduser('~')), 'stock_system_secondary.port')

# ============================================================================
# VERSION
# ============================================================================

__version__ = '1.2.0'

CHANGELOG = [
    {'version': '1.2.0', 'date': '2026-08-10', 'changes': [
        'Fix /chart rendering nothing when a money-market fund is charted with an equity '
        '(date-gap points now serialize as JS null, not Python None)',
        'Chart shows percent change by default for multiple symbols (comparable across price '
        'levels); &prices charts raw prices, &comparePct forces percent for a single symbol',
        'Money-market synthetic prices track each fund accurately: FRED fed-funds history is '
        're-leveled to the fund\'s own yfinance yield (per-fund spread; real yield accrued daily '
        'going forward), with FMP real-distribution reinvestment used when available and a '
        'FRED-minus-expense proxy (--expense-ratio override) as the final fallback',
        'Fix FMP integration: legacy /api/v3 endpoints were retired 2025-08-31; use the /stable '
        'historical-price and dividends endpoints',
    ]},
    {'version': '1.1.0', 'date': '2026-07-01', 'changes': [
        'Fix /quote/<symbol>/now returning "1" for money-market funds (now returns the synthetic price)',
        'Add watch list monitor (/watchlist route + --watchlist CLI): flag symbols down >loss-pct% (default 12%) '
        'and/or up >gain-pct% vs 1wk/1mo/YTD close',
        'Add /chart route: multi-symbol Chart.js line chart with configurable time span',
        'Add version/changelog reporting via --version CLI flag and /version route',
        'Add --secondary/--port: run a second test server instance alongside the primary, with its own '
        'lock/port/log files, stoppable independently via --stop --secondary',
    ]},
]

# ============================================================================
# CONFIGURATION
# ============================================================================

DEFAULT_DB_PATH = os.path.join(os.environ.get('USERPROFILE', os.path.expanduser('~')), 'stock_quotes.db')
LOG_PATH = os.path.join(os.environ.get('USERPROFILE', os.path.expanduser('~')), 'stock_system.log')
LOG_PATH_SECONDARY = os.path.join(os.environ.get('USERPROFILE', os.path.expanduser('~')), 'stock_system_secondary.log')

# Reference symbols for market closure detection (must have 4+ years of data)
REFERENCE_SYMBOLS = ['QQQ', 'CSCO']

# Providers that don't carry these symbol types — skip them silently rather than logging 403 errors
PROVIDER_SKIP_TYPES = {
    'fmp':     {'MUTUALFUND', 'MONEY_MARKET', 'INDEX'},
    'finnhub': {'MUTUALFUND', 'MONEY_MARKET', 'INDEX'},
}

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

def use_secondary_log_file():
    """Redirect logging to LOG_PATH_SECONDARY instead of LOG_PATH, so a --secondary
    instance doesn't interleave its log lines with the primary's."""
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    file_handler = logging.FileHandler(LOG_PATH_SECONDARY)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    root.addHandler(file_handler)

# yfinance logs "possibly delisted" at ERROR when a ticker returns no data for a
# date range — this is expected and non-actionable for mutual funds and recent gaps.
# Downgrade those messages to DEBUG so they don't pollute the log.
class _YFinanceDelistedFilter(logging.Filter):
    def filter(self, record):
        if record.levelno >= logging.ERROR and 'possibly delisted' in record.getMessage():
            record.levelno = logging.DEBUG
            record.levelname = 'DEBUG'
        return True

_yf_filter = _YFinanceDelistedFilter()
for _yf_logger_name in ('yfinance', 'yfinance.base', 'yfinance.ticker'):
    logging.getLogger(_yf_logger_name).addFilter(_yf_filter)

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
            added_at TEXT DEFAULT CURRENT_TIMESTAMP,
            symbol_type TEXT DEFAULT 'EQUITY',
            fund_family TEXT
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
            reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Data sources table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS data_sources (
            source_name TEXT PRIMARY KEY,
            api_key TEXT,
            rate_limit INTEGER,
            enabled INTEGER DEFAULT 1,
            priority INTEGER DEFAULT 99,
            notes TEXT
        )
    ''')
    
    # Fund metrics table (yield data for money market and other funds)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fund_metrics (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol           TEXT NOT NULL,
            metric_date      TEXT NOT NULL,
            yield_annual     REAL,
            yield_source     TEXT,
            total_assets     REAL,
            expense_ratio    REAL,
            data_source      TEXT,
            last_updated     TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, metric_date)
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_fund_metrics_symbol_date
        ON fund_metrics(symbol, metric_date DESC)
    ''')

    # Fund distributions table (real per-share dividends/distributions from FMP;
    # used to build a reinvested total-return series for money market funds)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fund_distributions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol         TEXT NOT NULL,
            ex_date        TEXT NOT NULL,
            amount         REAL,
            yield_reported REAL,
            frequency      TEXT,
            data_source    TEXT,
            last_updated   TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, ex_date)
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_fund_distributions_symbol_date
        ON fund_distributions(symbol, ex_date DESC)
    ''')

    # Symbol fundamentals table (P/E, dividend yield, expense ratio snapshots)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS symbol_fundamentals (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol         TEXT NOT NULL,
            snapshot_date  TEXT NOT NULL,
            pe_trailing    REAL,
            pe_forward     REAL,
            dividend_yield REAL,
            expense_ratio  REAL,
            data_source    TEXT,
            last_updated   TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, snapshot_date)
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_symbol_fundamentals_symbol_date
        ON symbol_fundamentals(symbol, snapshot_date DESC)
    ''')

    conn.commit()
    conn.close()
    migrate_schema()
    logger.info(f"Database initialized: {DB_PATH}")

def migrate_schema():
    """Apply schema migrations to an existing database (idempotent)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(symbols)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    if 'symbol_type' not in existing_cols:
        cursor.execute("ALTER TABLE symbols ADD COLUMN symbol_type TEXT DEFAULT 'EQUITY'")
    if 'fund_family' not in existing_cols:
        cursor.execute("ALTER TABLE symbols ADD COLUMN fund_family TEXT")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fund_metrics (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol           TEXT NOT NULL,
            metric_date      TEXT NOT NULL,
            yield_annual     REAL,
            yield_source     TEXT,
            total_assets     REAL,
            expense_ratio    REAL,
            data_source      TEXT,
            last_updated     TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, metric_date)
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_fund_metrics_symbol_date
        ON fund_metrics(symbol, metric_date DESC)
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fund_distributions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol         TEXT NOT NULL,
            ex_date        TEXT NOT NULL,
            amount         REAL,
            yield_reported REAL,
            frequency      TEXT,
            data_source    TEXT,
            last_updated   TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, ex_date)
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_fund_distributions_symbol_date
        ON fund_distributions(symbol, ex_date DESC)
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS symbol_fundamentals (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol         TEXT NOT NULL,
            snapshot_date  TEXT NOT NULL,
            pe_trailing    REAL,
            pe_forward     REAL,
            dividend_yield REAL,
            expense_ratio  REAL,
            data_source    TEXT,
            last_updated   TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, snapshot_date)
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_symbol_fundamentals_symbol_date
        ON symbol_fundamentals(symbol, snapshot_date DESC)
    ''')

    cursor.execute("PRAGMA table_info(market_closures)")
    mc_cols = {row[1] for row in cursor.fetchall()}
    if 'reason' not in mc_cols:
        cursor.execute("ALTER TABLE market_closures ADD COLUMN reason TEXT")

    cursor.execute("PRAGMA table_info(data_sources)")
    ds_cols = {row[1] for row in cursor.fetchall()}
    if ds_cols and 'enabled' not in ds_cols:
        cursor.execute("ALTER TABLE data_sources ADD COLUMN enabled INTEGER DEFAULT 1")

    conn.commit()
    conn.close()

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
        ('yfinance', '', 2000, 1, 1, 'Yahoo Finance via yfinance - Free, no API key https://ranaroussi.github.io/yfinance/'),
        ('finnhub', '', 60, 0, 2, 'Finnhub - Free tier: 60 calls/min https://finnhub.io/docs/api'),
        ('alphavantage', '', 25, 0, 3, 'Alpha Vantage - Free tier: 25 req/day https://www.alphavantage.co/documentation/'),
        ('fmp', '', 250, 0, 4, 'Financial Modeling Prep - Free tier: 250 req/day https://site.financialmodelingprep.com/developer/docs'),
    ]
    
    for source in sources:
        cursor.execute('''
            INSERT OR IGNORE INTO data_sources (source_name, api_key, rate_limit, enabled, priority, notes)
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
    cursor.execute('UPDATE data_sources SET priority = ? WHERE source_name = ?', (priority, source))

    # Check how many rows were affected
    if cursor.rowcount == 0:
        logger.warning(f"Source '{source}' not found in data_sources table so NOT set to '{priority}'")
        conn.close()
        return False
    
    conn.commit()
    logger.info(f"Updated priority for source '{source}' to {priority}")
    conn.close()
    return True

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
    """Add symbol to tracked list and classify its type."""
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
    except sqlite3.IntegrityError:
        logger.warning(f"Symbol already exists: {symbol.upper()}")
        print(f"Symbol already exists: {symbol.upper()}")
        conn.close()
        return False
    finally:
        conn.close()

    classification = classify_symbol(symbol.upper())
    save_classification(symbol.upper(), classification)
    family = f", Family: {classification['fund_family']}" if classification['fund_family'] else ""
    print(f"  Type: {classification['symbol_type']}{family}")
    return True

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

def clean_orphan_symbols():
    """Find and remove all active symbols that have zero quotes."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.symbol
        FROM symbols s
        LEFT JOIN daily_quotes dq ON dq.symbol = s.symbol
        WHERE s.active = 1
        GROUP BY s.symbol
        HAVING COUNT(dq.quote_date) = 0
        ORDER BY s.symbol
    ''')
    orphans = [row['symbol'] for row in cursor.fetchall()]
    conn.close()
    if not orphans:
        print("No orphan symbols found (all tracked symbols have quotes).")
        return
    print(f"Orphan symbols with 0 quotes: {', '.join(orphans)}")
    for sym in orphans:
        remove_symbol(sym)

def get_tracked_symbols():
    """Get list of active symbols"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT symbol FROM symbols WHERE active = 1 ORDER BY symbol')
    symbols = [row['symbol'] for row in cursor.fetchall()]
    conn.close()
    return symbols

def list_money_market_funds():
    """
    Return MONEY_MARKET symbols (the ones backed by a synthetic price series) with
    their name, fund family, and synthetic-row coverage. A fund classified but not
    yet built shows rows=0.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.symbol, s.name, s.fund_family,
               COUNT(dq.quote_date) AS rows,
               MAX(dq.quote_date) AS last_date
        FROM symbols s
        LEFT JOIN daily_quotes dq
          ON dq.symbol = s.symbol AND dq.data_source = 'synthetic_mmf'
        WHERE s.symbol_type = 'MONEY_MARKET'
        GROUP BY s.symbol
        ORDER BY s.symbol
    ''')
    funds = [
        {
            'symbol': r['symbol'],
            'name': r['name'] or '',
            'fund_family': r['fund_family'] or '',
            'rows': r['rows'],
            'last_date': r['last_date'] or 'N/A',
        }
        for r in cursor.fetchall()
    ]
    conn.close()
    return funds

def show_money_market_funds():
    """Print a table of MONEY_MARKET funds and their synthetic-row coverage."""
    funds = list_money_market_funds()
    if not funds:
        print("No money market funds (symbol_type='MONEY_MARKET') found.")
        return
    print(f"\n{'='*90}")
    print(f"Money Market Funds (synthetic): {len(funds)}")
    print(f"{'='*90}")
    print(f"{'Symbol':<10} {'Rows':>8} {'Last Date':<12} {'Family':<22} {'Name'}")
    print(f"{'-'*90}")
    for f in funds:
        print(f"{f['symbol']:<10} {f['rows']:>8,} {f['last_date']:<12} {f['fund_family']:<22} {f['name']}")
    print(f"{'='*90}\n")

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
# SYMBOL CLASSIFICATION
# ============================================================================

def classify_symbol(symbol):
    """Fetch yfinance metadata and return a classification dict."""
    try:
        info = yf.Ticker(symbol).info
        quote_type = (info.get('quoteType') or 'EQUITY').upper()
        type_map = {
            'EQUITY': 'EQUITY',
            'ETF': 'ETF',
            'MUTUALFUND': 'MUTUALFUND',
            'MONEY_MARKET': 'MONEY_MARKET',
            'MONEYMARKET': 'MONEY_MARKET',
            'INDEX': 'INDEX',
            'CURRENCY': 'CURRENCY',
            'CRYPTOCURRENCY': 'CRYPTO',
        }
        symbol_type = type_map.get(quote_type, 'UNKNOWN')
        category = (info.get('category') or '').lower()
        if symbol_type == 'MUTUALFUND' and 'money market' in category:
            symbol_type = 'MONEY_MARKET'
        return {
            'symbol_type': symbol_type,
            'fund_family': info.get('fundFamily'),
            'name': info.get('longName') or info.get('shortName'),
        }
    except Exception as e:
        logger.warning(f"Classification error for {symbol}: {e}")
        return {'symbol_type': 'UNKNOWN', 'fund_family': None, 'name': None}

def save_classification(symbol, classification):
    """Upsert symbol classification into the symbols table."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO symbols (symbol, name, symbol_type, fund_family, active)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(symbol) DO UPDATE SET
            symbol_type = excluded.symbol_type,
            fund_family = excluded.fund_family,
            name = COALESCE(excluded.name, symbols.name)
    ''', (symbol.upper(), classification['name'],
          classification['symbol_type'], classification['fund_family']))
    conn.commit()
    conn.close()

def get_symbol_type(symbol):
    """Return stored symbol_type, defaulting to EQUITY if not yet classified."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT symbol_type FROM symbols WHERE symbol = ?', (symbol.upper(),))
    row = cursor.fetchone()
    conn.close()
    return row['symbol_type'] if row and row['symbol_type'] else 'EQUITY'

def reclassify_all_symbols():
    """Classify all active symbols using yfinance metadata."""
    symbols = get_tracked_symbols()
    if not symbols:
        print("No symbols to classify.")
        return
    print(f"Classifying {len(symbols)} symbols...")
    for symbol in symbols:
        print(f"  {symbol}: ", end='', flush=True)
        classification = classify_symbol(symbol)
        save_classification(symbol, classification)
        family = f" ({classification['fund_family']})" if classification['fund_family'] else ""
        print(f"{classification['symbol_type']}{family}")
        time.sleep(0.5)
    print("Classification complete.")

# ============================================================================
# MARKET CLOSURE DETECTION
# ============================================================================

def get_market_closures():
    """Get list of known market closure dates with metadata"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT date, reason
        FROM market_closures
        ORDER BY date DESC
    ''')
    closures = cursor.fetchall()
    conn.close()

    result = []
    for row in closures:
        date_str = row['date']
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        result.append({
            'day': date_obj.strftime('%A'),
            'date': date_str,
            'reason': row['reason'] or '',
        })

    return result

def mark_market_closure(date, reason=None):
    """Mark a date as market closure"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO market_closures (date, confirmed_count, reason, created_at)
        VALUES (?, 1, ?, CURRENT_TIMESTAMP)
    ''', (date, reason))
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

def _extract_dividend_yield(info):
    """Return dividend yield as a fraction (e.g. 0.0035 = 0.35%), or None.
    Prefer `trailingAnnualDividendYield`, which yfinance reports as a fraction
    consistently across versions. Fall back to `dividendYield`, which newer yfinance
    reports as a percent (e.g. 0.36) — normalize by dividing when it dwarfs the trailing
    fraction or exceeds 1."""
    tady = info.get('trailingAnnualDividendYield')
    if tady is not None:
        try:
            return float(tady)
        except (TypeError, ValueError):
            pass
    dy = info.get('dividendYield')
    if dy is None:
        return None
    try:
        v = float(dy)
    except (TypeError, ValueError):
        return None
    # No trailing fraction to anchor against: dividendYield is a percent in current
    # yfinance, so scale to a fraction.
    return v / 100.0

def fetch_fundamentals_yfinance(symbol):
    """Fetch P/E, dividend yield and expense ratio for a symbol from yfinance.
    One .info call; returns a fundamentals record dict (None for absent keys)."""
    try:
        info = yf.Ticker(symbol).info
        expense = (info.get('annualReportExpenseRatio')
                   or info.get('netExpenseRatio')
                   or info.get('expenseRatio'))
        record = {
            'symbol': symbol.upper(),
            'snapshot_date': datetime.now().strftime('%Y-%m-%d'),
            'pe_trailing': info.get('trailingPE'),
            'pe_forward': info.get('forwardPE'),
            'dividend_yield': _extract_dividend_yield(info),
            'expense_ratio': float(expense) if expense is not None else None,
            'data_source': 'yfinance',
        }
        return record
    except Exception as e:
        logger.error(f"Fundamentals fetch failed for {symbol}: {e}")
        return None

def save_fundamentals(records):
    """Batch-save fundamentals records to symbol_fundamentals table."""
    if not records:
        return 0
    conn = get_db_connection()
    cursor = conn.cursor()
    inserted = 0
    for r in records:
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO symbol_fundamentals
                (symbol, snapshot_date, pe_trailing, pe_forward,
                 dividend_yield, expense_ratio, data_source, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (r['symbol'], r['snapshot_date'], r.get('pe_trailing'),
                  r.get('pe_forward'), r.get('dividend_yield'),
                  r.get('expense_ratio'), r['data_source']))
            inserted += 1
        except Exception as e:
            logger.error(f"Error saving fundamentals {r['symbol']} {r['snapshot_date']}: {e}")
    conn.commit()
    conn.close()
    return inserted

def update_fundamentals(symbols=None):
    """Fetch and store fundamentals snapshots. `symbols` may be None (all tracked
    symbols), a single symbol string, or a list of symbols."""
    if symbols is None:
        symbols = get_tracked_symbols()
    elif isinstance(symbols, str):
        symbols = [symbols]
    symbols = [s.strip().upper() for s in symbols if s and s.strip()]

    if not symbols:
        print("No symbols to update")
        logger.warning("No symbols for fundamentals update")
        return

    print(f"Updating fundamentals for {len(symbols)} symbol(s)...")
    logger.info(f"Starting fundamentals update for {len(symbols)} symbols")

    success_count = 0
    for i, symbol in enumerate(symbols, 1):
        print(f"[{i}/{len(symbols)}] Fundamentals {symbol}...")
        record = fetch_fundamentals_yfinance(symbol)
        if record and save_fundamentals([record]):
            success_count += 1

    print(f"\nFundamentals update complete: {success_count}/{len(symbols)} updated")
    logger.info(f"Fundamentals update complete: {success_count}/{len(symbols)} successful")

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

    symbol_type = get_symbol_type(symbol)

    for source_name, api_key in sources:
        skip_types = PROVIDER_SKIP_TYPES.get(source_name, set())
        if symbol_type in skip_types:
            logger.info(f"{source_name}: skipping {symbol} (type {symbol_type} not supported by this provider)")
            continue

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
    """Fetch data from Financial Modeling Prep (stable EOD endpoint).

    The legacy /api/v3/historical-price-full endpoint was retired 2025-08-31 and
    now returns 403; this uses the current /stable/historical-price-eod/full
    endpoint, which returns a flat list of {symbol,date,open,high,low,close,volume}."""
    try:
        import requests

        # FMP uses date strings directly (YYYY-MM-DD format)
        url = 'https://financialmodelingprep.com/stable/historical-price-eod/full'
        params = {
            'symbol': symbol,
            'from': start_date,
            'to': end_date,
            'apikey': api_key
        }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()

        # Errors come back as a dict with an 'Error Message' key
        if isinstance(data, dict) and 'Error Message' in data:
            logger.error(f"FMP: {data['Error Message']} for {symbol}")
            return None

        # Stable endpoint returns a flat list of daily records
        historical = data if isinstance(data, list) else []

        if not historical:
            logger.warning(f"FMP: no historical data available for {symbol}")
            return None

        # Convert to our quote format
        quotes = []
        for record in historical:
            try:
                quotes.append({
                    'symbol': symbol,
                    'quote_date': record['date'],
                    'open': round(record['open'], 2) if record.get('open') is not None else None,
                    'high': round(record['high'], 2) if record.get('high') is not None else None,
                    'low': round(record['low'], 2) if record.get('low') is not None else None,
                    'close': round(record['close'], 2) if record.get('close') is not None else None,
                    'volume': int(record['volume']) if record.get('volume') is not None else 0,
                    'dividends': 0,  # FMP includes this in separate dividend endpoint
                    'stock_splits': 0,  # FMP includes this in separate splits endpoint
                    'data_source': 'fmp'
                })
            except (KeyError, ValueError, TypeError) as e:
                logger.warning(f"Skipping record for {symbol} on {record.get('date', 'unknown')}: {e}")
                continue
        
        if quotes:
            quotes.sort(key=lambda x: x['quote_date'])
            logger.info(f"FMP: retrieved {len(quotes)} quotes for {symbol}")
        
        return quotes if quotes else None

    except requests.exceptions.HTTPError as e:
        # Handle HTTP errors separately
        if e.response.status_code == 403:
            logger.error(f"FMP: API key invalid or access denied for {symbol}")
        elif e.response.status_code == 404:
            logger.info(f"FMP: {symbol} not found")
        else:
            logger.error(f"FMP HTTP error for {symbol}: {e}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"FMP request error for {symbol}: {e}")
        return None
    except Exception as e:
        logger.error(f"FMP error for {symbol}: {e}")
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

def bulk_get_quotes(symbol=None, db_path=DEFAULT_DB_PATH):
    """
    Retrieve daily quotes from database.
    
    Args:
        symbol: Stock symbol to retrieve (None = all symbols)
        db_path: Path to SQLite database
        
    Returns:
        Tuple of (column_names, rows) where:
            column_names: List of column names
            rows: List of tuples containing quote data
        Returns (None, None) on error
    """
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Build query based on whether symbol is specified
        if symbol:
            query = '''
                SELECT symbol, quote_date, open, high, low, close, volume, 
                       dividends, stock_splits, data_source, last_updated
                FROM daily_quotes
                WHERE symbol = ?
                ORDER BY symbol, quote_date
            '''
            cursor.execute(query, (symbol.upper(),))
            logger.info(f"Retrieving quotes for {symbol.upper()}")
        else:
            query = '''
                SELECT symbol, quote_date, open, high, low, close, volume, 
                       dividends, stock_splits, data_source, last_updated
                FROM daily_quotes
                ORDER BY symbol, quote_date
            '''
            cursor.execute(query)
            logger.info("Retrieving quotes for all symbols")
        
        # Get column names from cursor description
        column_names = [description[0] for description in cursor.description]
        
        # Fetch all results
        rows = cursor.fetchall()
        
        conn.close()
        
        if not rows:
            logger.warning(f"No data found{' for ' + symbol if symbol else ''}")
            return column_names, []
        
        logger.info(f"Retrieved {len(rows)} quotes from database")
        return column_names, rows
        
    except sqlite3.Error as e:
        logger.error(f"Database error during retrieval: {e}")
        return None, None
    except Exception as e:
        logger.error(f"Unexpected error during retrieval: {e}")
        return None, None


def save_quotes(quotes):
    """Save quotes to database"""
    if not quotes:
        return 0
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    saved = 0
    for quote in quotes:
        # Guard against partial/empty candles (e.g. yfinance NaN rows). Saving a
        # null-close row would overwrite previously-good data via INSERT OR REPLACE.
        # Skip it; the date stays "missing" and is retried on the next update.
        if quote.get('close') is None:
            logger.warning(f"Skipping {quote['symbol']} {quote['quote_date']}: null close")
            continue
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO daily_quotes
                (symbol, quote_date, open, high, low, close, volume, dividends, stock_splits, data_source, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (
                quote['symbol'], quote['quote_date'],
                # Store 6-dp precision so slow-growing synthetic ($1-based) series keep
                # their daily gain; output is rounded to <=3 digits at the response layer.
                round(quote.get('open'), 6) if quote.get('open') is not None else None,
                round(quote.get('high'), 6) if quote.get('high') is not None else None,
                round(quote.get('low'), 6) if quote.get('low') is not None else None,
                round(quote.get('close'), 6) if quote.get('close') is not None else None,
                int(quote.get('volume', 0)),
                round(quote.get('dividends', 0), 6),
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
# MONEY MARKET FUND DATA
# ============================================================================

def fetch_mmf_yield_fred(symbol, start_date, end_date=None, expense_ratio=0.0):
    """
    Fetch the daily Federal Funds Rate from FRED as a yield proxy for money
    market funds. Uses FRED's public CSV endpoint — no API key required.
    Returns a list of fund_metrics dicts.

    A fund's net 7-day yield runs below the fed funds rate by its expense ratio,
    so `expense_ratio` (an annual fraction, e.g. 0.0011 = 0.11%) is subtracted
    from the DFF rate and the net yield (floored at 0) is stored in yield_annual.
    Passing 0.0 reproduces the old gross-of-fees behavior.
    """
    if end_date is None:
        end_date = datetime.now().strftime('%Y-%m-%d')
    expense_ratio = float(expense_ratio or 0.0)
    try:
        import urllib.request as _urllib
        import csv as _csv
        import io as _io

        # The fredgraph.csv endpoint uses cosd/coed for the date window (the
        # observation_start/observation_end names are for the developer API and are
        # silently ignored here, which would otherwise return the full DFF history
        # back to 1954).
        url = (
            f"https://fred.stlouisfed.org/graph/fredgraph.csv"
            f"?id=DFF&cosd={start_date}&coed={end_date}"
        )
        req = _urllib.Request(url, headers={'User-Agent': 'local_stock/1.0'})
        with _urllib.urlopen(req, timeout=30) as resp:
            content = resp.read().decode('utf-8')

        metrics = []
        reader = _csv.DictReader(_io.StringIO(content))
        for row in reader:
            date_str = row.get('observation_date', row.get('DATE', '')).strip()
            rate_str = row.get('DFF', '').strip()
            if not date_str or not rate_str or rate_str == '.':
                continue
            try:
                gross = float(rate_str) / 100.0
                net = max(0.0, gross - expense_ratio)
                metrics.append({
                    'symbol': symbol.upper(),
                    'metric_date': date_str,
                    'yield_annual': net,
                    'yield_source': 'fred_dff_net' if expense_ratio else 'fred_dff',
                    'total_assets': None,
                    'expense_ratio': expense_ratio or None,
                    'data_source': 'fred',
                })
            except ValueError:
                continue
        logger.info(f"FRED: fetched {len(metrics)} yield records for {symbol} "
                    f"(expense_ratio={expense_ratio:.4%})")
        return metrics
    except Exception as e:
        logger.error(f"FRED fetch error for {symbol}: {e}")
        return []

def fetch_mmf_yield_yfinance(symbol):
    """Current net yield for a money market fund from yfinance (info['sevenDayYield']
    or 'yield'), as an annual fraction (e.g. 0.0368 = 3.68%), or None.

    This is the fund's own realized yield — already net of fees and reflecting the
    fund's type (government/prime/muni) and repo spread — so it anchors the generic
    FRED fed-funds proxy to the correct level per fund. Yahoo does not carry MM-fund
    distribution history, but it does populate this yield field."""
    try:
        info = yf.Ticker(symbol).info
        y = info.get('sevenDayYield')
        if y is None:
            y = info.get('yield')
        if y is None:
            return None
        y = float(y)
        if y > 1.0:  # some feeds report a percent (e.g. 3.68) rather than 0.0368
            y = y / 100.0
        return y if y >= 0 else None
    except Exception as e:
        logger.error(f"yfinance yield fetch failed for {symbol}: {e}")
        return None

def get_fund_expense_ratio(symbol):
    """Latest stored expense ratio (annual fraction) for a fund from
    symbol_fundamentals, or 0.0 if none is on record. Used to net FRED yields."""
    er = _fundamental_value(symbol, 'expense_ratio')
    return float(er) if er is not None else 0.0

def _get_fmp_api_key():
    """Return the stored FMP API key, or None."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT api_key FROM data_sources WHERE source_name = 'fmp'")
    row = cursor.fetchone()
    conn.close()
    return (row['api_key'] if row else None) or None

def fetch_fmp_dividends(symbol, api_key):
    """Fetch real per-share distributions for a fund/stock from FMP's stable
    /dividends endpoint. Returns a list of distribution dicts, or None when the
    data is unavailable (no key, premium-gated 402, legacy 403, or empty).

    Money market fund symbols require an FMP plan that includes mutual funds;
    without it FMP returns HTTP 402 and this returns None so the caller can fall
    back to the FRED proxy."""
    if not api_key:
        return None
    try:
        import requests
        url = 'https://financialmodelingprep.com/stable/dividends'
        resp = requests.get(url, params={'symbol': symbol, 'apikey': api_key}, timeout=15)
        if resp.status_code in (401, 402, 403):
            logger.info(f"FMP dividends unavailable for {symbol} (HTTP {resp.status_code}; "
                        f"plan may not include this symbol)")
            return None
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            logger.info(f"FMP dividends: non-JSON response for {symbol}")
            return None
        if isinstance(data, dict):  # error payloads come back as a dict
            logger.info(f"FMP dividends: {data.get('Error Message', 'no data')} for {symbol}")
            return None
        records = []
        for r in data:
            ex_date = r.get('date')
            if not ex_date:
                continue
            amount = r.get('adjDividend')
            if amount is None:
                amount = r.get('dividend')
            records.append({
                'symbol': symbol.upper(),
                'ex_date': ex_date,
                'amount': float(amount) if amount is not None else None,
                'yield_reported': r.get('yield'),
                'frequency': r.get('frequency'),
                'data_source': 'fmp',
            })
        logger.info(f"FMP: fetched {len(records)} distributions for {symbol}")
        return records or None
    except Exception as e:
        logger.error(f"FMP dividends fetch error for {symbol}: {e}")
        return None

def save_fund_distributions(records):
    """Batch-save fund distribution records to fund_distributions."""
    if not records:
        return 0
    conn = get_db_connection()
    cursor = conn.cursor()
    n = 0
    for r in records:
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO fund_distributions
                (symbol, ex_date, amount, yield_reported, frequency, data_source, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (r['symbol'], r['ex_date'], r.get('amount'),
                  r.get('yield_reported'), r.get('frequency'), r.get('data_source', 'fmp')))
            n += 1
        except Exception as e:
            logger.error(f"Error saving distribution {r.get('symbol')} {r.get('ex_date')}: {e}")
    conn.commit()
    conn.close()
    return n

def delete_fund_metrics(symbol):
    """Remove all fund_metrics rows for a symbol. Used before re-seeding from a
    different yield source so sources are never mixed within one series."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM fund_metrics WHERE symbol = ?", (symbol.upper(),))
    conn.commit()
    conn.close()

def get_fund_yield_source(symbol):
    """yield_source of the most recent fund_metrics row for a symbol
    (e.g. 'fmp_distribution', 'fred_dff_net', 'fred_dff'), or None."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''SELECT yield_source FROM fund_metrics WHERE symbol = ?
                      ORDER BY metric_date DESC LIMIT 1''', (symbol.upper(),))
    row = cursor.fetchone()
    conn.close()
    return row['yield_source'] if row else None

def distributions_to_daily_metrics(symbol, pairs, end_date):
    """Convert real distributions into daily fund_metrics rows whose gap-aware
    compounding reproduces a reinvested total-return series.

    pairs: ascending [(ex_date 'YYYY-MM-DD', amount_per_share), ...] with amount>0.
    Money market NAV is held at $1.00, so a distribution of `d` per share reinvests
    to a growth factor of (1 + d) over the period since the prior ex-date. That
    growth is spread across the period's days as a constant implied annual yield
    (yield_annual = 365 * ((1+d)**(1/gap_days) - 1)), so the stored series is a
    smooth daily curve that hits the exact reinvested value on every ex-date.
    Days after the last distribution are projected at the last period's run-rate
    (self-corrects when the next real distribution arrives). The first ex-date is
    the $1.00 anchor (its own pre-history is not modeled)."""
    if len(pairs) < 2:
        return []
    rows = [(pairs[0][0], 0.0)]  # anchor at 1.00; first row gets no compounding
    prev = datetime.strptime(pairs[0][0], '%Y-%m-%d').date()
    last_ya = 0.0
    for ex_str, amount in pairs[1:]:
        ex = datetime.strptime(ex_str, '%Y-%m-%d').date()
        gap = (ex - prev).days
        if gap < 1:
            continue
        ya = 365.0 * ((1.0 + max(0.0, amount)) ** (1.0 / gap) - 1.0)
        last_ya = ya
        d = prev + timedelta(days=1)
        while d <= ex:
            rows.append((d.isoformat(), ya))
            d += timedelta(days=1)
        prev = ex
    end = datetime.strptime(end_date, '%Y-%m-%d').date()
    d = prev + timedelta(days=1)
    while d <= end:  # project run-rate to end_date
        rows.append((d.isoformat(), last_ya))
        d += timedelta(days=1)
    return [{'symbol': symbol.upper(), 'metric_date': dt, 'yield_annual': ya,
             'yield_source': 'fmp_distribution', 'total_assets': None,
             'expense_ratio': None, 'data_source': 'fmp'} for dt, ya in rows]

def seed_fund_from_fmp(symbol, end_date=None):
    """(Re)build a fund's yield metrics from real FMP distributions.
    Returns the number of daily metric rows written, or 0 when FMP has no usable
    distribution data for the symbol (caller then falls back to the FRED proxy).
    Replaces any existing metrics for the symbol so yield sources never mix."""
    symbol = symbol.upper()
    if end_date is None:
        end_date = datetime.now().strftime('%Y-%m-%d')
    dists = fetch_fmp_dividends(symbol, _get_fmp_api_key())
    if not dists:
        return 0
    save_fund_distributions(dists)
    pairs = sorted((d['ex_date'], d['amount']) for d in dists
                   if d.get('amount') and d['amount'] > 0)
    metrics = distributions_to_daily_metrics(symbol, pairs, end_date)
    if not metrics:
        return 0
    delete_fund_metrics(symbol)
    save_fund_metrics(metrics)
    return len(metrics)

def seed_fund_from_yield_anchor(symbol, years=3, end_date=None):
    """Seed MM metrics from FRED DFF re-leveled to the fund's own current yield
    (yfinance): net_yield[d] = max(0, DFF[d] + (fund_yield_now - DFF_now)). The
    measured offset captures the fund's fees, type and repo spread in one number
    while DFF supplies the historical rate shape. Returns row count, or 0 when the
    fund yield is unavailable (caller falls back to the plain expense-netted proxy)."""
    symbol = symbol.upper()
    if end_date is None:
        end_date = datetime.now().strftime('%Y-%m-%d')
    fund_yield = fetch_mmf_yield_yfinance(symbol)
    if fund_yield is None:
        return 0
    start_date = (datetime.now() - timedelta(days=365 * years)).strftime('%Y-%m-%d')
    dff = fetch_mmf_yield_fred(symbol, start_date, end_date)  # gross DFF (expense_ratio=0)
    if not dff:
        return 0
    dff_now = max(dff, key=lambda r: r['metric_date'])['yield_annual']
    spread = fund_yield - dff_now
    metrics = []
    for m in dff:
        m2 = dict(m)
        m2['yield_annual'] = max(0.0, (m['yield_annual'] or 0.0) + spread)
        m2['yield_source'] = 'fred_dff_anchored'
        metrics.append(m2)
    delete_fund_metrics(symbol)
    save_fund_metrics(metrics)
    print(f"  Anchored to yfinance yield {fund_yield:.4%} "
          f"(DFF now {dff_now:.4%}, spread {spread:+.4%})")
    return len(metrics)

def update_anchored_fund_today(symbol):
    """Append rows accruing the fund's real current yfinance yield since the last
    stored date (exact going forward). Returns rows written, or 0 if none/unavailable."""
    symbol = symbol.upper()
    today = datetime.now().strftime('%Y-%m-%d')
    fund_yield = fetch_mmf_yield_yfinance(symbol)
    if fund_yield is None:
        return 0
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''SELECT quote_date, close FROM daily_quotes
                      WHERE symbol = ? AND data_source = 'synthetic_mmf'
                      ORDER BY quote_date DESC LIMIT 1''', (symbol,))
    last = cursor.fetchone()
    conn.close()
    if not last:
        return 0
    last_date = last['quote_date']
    last_price = last['close']
    d0 = datetime.strptime(last_date, '%Y-%m-%d').date()
    dend = datetime.strptime(today, '%Y-%m-%d').date()
    if dend <= d0:
        return 0
    new_pairs = []
    d = d0 + timedelta(days=1)
    while d <= dend:
        new_pairs.append((d.isoformat(), fund_yield))
        d += timedelta(days=1)
    metric_rows = [{'symbol': symbol, 'metric_date': dt, 'yield_annual': fund_yield,
                    'yield_source': 'yfinance_yield', 'total_assets': None,
                    'expense_ratio': None, 'data_source': 'yfinance'} for dt, _ in new_pairs]
    save_fund_metrics(metric_rows)
    # Anchor on the last stored price so the first new row compounds across the gap.
    anchored = _synthetic_series_from_metrics(
        [(last_date, None)] + new_pairs, start_price=last_price)[1:]
    quotes = [{'symbol': symbol, 'quote_date': dt, 'open': p, 'high': p, 'low': p,
               'close': p, 'volume': 0, 'dividends': fund_yield, 'stock_splits': 0.0,
               'data_source': 'synthetic_mmf'} for dt, p in anchored]
    return save_quotes(quotes)

def save_fund_metrics(metrics_list):
    """Batch-save fund metric records to fund_metrics table."""
    if not metrics_list:
        return 0
    conn = get_db_connection()
    cursor = conn.cursor()
    inserted = 0
    for m in metrics_list:
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO fund_metrics
                (symbol, metric_date, yield_annual, yield_source,
                 total_assets, expense_ratio, data_source, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (m['symbol'], m['metric_date'], m['yield_annual'],
                  m['yield_source'], m.get('total_assets'),
                  m.get('expense_ratio'), m['data_source']))
            inserted += 1
        except Exception as e:
            logger.error(f"Error saving fund metric {m['symbol']} {m['metric_date']}: {e}")
    conn.commit()
    conn.close()
    return inserted

def _synthetic_series_from_metrics(rows, start_price=1.0):
    """
    Compound daily yield into a total-return price series across calendar gaps.

    rows: ordered iterable of (metric_date 'YYYY-MM-DD', yield_annual) ascending.
    price[i] = price[i-1] * (1 + yield_annual[i] / 365) ** gap_days
    where gap_days = (date[i] - date[i-1]).days. The first row is anchored at
    start_price (no compounding applied to it).

    Applying the exponent over the actual elapsed days credits weekends, holidays
    and any missing observations, eliminating the under-compounding that occurs
    when each row is treated as a single day. When all gaps are 1 day this reduces
    to the simple per-row formula.

    Returns a list of (metric_date, price) tuples.
    """
    series = []
    price = start_price
    prev_date = None
    for date_str, yield_annual in rows:
        cur_date = datetime.strptime(date_str, '%Y-%m-%d')
        if prev_date is not None:
            gap_days = (cur_date - prev_date).days
            if gap_days < 1:
                gap_days = 1
            price *= (1.0 + (yield_annual or 0.0) / 365.0) ** gap_days
        series.append((date_str, price))
        prev_date = cur_date
    return series

def compute_synthetic_prices(symbol):
    """
    Compound daily yield into a total-return price series starting at $1.00.
    Returns daily_quotes-compatible dicts for all dates in fund_metrics.
    Compounding is gap-aware (see _synthetic_series_from_metrics).
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT metric_date, yield_annual
        FROM fund_metrics WHERE symbol = ?
        ORDER BY metric_date ASC
    ''', (symbol.upper(),))
    rows = [(r['metric_date'], r['yield_annual']) for r in cursor.fetchall()]
    conn.close()

    if not rows:
        return []

    yields = dict(rows)
    quotes = []
    for date_str, price in _synthetic_series_from_metrics(rows):
        quotes.append({
            'symbol': symbol.upper(),
            'quote_date': date_str,
            'open': price,
            'high': price,
            'low': price,
            'close': price,
            'volume': 0,
            'dividends': yields.get(date_str) or 0.0,
            'stock_splits': 0.0,
            'data_source': 'synthetic_mmf',
        })
    return quotes

def rebuild_synthetic_prices(symbol):
    """Delete all price rows and recompute the synthetic series."""
    quotes = compute_synthetic_prices(symbol)
    if not quotes:
        return 0
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM daily_quotes WHERE symbol = ?", (symbol.upper(),))
    conn.commit()
    conn.close()
    return save_quotes(quotes)

def verify_synthetic_prices(symbol, tolerance=1e-6):
    """
    Recompute the expected synthetic series from fund_metrics and compare it to the
    stored daily_quotes synthetic rows. Read-only; does not modify the database.
    Returns a report dict (see _print_verify_report for fields).
    """
    symbol = symbol.upper()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT metric_date, yield_annual FROM fund_metrics
        WHERE symbol = ? ORDER BY metric_date ASC
    ''', (symbol,))
    metric_rows = [(r['metric_date'], r['yield_annual']) for r in cursor.fetchall()]
    cursor.execute('''
        SELECT quote_date, close FROM daily_quotes
        WHERE symbol = ? AND data_source = 'synthetic_mmf'
        ORDER BY quote_date ASC
    ''', (symbol,))
    stored = [(r['quote_date'], r['close']) for r in cursor.fetchall()]
    conn.close()

    report = {
        'symbol': symbol,
        'metric_count': len(metric_rows),
        'stored_count': len(stored),
    }
    if not metric_rows:
        report.update({'passed': False, 'error': 'no fund_metrics for symbol'})
        return report
    if not stored:
        report.update({'passed': False, 'error': 'no synthetic rows stored (run --rebuild-fund)'})
        return report

    expected_by_date = dict(_synthetic_series_from_metrics(metric_rows))
    stored_by_date = dict(stored)

    first_price_ok = abs(stored[0][1] - 1.0) < tolerance if stored[0][1] is not None else False
    count_ok = (len(stored) == len(metric_rows)
                and set(stored_by_date) == {d for d, _ in metric_rows})
    no_nulls = all(c is not None for _, c in stored)
    monotonic_ok = all(stored[i][1] is not None and stored[i-1][1] is not None
                       and stored[i][1] >= stored[i-1][1] - tolerance
                       for i in range(1, len(stored)))

    max_deviation = 0.0
    for d, c in stored:
        if c is None or d not in expected_by_date:
            continue
        dev = abs(c - expected_by_date[d])
        if dev > max_deviation:
            max_deviation = dev

    d0 = datetime.strptime(stored[0][0], '%Y-%m-%d')
    dN = datetime.strptime(stored[-1][0], '%Y-%m-%d')
    span_days = (dN - d0).days
    final_price = stored[-1][1]
    implied_annual_return = None
    if span_days > 0 and final_price and final_price > 0:
        implied_annual_return = final_price ** (365.0 / span_days) - 1.0
    mean_yield = sum((y or 0.0) for _, y in metric_rows) / len(metric_rows)

    passed = (first_price_ok and count_ok and no_nulls and monotonic_ok
              and max_deviation < 1e-5)

    report.update({
        'first_price_ok': first_price_ok,
        'count_ok': count_ok,
        'no_nulls': no_nulls,
        'monotonic_ok': monotonic_ok,
        'max_deviation': max_deviation,
        'final_price': final_price,
        'span_days': span_days,
        'implied_annual_return': implied_annual_return,
        'mean_yield': mean_yield,
        'passed': passed,
    })
    return report

def _print_verify_report(rep):
    """Pretty-print a verify_synthetic_prices report dict."""
    status = 'PASS' if rep.get('passed') else 'FAIL'
    print(f"\n[{status}] {rep['symbol']} synthetic verification")
    if 'error' in rep:
        print(f"  error: {rep['error']}")
        return
    print(f"  metric rows / stored rows : {rep['metric_count']} / {rep['stored_count']}")
    print(f"  first price == 1.0        : {rep['first_price_ok']}")
    print(f"  count & dates match       : {rep['count_ok']}")
    print(f"  no null closes            : {rep['no_nulls']}")
    print(f"  monotonic non-decreasing  : {rep['monotonic_ok']}")
    print(f"  max deviation vs recompute: {rep['max_deviation']:.2e}")
    print(f"  final price               : {rep['final_price']:.6f}")
    print(f"  span (days)               : {rep['span_days']}")
    if rep['implied_annual_return'] is not None:
        print(f"  implied annual return     : {rep['implied_annual_return']*100:.3f}%")
    print(f"  mean annual yield         : {rep['mean_yield']*100:.3f}%")

def rebuild_and_verify_fund(symbol):
    """Rebuild a fund's synthetic series from stored metrics, then verify it."""
    symbol = symbol.upper()
    n = rebuild_synthetic_prices(symbol)
    print(f"{symbol}: rebuilt {n} synthetic price records")
    _print_verify_report(verify_synthetic_prices(symbol))

def rebuild_and_verify_all_funds():
    """Rebuild + verify every MONEY_MARKET fund."""
    funds = list_money_market_funds()
    if not funds:
        print("No money market funds to rebuild.")
        return
    for f in funds:
        rebuild_and_verify_fund(f['symbol'])

def verify_all_funds():
    """Verify (no rebuild) every MONEY_MARKET fund."""
    funds = list_money_market_funds()
    if not funds:
        print("No money market funds to verify.")
        return
    for f in funds:
        _print_verify_report(verify_synthetic_prices(f['symbol']))

def update_synthetic_price_today(symbol):
    """Append new synthetic price rows since the last stored date."""
    today = datetime.now().strftime('%Y-%m-%d')

    src = get_fund_yield_source(symbol)
    # FMP distribution-sourced funds: re-pull distributions and rebuild the
    # reinvested series (cheap; folds in any new distribution and keeps the
    # run-rate projection current). Falls through to FRED if FMP data is gone.
    if src == 'fmp_distribution':
        n = seed_fund_from_fmp(symbol, today)
        if n:
            return rebuild_synthetic_prices(symbol)
    # yfinance-yield-anchored funds: accrue the fund's real current yield forward.
    elif src in ('fred_dff_anchored', 'yfinance_yield'):
        return update_anchored_fund_today(symbol)

    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    expense_ratio = get_fund_expense_ratio(symbol)
    recent = fetch_mmf_yield_fred(symbol, yesterday, today, expense_ratio=expense_ratio)
    if recent:
        save_fund_metrics(recent)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT quote_date, close FROM daily_quotes
        WHERE symbol = ? AND data_source = 'synthetic_mmf'
        ORDER BY quote_date DESC LIMIT 1
    ''', (symbol.upper(),))
    last_row = cursor.fetchone()
    conn.close()

    if not last_row:
        return rebuild_synthetic_prices(symbol)

    last_date = last_row['quote_date']
    last_price = last_row['close']

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT metric_date, yield_annual FROM fund_metrics
        WHERE symbol = ? AND metric_date > ?
        ORDER BY metric_date ASC
    ''', (symbol.upper(), last_date))
    new_rows = cursor.fetchall()
    conn.close()

    if not new_rows:
        return 0

    # Anchor on the last stored row so the first new row compounds across the
    # actual calendar gap (gap-aware, matching compute_synthetic_prices).
    new_pairs = [(r['metric_date'], r['yield_annual']) for r in new_rows]
    yields = dict(new_pairs)
    anchored = _synthetic_series_from_metrics(
        [(last_date, None)] + new_pairs, start_price=last_price)[1:]

    new_quotes = []
    for date_str, price in anchored:
        new_quotes.append({
            'symbol': symbol.upper(),
            'quote_date': date_str,
            'open': price, 'high': price, 'low': price, 'close': price,
            'volume': 0,
            'dividends': yields.get(date_str) or 0.0,
            'stock_splits': 0.0,
            'data_source': 'synthetic_mmf',
        })
    return save_quotes(new_quotes)

def add_money_market_fund(symbol, years=3, expense_ratio=None):
    """Add a symbol, force-classify as MONEY_MARKET, and seed historical yield.

    The stored yield is netted of the fund's expense ratio (see fetch_mmf_yield_fred).
    `expense_ratio` (annual fraction) overrides the yfinance-reported value when given;
    otherwise it is taken from fundamentals, defaulting to 0.0 if unavailable.
    Re-running this on an existing fund re-fetches FRED and rebuilds, so it is also
    the way to migrate an already-seeded gross series to net."""
    symbol = symbol.upper()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO symbols (symbol, active) VALUES (?, 1)', (symbol,))
    conn.commit()
    conn.close()

    classification = classify_symbol(symbol)
    classification['symbol_type'] = 'MONEY_MARKET'
    save_classification(symbol, classification)
    name_str = f" ({classification['name']})" if classification['name'] else ""
    print(f"Added {symbol} as MONEY_MARKET{name_str}")

    # Persist fundamentals so the expense ratio is on record for future updates.
    rec = fetch_fundamentals_yfinance(symbol)
    if rec:
        save_fundamentals([rec])
    if expense_ratio is None:
        expense_ratio = (rec.get('expense_ratio') if rec else None) or 0.0
    expense_ratio = float(expense_ratio)
    print(f"  Using expense ratio {expense_ratio:.4%} (net-of-fees yield)")

    today = datetime.now()
    end_date = today.strftime('%Y-%m-%d')

    # Prefer real distributions from FMP (exact reinvested total return); fall
    # back to the FRED fed-funds proxy net of expenses when FMP has no fund data
    # (e.g. the plan does not cover mutual funds -> HTTP 402).
    n_fmp = seed_fund_from_fmp(symbol, end_date)
    if n_fmp:
        rebuilt = rebuild_synthetic_prices(symbol)
        update_symbol_tracking(symbol)
        print(f"  Built {rebuilt} synthetic price records from FMP distributions "
              f"({n_fmp} daily metrics)")
        return

    # Next best (free): FRED DFF re-leveled to the fund's own yfinance yield.
    n_anchor = seed_fund_from_yield_anchor(symbol, years, end_date)
    if n_anchor:
        rebuilt = rebuild_synthetic_prices(symbol)
        update_symbol_tracking(symbol)
        print(f"  Built {rebuilt} synthetic price records from FRED DFF anchored to fund yield")
        return
    print("  yfinance yield unavailable; using FRED fed-funds proxy net of expense")

    start_date = (today - timedelta(days=365 * years)).strftime('%Y-%m-%d')
    print(f"  Fetching yield data from FRED ({start_date} to {end_date})...")
    delete_fund_metrics(symbol)  # clear any prior source so series stays homogeneous
    metrics = fetch_mmf_yield_fred(symbol, start_date, end_date, expense_ratio=expense_ratio)
    if metrics:
        saved = save_fund_metrics(metrics)
        print(f"  Saved {saved} yield records")
        rebuilt = rebuild_synthetic_prices(symbol)
        update_symbol_tracking(symbol)
        print(f"  Built {rebuilt} synthetic price records")
    else:
        print(f"  Failed to fetch yield data from FRED")

# ============================================================================
# SMART UPDATE LOGIC
# ============================================================================

def smart_update_symbol(symbol, years=None, since=None):
    """
    Smart update that only fetches missing data
    - New symbols: fetch full history (since date, years parameter, or default)
    - Existing symbols: fetch from last date forward
    - Money market funds: update synthetic price from FRED yield
    """
    symbol_type = get_symbol_type(symbol)
    if symbol_type == 'MONEY_MARKET':
        update_synthetic_price_today(symbol)
        update_symbol_tracking(symbol)
        return True

    last_date = get_last_date_for_symbol(symbol)
    today = datetime.now().date()

    if since:
        # Explicit --since always wins, whether the symbol is new or already
        # has data - otherwise a backfill request against an existing symbol
        # would silently be ignored in favor of the last-date+1 forward fetch.
        from datetime import date as _date
        start_date = _date.fromisoformat(since)
        logger.info(f"{symbol}: fetching from {since} (--since override)")
    elif last_date is None:
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

def force_update_symbol(symbol, years=None, since=None):
    """
    Force-refresh a single symbol, overwriting existing rows in the window.
    Unlike smart_update_symbol (which only fetches dates AFTER the last stored one),
    this re-fetches a window so it can REPAIR rows with null OHLC and backfill gaps.
    - Money market funds: refresh synthetic price from FRED yield.
    - Others: re-fetch from --since date, or years*365 days back, or 7 days (default).
      INSERT OR REPLACE overwrites the window.
    Returns True on success.
    """
    symbol = symbol.upper()
    symbol_type = get_symbol_type(symbol)
    if symbol_type == 'MONEY_MARKET':
        update_synthetic_price_today(symbol)
        update_symbol_tracking(symbol)
        print(f"{symbol}: synthetic price refreshed")
        return True

    today = datetime.now().date()
    if since:
        from datetime import date as _date
        start_date = _date.fromisoformat(since)
        logger.info(f"Force-updating {symbol} from {since} (--since) to {today}")
    else:
        lookback_days = years * 365 if years else 7
        start_date = today - timedelta(days=lookback_days)
        logger.info(f"Force-updating {symbol} from {start_date} to {today}")

    quotes = fetch_data_multi_source(symbol, start_date.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
    if quotes:
        saved = save_quotes(quotes)
        update_symbol_tracking(symbol)
        print(f"{symbol}: re-fetched {len(quotes)} rows, saved {saved}")
        return True
    print(f"{symbol}: no data fetched ({start_date} to {today})")
    logger.warning(f"Force update fetched no data for {symbol}")
    return False

def update_all_symbols(years=None, since=None):
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
        if smart_update_symbol(symbol, years, since=since):
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
    """Check if market is open (9:30 AM - 4:00 PM ET, Mon-Fri)"""
    now_et = datetime.now(ZoneInfo('America/New_York'))

    if now_et.weekday() >= 5:
        return False

    market_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)

    return market_open <= now_et <= market_close

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
        
        # Money market funds have no live quote worth fetching - yfinance just
        # reports the flat $1 NAV, not the yield-compounded synthetic price.
        # Go straight to the synthetic price series.
        if get_symbol_type(symbol) == 'MONEY_MARKET':
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT close FROM daily_quotes
                WHERE symbol = ? AND data_source = 'synthetic_mmf'
                ORDER BY quote_date DESC
                LIMIT 1
            ''', (symbol,))
            result = cursor.fetchone()
            conn.close()

            if result and result['close']:
                live_quote_cache[symbol] = (result['close'], now)
                return result['close']

            return None

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

def get_statistics():
    """Return database statistics as a dict."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(*) as count FROM daily_quotes')
    total_quotes = cursor.fetchone()['count']

    cursor.execute('''
        SELECT s.symbol, s.name, s.symbol_type, COUNT(dq.quote_date) as quotes,
               MIN(dq.quote_date) as first_date,
               MAX(dq.quote_date) as last_date
        FROM symbols s
        LEFT JOIN daily_quotes dq ON s.symbol = dq.symbol
        WHERE s.active = 1
        GROUP BY s.symbol
        ORDER BY s.symbol
    ''')
    symbols = cursor.fetchall()

    cursor.execute('SELECT COUNT(*) as count FROM market_closures')
    closure_count = cursor.fetchone()['count']

    conn.close()

    return {
        'db_path': DB_PATH,
        'total_quotes': total_quotes,
        'closure_count': closure_count,
        'symbols': [
            {
                'symbol': row['symbol'],
                'name': row['name'] or '',
                'symbol_type': row['symbol_type'] or 'EQUITY',
                'quotes': row['quotes'],
                'first_date': row['first_date'] or 'N/A',
                'last_date': row['last_date'] or 'N/A',
            }
            for row in symbols
        ],
    }


def show_statistics():
    """Show database statistics"""
    stats = get_statistics()

    print(f"\n{'='*90}")
    print(f"Database: {stats['db_path']}")
    print(f"Total Quotes: {stats['total_quotes']:,}")
    print(f"Market Closures Detected: {stats['closure_count']}")
    print(f"{'='*90}")
    print(f"{'Symbol':<10} {'Type':<12} {'Quotes':>10} {'First Date':<12} {'Last Date':<12} {'Name'}")
    print(f"{'-'*90}")

    for row in stats['symbols']:
        print(f"{row['symbol']:<10} {row['symbol_type']:<12} {row['quotes']:>10,} {row['first_date']:<12} {row['last_date']:<12} {row['name']}")

    print(f"{'='*90}\n")

def list_market_closures():
    """List all detected market closure dates"""
    closures = get_market_closures()

    if not closures:
        print("No market closures detected yet")
        return

    print(f"\n{'='*80}")
    print(f"Detected Market Closures: {len(closures)}")
    print(f"{'='*80}")
    print(f"{'Day':<12} {'Date':<15} {'Reason'}")
    print(f"{'-'*80}")

    for row in closures:
        print(f"{row['day']:<12} {row['date']:<15} {row['reason']}")

    print(f"{'='*80}\n")


def _print_watchlist_report(alerts, loss_pct, gain_pct=None):
    """Print a readable report of only the symbols/periods that breached loss_pct
    (and gain_pct, if given)."""
    header = f"Watch List Alert Report (loss >={loss_pct}%"
    header += f", gain >={gain_pct}%" if gain_pct is not None else ""
    header += ")"
    print(f"\n{'='*90}")
    print(header)
    print(f"{'='*90}")
    if not alerts:
        print("No symbols breached the threshold(s).")
        print(f"{'='*90}\n")
        return
    print(f"{'Symbol':<10} {'Period':<8} {'Dir':<6} {'Baseline Date':<15} {'Baseline':>10} {'Current':>10} {'% Change':>9}")
    print(f"{'-'*90}")
    for a in alerts:
        print(f"{a['symbol']:<10} {a['period']:<8} {a['direction']:<6} {a['baseline_date']:<15} "
              f"{fmt_price(a['baseline_close']):>10} {fmt_price(a['current_close']):>10} {a['percent_change']:>8.2f}%")
    print(f"{'='*90}\n")


def import_closures_from_file(filename):
    """Import market closures from a CSV file (DATE[,REASON] per line)."""
    if not os.path.exists(filename):
        print(f"Error: File not found: {filename}")
        return

    imported = 0
    skipped = 0
    with open(filename, newline='', encoding='utf-8') as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split(',', 1)
            date_str = parts[0].strip()
            reason = parts[1].strip() if len(parts) > 1 else None
            try:
                datetime.strptime(date_str, '%Y-%m-%d')
            except ValueError:
                print(f"  Line {lineno}: skipped (invalid date '{date_str}')")
                skipped += 1
                continue
            mark_market_closure(date_str, reason)
            imported += 1

    print(f"Import complete: {imported} closures imported, {skipped} lines skipped.")

def import_closures_from_builtin():
    """Seed market_closures table from the MARKET_CLOSURES constant in this file."""
    imported = 0
    for entry in MARKET_CLOSURES:
        date_str = entry.get('atDate', '').strip()
        if not date_str:
            continue
        reason = entry.get('eventName', '')
        hours = entry.get('tradingHour', '')
        if hours:
            reason = f"{reason} (early close {hours})" if reason else f"Early close {hours}"
        mark_market_closure(date_str, reason or None)
        imported += 1
    print(f"Import complete: {imported} closures seeded from built-in MARKET_CLOSURES.")

# ============================================================================
# WEB SERVER
# ============================================================================

app = Flask(__name__)

def fmt_price(v):
    """Format a price for output: round to <=3 decimals, strip trailing zeros.
    e.g. 123.3333 -> '123.333', 1.0010 -> '1.001', 123.0 -> '123'."""
    return ('%.3f' % v).rstrip('0').rstrip('.')

def fmt_yield(v):
    """Format a yield fraction for output, keeping small magnitudes and trimming
    float noise. e.g. 0.0363 -> '0.0363', 0.036300000000001 -> '0.0363'."""
    return ('%.6f' % v).rstrip('0').rstrip('.')

def _latest_close_value(symbol):
    """Return latest stored close for a symbol as float, or None. No auto-fetch."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT close FROM daily_quotes
        WHERE symbol = ? ORDER BY quote_date DESC LIMIT 1
    ''', (symbol.upper(),))
    row = cursor.fetchone()
    conn.close()
    return row['close'] if row and row['close'] is not None else None

def _latest_field_value(symbol, field):
    """Return latest stored OHLCV field for a symbol as float, or None. No auto-fetch.
    `field` must be one of open/high/low/close/volume (caller-validated)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT {field} AS val FROM daily_quotes
        WHERE symbol = ? ORDER BY quote_date DESC LIMIT 1
    ''', (symbol.upper(),))
    row = cursor.fetchone()
    conn.close()
    return row['val'] if row and row['val'] is not None else None

def _close_value_on_date(symbol, date):
    """Return close on a date (or the nearest prior date) as float, or None. No auto-fetch.
    Mirrors the exact-then-nearest-before logic of get_quote_helper."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT close FROM daily_quotes
        WHERE symbol = ? AND quote_date = ?
    ''', (symbol.upper(), date))
    row = cursor.fetchone()
    if not row or row['close'] is None:
        cursor.execute('''
            SELECT close FROM daily_quotes
            WHERE symbol = ? AND quote_date <= ?
            ORDER BY quote_date DESC LIMIT 1
        ''', (symbol.upper(), date))
        row = cursor.fetchone()
    conn.close()
    return row['close'] if row and row['close'] is not None else None

def _compute_watchlist_alerts(symbols, loss_pct=12.0, gain_pct=None):
    """For each symbol, compare latest close against close ~1wk ago, ~1mo ago, and
    YTD (Jan 1 of current year), using _close_value_on_date's nearest-prior-date
    logic (handles weekends/holidays automatically). Returns a list of alert dicts
    for any period where the move from the baseline meets/exceeds loss_pct (a drop)
    or, if gain_pct is given, gain_pct (a rise). Symbols or periods with no data
    are silently skipped, not an error. Each alert has a 'direction' of 'loss' or
    'gain' and a signed 'percent_change' (negative = drop, positive = rise)."""
    today = datetime.now().date()
    periods = [
        ('1wk', today - timedelta(days=7)),
        ('1mo', today - timedelta(days=30)),
        ('ytd', datetime(today.year, 1, 1).date()),
    ]
    alerts = []
    for symbol in symbols:
        sym = symbol.strip().upper()
        if not sym:
            continue
        current = _latest_close_value(sym)
        if current is None:
            continue
        for period_label, period_date in periods:
            date_str = period_date.strftime('%Y-%m-%d')
            baseline = _close_value_on_date(sym, date_str)
            if not baseline:
                continue
            pct_change = (current - baseline) / baseline * 100.0
            direction = None
            if pct_change <= -loss_pct:
                direction = 'loss'
            elif gain_pct is not None and pct_change >= gain_pct:
                direction = 'gain'
            if direction:
                alerts.append({
                    'symbol': sym,
                    'period': period_label,
                    'direction': direction,
                    'baseline_date': date_str,
                    'baseline_close': round(baseline, 4),
                    'current_close': round(current, 4),
                    'percent_change': round(pct_change, 4),
                })
    return alerts

def get_closes_in_range(symbol, start_date, end_date):
    """Return [(quote_date, close), ...] for a symbol within [start_date, end_date]
    (inclusive), ordered by date ascending. Dates are 'YYYY-MM-DD' strings.
    No auto-fetch; returns [] if the symbol has no rows in range."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT quote_date, close FROM daily_quotes
        WHERE symbol = ? AND quote_date BETWEEN ? AND ? AND close IS NOT NULL
        ORDER BY quote_date ASC
    ''', (symbol.upper(), start_date, end_date))
    rows = cursor.fetchall()
    conn.close()
    return [(row['quote_date'], row['close']) for row in rows]

_CHART_SPANS = {
    '1mo': lambda today: today - timedelta(days=30),
    '3mo': lambda today: today - timedelta(days=90),
    '6mo': lambda today: today - timedelta(days=182),
    '1yr': lambda today: today - timedelta(days=365),
    '5yr': lambda today: today - timedelta(days=365 * 5),
    'ytd': lambda today: datetime(today.year, 1, 1).date(),
}

def _resolve_chart_span(span):
    """Return (start_date, end_date) as 'YYYY-MM-DD' strings for a span keyword,
    or None if span is not recognized."""
    today = datetime.now().date()
    if span not in _CHART_SPANS:
        return None
    start = _CHART_SPANS[span](today)
    return start.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d')

@app.route('/funds')
def list_funds_endpoint():
    """List money market (synthetic) funds as JSON."""
    try:
        return jsonify(list_money_market_funds())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/export/<symbol>')
@app.route('/export')
def export_quotes_to_html(symbol=None):
    """
    Generate HTML table from daily quotes.
    
    Args:
        symbol: Stock symbol to export (None = all symbols)
        db_path: Path to SQLite database
        
    Returns:
        HTML string, or None on error
    """
    
    # Get data from database
    column_names, rows = bulk_get_quotes(symbol, DB_PATH)
    
    if column_names is None:
        logger.error("Failed to retrieve data from database")
        return '<H2>Failed to Retrieve Data</H2>'
    
    if not rows:
        logger.warning(f"No data to export{' for ' + symbol if symbol else ''}")
        return f"<H2>No Data for '{symbol}'</H2>"
    
    try:
        html_parts = []
        html_parts.append('<!DOCTYPE html>\n<html>\n<head>\n')
        html_parts.append('<title>Stock Quotes</title>\n')
        html_parts.append('<style>\n')
        html_parts.append('table { border-collapse: collapse; width: 100%; }\n')
        html_parts.append('th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }\n')
        html_parts.append('th { background-color: #4CAF50; color: white; }\n')
        html_parts.append('tr:nth-child(even) { background-color: #f2f2f2; }\n')
        html_parts.append('</style>\n</head>\n<body>\n')
        html_parts.append(f'<h1>Stock Quotes{" for " + symbol if symbol else ""}</h1>\n')
        html_parts.append('<table>\n<thead><tr>\n')
        
        # Write headers
        for col in column_names:
            html_parts.append(f'<th>{col}</th>\n')
        html_parts.append('</tr></thead>\n<tbody>\n')
        
        # Write data rows
        for row in rows:
            html_parts.append('<tr>\n')
            for value in row:
                html_parts.append(f'<td>{value if value is not None else ""}</td>\n')
            html_parts.append('</tr>\n')
        
        html_parts.append('</tbody>\n</table>\n</body>\n</html>')
        
        html_string = ''.join(html_parts)
        
        logger.info(f"Successfully generated HTML for {len(rows)} quotes")
        return html_string
        
    except Exception as e:
        logger.error(f"Error generating HTML: {e}")
        return None


@app.route('/watchlist')
def watchlist_endpoint():
    """Compare each symbol's latest close vs 1wk/1mo/YTD baselines and return
    alerts where the move meets/exceeds loss_pct (a drop, default 12.0) or,
    if gain_pct is given, gain_pct (a rise). Query params:
      symbols   - optional comma-separated list (default: all tracked symbols)
      loss_pct  - optional float threshold in percent (default 12.0)
      gain_pct  - optional float threshold in percent; omitted = no upside check
    Returns JSON: {"loss_pct": <float>, "gain_pct": <float|null>, "alerts": [
    {symbol, period, direction, baseline_date, baseline_close, current_close,
    percent_change}, ... ]}"""
    try:
        symbols_param = request.args.get('symbols')
        if symbols_param:
            symbols = [s.strip().upper() for s in symbols_param.split(',') if s.strip()]
        else:
            symbols = get_tracked_symbols()

        loss_pct_param = request.args.get('loss_pct')
        gain_pct_param = request.args.get('gain_pct')
        try:
            loss_pct = float(loss_pct_param) if loss_pct_param is not None else 12.0
            gain_pct = float(gain_pct_param) if gain_pct_param is not None else None
        except ValueError:
            return jsonify({'error': f"Invalid loss_pct/gain_pct value: {loss_pct_param!r}/{gain_pct_param!r}"}), 400

        alerts = _compute_watchlist_alerts(symbols, loss_pct, gain_pct)
        return jsonify({'loss_pct': loss_pct, 'gain_pct': gain_pct, 'alerts': alerts})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _build_chart_html(series, span, start_date, end_date, pct_mode=False):
    """Build a standalone HTML page with a Chart.js multi-line chart, one line per
    symbol in `series` ({symbol: [(date, close), ...]}). Symbols with no rows in
    range are still listed (empty dataset) so the caller can see they had no data,
    rather than being silently dropped.

    When pct_mode is True each series is rebased to percent change from its own
    first in-range value, so series at very different price levels (e.g. a $1 money
    market fund vs a $400 equity) are directly comparable on one axis."""
    all_dates = sorted({d for rows in series.values() for d, _ in rows})

    datasets_js = []
    palette = ['#4CAF50', '#2196F3', '#FF9800', '#9C27B0', '#F44336', '#00BCD4', '#795548', '#607D8B']
    for i, (sym, rows) in enumerate(series.items()):
        close_by_date = {d: c for d, c in rows}
        data_points = [close_by_date.get(d) for d in all_dates]
        if pct_mode:
            baseline = next((v for v in data_points if v is not None), None)
            if baseline:  # non-None and non-zero
                data_points = [None if v is None else (v / baseline - 1.0) * 100.0
                               for v in data_points]
            else:
                data_points = [None for _ in data_points]
            js_data = [None if v is None else round(v, 3) for v in data_points]
        else:
            js_data = [None if v is None else round(v, 4) for v in data_points]
        color = palette[i % len(palette)]
        # json.dumps so gaps serialize as `null` (not Python `None`, which is
        # invalid JS and silently kills the whole chart) and labels are quoted.
        datasets_js.append(
            "{ label: %s, data: %s, borderColor: '%s', backgroundColor: '%s', fill: false, spanGaps: true }"
            % (json.dumps(sym), json.dumps(js_data), color, color)
        )

    no_data_symbols = [sym for sym, rows in series.items() if not rows]

    html_parts = []
    html_parts.append('<!DOCTYPE html>\n<html>\n<head>\n')
    html_parts.append('<title>Stock Chart</title>\n')
    html_parts.append('<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>\n')
    html_parts.append('<script>\n')
    html_parts.append('Chart.defaults.plugins.tooltip.backgroundColor = "#333";\n')
    html_parts.append('Chart.defaults.plugins.tooltip.titleColor = "#fff";\n')
    html_parts.append('Chart.defaults.plugins.tooltip.bodyColor = "#fff";\n')
    html_parts.append('Chart.defaults.plugins.tooltip.borderWidth = 1;\n')
    html_parts.append('Chart.defaults.plugins.tooltip.padding = 10;\n')
    html_parts.append('Chart.defaults.plugins.tooltip.callbacks.labelColor = function(ctx) {\n')
    html_parts.append('  const c = ctx.dataset.borderColor;\n')
    html_parts.append('  return { borderColor: c, backgroundColor: c };\n')
    html_parts.append('};\n')
    html_parts.append('</script>\n')
    html_parts.append('<style>\n')
    html_parts.append('body { font-family: Arial, sans-serif; margin: 20px; }\n')
    html_parts.append('#chartContainer { max-width: 1100px; margin: auto; }\n')
    html_parts.append('</style>\n</head>\n<body>\n')
    mode_label = '% change' if pct_mode else 'prices'
    html_parts.append(f'<h1>Stock Chart ({span}: {start_date} to {end_date}) &mdash; {mode_label}</h1>\n')
    if no_data_symbols:
        html_parts.append(f'<p><b>No data in range for:</b> {", ".join(no_data_symbols)}</p>\n')
    html_parts.append('<div id="chartContainer"><canvas id="stockChart"></canvas></div>\n')
    html_parts.append('<script>\n')
    html_parts.append('const labels = %s;\n' % json.dumps(all_dates))
    html_parts.append('const datasets = [%s];\n' % ', '.join(datasets_js))
    html_parts.append('new Chart(document.getElementById("stockChart"), {\n')
    html_parts.append('  type: "line",\n')
    html_parts.append('  data: { labels: labels, datasets: datasets },\n')
    html_parts.append('  options: {\n')
    html_parts.append('    responsive: true,\n')
    html_parts.append('    interaction: { mode: "index", intersect: false },\n')
    if pct_mode:
        html_parts.append('    plugins: { tooltip: { callbacks: {\n')
        html_parts.append('      label: function(ctx) { return ctx.dataset.label + ": " + (ctx.parsed.y == null ? "-" : ctx.parsed.y.toFixed(2) + "%"); }\n')
        html_parts.append('    } } },\n')
        html_parts.append('    scales: { x: { ticks: { maxTicksLimit: 12 } },\n')
        html_parts.append('      y: { title: { display: true, text: "% change from start" },\n')
        html_parts.append('           ticks: { callback: function(v) { return v + "%"; } } } }\n')
    else:
        html_parts.append('    scales: { x: { ticks: { maxTicksLimit: 12 } }, y: { title: { display: true, text: "Close" } } }\n')
    html_parts.append('  }\n')
    html_parts.append('});\n')
    html_parts.append('</script>\n')
    html_parts.append('</body>\n</html>')
    return ''.join(html_parts)


@app.route('/chart')
@app.route('/chart/<symbols>')
def chart_endpoint(symbols=None):
    """Multi-symbol line chart (Chart.js, client-side rendering) over a configurable
    span. URL form: /chart/AAPL,MSFT,VOO?span=1yr ; query-only form: /chart?symbols=...&span=...
    span: one of 1mo, 3mo, 6mo, 1yr, ytd, 5yr (default: 6mo).
    symbols: comma-separated list (default: all tracked symbols).

    With more than one symbol the chart shows percent change from each series' first
    in-range value by default, so items at very different price levels are comparable.
    Append &prices to chart raw prices instead (synthetic price for a money market
    fund). A single symbol defaults to prices; append &comparePct to force percent."""
    try:
        symbols_param = symbols or request.args.get('symbols')
        if symbols_param:
            sym_list = [s.strip().upper() for s in symbols_param.split(',') if s.strip()]
        else:
            sym_list = get_tracked_symbols()

        span = request.args.get('span', '6mo')
        date_range = _resolve_chart_span(span)
        if date_range is None:
            return jsonify({'error': f"Invalid span '{span}'. Valid: {', '.join(_CHART_SPANS.keys())}"}), 400
        start_date, end_date = date_range

        if not sym_list:
            return jsonify({'error': 'No symbols specified and no tracked symbols found'}), 400

        # Mode: percent-change is the default for multi-symbol comparisons; &prices
        # forces raw prices; &comparePct/&pct forces percent (e.g. for one symbol).
        prices_req = ('prices' in request.args) or \
            str(request.args.get('prices', '')).lower() in ('1', 'true', 'yes', 'on')
        pct_req = ('comparePct' in request.args) or \
            str(request.args.get('pct', '')).lower() in ('1', 'true', 'yes', 'on') or \
            request.args.get('compare', '').lower() == 'pct'
        if prices_req:
            pct_mode = False
        elif pct_req:
            pct_mode = True
        else:
            pct_mode = len(sym_list) > 1

        series = {sym: get_closes_in_range(sym, start_date, end_date) for sym in sym_list}

        return _build_chart_html(series, span, start_date, end_date, pct_mode)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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

@app.route('/yield/<symbol>')
def get_yield_endpoint(symbol):
    """Get most recent annualized yield for a money market fund"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT yield_annual, metric_date, yield_source
            FROM fund_metrics
            WHERE symbol = ?
            ORDER BY metric_date DESC
            LIMIT 1
        ''', (symbol.upper(),))
        row = cursor.fetchone()
        conn.close()
        if row and row['yield_annual'] is not None:
            return fmt_yield(row['yield_annual'])
        return jsonify({'error': f'No yield data for {symbol.upper()}'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/fund_type/<symbol>')
def get_fund_type_endpoint(symbol):
    """Asset type for one symbol (JSON), or a CSV of types for a comma-separated list.
    Batch form (e.g. /fund_type/AOA,QQQ) mirrors /quote; missing symbol -> #N/A."""
    if ',' in symbol:
        parts = [s.strip().upper() for s in symbol.split(',') if s.strip()]
        conn = get_db_connection()
        cursor = conn.cursor()
        out = []
        for sym in parts:
            cursor.execute('SELECT symbol_type FROM symbols WHERE symbol = ?', (sym,))
            r = cursor.fetchone()
            out.append((r['symbol_type'] or 'EQUITY') if r else '#N/A')
        conn.close()
        return ','.join(out)
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT symbol_type, fund_family FROM symbols WHERE symbol = ?', (symbol.upper(),))
        row = cursor.fetchone()
        conn.close()
        if row:
            return jsonify({
                'symbol': symbol.upper(),
                'symbol_type': row['symbol_type'] or 'EQUITY',
                'fund_family': row['fund_family']
            })
        return jsonify({'error': f'Symbol {symbol.upper()} not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def get_cache_html():
    """Build an HTML page showing the current contents of live_quote_cache."""
    with cache_lock:
        snapshot = dict(live_quote_cache)

    html_parts = []
    html_parts.append('<!DOCTYPE html>\n<html>\n<head>\n')
    html_parts.append('<title>Live Quote Cache</title>\n')
    html_parts.append('<style>\n')
    html_parts.append('body { font-family: Arial, sans-serif; margin: 20px; }\n')
    html_parts.append('table { border-collapse: collapse; width: 100%; }\n')
    html_parts.append('th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }\n')
    html_parts.append('th { background-color: #4CAF50; color: white; }\n')
    html_parts.append('tr:nth-child(even) { background-color: #f2f2f2; }\n')
    html_parts.append('</style>\n</head>\n<body>\n')

    if not snapshot:
        html_parts.append('<h1>Live Quote Cache</h1>\n')
        html_parts.append('<p>Cache is empty — no live quotes have been fetched yet.</p>\n')
        html_parts.append('</body>\n</html>')
        return ''.join(html_parts)

    now = time.time()
    html_parts.append(f'<h1>Live Quote Cache ({len(snapshot)} symbols)</h1>\n')
    html_parts.append(f'<p>Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>\n')
    html_parts.append('<table>\n<thead><tr>\n')
    html_parts.append('<th>Symbol</th><th>Price</th><th>Cached At</th><th>Age (sec)</th>\n')
    html_parts.append('</tr></thead>\n<tbody>\n')

    for symbol in sorted(snapshot):
        price, ts = snapshot[symbol]
        cached_at = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
        age = int(now - ts)
        html_parts.append('<tr>\n')
        html_parts.append(f'<td><strong>{symbol}</strong></td>\n')
        html_parts.append(f'<td>{price}</td>\n')
        html_parts.append(f'<td>{cached_at}</td>\n')
        html_parts.append(f'<td>{age}s</td>\n')
        html_parts.append('</tr>\n')

    html_parts.append('</tbody>\n</table>\n</body>\n</html>')
    return ''.join(html_parts)


def get_stats_html():
    """Build an HTML page showing database statistics."""
    stats = get_statistics()

    html_parts = []
    html_parts.append('<!DOCTYPE html>\n<html>\n<head>\n')
    html_parts.append('<title>Stock System Statistics</title>\n')
    html_parts.append('<style>\n')
    html_parts.append('body { font-family: Arial, sans-serif; margin: 20px; }\n')
    html_parts.append('table { border-collapse: collapse; width: 100%; }\n')
    html_parts.append('th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }\n')
    html_parts.append('th { background-color: #4CAF50; color: white; }\n')
    html_parts.append('tr:nth-child(even) { background-color: #f2f2f2; }\n')
    html_parts.append('td.num { text-align: right; }\n')
    html_parts.append('</style>\n</head>\n<body>\n')
    html_parts.append('<h1>Database Statistics</h1>\n')
    html_parts.append(f'<p>Database: <code>{stats["db_path"]}</code></p>\n')
    html_parts.append(f'<p>Total quotes: <strong>{stats["total_quotes"]:,}</strong> &nbsp;|&nbsp; ')
    html_parts.append(f'Market closures recorded: <strong>{stats["closure_count"]}</strong></p>\n')
    html_parts.append('<table>\n<thead><tr>\n')
    html_parts.append('<th>Symbol</th><th>Type</th><th>Name</th><th class="num">Quotes</th><th>First Date</th><th>Last Date</th>\n')
    html_parts.append('</tr></thead>\n<tbody>\n')

    for row in stats['symbols']:
        html_parts.append('<tr>\n')
        html_parts.append(f'<td><strong>{row["symbol"]}</strong></td>\n')
        html_parts.append(f'<td>{row["symbol_type"]}</td>\n')
        html_parts.append(f'<td>{row["name"]}</td>\n')
        html_parts.append(f'<td class="num">{row["quotes"]:,}</td>\n')
        html_parts.append(f'<td>{row["first_date"]}</td>\n')
        html_parts.append(f'<td>{row["last_date"]}</td>\n')
        html_parts.append('</tr>\n')

    html_parts.append('</tbody>\n</table>\n</body>\n</html>')
    return ''.join(html_parts)


@app.route('/stats')
def view_stats():
    """Database statistics as HTML"""
    try:
        return get_stats_html()
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/cache')
@app.route('/cache/<fmt>')
def show_cache(fmt='html'):
    """Live quote cache contents as HTML"""
    try:
        return get_cache_html()
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/')
def home():
    """Show API documentation"""
    return f"<h1>Stock Quote Server v{__version__} (pid={os.getpid()})</h1>"+"""
    <h2>API Endpoints:</h2>
    <ul>
        <li><code>/quote/&lt;symbol&gt;[,&lt;symbol&gt;...]</code> - Latest close (synthetic price for MMFs). Comma list -> CSV, missing -> #N/A</li>
        <li><code>/quote/&lt;symbol&gt;[,&lt;symbol&gt;...]/now</code> - Live quote, 15min cache. Comma list -> CSV, missing -> #N/A</li>
        <li><code>/quote/&lt;symbol&gt;[,&lt;symbol&gt;...]/&lt;date&gt;</code> - Close on a date (YYYY-MM-DD). Comma list -> CSV, missing -> #N/A</li>
        <li><code>/quote/&lt;symbol&gt;[,&lt;symbol&gt;...]/field/&lt;field&gt;</code> - Latest value for field. OHLCV: open, high, low, close, volume. Fundamentals: pe, pe_forward, xr (expense ratio), divyield. Comma list -> CSV, missing -> #N/A</li>
        <li><code>/yield/&lt;symbol&gt;</code> - Latest annualized yield (bare value, e.g. 0.0363)</li>
        <li><code>/fund_type/&lt;symbol&gt;[,&lt;symbol&gt;...]</code> - Asset type (EQUITY, ETF, MUTUALFUND, MONEY_MARKET, …). Comma list -> CSV, missing -> #N/A</li>
        <li><code>/name/&lt;symbol&gt;[,&lt;symbol&gt;...]</code> - Name(s) -> CSV; commas in a name become _, missing -> #N/A</li>
        <li><a href="/funds"><code>/funds</code></a> - List money market (synthetic) funds</li>
        <li><code>/export/&lt;symbol&gt;</code> - Export all available quotes includes (open, high, low, close, volume)</li>
        <li><code>/update/&lt;symbol&gt;</code> - Force-refresh one symbol (repairs missing OHLC)</li>
        <li><code>/latest_date/&lt;symbol&gt;</code> - Most recent date with data</li>
        <li><a href="/closures"><code>/closures</code></a> - List all detected market closure dates</li>
        <li><a href="/config"><code>/config</code></a> - View configuration (read-only)</li>
        <li><a href="/health"><code>/health</code></a> - Server health check</li>
        <li><a href="/version"><code>/version</code></a> - Version and changelog</li>
        <li><a href="/stats"><code>/stats</code></a> - Database statistics (HTML)</li>
        <li><a href="/cache/html"><code>/cache/html</code></a> - Live quote cache contents</li>
        <li><code>/watchlist?symbols=&lt;csv&gt;&amp;loss_pct=&lt;float&gt;&amp;gain_pct=&lt;float&gt;</code> - Alert on symbols down &gt;loss_pct% (default 12%) or, if gain_pct given, up &gt;gain_pct%, vs 1wk/1mo/YTD (default: all tracked)</li>
        <li><code>/chart/&lt;symbol&gt;[,&lt;symbol&gt;...]?span=&lt;span&gt;</code> - Multi-symbol line chart (Chart.js HTML). span: 1mo,3mo,6mo,1yr,ytd,5yr (default 6mo). Multiple symbols show % change by default (compares across price levels, e.g. a MM fund vs an equity); append <code>&amp;prices</code> for raw prices, <code>&amp;comparePct</code> to force % for one symbol</li>
        <li><code>/shutdown</code> - Stop the server</li>
    </ul>
    <h2>Examples:</h2>
    <ul>
        <li><a href="/quote/AAPL">/quote/AAPL</a></li>
        <li><a href="/quote/CSCO,QQQ,AAPL">/quote/CSCO,QQQ,AAPL</a> (batch CSV)</li>
        <li><a href="/quote/AAPL/now">/quote/AAPL/now</a></li>
        <li><a href="/quote/CSCO,QQQ,AAPL/now">/quote/CSCO,QQQ,AAPL/now</a> (batch CSV)</li>
        <li><a href="/quote/CSCO/2025-01-15">/quote/CSCO/2025-01-15</a></li>
        <li><a href="/quote/CSCO,QQQ,AAPL/2025-01-15">/quote/CSCO,QQQ,AAPL/2025-01-15</a> (batch CSV)</li>
        <li><a href="/quote/AAPL/field/high">/quote/AAPL/field/high</a></li>
        <li><a href="/quote/AAPL/field/pe">/quote/AAPL/field/pe</a> (trailing P/E)</li>
        <li><a href="/quote/AAPL,MSFT,VOO/field/divyield">/quote/AAPL,MSFT,VOO/field/divyield</a> (batch CSV)</li>
        <li><a href="/export/QQQ">/export/QQQ</a> <B>Warning: Large amount of data</B> </li>
        <li><a href="/latest_date/AAPL">/latest_date/AAPL</a></li>
        <li><a href="/watchlist">/watchlist</a> (all tracked symbols, default 12% threshold)</li>
        <li><a href="/chart/AAPL,MSFT,VOO?span=1yr">/chart/AAPL,MSFT,VOO?span=1yr</a></li>
    </ul>
    <h2>Money Market Fund CLI:</h2>
    <ul>
        <li><code>python stock_system.py --add-fund FDLXX</code> - Add MMF and seed full yield history</li>
        <li><code>python stock_system.py --list-funds</code> - List money market funds and synthetic coverage</li>
        <li><code>python stock_system.py --rebuild-fund [SYMBOL]</code> - Rebuild synthetic prices from stored metrics and verify</li>
        <li><code>python stock_system.py --verify-fund [SYMBOL]</code> - Verify synthetic price accuracy</li>
        <li><code>python stock_system.py --reclassify</code> - Reclassify all tracked symbols via yfinance</li>
        <li><code>python stock_system.py --watchlist [SYMBOLS] --loss-pct 12 --gain-pct 10</code> - Report symbols down &gt;loss-pct% or up &gt;gain-pct% vs 1wk/1mo/YTD</li>
        <li><code>python stock_system.py --version</code> - Show version and latest changelog entry</li>
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

@app.route('/version')
def version_endpoint():
    """Version and full changelog as JSON."""
    return jsonify({'version': __version__, 'changelog': CHANGELOG})

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
                'api_key': item['api_key'] if item['api_key'] else 'NOT SET',  
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
    """Latest close for one symbol, or a CSV of latest closes for a comma-separated list.
    Batch form (e.g. /quote/CSCO,TSLA,AAPL) does no auto-fetch; missing symbol -> #N/A."""
    if ',' in symbol:
        parts = [s.strip().upper() for s in symbol.split(',') if s.strip()]
        out = []
        for sym in parts:
            v = _latest_close_value(sym)
            out.append(fmt_price(v) if v is not None else '#N/A')
        return ','.join(out)
    return get_quote_helper(symbol.upper(), field='close')


@app.route('/closures')
def list_closures():
    """List market closures"""
    return jsonify(get_market_closures())


@app.route('/quote/<symbol>/now')
def get_live_quote_endpoint(symbol):
    """Live quote (cached) for one symbol, or a CSV of live quotes for a comma-separated
    list. Batch form does no auto-fetch (it's the perf path); missing symbol -> #N/A."""
    try:
        if ',' in symbol:
            parts = [s.strip().upper() for s in symbol.split(',') if s.strip()]
            out = []
            for sym in parts:
                price = get_live_quote(sym)
                out.append(fmt_price(price) if price is not None else '#N/A')
            return ','.join(out)

        price = get_live_quote(symbol.upper())

        if price is not None:
            return fmt_price(price)
        else:
            # Try auto-fetch if not in database
            if auto_fetch_symbol(symbol.upper()):
                price = get_live_quote(symbol.upper())
                if price:
                    return fmt_price(price)

            return jsonify({'error': f'No data for {symbol.upper()}'}), 404

    except Exception as e:
        logger.error(f"Error getting live quote for {symbol}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/quote/<symbol>/<date>')
def get_quote_by_date_endpoint(symbol, date):
    """Close on a date for one symbol, or a CSV of closes for a comma-separated list.
    Falls back to the nearest prior date per symbol. Batch form does no auto-fetch;
    missing symbol -> #N/A."""
    if ',' in symbol:
        parts = [s.strip().upper() for s in symbol.split(',') if s.strip()]
        out = []
        for sym in parts:
            v = _close_value_on_date(sym, date)
            out.append(fmt_price(v) if v is not None else '#N/A')
        return ','.join(out)
    return get_quote_helper(symbol.upper(), date=date, field='close')

# Fundamental field name -> symbol_fundamentals column.
FUNDAMENTAL_FIELDS = {
    'pe': 'pe_trailing',
    'pe_forward': 'pe_forward',
    'xr': 'expense_ratio',
    'divyield': 'dividend_yield',
}

def _fundamental_value(symbol, column):
    """Return the latest stored fundamental value for a symbol, or None."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT {column} AS val
        FROM symbol_fundamentals
        WHERE symbol = ?
        ORDER BY snapshot_date DESC
        LIMIT 1
    ''', (symbol.upper(),))
    row = cursor.fetchone()
    conn.close()
    return row['val'] if row and row['val'] is not None else None

@app.route('/quote/<symbol>/field/<field>')
def get_quote_by_field_endpoint(symbol, field):
    """Latest value for a specific field. OHLCV fields come from daily quotes;
    fundamental fields (pe, pe_forward, xr, divyield) come from symbol_fundamentals.
    A comma-separated symbol list returns a CSV (missing -> #N/A), mirroring /quote."""
    field = field.lower()
    ohlcv_fields = ['open', 'high', 'low', 'close', 'volume']

    if field in FUNDAMENTAL_FIELDS:
        column = FUNDAMENTAL_FIELDS[field]
        fmt = fmt_yield if field in ('xr', 'divyield') else fmt_price
        if ',' in symbol:
            parts = [s.strip().upper() for s in symbol.split(',') if s.strip()]
            out = []
            for sym in parts:
                v = _fundamental_value(sym, column)
                out.append(fmt(v) if v is not None else '#N/A')
            return ','.join(out)
        v = _fundamental_value(symbol.upper(), column)
        if v is not None:
            return fmt(v)
        return jsonify({'error': f'No {field} data for {symbol.upper()}'}), 404

    if field not in ohlcv_fields:
        valid = ohlcv_fields + list(FUNDAMENTAL_FIELDS.keys())
        return jsonify({'error': f'Invalid field. Must be one of: {", ".join(valid)}'}), 400

    if ',' in symbol:
        parts = [s.strip().upper() for s in symbol.split(',') if s.strip()]
        out = []
        for sym in parts:
            v = _latest_field_value(sym, field)
            out.append(fmt_price(v) if v is not None else '#N/A')
        return ','.join(out)
    return get_quote_helper(symbol.upper(), field=field)

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

@app.route('/update/<symbol>')
def update_symbol_endpoint(symbol):
    """Force-refresh a single symbol (repairs null-OHLC rows). Returns JSON status."""
    try:
        updated = force_update_symbol(symbol.upper())
        return jsonify({'symbol': symbol.upper(), 'updated': bool(updated)})
    except Exception as e:
        logger.error(f"Error force-updating {symbol}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/name/<symbols>')
def get_name_endpoint(symbols):
    """CSV of names for one or more comma-separated symbols. Commas inside a name are
    replaced with '_' so the CSV stays parseable; missing/unnamed -> #N/A."""
    parts = [s.strip().upper() for s in symbols.split(',') if s.strip()]
    conn = get_db_connection()
    cursor = conn.cursor()
    out = []
    for sym in parts:
        cursor.execute('SELECT name FROM symbols WHERE symbol = ?', (sym,))
        r = cursor.fetchone()
        if r and r['name']:
            out.append(r['name'].replace(',', '_'))
        else:
            out.append('#N/A')
    conn.close()
    return ','.join(out)

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
            return fmt_price(result[field])
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
                    return fmt_price(result[field])

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

def acquire_server_lock(secondary=False):
    """
    Acquire exclusive server lock to prevent multiple instances of the same kind
    (primary or secondary each get their own lock file, so one of each can run
    at once). Returns True if lock acquired, False if another instance of the
    same kind is running.
    """
    lock_file = LOCK_FILE_SECONDARY if secondary else LOCK_FILE
    stop_flag = ' --secondary' if secondary else ''
    try:
        # Check if lock file exists
        if os.path.exists(lock_file):
            # Try to read PID from lock file
            with open(lock_file, 'r') as f:
                pid = int(f.read().strip())

            # Check if process is still running (Windows-compatible)
            if is_process_running(pid):
                # Process exists
                logger.warning(f"Server already running (PID: {pid})")
                print(f"Error: Server is already running (PID: {pid})")
                print(f"Use 'python stock_system.py --stopserver{stop_flag}' to stop it")
                return False
            else:
                # Process doesn't exist, stale lock file
                logger.info("Removing stale lock file")
                os.remove(lock_file)

        # Write current PID to lock file
        with open(lock_file, 'w') as f:
            f.write(str(os.getpid()))

        # Register cleanup on exit
        atexit.register(release_server_lock, secondary)

        logger.info(f"Server lock acquired (PID: {os.getpid()}, secondary={secondary})")
        return True

    except Exception as e:
        logger.error(f"Error acquiring server lock: {e}")
        return False

def release_server_lock(secondary=False):
    """Release server lock on exit"""
    lock_file = LOCK_FILE_SECONDARY if secondary else LOCK_FILE
    try:
        if os.path.exists(lock_file):
            os.remove(lock_file)
            logger.info("Server lock released")
    except Exception as e:
        logger.error(f"Error releasing lock: {e}")

def save_server_port(port, secondary=False):
    """Save server port for stop command"""
    port_file = SERVER_PORT_FILE_SECONDARY if secondary else SERVER_PORT_FILE
    try:
        with open(port_file, 'w') as f:
            f.write(f"{port}\n{os.getpid()}")
    except Exception as e:
        logger.error(f"Error saving server port: {e}")

def get_server_info(secondary=False):
    """Get running server port and PID"""
    port_file = SERVER_PORT_FILE_SECONDARY if secondary else SERVER_PORT_FILE
    try:
        if os.path.exists(port_file):
            with open(port_file, 'r') as f:
                lines = f.read().strip().split('\n')
                if len(lines) >= 2:
                    return int(lines[0]), int(lines[1])
        return None, None
    except Exception as e:
        logger.error(f"Error reading server info: {e}")
        return None, None

def stop_server(secondary=False):
    """Stop running server instance (cross-platform)"""
    import platform
    import subprocess

    lock_file = LOCK_FILE_SECONDARY if secondary else LOCK_FILE
    port_file = SERVER_PORT_FILE_SECONDARY if secondary else SERVER_PORT_FILE
    port, pid = get_server_info(secondary)

    if not pid:
        print(f"No {'secondary' if secondary else 'primary'} server is currently running")
        logger.info("Stop requested but no server running")
        return

    # Check if process exists
    if not is_process_running(pid):
        print("Server process not found (stale PID file)")
        # Clean up stale files
        if os.path.exists(lock_file):
            os.remove(lock_file)
        if os.path.exists(port_file):
            os.remove(port_file)
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
        if os.path.exists(lock_file):
            os.remove(lock_file)
        if os.path.exists(port_file):
            os.remove(port_file)

        print("Server stopped")
        logger.info("Server stopped successfully")
    
    except Exception as e:
        print(f"Error stopping server: {e}")
        logger.error(f"Error stopping server: {e}")

def export_quotes_to_csv(output_filename = 'stock_system_export.csv', symbol=None):
    """
    Export daily quotes to CSV file.
    
    Args:
        output_filename: Path to output CSV file
        symbol: Stock symbol to export (None = all symbols)
        db_path: Path to SQLite database
        
    Returns:
        Number of records exported, or None on error
    """

    logger.info(f"Exporting quotes for symbol '{symbol}' to file '{output_filename}'")


    # Get data from database
    column_names, rows = bulk_get_quotes(symbol, DB_PATH)
    
    if column_names is None:
        logger.error("Failed to retrieve data from database")
        return None
    
    if not rows:
        logger.warning(f"No data to export{' for ' + symbol if symbol else ''}")
        return 0
    
    try:
        # Write to CSV
        with open(output_filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            
            # Write header
            writer.writerow(column_names)
            
            # Write data rows
            writer.writerows(rows)
        
        logger.info(f"Successfully exported {len(rows)} quotes to {output_filename}")
        return len(rows)
        
    except IOError as e:
        logger.error(f"File error during export: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error during export: {e}")
        return None



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
    parser.add_argument('--port', type=int, metavar='PORT',
                        help='Port to bind for --server (default: 5000, or 5050 with --secondary)')
    parser.add_argument('--secondary', action='store_true',
                        help='Run/stop a second, independent instance (its own lock/port files, '
                             'default port 5050, no auto-update scheduler) so it can run alongside '
                             'the primary on port 5000. Use with --server or --shutdown.')

    # Symbol management
    parser.add_argument('--init', action='store_true', help='Initialize database')
    parser.add_argument('--add', action='append', help='Add symbol(s) - can use multiple times')
    parser.add_argument('--remove', nargs='?', const='__CLEAN__', metavar='SYMBOL',
                        help='Remove a symbol and its data. Without SYMBOL: find and remove all symbols with 0 quotes.')
    parser.add_argument('--export', '--exportcsv', action='store_true', help='Export to CSV')
    parser.add_argument('--filename', type=str, default='stock_system_quotes.csv', help='CSV output filename (default: stock_system_quotes.csv)')
    parser.add_argument('--symbol', type=str, help='Symbol to export eg "QQQ" (default: all symbols)')    


    # Updates
    parser.add_argument('--update', nargs='?', const='__ALL__', metavar='SYMBOL',
                        help='Update all symbols, or force-refresh one symbol (e.g. --update AOA)')
    parser.add_argument('--update-fundamentals', nargs='?', const='__ALL__', metavar='SYMBOLS',
                        help='Fetch P/E, dividend yield, expense ratio from yfinance. No value = '
                             'all tracked symbols; one symbol or a comma-separated list '
                             '(e.g. --update-fundamentals AAPL,MSFT,VOO)')
    parser.add_argument('--years', type=int, help='Years of history to fetch (for new symbols)')
    parser.add_argument('--since', type=str, metavar='DATE',
                        help='Absolute start date for fetch (YYYY-MM-DD). Overrides --years. '
                             'Use with --update to backfill gaps (e.g. --update USFR --since 2023-01-01)')

    # Money market fund management
    parser.add_argument('--add-fund', type=str, metavar='SYMBOL', help='Add a money market fund and seed full yield history from FRED')
    parser.add_argument('--expense-ratio', type=float, metavar='FRACTION',
                        help='Override the fund expense ratio for --add-fund (annual fraction, '
                             'e.g. 0.0011 for 0.11%%); FRED yield is stored net of this')
    parser.add_argument('--reclassify', action='store_true', help='Reclassify all tracked symbols via yfinance (run once after migration)')
    parser.add_argument('--list-funds', action='store_true', help='List money market (synthetic) funds and their synthetic-row coverage')
    parser.add_argument('--rebuild-fund', nargs='?', const='__ALL__', metavar='SYMBOL', help='Rebuild synthetic prices from stored FRED metrics and verify (no SYMBOL = all funds)')
    parser.add_argument('--verify-fund', nargs='?', const='__ALL__', metavar='SYMBOL', help='Verify stored synthetic prices against recomputed series (no SYMBOL = all funds)')
    
    # Configuration
    parser.add_argument('--config', nargs='+', help='Config operations: list, get KEY, set KEY VALUE, set apikey SOURCE KEY')
    
    # Info
    parser.add_argument('--stats', '--statistics', action='store_true', help='Show database statistics')
    parser.add_argument('--dbpath', action='store_true', help='Show database path')
    parser.add_argument('--closures', action='store_true', help='List all market closure dates')
    parser.add_argument('--import-closures', nargs='?', const='__BUILTIN__', metavar='FILE',
                        help='Import market closures from CSV file (DATE[,REASON] per line, # for comments). Without FILE: seed from built-in MARKET_CLOSURES list.')
    parser.add_argument('--version', action='store_true', help='Show version and latest changelog entry')

    # Watch list
    parser.add_argument('--watchlist', nargs='?', const='__ALL__', metavar='SYMBOLS',
                        help='Check watch list for drops (and gains, with --gain-pct) vs 1wk/1mo/YTD '
                             'baselines. No value = all tracked symbols; one symbol or comma-separated '
                             'list (e.g. --watchlist AAPL,MSFT). Use with --loss-pct/--gain-pct.')
    parser.add_argument('--loss-pct', type=float, default=12.0,
                        help='Loss percent threshold for --watchlist (default: 12.0)')
    parser.add_argument('--gain-pct', type=float, default=None,
                        help='Gain percent threshold for --watchlist (default: none, upside not checked)')

    args = parser.parse_args()

    # Set database path if provided
    if args.db:
        DB_PATH = args.db

    if args.secondary:
        use_secondary_log_file()

    # Handle --stopserver FIRST (before other commands)
    if args.shutdown:
        stop_server(args.secondary)
        return

    if args.version:
        print(f"stock_system.py version {__version__}")
        latest = CHANGELOG[0]
        print(f"\nLatest changes ({latest['version']}, {latest['date']}):")
        for change in latest['changes']:
            print(f"  - {change}")
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
            added = add_symbol(symbol)
            if added and args.since:
                print(f"  Seeding history from {args.since}...")
                smart_update_symbol(symbol.upper(), since=args.since)
        return
    
    if args.remove:
        if args.remove == '__CLEAN__':
            clean_orphan_symbols()
        else:
            remove_symbol(args.remove)
        return

    if args.update:
        if args.update == '__ALL__':
            update_all_symbols(args.years, since=args.since)
        else:
            force_update_symbol(args.update.upper(), args.years, since=args.since)
        return

    if args.update_fundamentals:
        if args.update_fundamentals == '__ALL__':
            update_fundamentals(None)
        else:
            syms = [s.strip().upper() for s in args.update_fundamentals.split(',') if s.strip()]
            update_fundamentals(syms)
        return

    if args.add_fund:
        add_money_market_fund(args.add_fund.upper(), years=args.years or 3,
                              expense_ratio=args.expense_ratio)
        return

    if args.reclassify:
        reclassify_all_symbols()
        return

    if args.list_funds:
        show_money_market_funds()
        return

    if args.rebuild_fund:
        if args.rebuild_fund == '__ALL__':
            rebuild_and_verify_all_funds()
        else:
            rebuild_and_verify_fund(args.rebuild_fund)
        return

    if args.verify_fund:
        if args.verify_fund == '__ALL__':
            verify_all_funds()
        else:
            _print_verify_report(verify_synthetic_prices(args.verify_fund))
        return

    if args.watchlist:
        if args.watchlist == '__ALL__':
            syms = get_tracked_symbols()
        else:
            syms = [s.strip().upper() for s in args.watchlist.split(',') if s.strip()]
        alerts = _compute_watchlist_alerts(syms, args.loss_pct, args.gain_pct)
        _print_watchlist_report(alerts, args.loss_pct, args.gain_pct)
        return

    if args.export:
        # We expect up to two arguments: filename, and symbol.
        export_quotes_to_csv(args.filename, args.symbol)
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
                if set_source_priority(args.config[2], args.config[3]):
                    print(f"Priority updated: {args.config[2]} = {args.config[3]}")
                else: 
                    print(f"Priority NOT updated: {args.config[2]} = {args.config[3]}")
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

    if args.import_closures:
        if args.import_closures == '__BUILTIN__':
            import_closures_from_builtin()
        else:
            import_closures_from_file(args.import_closures)
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

        secondary = args.secondary
        port = args.port or (5050 if secondary else 5000)
        stop_flag = ' --secondary' if secondary else ''

        # Acquire server lock (prevent multiple instances of the same kind)
        if not acquire_server_lock(secondary):
            return

        # Save server info for stop command
        save_server_port(port, secondary)

        # Secondary instances skip the auto-update scheduler: if it shares the
        # primary's database, running the same cron job twice would duplicate
        # writes and API calls against the same rows at the same time.
        if secondary:
            scheduler = None
            print("Secondary instance: auto-update scheduler disabled (avoids duplicate "
                  "scheduled writes if this shares the primary's database).")
        else:
            scheduler = start_scheduler()

        # Start server
        print("=" * 70)
        print(f"Stock Quote Server Starting{' (secondary)' if secondary else ''}")
        print("=" * 70)
        print(f"Database: {DB_PATH}")
        print(f"Server: http://localhost:{port}")
        print(f"PID: {os.getpid()}")
        print()
        print(f"Test: http://localhost:{port}/quote/AAPL")
        print(f'Use in Calc: =WEBSERVICE("http://localhost:{port}/quote/AAPL")')
        print()
        print(f"To stop: 'python stock_system.py --stopserver{stop_flag}' or 'stop_server.bat'")
        print("Or press Ctrl+C")
        print("=" * 70)

        try:
            app.run(host='127.0.0.1', port=port, debug=False)
        except KeyboardInterrupt:
            print("\nShutting down...")
            if scheduler:
                scheduler.shutdown()

        return
    
    # No arguments - show help
    parser.print_help()

if __name__ == '__main__':
    main()