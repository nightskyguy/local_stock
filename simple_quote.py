"""
File: simple_quote.py
Simple Python-UNO function to fetch quotes from local server
Bypasses LibreOffice WEBSERVICE security restrictions

Install: Copy to %APPDATA%\LibreOffice\4\user\Scripts\python\

Usage in Calc:
    =PYUNO("get_quote_url", "AAPL")
    =PYUNO("get_quote_url", "AAPL", "2025-01-15")
    =PYUNO("get_quote_url", "AAPL", "", "high")
"""

import urllib.request
import urllib.error

def get_quote_url(symbol, date="", field="close"):
    """
    Fetch stock quote from local web server
    
    Args:
        symbol: Stock ticker (e.g., "AAPL")
        date: Optional date in YYYY-MM-DD format
        field: Which field to get (open, high, low, close, volume)
    
    Returns:
        The quote value or error message
    """
    try:
        # Build URL
        if date:
            url = f"http://localhost:5000/quote/{symbol}/{date}"
        elif field != "close":
            url = f"http://localhost:5000/quote/{symbol}/field/{field}"
        else:
            url = f"http://localhost:5000/quote/{symbol}"
        
        # Make request (Python urllib is allowed by LibreOffice)
        with urllib.request.urlopen(url, timeout=5) as response:
            result = response.read().decode('utf-8')
            
            # Try to convert to number
            try:
                return float(result)
            except ValueError:
                return result
    
    except urllib.error.HTTPError as e:
        return f"#ERROR: HTTP {e.code}"
    except urllib.error.URLError as e:
        return f"#ERROR: Server not running"
    except Exception as e:
        return f"#ERROR: {str(e)}"

def get_latest_date_url(symbol):
    """Get latest date for symbol from web server"""
    try:
        url = f"http://localhost:5000/latest_date/{symbol}"
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.read().decode('utf-8')
    except Exception as e:
        return f"#ERROR: {str(e)}"

# Make functions available to LibreOffice
g_exportedScripts = (get_quote_url, get_latest_date_url)