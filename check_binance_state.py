#!/usr/bin/env python3
"""Quick check of Binance Futures account state."""
import os
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()
c = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"))

# Balance
bal = c.futures_account_balance()
for b in bal:
    if b["asset"] == "USDT":
        print(f"USDT: available={b['availableBalance']}, wallet={b['balance']}")

# Open positions
pos = c.futures_account()["positions"]
open_pos = [p for p in pos if float(p["positionAmt"]) != 0]
print(f"\nOpen positions: {len(open_pos)}")
for p in open_pos:
    print(f"  {p['symbol']} amt={p['positionAmt']} pnl={p['unrealizedProfit']}")

# Open orders
orders = c.futures_get_open_orders()
print(f"\nOpen orders: {len(orders)}")
for o in orders:
    print(f"  {o['symbol']} {o['side']} {o['type']} price={o['price']} qty={o['origQty']}")
