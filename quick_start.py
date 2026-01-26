"""
Quick Start Installation Script
Sets up the complete stock quote system
"""
import subprocess
import sys
import os

def check_python_version():
    """Ensure Python 3.8+"""
    if sys.version_info < (3, 8):
        print("ERROR: Python 3.8 or higher required")
        return False
    print(f"✓ Python {sys.version_info.major}.{sys.version_info.minor} detected")
    return True

def install_dependencies():
    """Install required Python packages"""
    packages = ['yfinance', 'pandas', 'numpy']
    
    print("\nInstalling dependencies...")
    for package in packages:
        try:
            print(f"  Installing {package}...")
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', package, '--quiet'])
            print(f"  ✓ {package} installed")
        except subprocess.CalledProcessError:
            print(f"  ✗ Failed to install {package}")
            return False
    return True

def setup_database():
    """Initialize the database"""
    print("\nSetting up database...")
    try:
        from stock_db_setup import create_database, initialize_data_sources
        create_database()
        initialize_data_sources()
        print("✓ Database created")
        return True
    except Exception as e:
        print(f"✗ Database setup failed: {e}")
        return False

def initialize_portfolio():
    """Add portfolio symbols"""
    print("\nInitializing portfolio symbols...")
    
    PORTFOLIO_SYMBOLS = [
        'BND', 'BOXX', 'AAPL', 'CSCO', 'CSOAX', 'EFA', 'FNILX', 'FZROX',
        'HYDB', 'HYGH', 'IBIT', 'IVV', 'QQQ', 'SCHD', 'SIYYX', 'STAYX',
        'SUSYX', 'TSLA', 'VASGX', 'VIOO', 'VOO', 'VTI', 'VWEHX', 'VWO'
    ]
    
    try:
        import sqlite3
        DB_PATH = os.path.join(os.path.expanduser('~'), 'stock_quotes.db')
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        for symbol in PORTFOLIO_SYMBOLS:
            cursor.execute('''
                INSERT OR IGNORE INTO symbols (symbol, active)
                VALUES (?, 1)
            ''', (symbol,))
        
        conn.commit()
        conn.close()
        print(f"✓ Added {len(PORTFOLIO_SYMBOLS)} symbols")
        return True
    except Exception as e:
        print(f"✗ Portfolio initialization failed: {e}")
        return False

def test_data_fetch():
    """Test fetching data for one symbol"""
    print("\nTesting data fetch...")
    try:
        from stock_fetcher import StockDataFetcher
        from datetime import datetime, timedelta
        
        fetcher = StockDataFetcher()
        start = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        
        print("  Fetching AAPL (last 7 days)...")
        quotes = fetcher.fetch_data('AAPL', start)
        
        if quotes:
            saved = fetcher.save_quotes(quotes)
            fetcher.update_symbol_tracking('AAPL')
            print(f"  ✓ Fetched and saved {saved} quotes for AAPL")
            return True
        else:
            print("  ✗ No data retrieved")
            return False
    except Exception as e:
        print(f"  ✗ Data fetch failed: {e}")
        return False

def print_next_steps():
    """Display next steps for user"""
    print("\n" + "="*60)
    print("✓ INSTALLATION COMPLETE!")
    print("="*60)
    
    db_path = os.path.join(os.path.expanduser('~'), 'stock_quotes.db')
    print(f"\nDatabase location: {db_path}")
    
    print("\nNext Steps:")
    print("\n1. Fetch historical data (3 years) for all symbols:")
    print("   python daily_update.py --update --years 3")
    print("   (This will take several minutes)")
    
    print("\n2. Check database statistics:")
    print("   python daily_update.py --stats")
    
    print("\n3. View/configure data sources:")
    print("   python config.py list")
    
    print("\n4. Set up LibreOffice Calc integration:")
    print("   See README.md for detailed instructions")
    
    print("\n5. Schedule daily updates:")
    print("   Add to Windows Task Scheduler:")
    print("   python daily_update.py --update")
    
    print("\nQuick Reference:")
    print("  Add symbol:    python daily_update.py --add NVDA")
    print("  Update all:    python daily_update.py --update")
    print("  Show stats:    python daily_update.py --stats")
    print("  Configuration: python config.py list")
    
    print("\nFor full documentation, see README.md")
    print("="*60)

def main():
    """Run the installation"""
    print("="*60)
    print("Stock Quote Database System - Quick Start Installation")
    print("="*60)
    
    steps = [
        ("Checking Python version", check_python_version),
        ("Installing dependencies", install_dependencies),
        ("Setting up database", setup_database),
        ("Initializing portfolio", initialize_portfolio),
        ("Testing data fetch", test_data_fetch),
    ]
    
    for step_name, step_func in steps:
        if not step_func():
            print(f"\n✗ Installation failed at: {step_name}")
            print("Please check the error messages above and try again.")
            return False
    
    print_next_steps()
    return True

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
