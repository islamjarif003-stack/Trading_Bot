#!/usr/bin/env python3
"""AGGRESSIVE cleanup — cancel ALL orders for EVERY USDT futures symbol on testnet."""
import os, time
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()
c = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'), testnet=True)
c.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
st = c.futures_time()
c.timestamp_offset = st['serverTime'] - int(time.time()*1000)

# Get ALL futures symbols
info = c.futures_exchange_info()
all_symbols = [s['symbol'] for s in info['symbols'] if s['symbol'].endswith('USDT')]
print(f'Total USDT futures symbols: {len(all_symbols)}')

# Cancel orders for EVERY single symbol
total_cancelled = 0
for i, sym in enumerate(all_symbols):
    try:
        result = c.futures_cancel_all_open_orders(symbol=sym)
        if result.get('code') == 200 or result.get('msg') == 'The operation of cancel all open order is done.':
            # Check if any were actually cancelled
            pass
    except Exception as e:
        err_str = str(e)
        if 'Unknown order sent' not in err_str and '-2011' not in err_str:
            pass  # Silently skip symbols with no orders
    
    if i % 50 == 0 and i > 0:
        print(f'  Processed {i}/{len(all_symbols)} symbols...')
        time.sleep(1)

print(f'\nDone processing all {len(all_symbols)} symbols.')

# Now verify
time.sleep(3)
remaining = c.futures_get_open_orders()
print(f'Remaining open orders: {len(remaining)}')

if len(remaining) > 0:
    print('\nStill remaining:')
    for o in remaining[:20]:
        print(f'  {o["symbol"]} {o["type"]} {o["side"]} orderId={o["orderId"]}')
    
    # Try individual cancel for stubborn orders
    print('\nForce-cancelling individual orders...')
    for o in remaining:
        try:
            c.futures_cancel_order(symbol=o['symbol'], orderId=o['orderId'])
            total_cancelled += 1
            print(f'  Cancelled: {o["symbol"]} #{o["orderId"]}')
        except Exception as e:
            print(f'  Failed: {o["symbol"]} #{o["orderId"]}: {e}')

# Final verify
time.sleep(2)
final = c.futures_get_open_orders()
print(f'\n=== FINAL: {len(final)} orders remaining ===')
