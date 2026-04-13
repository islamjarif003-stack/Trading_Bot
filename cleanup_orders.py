#!/usr/bin/env python3
"""Nuclear order cleanup — cancel ALL open orders across ALL symbols."""
import os, time
from dotenv import load_dotenv
from binance.client import Client
from collections import Counter

load_dotenv()
c = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'), testnet=True)
c.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
st = c.futures_time()
c.timestamp_offset = st['serverTime'] - int(time.time()*1000)

# Count ALL open orders
orders = c.futures_get_open_orders()
print(f'Total open orders across ALL symbols: {len(orders)}')

by_sym = Counter(o['symbol'] for o in orders)
for sym, cnt in by_sym.most_common(30):
    types = [o.get('type','?') for o in orders if o['symbol']==sym]
    print(f'  {sym}: {cnt} orders — {types}')

# Cancel ALL orders for ALL symbols with open orders
print(f'\n=== CANCELLING ALL ORDERS ===')
for sym in by_sym:
    try:
        c.futures_cancel_all_open_orders(symbol=sym)
        print(f'  ✅ {sym}: All orders cancelled')
    except Exception as e:
        print(f'  ❌ {sym}: {e}')

# Verify
time.sleep(2)
remaining = c.futures_get_open_orders()
print(f'\nRemaining orders after cleanup: {len(remaining)}')
