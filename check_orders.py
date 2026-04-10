import os
from binance.client import Client
from dotenv import load_dotenv

load_dotenv('/opt/trading-bot/.env')
client = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'), testnet=True)

symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT']
total = 0
for sym in symbols:
    try:
        orders = client.futures_get_open_orders(symbol=sym, recvWindow=60000)
        if orders:
            print(f"--- {sym} Open Orders ---")
            for o in orders:
                print(f"Type: {o['type']} | Side: {o['side']} | ID: {o['orderId']} | StopPrice: {o.get('stopPrice', 'N/A')}")
                total += 1
    except Exception as e:
        print(f"Error on {sym}: {e}")
        
print(f"\nTotal Open Orders: {total}")
