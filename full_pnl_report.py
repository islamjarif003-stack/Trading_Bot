import re
import requests

log_file = '/root/.pm2/logs/trading-bot-error.log'
active_trades = {}
closed_trades = []

with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
    for line in f:
        reg_match = re.search(r'\[DRY RUN\] \[([A-Z0-9]+)\].*?Position registered: (BUY|SELL) @ \$([0-9.]+)', line)
        if reg_match:
            sym, side, entry = reg_match.groups()
            active_trades[sym] = {"side": side, "entry": float(entry)}

        sl_match = re.search(r'\[DRY RUN\] \[([A-Z0-9]+)\] SL HIT.*?Sim PnL: \$(-?[0-9.]+)', line)
        if sl_match:
            sym = sl_match.group(1)
            pnl = float(sl_match.group(2))
            closed_trades.append({"symbol": sym, "pnl": pnl})
            if sym in active_trades:
                del active_trades[sym]

        tp_match = re.search(r'\[DRY RUN\] \[([A-Z0-9]+)\] TP HIT.*?Sim PnL: \$(-?[0-9.]+)', line)
        if tp_match:
            sym = tp_match.group(1)
            pnl = float(tp_match.group(2))
            closed_trades.append({"symbol": sym, "pnl": pnl})
            if sym in active_trades:
                del active_trades[sym]

try:
    res = requests.get('https://fapi.binance.com/fapi/v1/ticker/price', timeout=5)
    prices = {item['symbol']: float(item['price']) for item in res.json()}
except:
    print('Failed to get prices.')
    exit()

print('\n===== CLOSED TRADES (SL/TP HIT) =====')
total_closed = 0.0
for t in closed_trades:
    total_closed += t["pnl"]
    print("  %s => $%.4f" % (t["symbol"], t["pnl"]))
print("\n  Total Closed PnL: $%.4f" % total_closed)
print("  Total Closed Trades: %d" % len(closed_trades))

print('\n===== ACTIVE TRADES (RUNNING) =====')
total_unrealized = 0.0
for sym, data in active_trades.items():
    current = prices.get(sym, 0)
    if current == 0:
        continue
    diff = current - data["entry"]
    if data["side"] == "SELL":
        diff = -diff
    pct = (diff / data["entry"]) * 100
    notional = 20.0
    usd = notional * (pct / 100)
    total_unrealized += usd
    status = "PROFIT" if pct > 0 else "LOSS"
    print("  %s (%s) | %+.2f%% | $%+.3f (%s)" % (sym, data["side"], pct, usd, status))

print("\n  Total Unrealized PnL: $%.4f" % total_unrealized)
print("  Total Active Trades: %d" % len(active_trades))

grand = total_closed + total_unrealized
print('\n========================================')
print('  GRAND TOTAL PnL: $%.4f' % grand)
print('========================================')
