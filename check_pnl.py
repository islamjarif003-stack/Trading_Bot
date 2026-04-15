import json, requests, os

positions_file = '/opt/trading-bot/dry_run_positions.json'
if not os.path.exists(positions_file):
    print("No active dry run positions found.")
    exit(0)

try:
    with open(positions_file, 'r') as f:
        positions = json.load(f)
except Exception as e:
    print("Error loading positions:", e)
    exit(1)

if not positions:
    print("Currently no open trades.")
    exit(0)

try:
    res = requests.get('https://fapi.binance.com/fapi/v1/ticker/price', timeout=5)
    res.raise_for_status()
    prices = {item['symbol']: float(item['price']) for item in res.json()}
except Exception as e:
    print("Failed to fetch current Binance prices:", e)
    exit(1)

print("="*60)
print('   📈 CURRENT RUNNING TRADES (PNL REPORT)')
print("="*60)

total_pnl = 0.0
for sym, data in positions.items():
    current_price = prices.get(sym)
    if current_price is None:
        print(f"{sym} : Unable to find current price.")
        continue
    
    entry = data['entry_price']
    qty = data['qty']
    side = data['side']
    
    if side == 'BUY':
        diff = current_price - entry
    else:
        diff = entry - current_price
        
    pnl = diff * qty
    total_pnl += pnl
    
    # Leveraged % (assume 20x)
    pnl_pct = (diff / entry) * 100 * 20
    raw_pct = (diff / entry) * 100
    
    status_icon = "🟩" if pnl > 0 else "🟥"
    print(f"{status_icon} [{sym}] ({side}) | Entry: ${entry:.4f} | Cur: ${current_price:.4f}")
    print(f"   ├─ PnL: ${pnl:+.4f} | Raw: {raw_pct:+.2f}% | Lev(20x): {pnl_pct:+.2f}%")

print("-" * 60)
final_icon = "💰" if total_pnl > 0 else "📉"
print(f"{final_icon} TOTAL UNREALIZED PNL: ${total_pnl:+.4f}")
print("="*60)
