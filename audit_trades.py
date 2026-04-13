#!/usr/bin/env python3
"""Full A-to-Z Trade Audit Script — Fetches all trade history from Binance Testnet."""
import os, time
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()
client = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'), testnet=True)
client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'

# Time sync
try:
    st = client.futures_time()
    lt = int(time.time() * 1000)
    client.timestamp_offset = st['serverTime'] - lt
except:
    pass

# ── 1. Account Balance ──
print("=" * 70)
print("  FULL TRADING BOT AUDIT — A to Z")
print("=" * 70)

acc = client.futures_account_balance()
for a in acc:
    if a['asset'] == 'USDT':
        print(f"\n💰 Account Balance:    ${float(a['balance']):.2f}")
        print(f"   Available Balance:  ${float(a['availableBalance']):.2f}")
        print(f"   In Positions:       ${float(a['balance']) - float(a['availableBalance']):.2f}")

# ── 2. Open Positions ──
print("\n" + "=" * 70)
print("  📊 OPEN POSITIONS")
print("=" * 70)
positions = client.futures_position_information()
open_pos = [p for p in positions if float(p['positionAmt']) != 0]
total_unrealized = 0.0
for p in open_pos:
    sym = p['symbol']
    amt = float(p['positionAmt'])
    entry = float(p['entryPrice'])
    mark = float(p['markPrice'])
    upnl = float(p['unRealizedProfit'])
    lev = p.get('leverage', 'N/A')
    side = "LONG" if amt > 0 else "SHORT"
    pct = ((mark - entry) / entry * 100) if entry > 0 else 0
    if side == "SHORT":
        pct = -pct
    emoji = "🟢" if upnl >= 0 else "🔴"
    total_unrealized += upnl
    print(f"  {emoji} {sym:15s} | {side:5s} | Entry: ${entry:.6f} | Mark: ${mark:.6f} | PnL: ${upnl:+.4f} ({pct:+.2f}%) | Lev: {lev}x")

print(f"\n  Total Open Positions: {len(open_pos)}")
print(f"  Total Unrealized PnL: ${total_unrealized:+.2f}")

# ── 3. Realized PnL History ──
print("\n" + "=" * 70)
print("  📈 REALIZED PnL HISTORY (Last 100)")
print("=" * 70)
income = client.futures_income_history(incomeType='REALIZED_PNL', limit=100)
total_pnl = 0.0
wins = 0
losses = 0
win_total = 0.0
loss_total = 0.0
by_symbol = {}

for i in income:
    pnl = float(i['income'])
    if pnl == 0:
        continue
    total_pnl += pnl
    sym = i['symbol']
    ts = int(i['time']) / 1000
    t = time.strftime('%Y-%m-%d %H:%M', time.gmtime(ts))
    
    if pnl > 0:
        wins += 1
        win_total += pnl
    else:
        losses += 1
        loss_total += abs(pnl)
    
    if sym not in by_symbol:
        by_symbol[sym] = {'pnl': 0, 'wins': 0, 'losses': 0, 'trades': 0}
    by_symbol[sym]['pnl'] += pnl
    by_symbol[sym]['trades'] += 1
    if pnl > 0:
        by_symbol[sym]['wins'] += 1
    else:
        by_symbol[sym]['losses'] += 1
    
    emoji = '✅' if pnl >= 0 else '❌'
    print(f"  {emoji} {t} | {sym:15s} | PnL: ${pnl:+.4f}")

# ── 4. Summary Stats ──
total_trades = wins + losses
wr = (wins / total_trades * 100) if total_trades > 0 else 0
avg_win = (win_total / wins) if wins > 0 else 0
avg_loss = (loss_total / losses) if losses > 0 else 0
pf = (win_total / loss_total) if loss_total > 0 else float('inf')

print("\n" + "=" * 70)
print("  📊 PERFORMANCE SUMMARY")
print("=" * 70)
print(f"  Total Closed Trades : {total_trades}")
print(f"  Wins                : {wins}")
print(f"  Losses              : {losses}")
print(f"  Win Rate            : {wr:.1f}%")
print(f"  Total Realized PnL  : ${total_pnl:+.2f}")
print(f"  Avg Win             : ${avg_win:+.4f}")
print(f"  Avg Loss            : ${-avg_loss:+.4f}")
print(f"  Profit Factor       : {pf:.2f}")
print(f"  Unrealized PnL      : ${total_unrealized:+.2f}")
print(f"  Net (Realized+Unrealized): ${total_pnl + total_unrealized:+.2f}")

# ── 5. Per-Symbol Breakdown ──
print("\n" + "=" * 70)
print("  📋 PER-SYMBOL BREAKDOWN")
print("=" * 70)
sorted_syms = sorted(by_symbol.items(), key=lambda x: x[1]['pnl'], reverse=True)
for sym, data in sorted_syms:
    wr_sym = (data['wins'] / data['trades'] * 100) if data['trades'] > 0 else 0
    emoji = "🟢" if data['pnl'] >= 0 else "🔴"
    print(f"  {emoji} {sym:15s} | Trades: {data['trades']:3d} | W: {data['wins']:2d} L: {data['losses']:2d} | WR: {wr_sym:5.1f}% | PnL: ${data['pnl']:+.4f}")

# ── 6. Open Orders ──
print("\n" + "=" * 70)
print("  📝 OPEN ORDERS")
print("=" * 70)
orders = client.futures_get_open_orders()
if orders:
    for o in orders[:20]:
        print(f"  {o['symbol']:15s} | {o['side']:4s} | {o['type']:15s} | Qty: {o['origQty']} | Price: {o['price']} | Status: {o['status']}")
    print(f"  Total Open Orders: {len(orders)}")
else:
    print("  No open orders.")

print("\n" + "=" * 70)
print("  AUDIT COMPLETE")
print("=" * 70)
