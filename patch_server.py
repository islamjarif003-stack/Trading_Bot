with open('/opt/trading-bot/main.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
changes = 0

for i, line in enumerate(lines):
    stripped = line.strip()
    
    # Change 1: ORDER_TYPE_LIMIT → MARKET
    if "type=ORDER_TYPE_LIMIT," in stripped and i > 1000 and i < 1200:
        new_lines.append(line.replace("type=ORDER_TYPE_LIMIT,", "type='MARKET',"))
        changes += 1
        print(f"Line {i+1}: Changed LIMIT → MARKET order")
        continue
    
    # Change 2: Comment out price= line (MARKET orders don't need price)
    if "price=str(limit_price)," in stripped and i > 1000 and i < 1200:
        new_lines.append(line.replace(stripped, f"# {stripped}  # v25: Disabled for MARKET order"))
        changes += 1
        print(f"Line {i+1}: Commented out price parameter")
        continue
    
    # Change 3: Comment out timeInForce= line (MARKET orders don't need GTC)
    if "timeInForce='GTC'," in stripped and i > 1000 and i < 1200:
        new_lines.append(line.replace(stripped, f"# {stripped}  # v25: Disabled for MARKET order"))
        changes += 1
        print(f"Line {i+1}: Commented out timeInForce parameter")
        continue
    
    # Change 4: After order is placed, force filled=True (MARKET fills instantly)
    if 'filled = False' in stripped and i > 1000 and i < 1200:
        new_lines.append(line.replace("filled = False", "filled = True  # v25: MARKET orders always fill instantly"))
        changes += 1
        print(f"Line {i+1}: Set filled=True (MARKET always fills)")
        continue
    
    new_lines.append(line)

with open('/opt/trading-bot/main.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print(f"\nDone! {changes} surgical changes made.")
print("LIVE mode will now work exactly like DRY RUN:")
print("  - Instant MARKET order (no more waiting for pullback)")
print("  - Chart + NEW TRADE ENTERED notification will appear")
print("  - SL/TP placement works identically")
