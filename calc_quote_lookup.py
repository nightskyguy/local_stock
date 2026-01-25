"""
Alternative LibreOffice Calc Integration (Simpler Method)
This creates a Python script that Calc can call via SHELL() or macros

For use with LibreOffice Basic macro:
Function STOCKQUOTE(symbol As String, Optional quoteDate As String, Optional field As String) As Variant
    Dim cmd As String
    Dim result As String
    
    If IsMissing(quoteDate) Then quoteDate = ""
    If IsMissing(field) Then field = "close"
    
    cmd = "python """ & Environ("USERPROFILE") & "\calc_quote_lookup.py"" " & symbol & " " & quoteDate & " " & field
    result = Trim(CreateUnoService("com.sun.star.system.SystemShellExecute").execute(cmd, "", 1))
    
    STOCKQUOTE = CDbl(result)
End Function
"""

import sqlite3
import sys
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.expanduser('~'), 'stock_quotes.db')

def get_quote_simple(symbol, date='', field='close'):
    """
    Simple quote lookup that can be called from command line
    Returns just the value for easy parsing
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Validate field to prevent SQL injection
        valid_fields = ['open', 'high', 'low', 'close', 'volume']
        if field.lower() not in valid_fields:
            field = 'close'
        
        if date:
            cursor.execute(f'''
                SELECT {field}
                FROM daily_quotes
                WHERE symbol = ? AND quote_date = ?
            ''', (symbol.upper(), date))
        else:
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
            print(result[0])
            return 0
        else:
            # Try to fetch if missing
            auto_fetch(symbol)
            print('ERROR: No data')
            return 1
    
    except Exception as e:
        print(f'ERROR: {e}')
        return 1

def auto_fetch(symbol):
    """Automatically fetch missing data"""
    try:
        # Import here to avoid issues if module not available
        sys.path.insert(0, os.path.expanduser('~'))
        from stock_fetcher import StockDataFetcher
        
        fetcher = StockDataFetcher()
        start_date = (datetime.now() - timedelta(days=3*365)).strftime('%Y-%m-%d')
        
        quotes = fetcher.fetch_data(symbol.upper(), start_date)
        if quotes:
            fetcher.save_quotes(quotes)
            fetcher.update_symbol_tracking(symbol.upper())
    except Exception as e:
        pass  # Silently fail on auto-fetch

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python calc_quote_lookup.py SYMBOL [DATE] [FIELD]')
        sys.exit(1)
    
    symbol = sys.argv[1]
    date = sys.argv[2] if len(sys.argv) > 2 else ''
    field = sys.argv[3] if len(sys.argv) > 3 else 'close'
    
    sys.exit(get_quote_simple(symbol, date, field))
