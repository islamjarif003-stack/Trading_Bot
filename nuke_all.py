#!/usr/bin/env python3
"""Close ALL open positions for $1 Sniper Challenge fresh start."""
import os, time
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()
c = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'), testnet=True)
c.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
st = c.futures_time()
c.timestamp_offset = st['serverTime'] - int(time.time()*1000)

# Get all positions
positions = c.futures_position_information()
closed = 0

for p in positions:
    amt = float(p.get('positionAmt', 0))
    if amt != 0:
        symbol = p['symbol']
        side = 'SELL' if amt > 0 else 'BUY'
        qty = abs(amt)
        
        # Cancel all orders first
        try:
            c.futures_cancel_all_open_orders(symbol=symbol)
            print(f"  Orders cancelled for {symbol}")
        except Exception as e:
            print(f"  Cancel error {symbol}: {e}")
        
        # Close position
        try:
            r = c.futures_create_order(
                symbol=symbol, side=side, type='MARKET',
                quantity=qty, reduceOnly=True
            )
            pnl = float(p.get('unRealizedProfit', 0))
            print(f"✅ CLOSED {symbol}: {side} {qty} | PnL: ${pnl:+.2f}")
            closed += 1
        except Exception as e:
            print(f"❌ Failed to close {symbol}: {e}")

print(f"\n{'='*50}")
print(f"Total positions closed: {closed}")
print(f"Account is now CLEAN for $1 Sniper Challenge! 🎯")
