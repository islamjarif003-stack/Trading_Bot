"""Full trade report — all coins, last 48 hours"""
from binance.client import Client
from dotenv import load_dotenv
import os, time

load_dotenv()
c = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'))
c.API_URL = 'https://fapi.binance.com/fapi'

# Get all trades from last 48 hours
start_time = int((time.time() - 48*3600) * 1000)

# Check account
acct = c.futures_account()
balance = float(acct['totalWalletBalance'])
available = float(acct['availableBalance'])
unrealized = float(acct['totalUnrealizedProfit'])

# Get open positions
pos = c.futures_position_information()
open_pos = [p for p in pos if float(p.get('positionAmt', 0)) != 0]

# Get all recent trades across all symbols
all_symbols = set()
income = c.futures_income_history(incomeType='REALIZED_PNL', startTime=start_time, limit=100)
for inc in income:
    all_symbols.add(inc['symbol'])

print("=" * 70)
print(f"  FULL TRADE REPORT — Last 48 Hours")
print(f"  Balance: ${balance:.2f} | Available: ${available:.2f} | Unrealized: ${unrealized:.2f}")
print("=" * 70)

# Open positions
print(f"\n📊 OPEN POSITIONS: {len(open_pos)}")
for p in open_pos:
    amt = float(p['positionAmt'])
    entry = float(p['entryPrice'])
    mark = float(p['markPrice'])
    pnl = float(p['unRealizedProfit'])
    side = "LONG" if amt > 0 else "SHORT"
    print(f"  {p['symbol']:12s} | {side:5s} | Qty: {abs(amt):.4f} | Entry: ${entry:.4f} | Mark: ${mark:.4f} | PnL: ${pnl:+.4f}")

# Trade history per symbol
print(f"\n📈 REALIZED TRADES:")
total_pnl = 0
total_fees = 0
trade_count = 0
wins = 0
losses = 0

for sym in sorted(all_symbols):
    try:
        trades = c.futures_account_trades(symbol=sym, startTime=start_time, limit=50)
        if not trades:
            continue
        
        sym_pnl = sum(float(t['realizedPnl']) for t in trades)
        sym_fee = sum(float(t['commission']) for t in trades)
        sym_net = sym_pnl - sym_fee
        n_trades = len([t for t in trades if float(t['realizedPnl']) != 0])
        
        if n_trades == 0:
            continue
        
        total_pnl += sym_pnl
        total_fees += sym_fee
        trade_count += n_trades
        
        result = "✅ WIN" if sym_net > 0 else "❌ LOSS"
        if sym_net > 0:
            wins += 1
        else:
            losses += 1
        
        print(f"  {sym:12s} | PnL: ${sym_pnl:+.4f} | Fee: ${sym_fee:.4f} | Net: ${sym_net:+.4f} | {result} | Fills: {len(trades)}")
    except Exception as e:
        print(f"  {sym:12s} | Error: {e}")

print(f"\n{'=' * 70}")
print(f"  SUMMARY")
print(f"{'=' * 70}")
print(f"  Total Realized PnL : ${total_pnl:+.4f}")
print(f"  Total Fees         : ${total_fees:.4f}")
print(f"  NET PnL            : ${total_pnl - total_fees:+.4f}")
print(f"  Trades             : {trade_count} ({wins} wins, {losses} losses)")
print(f"  Win Rate           : {wins/(wins+losses)*100:.0f}%" if (wins+losses) > 0 else "  Win Rate: N/A")
print(f"  Starting Balance   : $16.54")
print(f"  Current Balance    : ${balance:.2f}")
print(f"  Total Change       : ${balance - 16.54:+.2f}")
