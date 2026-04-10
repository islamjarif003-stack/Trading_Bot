#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════
  🐋 WHALE WALL DETECTOR v2.0 — Multi-Coin DOM Tracker
  Monitors 5 Coins Order Book for massive resting limit orders.
  NO TRADES. NO RISK. Pure observation.
═══════════════════════════════════════════════════════════════════
"""

import json
import time
import threading
import websocket
import requests

# ─── CONFIGURATION ──────────────────────────────────────────────
SYMBOLS = {
    "btcusdt": {"name": "BTCUSDT", "whale_qty": 15.0,  "whale_usd": 1_000_000},
    "ethusdt": {"name": "ETHUSDT", "whale_qty": 200.0,  "whale_usd": 500_000},
    "bnbusdt": {"name": "BNBUSDT", "whale_qty": 2000.0, "whale_usd": 500_000},
    "solusdt": {"name": "SOLUSDT", "whale_qty": 5000.0, "whale_usd": 500_000},
    "xrpusdt": {"name": "XRPUSDT", "whale_qty": 500000, "whale_usd": 500_000},
}

PRICE_RANGE_PCT = 0.2                    # Only track levels within ±0.2% of mid
HEARTBEAT_INTERVAL = 10                  # Print heartbeat every N seconds
RECONNECT_DELAY = 5                      # Seconds before reconnect on error
COOLDOWN_PER_WHALE = 30                  # Don't re-alert same whale for N seconds

# ─── STATE ──────────────────────────────────────────────────────
start_time = time.time()
message_count = 0
last_heartbeat = time.time()
whale_cooldowns = {}                     # {"SYMBOL:price": last_alert_time}
latest_mid = {}                          # {"BTCUSDT": 71500.0, ...}
latest_imb = {}                          # {"BTCUSDT": {"bid5": 10, "ask5": 5, "imb": 2.0}}


def format_usd(amount):
    if amount >= 1_000_000:
        return f"${amount/1_000_000:.2f}M"
    elif amount >= 1_000:
        return f"${amount/1_000:.1f}K"
    return f"${amount:.0f}"


def format_qty(qty, symbol_name):
    if qty >= 1_000_000:
        return f"{qty/1_000_000:.1f}M"
    elif qty >= 1_000:
        return f"{qty/1_000:.1f}K"
    return f"{qty:.2f}"


def check_for_whales(symbol_key, bids, asks, mid_price):
    global whale_cooldowns
    now = time.time()
    cfg = SYMBOLS[symbol_key]
    name = cfg["name"]
    whale_qty = cfg["whale_qty"]
    whale_usd = cfg["whale_usd"]
    
    price_range = mid_price * (PRICE_RANGE_PCT / 100.0)
    low_bound = mid_price - price_range
    high_bound = mid_price + price_range
    
    # Check BID side (buy walls)
    for price_str, qty_str in bids:
        price = float(price_str)
        qty = float(qty_str)
        if low_bound <= price <= high_bound:
            usd_value = price * qty
            if qty >= whale_qty or usd_value >= whale_usd:
                cooldown_key = f"{name}:{round(price, 2)}"
                if now - whale_cooldowns.get(cooldown_key, 0) > COOLDOWN_PER_WHALE:
                    whale_cooldowns[cooldown_key] = now
                    dist_pct = ((mid_price - price) / mid_price) * 100
                    _print_whale(name, "BUY", "SUPPORT", price, qty, usd_value, dist_pct, mid_price)
    
    # Check ASK side (sell walls)
    for price_str, qty_str in asks:
        price = float(price_str)
        qty = float(qty_str)
        if low_bound <= price <= high_bound:
            usd_value = price * qty
            if qty >= whale_qty or usd_value >= whale_usd:
                cooldown_key = f"{name}:{round(price, 2)}"
                if now - whale_cooldowns.get(cooldown_key, 0) > COOLDOWN_PER_WHALE:
                    whale_cooldowns[cooldown_key] = now
                    dist_pct = ((price - mid_price) / mid_price) * 100
                    _print_whale(name, "SELL", "RESISTANCE", price, qty, usd_value, dist_pct, mid_price)
    
    # Cleanup old cooldowns
    if len(whale_cooldowns) > 200:
        whale_cooldowns = {k: v for k, v in whale_cooldowns.items() if now - v < COOLDOWN_PER_WHALE * 2}


def _print_whale(name, side, wall_type, price, qty, usd, dist_pct, mid):
    emoji = "🟢" if side == "BUY" else "🔴"
    print()
    print("=" * 72)
    print(f"  🐋🚨 [{name}] WHALE {wall_type} WALL DETECTED!")
    print(f"  {emoji} Side     : {side} WALL")
    print(f"  💰 Size     : {format_qty(qty, name)} ({format_usd(usd)})")
    print(f"  📍 Price    : ${price:,.4f}")
    print(f"  📏 Distance : {dist_pct:.3f}% from mid (${mid:,.2f})")
    print(f"  ⏰ Time     : {time.strftime('%H:%M:%S')}")
    print("=" * 72)
    print()


# ─── WEBSOCKET HANDLERS ────────────────────────────────────────

def on_message(ws, message):
    global last_heartbeat, message_count
    
    try:
        data = json.loads(message)
        
        # Combined stream format: {"stream": "btcusdt@depth20@100ms", "data": {...}}
        stream = data.get("stream", "")
        payload = data.get("data", data)
        
        # Extract symbol from stream name
        symbol_key = stream.split("@")[0] if "@" in stream else ""
        if symbol_key not in SYMBOLS:
            return
        
        bids = payload.get("b", [])
        asks = payload.get("a", [])
        
        if not bids or not asks:
            return
        
        message_count += 1
        name = SYMBOLS[symbol_key]["name"]
        
        # Calculate mid price
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        mid_price = (best_bid + best_ask) / 2.0
        latest_mid[name] = mid_price
        
        # DOM imbalance for top 5
        total_bid_qty = sum(float(b[1]) for b in bids[:5])
        total_ask_qty = sum(float(a[1]) for a in asks[:5])
        imb = total_bid_qty / total_ask_qty if total_ask_qty > 0 else 0
        latest_imb[name] = {"bid5": total_bid_qty, "ask5": total_ask_qty, "imb": imb}
        
        # Check for whale walls
        check_for_whales(symbol_key, bids, asks, mid_price)
        
        # Heartbeat (print combined status for all coins)
        now = time.time()
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            elapsed = now - start_time
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            
            print(f"\n  💓 Heartbeat │ Msgs: {message_count} │ Up: {mins}m{secs}s")
            for sym_name in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]:
                if sym_name in latest_mid and sym_name in latest_imb:
                    m = latest_mid[sym_name]
                    d = latest_imb[sym_name]
                    pressure = "🟢 BUY" if d["imb"] > 1.2 else "🔴 SELL" if d["imb"] < 0.8 else "⚖️ BAL"
                    print(
                        f"     {sym_name:8s} │ ${m:>10,.2f} │ "
                        f"Bid5: {d['bid5']:>8.1f} │ Ask5: {d['ask5']:>8.1f} │ "
                        f"IMB: {d['imb']:>5.2f}x {pressure}"
                    )
            print()
            last_heartbeat = now
            
    except Exception as e:
        if "JSON" not in str(e):
            print(f"  ⚠ Error: {e}")


def on_error(ws, error):
    print(f"  ❌ WebSocket Error: {error}")


def on_close(ws, close_status_code, close_msg):
    print(f"  🔌 WebSocket Closed. Reconnecting in {RECONNECT_DELAY}s...")


def on_open(ws):
    print()
    print("  ✅ WebSocket Connected! Tracking 5 coins...")
    for key, cfg in SYMBOLS.items():
        print(f"     {cfg['name']:8s} │ Whale: ≥{format_qty(cfg['whale_qty'], cfg['name'])} or ≥{format_usd(cfg['whale_usd'])}")
    print(f"  📏 Price range: ±{PRICE_RANGE_PCT}% │ 💓 Heartbeat: {HEARTBEAT_INTERVAL}s")
    print()


# ─── MAIN ───────────────────────────────────────────────────────

def run():
    print()
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║   🐋 WHALE WALL DETECTOR v2.0 — Multi-Coin DOM Tracker        ║")
    print("║   BTCUSDT • ETHUSDT • SOLUSDT • BNBUSDT • XRPUSDT             ║")
    print("║   NO TRADES • NO RISK • Pure Observation                       ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    
    # Build combined stream URL for all 5 coins
    streams = "/".join(f"{sym}@depth20@100ms" for sym in SYMBOLS.keys())
    ws_url = f"wss://fstream.binance.com/stream?streams={streams}"
    
    print(f"\n  📡 Connecting to {len(SYMBOLS)} streams...")
    
    while True:
        try:
            ws = websocket.WebSocketApp(
                ws_url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except KeyboardInterrupt:
            print("\n  🛑 Whale Detector stopped by user.")
            break
        except Exception as e:
            print(f"  ❌ Connection failed: {e}")
        
        print(f"  🔄 Reconnecting in {RECONNECT_DELAY}s...")
        time.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    run()
