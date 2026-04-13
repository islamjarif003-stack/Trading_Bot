import os
from dotenv import load_dotenv
load_dotenv()
from binance.client import Client

def check():
    try:
        client = Client(os.environ.get('BINANCE_API_KEY'), os.environ.get('BINANCE_API_SECRET'), testnet=True)
        positions = client.futures_position_information()
        active = [p for p in positions if float(p['positionAmt']) != 0]
        if not active:
            print("NO ACTIVE POSITIONS")
        for p in active:
            sym = p['symbol']
            amt = float(p['positionAmt'])
            entry = float(p['entryPrice'])
            pnl = float(p['unRealizedProfit'])
            mark = float(p['markPrice'])
            side = 'LONG' if amt > 0 else 'SHORT'
            print(f"✅ {sym} {side} | Entry: {entry:.4f} | Mark: {mark:.4f} | PNL: ${pnl:.3f}")
    except Exception as e:
        print("Error:", e)

if __name__ == '__main__':
    check()
