import os
from binance.client import Client

key = os.environ["BINANCE_API_KEY"]
secret = os.environ["BINANCE_API_SECRET"]
print(f"Key length: {len(key)} chars")
print(f"Secret length: {len(secret)} chars")
print(f"Key preview: {key[:8]}...{key[-4:]}")

c = Client(key, secret, testnet=True)
print(f"FUTURES_URL: {c.FUTURES_URL}")
print(f"API_URL: {c.API_URL}")

# Try unauthenticated futures call first
try:
    t = c.futures_ticker(symbol="BTCUSDT")
    print(f"Futures ticker OK: {t['lastPrice']}")
except Exception as e:
    print(f"Futures ticker FAIL: {e}")

# Try authenticated call
try:
    b = c.futures_account_balance()
    print(f"Balance OK")
except Exception as e:
    print(f"Balance FAIL: {e}")
