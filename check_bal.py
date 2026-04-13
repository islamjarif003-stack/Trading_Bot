import os
from dotenv import load_dotenv
from binance.client import Client
load_dotenv('/opt/trading-bot/.env')
c = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'), testnet=False, ping=False)

balances = c.futures_account_balance()
b = [x for x in balances if x['asset'] == 'USDT']
if b:
    print(f"USDT Balance: {b[0]['balance']}")
    print(f"Available Balance: {b[0]['availableBalance']}")
