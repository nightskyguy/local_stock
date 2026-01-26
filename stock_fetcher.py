"""
Stock Data Fetcher
Supports multiple data sources with automatic fallback
"""
import sqlite3
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import time
import os

DB_PATH = os.path.join(os.path.expanduser('~'), 'stock_quotes.db')

class StockDataFetcher:
    """Manages fetching stock data from multiple sources"""

    def __init__(self):
        self.db_path = DB_PATH

    def get_active_source(self):
        """Get the highest priority enabled data source"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
                       SELECT source_name, api_key
                       FROM data_sources
                       WHERE enabled = 1
                       ORDER BY priority ASC
                           LIMIT 1
                       ''')
        result = cursor.fetchone()
        conn.close()
        return result if result else ('yfinance', None)

    def get_all_enabled_sources(self):
        """Get all enabled data sources in priority order"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
                       SELECT source_name, api_key
                       FROM data_sources
                       WHERE enabled = 1
                       ORDER BY priority ASC
                       ''')
        results = cursor.fetchall()
        conn.close()
        return results if results else [('yfinance', None)]

    def fetch_yfinance(self, symbol, start_date, end_date):
        """Fetch data using yfinance"""
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=end_date, auto_adjust=False)

            if df.empty:
                return None

            # Prepare data for database
            quotes = []
            for date, row in df.iterrows():
                quotes.append({
                    'symbol': symbol,
                    'quote_date': date.strftime('%Y-%m-%d'),
                    'open': float(row['Open']) if not pd.isna(row['Open']) else None,
                    'high': float(row['High']) if not pd.isna(row['High']) else None,
                    'low': float(row['Low']) if not pd.isna(row['Low']) else None,
                    'close': float(row['Close']) if not pd.isna(row['Close']) else None,
                    'volume': int(row['Volume']) if not pd.isna(row['Volume']) else None,
                    'dividends': float(row['Dividends']) if 'Dividends' in row and not pd.isna(row['Dividends']) else 0.0,
                    'stock_splits': float(row['Stock Splits']) if 'Stock Splits' in row and not pd.isna(row['Stock Splits']) else 0.0,
                    'data_source': 'yfinance'
                })

            return quotes
        except Exception as e:
            print(f"Error fetching {symbol} from yfinance: {e}")
            return None

    def fetch_alphavantage(self, symbol, start_date, end_date, api_key):
        """Fetch data using Alpha Vantage API"""
        # Placeholder for Alpha Vantage implementation
        # Requires API key from https://www.alphavantage.co/support/#api-key
        print(f"Alpha Vantage support requires API key configuration")
        return None

    def fetch_data(self, symbol, start_date, end_date=None):
        """Fetch data using the configured source with automatic fallback"""
        if end_date is None:
            end_date = datetime.now().strftime('%Y-%m-%d')

        # Get all enabled sources in priority order
        sources = self.get_all_enabled_sources()

        for source_name, api_key in sources:
            try:
                if source_name == 'yfinance':
                    result = self.fetch_yfinance(symbol, start_date, end_date)
                elif source_name == 'alphavantage':
                    result = self.fetch_alphavantage(symbol, start_date, end_date, api_key)
                else:
                    print(f"Unsupported data source: {source_name}, trying next...")
                    continue

                # If we got data, return it
                if result:
                    return result
                else:
                    print(f"No data from {source_name}, trying next source...")

            except Exception as e:
                print(f"Error with {source_name}: {e}, trying next source...")
                continue

        # If all sources failed
        print(f"All enabled data sources failed for {symbol}")
        return None

    def save_quotes(self, quotes):
        """Save quotes to database"""
        if not quotes:
            return 0

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        inserted = 0
        for quote in quotes:
            try:
                cursor.execute('''
                    INSERT OR REPLACE INTO daily_quotes 
                    (symbol, quote_date, open, high, low, close, volume, 
                     dividends, stock_splits, data_source, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (
                    quote['symbol'], quote['quote_date'], quote['open'],
                    quote['high'], quote['low'], quote['close'], quote['volume'],
                    quote['dividends'], quote['stock_splits'], quote['data_source']
                ))
                inserted += 1
            except sqlite3.Error as e:
                print(f"Error inserting {quote['symbol']} {quote['quote_date']}: {e}")

        conn.commit()
        conn.close()
        return inserted

    def update_symbol_tracking(self, symbol):
        """Update symbol tracking information"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Get date range for this symbol
        cursor.execute('''
                       SELECT MIN(quote_date), MAX(quote_date)
                       FROM daily_quotes
                       WHERE symbol = ?
                       ''', (symbol,))

        result = cursor.fetchone()
        if result and result[0]:
            cursor.execute('''
                INSERT OR REPLACE INTO symbols 
                (symbol, first_fetch_date, last_fetch_date, active)
                VALUES (?, ?, ?, 1)
            ''', (symbol, result[0], result[1]))

        conn.commit()
        conn.close()

    def get_missing_dates(self, symbol, start_date, end_date=None):
        """Find which dates are missing from the database, excluding weekends"""
        if end_date is None:
            end_date = datetime.now().strftime('%Y-%m-%d')

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
                       SELECT quote_date
                       FROM daily_quotes
                       WHERE symbol = ?
                         AND quote_date BETWEEN ? AND ?
                       ORDER BY quote_date
                       ''', (symbol, start_date, end_date))

        existing_dates = set(row[0] for row in cursor.fetchall())
        conn.close()

        # Generate all weekdays in range (Monday=0 to Friday=4)
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')

        expected_dates = set()
        current = start
        while current <= end:
            # Only include Monday-Friday (0-4)
            if current.weekday() < 5:
                expected_dates.add(current.strftime('%Y-%m-%d'))
            current += timedelta(days=1)

        # Return difference (these are weekdays we don't have data for)
        missing_dates = expected_dates - existing_dates

        # Filter out very recent dates (today/yesterday might not be available yet)
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)

        # Only report as "missing" if it's at least 2 days old
        # (This avoids complaining about data that simply hasn't been released yet)
        cutoff_date = (today - timedelta(days=2)).strftime('%Y-%m-%d')
        missing_dates = {d for d in missing_dates if d <= cutoff_date}

        return sorted(list(missing_dates))

def main():
    """Test the fetcher"""

    fetcher = StockDataFetcher()

    # Print database path
    print(f"Database path: {DB_PATH}")
    print()

    # Test with CSCO
    test_symbol = 'CSCO'
    start = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

    print(f"Fetching {test_symbol} from {start}...")
    quotes = fetcher.fetch_data(test_symbol, start)

    if quotes:
        print(f"Retrieved {len(quotes)} quotes")
        print()
        print("Most recent quotes:")
        print("-" * 80)
        # Show last 5 quotes (most recent)
        for quote in quotes[-5:]:
            date_obj = datetime.strptime(quote['quote_date'], '%Y-%m-%d')
            formatted = date_obj.strftime('%a %Y-%m-%d')  # "Saturday, 2025-01-25"
            print(f"{formatted}: Open=${quote['open']:.2f} High=${quote['high']:.2f} "
                  f"Low=${quote['low']:.2f} Close=${quote['close']:.2f} Vol={quote['volume']:,}")
        if len(quotes) > 5:
            print(f"... and {len(quotes) - 5} older quotes")
        print("-" * 80)
        print()

        saved = fetcher.save_quotes(quotes)
        print(f"Saved {saved} quotes to database")
        fetcher.update_symbol_tracking(test_symbol)
    else:
        print("No data retrieved")

if __name__ == '__main__':
    main()