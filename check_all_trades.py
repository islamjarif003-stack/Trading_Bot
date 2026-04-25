#!/usr/bin/env python3
"""Check all recent trades, fees, and balance."""
from binance.client import Client
import os, time
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

c = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"))

# Get account balance
acc = c.futures_account()
balance = float(acc["totalWalletBalance"])
pnl = float(acc["totalUnrealizedProfit"])
margin = float(acc["totalMarginBalance"])

print("=" * 70)
print("   💰 ACCOUNT SUMMARY")
print("=" * 70)
print(f"   Wallet Balance  : ${balance:.4f}")
print(f"   Unrealized PnL  : ${pnl:.4f}")
print(f"   Margin Balance  : ${margin:.4f}")
print("=" * 70)

# Get recent trades for all symbols
now = int(time.time() * 1000)
week_ago = now - (7 * 24 * 60 * 60 * 1000)

total_pnl = 0.0
total_fees = 0.0
trade_count = 0

# Check all symbols that had trades
symbols_to_check = ["AXSUSDT", "BTCUSDT", "MSTRUSDT", "MOVRUSDT", "XRPUSDT", "FILUSDT", "AVAXUSDT"]

for sym in symbols_to_check:
    try:
        trades = c.futures_account_trades(symbol=sym, startTime=week_ago, limit=50)
        if trades:
            print(f"\n📊 {sym}:")
            for t in trades:
                ts = int(t["time"]) / 1000
                dt = datetime.utcfromtimestamp(ts).strftime("%m-%d %H:%M")
                side = t["side"]
                price = float(t["price"])
                qty = float(t["qty"])
                rpnl = float(t["realizedPnl"])
                fee = float(t["commission"])
                total_pnl += rpnl
                total_fees += fee
                if rpnl != 0:
                    trade_count += 1
                pnl_icon = "🟢" if rpnl > 0 else ("🔴" if rpnl < 0 else "⚪")
                print(f"   {dt} | {side:4s} | ${price:.4f} | Qty: {qty} | PnL: ${rpnl:+.4f} {pnl_icon} | Fee: ${fee:.4f}")
    except Exception as e:
        pass

print("\n" + "=" * 70)
print("   📈 TRADE TOTALS")
print("=" * 70)
print(f"   Total Realized PnL : ${total_pnl:+.4f}")
print(f"   Total Fees Paid    : ${total_fees:.4f}")
print(f"   Net After Fees     : ${total_pnl - total_fees:+.4f}")
print(f"   Wallet Balance     : ${balance:.4f}")
print("=" * 70)

# Open positions
positions = c.futures_position_information()
open_pos = [p for p in positions if float(p["positionAmt"]) != 0]
if open_pos:
    print("\n🔓 OPEN POSITIONS:")
    for p in open_pos:
        sym = p["symbol"]
        amt = float(p["positionAmt"])
        entry = float(p["entryPrice"])
        upnl = float(p["unRealizedProfit"])
        side = "LONG" if amt > 0 else "SHORT"
        icon = "🟢" if upnl > 0 else "🔴"
        print(f"   {sym}: {side} | Entry: ${entry:.4f} | PnL: ${upnl:+.4f} {icon}")
else:
    print("\n✅ No open positions.")
