"""Patch dashboard.py to show 5 coins instead of 3."""
path = "/opt/trading-bot/dashboard.py"
with open(path, "r") as f:
    content = f.read()

old = 'SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]'
new = 'SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]'

if old in content:
    content = content.replace(old, new)
    with open(path, "w") as f:
        f.write(content)
    print("PATCHED: 3 coins -> 5 coins")
elif new in content:
    print("ALREADY PATCHED: 5 coins found")
else:
    print("ERROR: Could not find SYMBOLS line")
