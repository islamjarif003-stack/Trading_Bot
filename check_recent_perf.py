#!/usr/bin/env python3
import os, time
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()
c = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'), testnet=True)
c.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
st = c.futures_time()
c.timestamp_offset = st['serverTime'] - int(time.time()*1000)

print(f'\n--- Performance since Last Update (approx last 4h) ---')
# Fetch more income history
income = c.futures_income_history(incomeType='REALIZED_PNL', limit=100)

# Filter trades from the last 4 hours
cutoff_time = time.time() * 1000 - (4 * 3600 * 1000) # 4 hours ago

total_pnl = 0.0
wins = 0
losses = 0

for i in reversed(income):
    if i['time'] > cutoff_time:
        pnl = float(i['income'])
        if pnl != 0:
            total_pnl += pnl
            if pnl > 0:
                wins += 1
            else:
                losses += 1
            print(f"Income: {i['symbol']:<10} | PnL: ${pnl:<8.4f} | Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(i['time']/1000))}")

total_trades = wins + losses
win_rate = (wins/total_trades*100) if total_trades > 0 else 0

print(f'\n--- SUMMARY ---')
print(f'Total Trades: {total_trades}')
print(f'Wins: {wins}')
print(f'Losses: {losses}')
print(f'Win Rate: {win_rate:.1f}%')
print(f'Total PnL : ${total_pnl:.4f}')
