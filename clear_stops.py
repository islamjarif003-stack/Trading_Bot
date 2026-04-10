import os
from binance.client import Client
from dotenv import load_dotenv

load_dotenv('/opt/trading-bot/.env')
client = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'), testnet=True)

symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT']
count = 0
for sym in symbols:
    try:
        orders = client.futures_get_open_orders(symbol=sym)
        for o in orders:
            if 'STOP' in o['type'] and 'TAKE_PROFIT' not in o['type']:
                print(f"Cancelling STOP order on {sym}: {o['orderId']}")
                client.futures_cancel_order(symbol=sym, orderId=o['orderId'])
                count += 1
    except Exception as e:
        print(f"Error on {sym}: {e}")
print(f'Done. Cleared {count} stuck stop orders.')
