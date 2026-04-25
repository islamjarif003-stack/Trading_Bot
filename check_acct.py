"""Check: Where was price exactly 1 hour after entry? (FIXED timestamps)"""
from binance.client import Client
from dotenv import load_dotenv
import os, time
from datetime import datetime, timezone

load_dotenv()
c = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'))
c.API_URL = 'https://fapi.binance.com/fapi'

# Get actual entry times from trade history
trades_info = [
    {"coin": "FOLKSUSDT", "side": "SELL", "entry": 1.442, "sl": 1.494},
    {"coin": "LINKUSDT", "side": "BUY", "entry": 9.40, "sl": 9.38},
    {"coin": "ZECUSDT", "side": "SELL", "entry": 362.66, "sl": 361.33},
]

print("=" * 80)
print("  1-HOUR POST-ENTRY ANALYSIS (using actual trade timestamps)")
print("=" * 80)

for t in trades_info:
    sym = t["coin"]
    side = t["side"]
    entry = t["entry"]
    sl = t["sl"]
    
    # Get actual trades to find exact entry time
    all_trades = c.futures_account_trades(symbol=sym, limit=20)
    
    # Find the entry trade (matching side and approximate price)
    entry_trade = None
    for tr in all_trades:
        if tr['side'] == side.upper() and abs(float(tr['price']) - entry) < entry * 0.01:
            entry_trade = tr
            break
    
    if not entry_trade:
        # Just use the most recent trade for this symbol
        for tr in reversed(all_trades):
            entry_trade = tr
            break
    
    entry_time_ms = int(entry_trade['time'])
    entry_price = float(entry_trade['price'])
    entry_dt = datetime.fromtimestamp(entry_time_ms/1000, tz=timezone.utc)
    
    print(f"\n{'─' * 80}")
    print(f"  {sym} | {side} @ ${entry_price:.4f} | SL: ${sl}")
    print(f"  Entry Time: {entry_dt.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'─' * 80}")
    
    # Get 5m candles starting from entry
    klines = c.futures_klines(symbol=sym, interval='5m', startTime=entry_time_ms, limit=15)
    
    if len(klines) < 2:
        print(f"  Not enough data")
        continue
    
    worst_dd = 0
    best_pf = 0
    
    for i, k in enumerate(klines[:13]):
        mins = i * 5
        close_p = float(k[4])
        high_p = float(k[2])
        low_p = float(k[3])
        
        if side == "SELL":
            dd = high_p - entry_price
            pf = entry_price - low_p
        else:
            dd = entry_price - low_p
            pf = high_p - entry_price
        
        if dd > 0 and dd > worst_dd: worst_dd = dd
        if pf > 0 and pf > best_pf: best_pf = pf
    
    checkpoints = [(3, "15min"), (6, "30min"), (9, "45min"), (12, "1hour")]
    
    for bars, label in checkpoints:
        if bars < len(klines):
            price_at = float(klines[bars][4])
            if side == "SELL":
                move = entry_price - price_at
                pct = move / entry_price * 100
                tag = "✅ DOWN (RIGHT)" if move > 0 else "❌ UP (WRONG)"
            else:
                move = price_at - entry_price
                pct = move / entry_price * 100
                tag = "✅ UP (RIGHT)" if move > 0 else "❌ DOWN (WRONG)"
            
            print(f"  +{label:6s}: ${price_at:.4f} | Move: ${move:+.4f} ({pct:+.2f}%) | {tag}")
    
    sl_dist = abs(sl - entry_price)
    print(f"")
    print(f"  SL Distance        : ${sl_dist:.4f} ({sl_dist/entry_price*100:.2f}%)")
    print(f"  Worst Against (1hr): ${worst_dd:.4f} ({worst_dd/entry_price*100:.2f}%)")
    print(f"  Best For Bot (1hr) : ${best_pf:.4f} ({best_pf/entry_price*100:.2f}%)")
    
    if worst_dd > sl_dist:
        print(f"  ⚠️  SL TOO TIGHT! Drawdown ${worst_dd:.4f} > SL ${sl_dist:.4f}")
    if best_pf > sl_dist:
        print(f"  💰 Would have profited ${best_pf:.4f} if SL survived!")
