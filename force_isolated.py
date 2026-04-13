import os
import time
from dotenv import load_dotenv
from binance.client import Client

load_dotenv('/opt/trading-bot/.env')
c = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'), testnet=False, ping=False)

print("Fetching exchange info...")
info = c.futures_exchange_info()
symbols = [s['symbol'] for s in info['symbols'] if s['status'] == 'TRADING' and s['contractType'] == 'PERPETUAL' and s['quoteAsset'] == 'USDT']

isolated_count = 0
failed_count = 0

print(f"Setting {len(symbols)} pairs to ISOLATED margin mode...")
for sym in symbols:
    try:
        c.futures_change_margin_type(symbol=sym, marginType='ISOLATED')
        isolated_count += 1
    except Exception as e:
        if '-4046' in str(e) or 'No need to change' in str(e):
            isolated_count += 1
        elif '-4171' in str(e):
            pass # Multi-Assets mode failure?
        else:
            failed_count += 1
            print(f"Error {sym}: {e}")
    time.sleep(0.01) # Avoid rate limit

print(f"Done! {isolated_count} symbols verified as ISOLATED. {failed_count} failed to change.")
