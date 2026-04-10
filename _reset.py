import os
import subprocess
from binance.client import Client

print("1. Closing all Binance positions...")
API_KEY    = "lf26O58uVx3WWgforjjyzBC5CjMoIVMR70huZPI8vDq00TCYvKYnjIfdvi5HwacY"
API_SECRET = "Y9iYOxF6jCgt8dz6AfFXvKSlJaFMpmoxAeN8konc3lKMwI2BWdgabNbYOHRN2MQ4"

try:
    client = Client(API_KEY, API_SECRET, testnet=True)
    for p in client.futures_position_information():
        amt = float(p['positionAmt'])
        if abs(amt) > 0:
            client.futures_cancel_all_open_orders(symbol=p['symbol'])
            side = 'SELL' if amt > 0 else 'BUY'
            client.futures_create_order(symbol=p['symbol'], side=side, type='MARKET', quantity=abs(amt), reduceOnly=True)
            print(f"Closed {p['symbol']}")
except Exception as e:
    print(f"Binance Error: {e}")

print("\n2. Connecting to VPS to kill old bots and clear history...")
vps_commands = """
pkill -9 -f main.py
rm -f /root/btc-bot/pnl_history_*.json
rm -f /root/btc-bot/live_state_*.json
echo '[]' > /root/btc-bot/ml_trade_memory.json
rm -f /root/btc-bot/bot.log /root/btc-bot/trading_bot.log
screen -wipe
screen -dmS bot bash -c 'cd /root/btc-bot && python3 main.py > bot.log 2>&1'
"""

print(f"Running full reset sequence on VPS in one connection...")
subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no", "root@163.227.239.196", vps_commands])

print("\n✅ All done! The bot has been perfectly reset. Please reload your dashboard page.")
