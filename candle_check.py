#!/usr/bin/env python3
from binance.client import Client
import os, datetime
from dotenv import load_dotenv
load_dotenv()

client = Client(os.environ.get("BINANCE_API_KEY",""), os.environ.get("BINANCE_API_SECRET",""), testnet=False, ping=False)
k = client.futures_klines(symbol="BNBUSDT", interval="15m", limit=20)

print("="*80)
print(f"{'#':>3} {'Time':>8} {'Open':>10} {'Close':>10} {'Body%':>8} {'Color':>6} {'UpWick%':>8} {'DnWick%':>8} {'Vol':>10}")
print("="*80)

for i, c in enumerate(k):
    ts = datetime.datetime.fromtimestamp(int(c[0])/1000)
    o, h, l, cl, vol = float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])
    body_pct = ((cl - o) / o) * 100
    up_wick = ((h - max(o, cl)) / o) * 100
    dn_wick = ((min(o, cl) - l) / o) * 100
    color = "GREEN" if cl > o else "RED"
    marker = " <<<" if i >= len(k)-4 else ""
    print(f"{i+1:>3} {ts.strftime('%H:%M'):>8} {o:>10.2f} {cl:>10.2f} {body_pct:>+8.3f}% {color:>6} {up_wick:>7.3f}% {dn_wick:>7.3f}% {vol:>10.0f}{marker}")

# Buyer/Seller analysis
print("\n" + "="*80)
print("BUYER vs SELLER ANALYSIS (last 7 completed candles):")
buy_vol = sum(float(c[5]) for c in k[-8:-1] if float(c[4]) > float(c[1]))
sell_vol = sum(float(c[5]) for c in k[-8:-1] if float(c[4]) <= float(c[1]))
total = buy_vol + sell_vol
print(f"  Buy Vol:  {buy_vol:>12.0f} ({buy_vol/total*100:.1f}%)")
print(f"  Sell Vol: {sell_vol:>12.0f} ({sell_vol/total*100:.1f}%)")

print("\nRECENT 3 candles:")
r_buy = sum(float(c[5]) for c in k[-4:-1] if float(c[4]) > float(c[1]))
r_sell = sum(float(c[5]) for c in k[-4:-1] if float(c[4]) <= float(c[1]))
r_total = r_buy + r_sell
if r_total > 0:
    print(f"  Buy Vol:  {r_buy:>12.0f} ({r_buy/r_total*100:.1f}%)")
    print(f"  Sell Vol: {r_sell:>12.0f} ({r_sell/r_total*100:.1f}%)")
