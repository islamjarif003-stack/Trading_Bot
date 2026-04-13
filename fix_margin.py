import os
from dotenv import load_dotenv
from binance.client import Client

load_dotenv('/opt/trading-bot/.env')
client = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'), testnet=False, ping=False)

try:
    print("Checking current status...")
    status = client.futures_get_multi_assets_mode()
    print(f"Current Multi-Asset Mode: {status}")
    if status.get('multiAssetsMargin'):
        print("Disabling Multi-Assets mode...")
        # False means Single-Asset Mode
        response = client.futures_change_multi_assets_mode(multiAssetsMargin="false")
        print(f"Response: {response}")
    else:
        print("Already in Single-Asset Mode.")
except Exception as e:
    print(f"Error: {e}")
