#!/usr/bin/env python3
"""Check all open positions and orders, then show which ones are eating the stop order limit."""
import os, time
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()
c = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'), testnet=True)
c.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
st = c.futures_time()
c.timestamp_offset = st['serverTime'] - int(time.time()*1000)

# List all open positions
pos = c.futures_position_information()
open_pos = [p for p in pos if float(p.get('positionAmt', 0)) != 0]
print(f'Open positions: {len(open_pos)}')
for p in open_pos:
    sym = p['symbol']
    amt = float(p['positionAmt'])
    upnl = float(p['unRealizedProfit'])
    entry = float(p['entryPrice'])
    print(f'  {sym}: amt={amt} entry={entry} upnl={upnl:+.4f}')

# List all open orders GLOBALLY
ords = c.futures_get_open_orders()
print(f'\nTotal open orders globally: {len(ords)}')
for o in ords[:30]:
    print(f'  {o["symbol"]} {o["type"]} {o["side"]} qty={o["origQty"]} stopPrice={o.get("stopPrice","N/A")}')

# If too many, do a global cancel
if len(ords) > 5:
    print(f'\n=== TOO MANY ORDERS ({len(ords)}). Cancelling ALL... ===')
    cancelled_syms = set()
    for o in ords:
        sym = o['symbol']
        if sym not in cancelled_syms:
            try:
                c.futures_cancel_all_open_orders(symbol=sym)
                print(f'  Cancelled: {sym}')
                cancelled_syms.add(sym)
            except Exception as e:
                print(f'  Error {sym}: {e}')
    time.sleep(3)
    remaining = c.futures_get_open_orders()
    print(f'Remaining after cleanup: {len(remaining)}')
