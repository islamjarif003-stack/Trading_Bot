#!/usr/bin/env python3
"""Quick remote account state check."""
import os, sys
sys.path.insert(0, '/opt/trading-bot')
os.chdir('/opt/trading-bot')
from dotenv import load_dotenv
load_dotenv('/opt/trading-bot/.env')
from binance.client import Client

c = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'))

# Open positions
positions = c.futures_position_information()
open_pos = [p for p in positions if float(p['positionAmt']) != 0]
print('=== OPEN POSITIONS ===')
for p in open_pos:
    sym = p['symbol']
    amt = p['positionAmt']
    entry = p['entryPrice']
    pnl = p['unRealizedProfit']
    lev = p['leverage']
    print(f"  {sym} | Amt: {amt} | Entry: {entry} | PnL: ${float(pnl):.4f} | Lev: {lev}x")
if not open_pos:
    print('  None')

# Open orders
orders = c.futures_get_open_orders()
print(f'\n=== OPEN ORDERS ({len(orders)}) ===')
for o in orders:
    sym = o['symbol']
    side = o['side']
    otype = o['type']
    price = o['price']
    qty = o['origQty']
    print(f"  {sym} | {side} | {otype} | Price: {price} | Qty: {qty}")
if not orders:
    print('  None')

# Balance
account = c.futures_account()
total = float(account['totalWalletBalance'])
avail = float(account['availableBalance'])
upnl = float(account['totalUnrealizedProfit'])
print(f'\n=== BALANCE ===')
print(f'  Total Wallet: ${total:.2f}')
print(f'  Available:    ${avail:.2f}')
print(f'  Unrealized:   ${upnl:.2f}')
print(f'  Equity:       ${total + upnl:.2f}')
