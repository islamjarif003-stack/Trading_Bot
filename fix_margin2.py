import os
from dotenv import load_dotenv
from binance.client import Client

load_dotenv('/opt/trading-bot/.env')
client = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'), testnet=False, ping=False)

response = client.futures_change_multi_assets_mode(multiAssetsMargin='false')
print(f"Response: {response}")
