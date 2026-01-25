"""
Configuration Manager for Stock Quote System
Provides easy interface to manage data sources and settings
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.expanduser('~'), 'stock_quotes.db')

class ConfigManager:
    """Manage system configuration"""
    
    def __init__(self):
        self.db_path = DB_PATH
    
    def list_sources(self):
        """Display all configured data sources"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT source_name, api_key, rate_limit, enabled, priority, notes
            FROM data_sources
            ORDER BY priority
        ''')
        
        print("\n=== Configured Data Sources ===")
        print(f"{'Source':<15} {'Enabled':<8} {'Priority':<9} {'Rate Limit':<11} {'API Key':<10} {'Notes'}")
        print("-" * 100)
        
        for row in cursor.fetchall():
            source, api_key, rate_limit, enabled, priority, notes = row
            enabled_str = "YES" if enabled else "NO"
            key_str = "Set" if api_key else "Not Set"
            print(f"{source:<15} {enabled_str:<8} {priority:<9} {rate_limit:<11} {key_str:<10} {notes}")
        
        conn.close()
    
    def set_api_key(self, source_name, api_key):
        """Set API key for a data source"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                UPDATE data_sources
                SET api_key = ?
                WHERE source_name = ?
            ''', (api_key, source_name.lower()))
            
            if cursor.rowcount > 0:
                conn.commit()
                print(f"API key set for {source_name}")
            else:
                print(f"Source '{source_name}' not found")
        finally:
            conn.close()
    
    def enable_source(self, source_name, priority=None):
        """Enable a data source and optionally set priority"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            if priority is not None:
                cursor.execute('''
                    UPDATE data_sources
                    SET enabled = 1, priority = ?
                    WHERE source_name = ?
                ''', (priority, source_name.lower()))
            else:
                cursor.execute('''
                    UPDATE data_sources
                    SET enabled = 1
                    WHERE source_name = ?
                ''', (source_name.lower(),))
            
            if cursor.rowcount > 0:
                conn.commit()
                print(f"Enabled {source_name}")
            else:
                print(f"Source '{source_name}' not found")
        finally:
            conn.close()
    
    def disable_source(self, source_name):
        """Disable a data source"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                UPDATE data_sources
                SET enabled = 0
                WHERE source_name = ?
            ''', (source_name.lower(),))
            
            if cursor.rowcount > 0:
                conn.commit()
                print(f"Disabled {source_name}")
            else:
                print(f"Source '{source_name}' not found")
        finally:
            conn.close()
    
    def set_default_source(self, source_name):
        """Set a source as the default (priority 1, disable others)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Disable all sources
            cursor.execute('UPDATE data_sources SET enabled = 0')
            
            # Enable and prioritize selected source
            cursor.execute('''
                UPDATE data_sources
                SET enabled = 1, priority = 1
                WHERE source_name = ?
            ''', (source_name.lower(),))
            
            if cursor.rowcount > 0:
                conn.commit()
                print(f"Set {source_name} as default data source")
            else:
                print(f"Source '{source_name}' not found")
        finally:
            conn.close()
    
    def get_active_source(self):
        """Get the currently active data source"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT source_name, api_key, rate_limit
            FROM data_sources
            WHERE enabled = 1
            ORDER BY priority
            LIMIT 1
        ''')
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            print(f"\nActive Source: {result[0]}")
            print(f"API Key: {'Set' if result[1] else 'Not Required'}")
            print(f"Rate Limit: {result[2]} requests/day")
        else:
            print("No active data source configured!")
        
        return result
    
    def add_source(self, source_name, rate_limit, api_key=None, notes=None):
        """Add a new data source"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO data_sources
                (source_name, api_key, rate_limit, enabled, priority, notes)
                VALUES (?, ?, ?, 0, 99, ?)
            ''', (source_name.lower(), api_key, rate_limit, notes))
            
            conn.commit()
            print(f"Added data source: {source_name}")
        except sqlite3.Error as e:
            print(f"Error adding source: {e}")
        finally:
            conn.close()

def main():
    """Interactive configuration menu"""
    import sys
    
    config = ConfigManager()
    
    if len(sys.argv) < 2:
        print("\nStock Quote Database Configuration")
        print("=" * 50)
        print("\nUsage:")
        print("  python config.py list                       - List all sources")
        print("  python config.py active                     - Show active source")
        print("  python config.py enable SOURCE [PRIORITY]   - Enable a source")
        print("  python config.py disable SOURCE             - Disable a source")
        print("  python config.py default SOURCE             - Set default source")
        print("  python config.py setkey SOURCE KEY          - Set API key")
        print("\nExamples:")
        print("  python config.py list")
        print("  python config.py default yfinance")
        print("  python config.py setkey alphavantage YOUR_KEY_HERE")
        print("  python config.py enable alphavantage 1")
        return
    
    command = sys.argv[1].lower()
    
    if command == 'list':
        config.list_sources()
    
    elif command == 'active':
        config.get_active_source()
    
    elif command == 'enable' and len(sys.argv) >= 3:
        source = sys.argv[2]
        priority = int(sys.argv[3]) if len(sys.argv) > 3 else None
        config.enable_source(source, priority)
    
    elif command == 'disable' and len(sys.argv) >= 3:
        config.disable_source(sys.argv[2])
    
    elif command == 'default' and len(sys.argv) >= 3:
        config.set_default_source(sys.argv[2])
    
    elif command == 'setkey' and len(sys.argv) >= 4:
        config.set_api_key(sys.argv[2], sys.argv[3])
    
    else:
        print("Invalid command or missing arguments")
        print("Run 'python config.py' for usage help")

if __name__ == '__main__':
    main()
