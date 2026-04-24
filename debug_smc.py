"""Deep debug: WHY is BOS/CHOCH not detecting?"""
import numpy as np, os
from binance.client import Client
from dotenv import load_dotenv

load_dotenv()
client = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'))
client.API_URL = 'https://fapi.binance.com/fapi'

m5 = client.futures_klines(symbol='BTCUSDT', interval='5m', limit=200)
highs = np.array([float(k[2]) for k in m5])
lows = np.array([float(k[3]) for k in m5])
closes = np.array([float(k[4]) for k in m5])
n = len(closes)

# Swing points (lookback=3)
sh, sl = [], []
for i in range(3, n - 3):
    wh = highs[i-3:i+4]
    wl = lows[i-3:i+4]
    if highs[i] == np.max(wh) and (highs[i] > highs[i-1] or highs[i] > highs[i+1]):
        sh.append((i, float(highs[i])))
    if lows[i] == np.min(wl) and (lows[i] < lows[i-1] or lows[i] < lows[i+1]):
        sl.append((i, float(lows[i])))

print(f'Total SH: {len(sh)}, SL: {len(sl)}, Price: ${closes[-1]:.2f}')
print(f'\n=== LAST 8 SWING HIGHS ===')
for idx, price in sh[-8:]:
    print(f'  SH bar {idx} (age {n-1-idx} bars = {(n-1-idx)*5}min): ${price:.2f}')
print(f'\n=== LAST 8 SWING LOWS ===')
for idx, price in sl[-8:]:
    print(f'  SL bar {idx} (age {n-1-idx} bars = {(n-1-idx)*5}min): ${price:.2f}')

# Trend
l2sh = sh[-2:]
l2sl = sl[-2:]
sh_r = l2sh[-1][1] > l2sh[-2][1]
sl_r = l2sl[-1][1] > l2sl[-2][1]
trend = 'UP' if sh_r and sl_r else ('DOWN' if not sh_r and not sl_r else 'MIXED')
print(f'\nTrend: {trend} (SH {"HH" if sh_r else "LH"}, SL {"HL" if sl_r else "LL"})')

# Check breaks
print(f'\n{"="*60}')
print(f'WHY NO STRUCTURE BREAK?')
print(f'{"="*60}')

if trend in ('DOWN', 'MIXED'):
    last_sh_idx, last_sh_price = sh[-1]
    bars_after = n - 1 - last_sh_idx
    if bars_after > 0:
        max_close = max(closes[last_sh_idx+1:])
        gap = last_sh_price - max_close
        print(f'\n  CHOCH_BULL: Need CLOSE > ${last_sh_price:.2f} (last SH @ bar {last_sh_idx})')
        print(f'  Bars available after SH: {bars_after}')
        print(f'  Best close after SH: ${max_close:.2f}')
        print(f'  GAP remaining: ${gap:.2f} ({gap/last_sh_price*100:.3f}%)')
        print(f'  >>> {"ALMOST!" if gap/last_sh_price < 0.005 else "TOO FAR"}')

if trend in ('UP', 'MIXED'):
    last_sl_idx, last_sl_price = sl[-1]
    bars_after = n - 1 - last_sl_idx
    if bars_after > 0:
        min_close = min(closes[last_sl_idx+1:])
        gap = min_close - last_sl_price
        print(f'\n  CHOCH_BEAR: Need CLOSE < ${last_sl_price:.2f} (last SL @ bar {last_sl_idx})')
        print(f'  Bars available after SL: {bars_after}')
        print(f'  Best close after SL: ${min_close:.2f}')
        print(f'  GAP remaining: ${gap:.2f} ({gap/last_sl_price*100:.3f}%)')
        print(f'  >>> {"ALMOST!" if gap/last_sl_price < 0.005 else "TOO FAR"}')

# THE KEY PROBLEM
print(f'\n{"="*60}')
print(f'ROOT CAUSE ANALYSIS')
print(f'{"="*60}')
last_sh_age = n - 1 - sh[-1][0]
last_sl_age = n - 1 - sl[-1][0]
print(f'  Last Swing High: bar {sh[-1][0]} of {n-1} (only {last_sh_age} bars ago)')
print(f'  Last Swing Low:  bar {sl[-1][0]} of {n-1} (only {last_sl_age} bars ago)')
print(f'')
print(f'  PROBLEM: lookback=3 creates swing points that are')
print(f'  only 3 bars from the current bar. The current bar')
print(f'  (199) needs to CLOSE above/below the swing level,')
print(f'  but the swing was just formed {last_sh_age} bars ago!')
print(f'')
print(f'  With lookback=3: Swing at bar 196 = confirmed at bar 199')
print(f'  = current bar IS the confirmation bar, no time to break!')
print(f'')
print(f'  SOLUTION: Use lookback=2 instead of 3, OR check if')
print(f'  ANY bar between the 2nd-last and last swing broke')
print(f'  the PREVIOUS swing level (not just after last swing)')
