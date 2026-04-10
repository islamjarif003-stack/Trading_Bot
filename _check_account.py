"""Quick Binance account health check"""
from binance.client import Client
from dotenv import load_dotenv
from datetime import datetime
import os

load_dotenv()
client = Client(os.environ['BINANCE_API_KEY'], os.environ['BINANCE_API_SECRET'], testnet=True, requests_params={"timeout": 20})

# Open Positions
print("=" * 60)
print("  OPEN POSITIONS")
print("=" * 60)
found = False
positions = client.futures_position_information()
for p in positions:
    amt = float(p['positionAmt'])
    if amt != 0:
        found = True
        pnl = float(p['unRealizedProfit'])
        side = "LONG" if amt > 0 else "SHORT"
        print(f"  {p['symbol']} | {side} | Qty: {amt} | PnL: ${pnl:.2f}")
if not found:
    print("  No open positions")

# Open Orders
print()
print("=" * 60)
print("  OPEN ORDERS")
print("=" * 60)
orders = client.futures_get_open_orders()
if orders:
    for o in orders:
        print(f"  {o['symbol']} | {o['side']} {o['type']} | Price: {o['price']} | Qty: {o['origQty']}")
else:
    print("  No open orders")

# Recent Orders History
print()
print("=" * 60)
print("  ORDER HISTORY (Last 5 per coin)")
print("=" * 60)
for sym in ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT']:
    try:
        all_orders = client.futures_get_all_orders(symbol=sym, limit=5)
        for o in all_orders:
            ts = datetime.fromtimestamp(o['time']/1000).strftime('%m-%d %H:%M:%S')
            print(f"  {ts} | {sym} | {o['side']} {o['type']} | Status: {o['status']} | Price: {o['price']} | Qty: {o['origQty']}")
    except Exception as e:
        print(f"  {sym}: Error - {e}")

# Recent Trades
print()
print("=" * 60)
print("  RECENT TRADES (with PnL)")
print("=" * 60)
for sym in ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT']:
    try:
        trades = client.futures_account_trades(symbol=sym, limit=5)
        for t in trades:
            pnl = float(t['realizedPnl'])
            if abs(pnl) >= 0.0000: # Show all trades
                ts = datetime.fromtimestamp(t['time']/1000).strftime('%m-%d %H:%M:%S')
                print(f"  {ts} | {sym} | {t['side']} | Qty: {t['qty']} | PnL: ${pnl:.4f}")
    except Exception as e:
        pass

# Account Balance
print()
print("=" * 60)
print("  ACCOUNT BALANCE")
print("=" * 60)
account = client.futures_account()
print(f"  Total Balance: ${float(account['totalWalletBalance']):.2f}")
print(f"  Available:     ${float(account['availableBalance']):.2f}")
print(f"  Unrealized:    ${float(account['totalUnrealizedProfit']):.2f}")
print("=" * 60)
