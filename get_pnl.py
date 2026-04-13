#!/usr/bin/env python3
import os, time
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()
c = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'), testnet=True)
c.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
st = c.futures_time()
c.timestamp_offset = st['serverTime'] - int(time.time()*1000)

print(f'\n--- Recent Income (Closed PnL) ---')
income = c.futures_income_history(incomeType='REALIZED_PNL', limit=15)
total = 0
for i in reversed(income):
    pnl = float(i['income'])
    if pnl != 0:
        total += pnl
        print(f"Income: {i['symbol']:<10} | PnL: ${pnl:<8.4f} | Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(i['time']/1000))}")

print(f"\nTotal PnL from last 15 trades: ${total:.4f}")
