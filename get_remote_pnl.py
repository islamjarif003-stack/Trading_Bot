import re
import requests

log_file = '/root/.pm2/logs/trading-bot-error.log'
active_trades = {}

with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
    for line in f:
        # Register
        reg_match = re.search(r'\[DRY RUN\] \[([A-Z0-9]+)\].*?Position registered: (BUY|SELL) @ \$([0-9.]+)', line)
        if reg_match:
            sym, side, entry = reg_match.groups()
            active_trades[sym] = {'side': side, 'entry': float(entry)}

        # Close
        if 'SL HIT' in line or 'TP HIT' in line or 'Trade Result' in line:
            close_match = re.search(r'\[([A-Z0-9]+)\]', line)
            if close_match:
                sym = close_match.group(1)
                if sym in active_trades:
                    del active_trades[sym]

if not active_trades:
    print('No active trades right now. All hit SL/TP.')
    exit()

try:
    res = requests.get('https://fapi.binance.com/fapi/v1/ticker/price', timeout=5)
    prices = {item['symbol']: float(item['price']) for item in res.json()}
except:
    print('Failed to get prices.')
    exit()

print('==== CURRENT ACTIVE TRADES ====')
for sym, data in active_trades.items():
    current = prices.get(sym, 0)
    if current == 0: continue
    
    diff = current - data['entry']
    if data['side'] == 'SELL': diff = -diff
    pct = (diff / data['entry']) * 100
    
    status = 'PROFIT' if pct > 0 else 'LOSS'
    print(f"{sym} ({data['side']}) | Entry: {data['entry']:.4f} | Cur: {current:.4f} | {pct:+.2f}% ({status})")
