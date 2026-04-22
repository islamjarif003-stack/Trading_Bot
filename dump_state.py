#!/usr/bin/env python3
"""Dump the structure of a live_state JSON file.
★ AUDIT FIX: Now accepts symbol as CLI argument instead of hardcoded VPS path.

Usage:
    python dump_state.py BTCUSDT
    python dump_state.py SOLUSDT
"""
import json, sys, os

if len(sys.argv) < 2:
    print("Usage: python dump_state.py <SYMBOL>")
    print("Example: python dump_state.py BTCUSDT")
    sys.exit(1)

symbol = sys.argv[1].upper()
script_dir = os.path.dirname(os.path.abspath(__file__))
state_file = os.path.join(script_dir, f"live_state_{symbol}.json")

if not os.path.exists(state_file):
    print(f"❌ State file not found: {state_file}")
    sys.exit(1)

with open(state_file) as f:
    d = json.load(f)

print(f"═══ State Dump for {symbol} ═══")
print("Top keys:", list(d.keys()))

pos = d.get("position", {})
print("\nPosition data:")
print(json.dumps(pos, indent=2))

sl = d.get("sl_tp", d.get("stop_loss", d.get("trailing", {})))
print("\nSL/TP data:")
print(json.dumps(sl, indent=2) if sl else "No SL/TP key found")

# Check for any key containing "entry" or "sl" or "trail"
for k, v in d.items():
    if any(x in k.lower() for x in ['entry', 'sl', 'trail', 'phase', 'break']):
        print(f"\n{k}:")
        if isinstance(v, dict):
            print(json.dumps(v, indent=2))
        else:
            print(v)
