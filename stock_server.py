"""
File: stock_server.py
Local web server for stock quotes - serves data to LibreOffice Calc via WEBSERVICE()

Usage:
    python stock_server.py
    
Then in LibreOffice Calc:
    =WEBSERVICE("http://localhost:5000/quote/AAPL")
    =WEBSERVICE("http://localhost:5000/quote/AAPL/2025-01-15")
    =WEBSERVICE("http://localhost:5000/quote/AAPL/field/high")
"""

from flask import Flask, jsonify
import sqlite3
import os

app = Flask(__name__)
DB_PATH = os.path.join(os.environ['USERPROFILE'], 'stock_quotes.db')

@app.route('/')
def home():
    """Show API documentation"""
    return """
    <h1>Stock Quote Server</h1>
    <p>Server is running!</p>
    <h2>API Endpoints:</h2>
    <ul>
        <li><code>/quote/&lt;symbol&gt;</code> - Latest close price</li>
        <li><code>/quote/&lt;symbol&gt;/&lt;date&gt;</code> - Price on specific date (YYYY-MM-DD)</li>
        <li><code>/quote/&lt;symbol&gt;/field/&lt;field&gt;</code> - Latest value for field (open, high, low, close, volume)</li>
        <li><code>/latest_date/&lt;symbol&gt;</code> - Most recent date with data</li>
        <li><code>/health</code> - Server health check</li>
    </ul>
    <h2>Examples:</h2>
    <ul>
        <li><a href="/quote/AAPL">/quote/AAPL</a></li>
        <li><a href="/quote/CSCO/2025-01-15">/quote/CSCO/2025-01-15</a></li>
        <li><a href="/quote/AAPL/field/high">/quote/AAPL/field/high</a></li>
        <li><a href="/latest_date/AAPL">/latest_date/AAPL</a></li>
    </ul>
    """

@app.route('/health')
def health():
    """Health check endpoint"""
    db_exists = os.path.exists(DB_PATH)
    return jsonify({
        'status': 'ok' if db_exists else 'error',
        'database': DB_PATH,
        'database_exists': db_exists
    })

@app.route('/quote/<symbol>')
def get_latest_quote(symbol):
    """Get latest close price for symbol"""
    return get_quote(symbol.upper(), field='close')

@app.route('/quote/<symbol>/<date>')
def get_quote_by_date(symbol, date):
    """Get close price for symbol on specific date (or nearest before)"""
    return get_quote(symbol.upper(), date=date, field='close')

@app.route('/quote/<symbol>/field/<field>')
def get_quote_by_field(symbol, field):
    """Get latest value for specific field"""
    valid_fields = ['open', 'high', 'low', 'close', 'volume']
    if field.lower() not in valid_fields:
        return jsonify({'error': f'Invalid field. Must be one of: {", ".join(valid_fields)}'}), 400
    return get_quote(symbol.upper(), field=field.lower())

@app.route('/latest_date/<symbol>')
def get_latest_date(symbol):
    """Get the most recent date we have data for"""
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
        
        if result and result[0]:
            return result[0]
        else:
            return jsonify({'error': f'No data for {symbol.upper()}'}), 404
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def get_quote(symbol, date=None, field='close'):
    """Core function to get quote data"""
    try:
        if not os.path.exists(DB_PATH):
            return jsonify({'error': f'Database not found: {DB_PATH}'}), 500
        
        conn = sqlite3.connect(DB_PATH)
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
            if not result or result[0] is None:
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
        
        if result and result[0] is not None:
            # Return just the number for WEBSERVICE() function
            return str(result[0])
        else:
            return jsonify({'error': f'No data for {symbol}'}), 404
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("=" * 60)
    print("Stock Quote Server Starting")
    print("=" * 60)
    print(f"Database: {DB_PATH}")
    print(f"Database exists: {os.path.exists(DB_PATH)}")
    print()
    print("Server will run at: http://localhost:5000")
    print()
    print("Test in browser:")
    print("  http://localhost:5000/quote/AAPL")
    print()
    print("Use in LibreOffice Calc:")
    print('  =WEBSERVICE("http://localhost:5000/quote/AAPL")')
    print()
    print("Press Ctrl+C to stop the server")
    print("=" * 60)
    
    # Run server
    app.run(host='127.0.0.1', port=5000, debug=False)