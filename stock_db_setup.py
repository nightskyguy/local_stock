"""
Stock Quote Database Setup
Creates SQLite database with schema for storing stock quotes
"""
import sqlite3
from datetime import datetime, timedelta
import os

# Database configuration
DB_PATH = os.path.join(os.path.expanduser('~'), 'stock_quotes.db')

def create_database():
    """Create the database and tables if they don't exist"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Table for storing daily stock quotes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            quote_date DATE NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            dividends REAL,
            stock_splits REAL,
            data_source TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, quote_date)
        )
    ''')
    
    # Table for tracking symbols
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS symbols (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            first_fetch_date DATE,
            last_fetch_date DATE,
            active INTEGER DEFAULT 1,
            notes TEXT
        )
    ''')
    
    # Table for tracking data sources
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS data_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT UNIQUE NOT NULL,
            api_key TEXT,
            rate_limit INTEGER,
            enabled INTEGER DEFAULT 1,
            priority INTEGER DEFAULT 0,
            notes TEXT
        )
    ''')
    
    # Create indexes for faster queries
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_symbol_date 
        ON daily_quotes(symbol, quote_date DESC)
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_quote_date 
        ON daily_quotes(quote_date DESC)
    ''')
    
    conn.commit()
    conn.close()
    print(f"Database created at: {DB_PATH}")

def initialize_data_sources():
    """Initialize the default data sources"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    sources = [
        ('yfinance', None, 2000, 1, 1, 'Yahoo Finance via yfinance library - Free, no API key required'),
        ('alphavantage', None, 25, 1, 2, 'Alpha Vantage - Free tier: 25 requests/day'),
        ('fmp', None, 250, 0, 3, 'Financial Modeling Prep - Free tier: 250 requests/day'),
        ('finnhub', None, 60, 0, 4, 'Finnhub - Free tier: 60 calls/minute'),
    ]
    
    for source in sources:
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO data_sources 
                (source_name, api_key, rate_limit, enabled, priority, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', source)
        except sqlite3.IntegrityError:
            pass
    
    conn.commit()
    conn.close()
    print("Data sources initialized")

if __name__ == '__main__':
    create_database()
    initialize_data_sources()
    print(f"\nDatabase setup complete!")
    print(f"Database location: {DB_PATH}")
