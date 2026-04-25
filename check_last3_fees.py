from binance.client import Client
import os, time
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
c = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"))

print("=" * 65)
print("  LAST 3 TRADES — Detailed Fee Breakdown")
print("=" * 65)

total_fee = 0
total_pnl = 0

for sym in ["AXSUSDT", "BTCUSDT"]:
    now = int(time.time() * 1000)
    day_ago = now - (2 * 24 * 60 * 60 * 1000)
    trades = c.futures_account_trades(symbol=sym, startTime=day_ago, limit=50)
    if trades:
        print(f"\n  {sym}:")
        for t in trades:
            ts = int(t["time"]) / 1000
            dt = datetime.utcfromtimestamp(ts).strftime("%m-%d %H:%M")
            side = t["side"]
            price = float(t["price"])
            qty = float(t["qty"])
            rpnl = float(t["realizedPnl"])
            fee = float(t["commission"])
            notional = price * qty
            fee_pct = (fee / notional * 100) if notional > 0 else 0
            total_fee += fee
            total_pnl += rpnl
            maker = t.get("maker", False)
            fee_type = "MAKER" if maker else "TAKER"
            print(f"    {dt} {side:4s} | ${price:.4f} x {qty}")
            print(f"           Notional: ${notional:.2f} | Fee: ${fee:.4f} ({fee_pct:.4f} pct) [{fee_type}]")
            if rpnl != 0:
                print(f"           PnL: ${rpnl:+.4f}")

# Also check ALL trades total
print("\n" + "=" * 65)
now = int(time.time() * 1000)
week_ago = now - (7 * 24 * 60 * 60 * 1000)
all_fee = 0
all_pnl = 0
all_syms = ["AXSUSDT","BTCUSDT","MSTRUSDT","MOVRUSDT","XRPUSDT","FILUSDT","AVAXUSDT"]
for sym in all_syms:
    try:
        trades = c.futures_account_trades(symbol=sym, startTime=week_ago, limit=50)
        for t in trades:
            all_fee += float(t["commission"])
            all_pnl += float(t["realizedPnl"])
    except:
        pass

print(f"  ALL TRADES (7 days):")
print(f"    Total PnL     : ${all_pnl:+.4f}")
print(f"    Total Fees    : ${all_fee:.4f}")
print(f"    Net (PnL-Fee) : ${all_pnl - all_fee:+.4f}")
print(f"\n  LAST 3 TRADES:")
print(f"    Total PnL     : ${total_pnl:+.4f}")
print(f"    Total Fees    : ${total_fee:.4f}")
print(f"    Net (PnL-Fee) : ${total_pnl - total_fee:+.4f}")
print("=" * 65)
