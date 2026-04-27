#!/usr/bin/env python3
from binance.client import Client
import os
from dotenv import load_dotenv

load_dotenv()
c = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'))
orders = c.futures_get_open_orders()
print(f"Total open orders: {len(orders)}")
for o in orders:
    sym = o['symbol']
    side = o['side']
    price = o['price']
    print(f"  CANCELLING {sym} {side} @ {price}...", end=" ")
    try:
        c.futures_cancel_order(symbol=sym, orderId=o['orderId'])
        print("OK")
    except Exception as e:
        print(f"ERR: {e}")
print("Done!")
