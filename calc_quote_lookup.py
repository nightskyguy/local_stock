"""
File: calc_quote_lookup.py
Alternative LibreOffice Calc Integration (Simpler Method)
This Python script is called from a LibreOffice Basic macro.
See README.md for the Basic macro code.
"""

import sqlite3
import sys
import os
import logging
from datetime import datetime, timedelta

# Use USERPROFILE environment variable instead of expanduser
# Set up logging
log_path = os.path.join(os.environ['USERPROFILE'], 'calc_quote_lookup.log')
logging.basicConfig(
    filename=log_path,
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.environ['USERPROFILE'], 'stock_quotes.db')

def get_quote_simple(symbol, date='', field='close'):
    """
    Simple quote lookup that can be called from command line
    Returns just the value for easy parsing
    """
    try:
        logger.info(f"get_quote_simple called: symbol={symbol}, date={date}, field={field}")
        logger.info(f"DB_PATH={DB_PATH}, exists={os.path.exists(DB_PATH)}")
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Validate field to prevent SQL injection
        valid_fields = ['open', 'high', 'low', 'close', 'volume']
        if field.lower() not in valid_fields:
            field = 'close'
        
        if date:
            logger.debug(f"Querying with date: {date}")
            cursor.execute(f'''
                SELECT {field}
                FROM daily_quotes
                WHERE symbol = ? AND quote_date = ?
            ''', (symbol.upper(), date))
        else:
            logger.debug(f"Querying latest quote")
            cursor.execute(f'''
                SELECT {field}
                FROM daily_quotes
                WHERE symbol = ?
                ORDER BY quote_date DESC
                LIMIT 1
            ''', (symbol.upper(),))
        
        result = cursor.fetchone()
        logger.debug(f"Query result: {result}")
        conn.close()
        
        if result and result[0] is not None:
            logger.info(f"Success: returning {result[0]}")
            print(result[0])
            return 0
        else:
            # Try to fetch if missing
            logger.warning(f"No data found, attempting auto-fetch")
            auto_fetch(symbol)
            print('ERROR: No data')
            return 1
    
    except Exception as e:
        logger.error(f"Exception in get_quote_simple: {e}", exc_info=True)
        print(f'ERROR: {e}')
        return 1

def auto_fetch(symbol):
    """Automatically fetch missing data"""
    try:
        logger.info(f"auto_fetch called for {symbol}")
        # Import here to avoid issues if module not available
        sys.path.insert(0, os.environ['USERPROFILE'])
        from stock_fetcher import StockDataFetcher
        
        fetcher = StockDataFetcher()
        start_date = (datetime.now() - timedelta(days=3*365)).strftime('%Y-%m-%d')
        
        quotes = fetcher.fetch_data(symbol.upper(), start_date)
        if quotes:
            logger.info(f"auto_fetch: fetched {len(quotes)} quotes")
            fetcher.save_quotes(quotes)
            fetcher.update_symbol_tracking(symbol.upper())
        else:
            logger.warning(f"auto_fetch: no quotes returned")
    except Exception as e:
        logger.error(f"auto_fetch failed: {e}", exc_info=True)
        pass  # Silently fail on auto-fetch

if __name__ == '__main__':
    logger.info(f"Script started with args: {sys.argv}")
    
    if len(sys.argv) < 2:
        print('Usage: python calc_quote_lookup.py SYMBOL [DATE] [FIELD]')
        sys.exit(1)
    
    symbol = sys.argv[1]
    date = sys.argv[2] if len(sys.argv) > 2 else ''
    field = sys.argv[3] if len(sys.argv) > 3 else 'close'
    
    exit_code = get_quote_simple(symbol, date, field)
    logger.info(f"Script exiting with code: {exit_code}")
    sys.exit(exit_code)