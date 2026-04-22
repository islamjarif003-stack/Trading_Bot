#!/usr/bin/env python3
"""
Fast cleanup — cancel ALL orders for open position symbols on testnet.
★ AUDIT FIX: Dynamically fetches symbols from API instead of hardcoded dead list.
"""
import os, time
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()
c = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'), testnet=True)
c.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
st = c.futures_time()
c.timestamp_offset = st['serverTime'] - int(time.time()*1000)

# ★ AUDIT FIX: Dynamically get symbols with open positions + open orders
# (was hardcoded list of 41 symbols including invalid ones like "币安人生USDT")
positions = c.futures_position_information()
pos_symbols = [p['symbol'] for p in positions if float(p.get('positionAmt', 0)) != 0]

all_orders = c.futures_get_open_orders()
order_symbols = list(set(o['symbol'] for o in all_orders))

all_symbols = list(set(pos_symbols + order_symbols))
print(f'Found {len(pos_symbols)} open positions, {len(order_symbols)} symbols with orders')
print(f'Checking {len(all_symbols)} symbols for stuck orders...')

count = 0
for sym in all_symbols:
    try:
        orders = c.futures_get_open_orders(symbol=sym)
        if orders:
            print(f'  {sym} has {len(orders)} open orders. Cancelling...')
            c.futures_cancel_all_open_orders(symbol=sym)
            count += len(orders)
    except Exception as e:
        if '-2011' not in str(e):
            print(f"  Failed for {sym}: {e}")

print(f'\nDone. Cancelled {count} orders.')
