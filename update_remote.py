import sys

def main():
    try:
        with open('/opt/trading-bot/main.py', 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print("Failed to read file:", e)
        return

    s1 = 'log.info(f"⏳  [{symbol}] GTC PENDING — Waiting for pullback to ${limit_price}. Auto-cancel in 12 candles (1h).")'
    r1 = s1 + '\n                    send_telegram_alert(f"⏳ <b>PENDING LIMIT ORDER</b>\\nCoin: {symbol}\\nSide: {signal}\\nTarget Entry: ${limit_price:.4f}\\nStatus: Waiting for price pullback.")'
    if s1 in content and r1 not in content:
        content = content.replace(s1, r1)
        print("Patched Limit Order PENDING notice.")
    else:
        print("Limit Order PENDING notice not found or already patched.")

    s2 = 'log.info(f"📊  [{symbol}] Open {pos[\'side\']} position detected.")\n                        if'
    r2 = 'log.info(f"📊  [{symbol}] Open {pos[\'side\']} position detected.")\n                        send_telegram_alert(f"🚀 <b>TRADE ACTIVATED (Limit Filled)</b>\\nCoin: {symbol}\\nSide: {pos[\'side\']}\\nActual Entry: ${pos[\'entry_price\']:.4f}\\nStatus: Order hit and active.\\nTrailing SL logic activated.")\n                        if'
    if s2 in content and "TRADE ACTIVATED" not in content:
        content = content.replace(s2, r2)
        print("Patched Limit Order FILLED notice.")
    else:
        print("Limit Order FILLED notice not found or already patched.")

    try:
        with open('/opt/trading-bot/main.py', 'w', encoding='utf-8') as f:
            f.write(content)
        print("File updated successfully.")
    except Exception as e:
        print("Failed to write to file:", e)

if __name__ == "__main__":
    main()
