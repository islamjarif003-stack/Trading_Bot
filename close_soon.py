#!/usr/bin/env python3
"""Emergency close SOONUSDT position."""
import os, time
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()
c = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'), testnet=True)
c.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
st = c.futures_time()
c.timestamp_offset = st['serverTime'] - int(time.time()*1000)

# Cancel all orders first
try:
    c.futures_cancel_all_open_orders(symbol='SOONUSDT')
    print('Orders cancelled for SOONUSDT')
except Exception as e:
    print(f'Cancel orders: {e}')

# Close position
pos = c.futures_position_information(symbol='SOONUSDT')
for p in pos:
    amt = float(p.get('positionAmt', 0))
    if amt != 0:
        side = 'SELL' if amt > 0 else 'BUY'
        qty = abs(amt)
        r = c.futures_create_order(symbol='SOONUSDT', side=side, type='MARKET', quantity=qty, reduceOnly=True)
        print(f'CLOSED SOONUSDT: {side} {qty} units')
        print(f"Status: {r.get('status')}")
    else:
        print('No open position for SOONUSDT')
