#!/usr/bin/env python3
"""24h Trade Report Generator"""
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
load_dotenv('/opt/trading-bot/.env')

from binance.client import Client

c = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'), testnet=True)

# Fetch last 35 realized PnL entries
income = c.futures_income_history(incomeType='REALIZED_PNL', limit=35)

now = datetime.now(timezone.utc)

total_pnl = 0.0
wins = 0
losses = 0

print("=" * 70)
print(f"  LAST 35 TRADES REPORT  |  Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}")
print("=" * 70)
print(f"{'#':>3} | {'SYMBOL':10s} | {'PnL (USDT)':>12s} | {'RESULT':>8s} | {'TIME (UTC)'}")
print("-" * 70)

count = 0
for i in income:
    ts = datetime.fromtimestamp(int(i['time']) / 1000, tz=timezone.utc)
    pnl = float(i.get('income', 0.0))
    total_pnl += pnl
    if pnl > 0:
        wins += 1
        result = "WIN"
    else:
        losses += 1
        result = "LOSS"
    count += 1
    print(f"{count:>3} | {i['symbol']:10s} | ${pnl:>+10.2f} | {result:>8s} | {ts.strftime('%Y-%m-%d %H:%M')}")

print("-" * 70)
total_trades = wins + losses
wr = (wins / total_trades * 100) if total_trades > 0 else 0
print(f"TOTAL TRADES: {total_trades}  |  WINS: {wins}  |  LOSSES: {losses}  |  WIN RATE: {wr:.1f}%")
print(f"NET PnL: ${total_pnl:+.2f}")

# Balance
bal = c.futures_account_balance()
for b in bal:
    if b['asset'] == 'USDT':
        print(f"CURRENT BALANCE: ${float(b['balance']):.2f}")
        break
print("=" * 70)
