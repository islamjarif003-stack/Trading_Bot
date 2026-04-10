from binance.client import Client

client = Client(
    "lf26O58uVx3WWgforjjyzBC5CjMoIVMR70huZPI8vDq00TCYvKYnjIfdvi5HwacY",
    "Y9iYOxF6jCgt8dz6AfFXvKSlJaFMpmoxAeN8konc3lKMwI2BWdgabNbYOHRN2MQ4",
    testnet=True
)

print("=" * 60)
print("  BINANCE FUTURES TESTNET — POSITION CHECK")
print("=" * 60)

positions = client.futures_position_information()
found = False
for p in positions:
    amt = float(p['positionAmt'])
    if abs(amt) > 0:
        found = True
        side = "BUY (LONG)" if amt > 0 else "SELL (SHORT)"
        entry = float(p['entryPrice'])
        mark = float(p['markPrice'])
        pnl = float(p['unRealizedProfit'])
        print(f"\n  Symbol : {p['symbol']}")
        print(f"  Side   : {side}")
        print(f"  Qty    : {abs(amt)}")
        print(f"  Entry  : ${entry:.2f}")
        print(f"  Mark   : ${mark:.2f}")
        print(f"  PnL    : ${pnl:.2f}")

if not found:
    print("\n  ✅ No open positions! Account is clean.")

# Check open orders
orders = client.futures_get_open_orders()
if orders:
    print(f"\n  ⚠ Open Orders: {len(orders)}")
    for o in orders:
        print(f"    {o['symbol']} {o['side']} {o['type']} qty:{o['origQty']} stop:{o.get('stopPrice','N/A')}")
else:
    print("  ✅ No open orders.")

# Balance
balances = client.futures_account_balance()
for b in balances:
    if b['asset'] == 'USDT':
        print(f"\n  💰 Balance: ${float(b['balance']):.2f} USDT (Available: ${float(b['availableBalance']):.2f})")

print("\n" + "=" * 60)
