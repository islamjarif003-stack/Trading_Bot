#!/usr/bin/env python3
"""Debug: dump open orders structure"""
import json
from binance.client import Client

API_KEY = "lf26O58uVx3WWgforjjyzBC5CjMoIVMR70huZPI8vDq00TCYvKYnjIfdvi5HwacY"
API_SECRET = "Y9iYOxF6jCgt8dz6AfFXvKSlJaFMpmoxAeN8konc3lKMwI2BWdgabNbYOHRN2MQ4"

c = Client(API_KEY, API_SECRET, testnet=True)
c.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

orders = c.futures_get_open_orders(symbol="BTCUSDT")
print(f"Total orders: {len(orders)}")
for o in orders:
    print(json.dumps(o, indent=2))
