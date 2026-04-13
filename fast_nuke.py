#!/usr/bin/env python3
"""Fast cleanup — cancel ALL orders for recent symbols and open positions only."""
import os, time
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()
c = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'), testnet=True)
c.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
st = c.futures_time()
c.timestamp_offset = st['serverTime'] - int(time.time()*1000)

SYMBOLS = ["OLUSDT", "BTCUSDT", "ETHUSDT", "DOGEUSDT", "XRPUSDT", "BCHUSDT", "QUICKUSDT", "BNBUSDT", "LRCUSDT", "MYROUSDT", "BIDUSDT", "VOXELUSDT", "DMCUSDT", "GHSTUSDT", "FISUSDT", "ARIAUSDT", "CUDISUSDT", "REIUSDT", "PORT3USDT", "1000XUSDT", "RAVEUSDT", "EPTUSDT", "TOKENUSDT", "UXLINKUSDT", "PONKEUSDT", "ZRCUSDT", "BASUSDT", "TRADOORUSDT", "AGTUSDT", "AKEUSDT", "RDNTUSDT", "CHESSUSDT", "CYSUSDT", "LABUSDT", "SOONUSDT", "币安人生USDT", "SKATEUSDT", "MAGMAUSDT", "BULLAUSDT", "TNSRUSDT", "SIRENUSDT"]

# Add symbols of open positions
positions = c.futures_position_information()
pos_symbols = [p['symbol'] for p in positions if float(p.get('positionAmt', 0)) != 0]
all_symbols = list(set(SYMBOLS + pos_symbols))

print(f'Checking {len(all_symbols)} symbols for stuck orders...')

count = 0
for sym in all_symbols:
    try:
        orders = c.futures_get_open_orders(symbol=sym)
        if orders:
            print(f'  {sym} has {len(orders)} open orders. Cancelling...')
            c.futures_cancel_all_open_orders(symbol=sym)
            count += len(orders)
    except Exception as e:
        if '-2011' not in str(e): # Ignore Unknown order
            print(f"  Failed for {sym}: {e}")

print(f'\nDone. Cancelled {count} orders.')
