"""Quick script to check real Binance PnL from recent trades."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from main import API_KEY, API_SECRET
from binance.client import Client

client = Client(API_KEY, API_SECRET, testnet=False, ping=False)

# Get last 50 trades
trades = client.futures_account_trades(limit=100)

# Group by orderId to get per-trade PnL
from collections import defaultdict
import datetime

orders = defaultdict(lambda: {"pnl": 0.0, "symbol": "", "side": "", "time": 0})
for t in trades:
    oid = t["orderId"]
    orders[oid]["pnl"] += float(t["realizedPnl"])
    orders[oid]["symbol"] = t["symbol"]
    orders[oid]["side"] = t["side"]
    orders[oid]["time"] = int(t["time"])

# Filter out zero-PnL (entry orders)
real_trades = [(k, v) for k, v in orders.items() if abs(v["pnl"]) > 0.001]
real_trades.sort(key=lambda x: x[1]["time"])

print("=" * 70)
print("REAL BINANCE TRADE HISTORY (Last 50 trades)")
print("=" * 70)

total_pnl = 0
wins = 0
losses = 0
for oid, data in real_trades[-30:]:
    ts = datetime.datetime.fromtimestamp(data["time"] / 1000)
    emoji = "✅" if data["pnl"] > 0 else "❌"
    print(f"  {emoji} {ts.strftime('%m-%d %H:%M')} │ {data['symbol']:12} │ {data['side']:5} │ PnL: ${data['pnl']:+.4f}")
    total_pnl += data["pnl"]
    if data["pnl"] > 0.01:
        wins += 1
    elif data["pnl"] < -0.01:
        losses += 1

print("=" * 70)
print(f"Total PnL: ${total_pnl:+.4f}")
print(f"Wins: {wins} | Losses: {losses} | Win Rate: {wins/(wins+losses)*100:.1f}%" if wins+losses > 0 else "No trades")

# Account balance
acc = client.futures_account()
print(f"Balance: ${float(acc['totalWalletBalance']):.2f}")
print(f"Unrealized PnL: ${float(acc['totalUnrealizedProfit']):.4f}")

# Open positions
positions = client.futures_position_information()
open_pos = [p for p in positions if float(p.get('positionAmt', 0)) != 0]
if open_pos:
    print(f"\nOpen Positions ({len(open_pos)}):")
    for p in open_pos:
        amt = float(p['positionAmt'])
        entry = float(p['entryPrice'])
        mark = float(p['markPrice'])
        upnl = float(p['unRealizedProfit'])
        side = "LONG" if amt > 0 else "SHORT"
        print(f"  {p['symbol']:12} │ {side:5} │ Entry: ${entry:.4f} │ Mark: ${mark:.4f} │ uPnL: ${upnl:+.4f}")
