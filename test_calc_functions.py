"""
Test script for calc_functions.py
Tests the LibreOffice Calc functions from command line
"""
from calc_functions import get_quote, get_latest_date, refresh_symbol

print("Testing calc_functions.py")
print("=" * 60)

# Test 1: Get latest quote
print("\n1. Get latest AAPL quote:")
result = get_quote('AAPL')
print(f"   Result: {result}")

# Test 2: Get specific date
print("\n2. Get AAPL quote for 2025-01-15:")
result = get_quote('AAPL', '2025-01-15')
print(f"   Result: {result}")

# Test 3: Get high price
print("\n3. Get latest AAPL high:")
result = get_quote('AAPL', '', 'high')
print(f"   Result: {result}")

# Test 4: Get latest date
print("\n4. Get latest date for AAPL:")
result = get_latest_date('AAPL')
print(f"   Result: {result}")

# Test 5: Test CSCO
print("\n5. Get latest CSCO quote:")
result = get_quote('CSCO')
print(f"   Result: {result}")

# Test 6: Weekend date (should return Friday)
print("\n6. Get AAPL quote for Sunday 2025-01-19 (should return Friday 2025-01-17):")
result = get_quote('AAPL', '2025-01-19')
print(f"   Result: {result}")

# Test 7: Test a symbol that doesn't exist
print("\n7. Test invalid symbol:")
result = get_quote('INVALID')
print(f"   Result: {result}")

print("\n" + "=" * 60)
print("Testing complete!")