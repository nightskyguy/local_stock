"""
LibreOffice Calc Functions for Stock Quotes
Install in LibreOffice: Tools > Macros > Organize Python Scripts > My Macros
"""
import sqlite3
from datetime import datetime, timedelta
import os
import sys

# Add path for our modules
sys.path.insert(0, os.path.expanduser('~'))

DB_PATH = os.path.join(os.path.expanduser('~'), 'stock_quotes.db')

def get_quote(symbol, date=None, field='close'):
    """
    Get a stock quote from the database
    
    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        date: Date in YYYY-MM-DD format (default: most recent)
        field: Which field to return (open, high, low, close, volume)
    
    Returns:
        The requested value or error message
    
    Usage in Calc:
        =PYUNO("get_quote", "AAPL")                    -> Latest close price for AAPL
        =PYUNO("get_quote", "AAPL", "2025-01-15")      -> Close price on specific date
        =PYUNO("get_quote", "AAPL", "", "high")        -> Latest high price
    """
    try:
        # Auto-fetch if data is missing
        _ensure_data_available(symbol)
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        if date:
            # Try exact date first
            cursor.execute(f'''
                SELECT {field}
                FROM daily_quotes
                WHERE symbol = ? AND quote_date = ?
            ''', (symbol.upper(), date))
            
            result = cursor.fetchone()
            
            # If no exact match, find nearest date before
            if not result or result[0] is None:
                cursor.execute(f'''
                    SELECT {field}
                    FROM daily_quotes
                    WHERE symbol = ? AND quote_date <= ?
                    ORDER BY quote_date DESC
                    LIMIT 1
                ''', (symbol.upper(), date))
                result = cursor.fetchone()
        else:
            # Get most recent
            cursor.execute(f'''
                SELECT {field}
                FROM daily_quotes
                WHERE symbol = ?
                ORDER BY quote_date DESC
                LIMIT 1
            ''', (symbol.upper(),))
            result = cursor.fetchone()
        
        conn.close()
        
        if result and result[0] is not None:
            return float(result[0])
        else:
            return f"#N/A: No data for {symbol}"
    
    except Exception as e:
        return f"#ERROR: {str(e)}"

def get_latest_date(symbol):
    """
    Get the most recent date we have data for a symbol
    
    Usage in Calc:
        =PYUNO("get_latest_date", "AAPL")
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT MAX(quote_date)
            FROM daily_quotes
            WHERE symbol = ?
        ''', (symbol.upper(),))
        
        result = cursor.fetchone()
        conn.close()
        
        return result[0] if result and result[0] else "No data"
    
    except Exception as e:
        return f"#ERROR: {str(e)}"

def get_quote_range(symbol, start_date, end_date, field='close'):
    """
    Get multiple quotes for a date range (returns array)
    
    Usage in Calc (as array formula):
        Select range, type formula, press Ctrl+Shift+Enter
        =PYUNO("get_quote_range", "AAPL", "2025-01-01", "2025-01-31", "close")
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(f'''
            SELECT quote_date, {field}
            FROM daily_quotes
            WHERE symbol = ?
            AND quote_date BETWEEN ? AND ?
            ORDER BY quote_date
        ''', (symbol.upper(), start_date, end_date))
        
        results = cursor.fetchall()
        conn.close()
        
        if results:
            # Return as tuple of tuples for Calc array formula
            return tuple(results)
        else:
            return ((f"No data for {symbol}",),)
    
    except Exception as e:
        return ((f"#ERROR: {str(e)}",),)

def _ensure_data_available(symbol):
    """
    Internal function: Fetch data if missing
    Checks if we have recent data, if not, triggers a fetch
    """
    try:
        from stock_fetcher import StockDataFetcher
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check if we have any data for this symbol
        cursor.execute('''
            SELECT MAX(quote_date)
            FROM daily_quotes
            WHERE symbol = ?
        ''', (symbol.upper(),))
        
        result = cursor.fetchone()
        latest_date = result[0] if result and result[0] else None
        
        conn.close()
        
        # If no data or data is old, fetch it
        needs_fetch = False
        if not latest_date:
            needs_fetch = True
            start_date = (datetime.now() - timedelta(days=3*365)).strftime('%Y-%m-%d')
        else:
            latest_dt = datetime.strptime(latest_date, '%Y-%m-%d')
            days_old = (datetime.now() - latest_dt).days
            if days_old > 7:  # Fetch if data is more than a week old
                needs_fetch = True
                start_date = latest_date
        
        if needs_fetch:
            print(f"Auto-fetching data for {symbol}...")
            fetcher = StockDataFetcher()
            quotes = fetcher.fetch_data(symbol, start_date)
            if quotes:
                fetcher.save_quotes(quotes)
                fetcher.update_symbol_tracking(symbol)
                print(f"Fetched {len(quotes)} quotes for {symbol}")
    
    except Exception as e:
        print(f"Auto-fetch error for {symbol}: {e}")
        pass  # Don't block the quote lookup on fetch errors

def refresh_symbol(symbol):
    """
    Force refresh data for a symbol (can be called from Calc)
    
    Usage in Calc:
        =PYUNO("refresh_symbol", "AAPL")
    """
    try:
        from stock_fetcher import StockDataFetcher
        
        fetcher = StockDataFetcher()
        start_date = (datetime.now() - timedelta(days=3*365)).strftime('%Y-%m-%d')
        
        quotes = fetcher.fetch_data(symbol.upper(), start_date)
        if quotes:
            saved = fetcher.save_quotes(quotes)
            fetcher.update_symbol_tracking(symbol.upper())
            return f"Updated {saved} quotes"
        else:
            return "Failed to fetch data"
    
    except Exception as e:
        return f"#ERROR: {str(e)}"

# Make functions available to LibreOffice
g_exportedScripts = (get_quote, get_latest_date, get_quote_range, refresh_symbol)