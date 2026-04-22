import json
import time
from binance.client import Client

DRY_RUN_FILE = '/opt/trading-bot/dry_run_positions.json'
KELLY_FILE = '/opt/trading-bot/kelly_state.json'
PROP_FILE = '/opt/trading-bot/prop_state.json'

with open(DRY_RUN_FILE, 'r') as f:
    positions = json.load(f)

with open(KELLY_FILE, 'r') as f:
    kelly = json.load(f)

client = Client()

for sym, data in list(positions.items()):
    try:
        ticker = client.futures_symbol_ticker(symbol=sym)
        current_price = float(ticker['price'])
        
        entry = data['entry_price']
        qty = data['qty']
        
        if data['side'] == 'BUY':
            pnl = (current_price - entry) * qty
        else:
            pnl = (entry - current_price) * qty
            
        print(f"{sym} ({data['side']}) - Entry: {entry:.4f}, Cur: {current_price:.4f} => PnL: ${pnl:.2f}")
        
        if pnl > 0:
            kelly['total_wins'] = kelly.get('total_wins', 0) + 1
            kelly['total_win_pnl'] = kelly.get('total_win_pnl', 0.0) + pnl
        else:
            kelly['total_losses'] = kelly.get('total_losses', 0) + 1
            kelly['total_loss_pnl'] = kelly.get('total_loss_pnl', 0.0) + abs(pnl)
            
    except Exception as e:
        print(f"Error processing {sym}: {e}")

with open(DRY_RUN_FILE, 'w') as f:
    json.dump({}, f, indent=2)

with open(KELLY_FILE, 'w') as f:
    json.dump(kelly, f, indent=2)

print("\n--- FINAL KELLY STATS AFTER CLOSE ---")
print(json.dumps(kelly, indent=2))

prop = {
  'current_balance': 5000.00,
  'today_start_balance': 5000.00,
  'date_str': time.strftime('%Y-%m-%d')
}
with open(PROP_FILE, 'w') as f:
    json.dump(prop, f, indent=2)

print("\nProp balance reset to 5000.")
