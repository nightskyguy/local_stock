"""
Daily Stock Update Script
Fetches missing historical data and updates current quotes for all tracked symbols
"""
import sqlite3
import os
import sys
from datetime import datetime, timedelta
from stock_fetcher import StockDataFetcher

DB_PATH = os.path.join(os.path.expanduser('~'), 'stock_quotes.db')

def get_tracked_symbols():
    """Get list of symbols to track"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT symbol FROM symbols WHERE active = 1')
    symbols = [row[0] for row in cursor.fetchall()]
    conn.close()
    return symbols

def add_symbol(symbol, name=None, notes=None):
    """Add a new symbol to track"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT OR IGNORE INTO symbols (symbol, name, notes, active)
            VALUES (?, ?, ?, 1)
        ''', (symbol.upper(), name, notes))
        conn.commit()
        print(f"Added symbol: {symbol.upper()}")
    except sqlite3.Error as e:
        print(f"Error adding symbol {symbol}: {e}")
    finally:
        conn.close()

def initialize_portfolio_symbols(symbols_list):
    """Initialize the database with a list of portfolio symbols"""
    for symbol in symbols_list:
        add_symbol(symbol)

def update_historical_data(symbol, years_back=3):
    """Fetch historical data for a symbol"""
    fetcher = StockDataFetcher()
    
    # Calculate start date (3 years back)
    start_date = (datetime.now() - timedelta(days=years_back*365)).strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')
    
    print(f"\nFetching historical data for {symbol} from {start_date} to {end_date}...")
    
    # Check what dates we already have
    missing_dates = fetcher.get_missing_dates(symbol, start_date, end_date)
    
    if not missing_dates:
        print(f"  {symbol}: All data up to date")
        return True
    
    print(f"  {symbol}: {len(missing_dates)} dates missing")
    
    # Fetch the data
    quotes = fetcher.fetch_data(symbol, start_date, end_date)
    
    if quotes:
        saved = fetcher.save_quotes(quotes)
        fetcher.update_symbol_tracking(symbol)
        print(f"  {symbol}: Saved {saved} quotes")
        return True
    else:
        print(f"  {symbol}: Failed to fetch data")
        return False

def update_all_symbols(years_back=3):
    """Update historical data for all tracked symbols"""
    symbols = get_tracked_symbols()
    
    if not symbols:
        print("No symbols to update. Add symbols first.")
        return
    
    print(f"Updating {len(symbols)} symbols...")
    
    success_count = 0
    for symbol in symbols:
        if update_historical_data(symbol, years_back):
            success_count += 1
    
    print(f"\nUpdate complete: {success_count}/{len(symbols)} symbols updated successfully")

def get_quote_count():
    """Get statistics on stored quotes"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            COUNT(*) as total_quotes,
            COUNT(DISTINCT symbol) as unique_symbols,
            MIN(quote_date) as earliest_date,
            MAX(quote_date) as latest_date
        FROM daily_quotes
    ''')
    
    stats = cursor.fetchone()
    
    print("\n=== Database Statistics ===")
    print(f"Total quotes: {stats[0]:,}")
    print(f"Unique symbols: {stats[1]}")
    print(f"Date range: {stats[2]} to {stats[3]}")
    
    # Per-symbol stats
    cursor.execute('''
        SELECT 
            symbol,
            COUNT(*) as quote_count,
            MIN(quote_date) as first_date,
            MAX(quote_date) as last_date
        FROM daily_quotes
        GROUP BY symbol
        ORDER BY symbol
    ''')
    
    print("\n=== Per-Symbol Statistics ===")
    for row in cursor.fetchall():
        print(f"{row[0]:8} {row[1]:5} quotes  {row[2]} to {row[3]}")
    
    conn.close()

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Update stock quote database')
    parser.add_argument('--init', action='store_true', help='Initialize with portfolio symbols')
    parser.add_argument('--add', type=str, help='Add a new symbol')
    parser.add_argument('--update', action='store_true', help='Update all symbols')
    parser.add_argument('--stats', action='store_true', help='Show database statistics')
    parser.add_argument('--years', type=int, default=3, help='Years of history to fetch (default: 3)')
    
    args = parser.parse_args()
    
    # Your portfolio symbols
    PORTFOLIO_SYMBOLS = [
        'BND', 'BOXX', 'AAPL', 'CSCO', 'CSOAX', 'EFA', 'FNILX', 'FZROX',
        'HYDB', 'HYGH', 'IBIT', 'IVV', 'QQQ', 'SCHD', 'SMYX', 'STAYX',
        'SUSTX', 'FDEV', 'FISOX', 'VIG', 'VOO', 'VTI', 'VWEHX', 'VWO'
    ]
    
    if args.init:
        print("Initializing portfolio symbols...")
        initialize_portfolio_symbols(PORTFOLIO_SYMBOLS)
        print(f"Added {len(PORTFOLIO_SYMBOLS)} symbols")
    
    if args.add:
        add_symbol(args.add.upper())
    
    if args.update:
        update_all_symbols(args.years)
    
    if args.stats:
        get_quote_count()
    
    if not any([args.init, args.add, args.update, args.stats]):
        parser.print_help()
