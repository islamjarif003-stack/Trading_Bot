from binance.client import Client
import os
from dotenv import load_dotenv
load_dotenv('/opt/trading-bot/.env')
c = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'))
coins = ['ETHUSDT','LABUSDT','LINKUSDT','EDGEUSDT','TAOUSDT','RAYSOLUSDT','HYPEUSDT','MOVRUSDT','LTCUSDT','ENSOUSDT','AXSUSDT']
for s in coins:
    try:
        p = c.futures_symbol_ticker(symbol=s)
        print(f'{s}: {p["price"]}')
    except Exception as e:
        print(f'{s}: ERROR {e}')
