#!/usr/bin/env python3
"""Dump the structure of a live_state JSON file."""
import json, sys
with open('/opt/trading-bot/live_state_LRCUSDT.json') as f:
    d = json.load(f)
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
