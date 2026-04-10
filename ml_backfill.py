"""
================================================================================
  ml_backfill.py — Historical Data Backfill for ML Training (VectorBT Edition)
  
  Downloads 6 months of Binance klines, runs VectorBT simulation with 
  the bot's actual ATR SL/TP (1:2 R:R), and generates labelled 22-feature 
  training data for the XGBoost ML model.
  
  Usage:
    python ml_backfill.py
  
  Output:
    ml_backfill_data.json — Ready to load via MLFilterV2.load_backfill()
================================================================================
"""

import os
import json
import time
import math
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from binance.client import Client
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ.get("BINANCE_API_KEY", "")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "")

# ─── CONFIGURATION ──────────────────────────────────────────────────────────
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
INTERVAL = Client.KLINE_INTERVAL_5MINUTE
MONTHS_BACK = 3
SL_ATR_MULT = 1.5
TP_ATR_MULT = 3.0
RSI_PERIOD = 14
ATR_PERIOD = 14
ADX_PERIOD = 14

OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml_backfill_data.json")


# ═════════════════════════════════════════════════════════════════════════════
# ██  INDICATOR CALCULATIONS                                                ██
# ═════════════════════════════════════════════════════════════════════════════

def calculate_rsi(closes, period=14):
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100.0 - (100.0 / (1.0 + rs))


def calculate_atr(highs, lows, closes, period=14):
    prev_close = closes.shift(1)
    tr1 = highs - lows
    tr2 = (highs - prev_close).abs()
    tr3 = (lows - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(alpha=1/period, min_periods=period, adjust=False).mean()


def calculate_adx(highs, lows, closes, period=14):
    prev_high = highs.shift(1)
    prev_low = lows.shift(1)
    prev_close = closes.shift(1)
    plus_dm = highs - prev_high
    minus_dm = prev_low - lows
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    tr1 = highs - lows
    tr2 = (highs - prev_close).abs()
    tr3 = (lows - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_smooth = true_range.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_di_smooth = plus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    minus_di_smooth = minus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_di = 100.0 * (plus_di_smooth / atr_smooth.replace(0, 1e-10))
    minus_di = 100.0 * (minus_di_smooth / atr_smooth.replace(0, 1e-10))
    di_sum = plus_di + minus_di
    di_diff = (plus_di - minus_di).abs()
    dx = 100.0 * (di_diff / di_sum.replace(0, 1e-10))
    return dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()


def calculate_supertrend(highs, lows, closes, period=10, multiplier=3.0):
    """Calculate Supertrend indicator."""
    atr = calculate_atr(highs, lows, closes, period)
    hl2 = (highs + lows) / 2
    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr
    
    supertrend = pd.Series(index=closes.index, dtype=float)
    direction = pd.Series(index=closes.index, dtype=float)
    
    supertrend.iloc[0] = upper_band.iloc[0]
    direction.iloc[0] = -1
    
    for i in range(1, len(closes)):
        if closes.iloc[i] > upper_band.iloc[i-1]:
            direction.iloc[i] = 1
        elif closes.iloc[i] < lower_band.iloc[i-1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i-1]
        
        if direction.iloc[i] == 1:
            supertrend.iloc[i] = max(lower_band.iloc[i], supertrend.iloc[i-1]) if direction.iloc[i-1] == 1 else lower_band.iloc[i]
        else:
            supertrend.iloc[i] = min(upper_band.iloc[i], supertrend.iloc[i-1]) if direction.iloc[i-1] == -1 else upper_band.iloc[i]
    
    return supertrend, direction


def calculate_vwap(highs, lows, closes, volumes):
    """Calculate simple rolling VWAP (20-period approximation)."""
    typical = (highs + lows + closes) / 3
    cum_tp_vol = (typical * volumes).rolling(20).sum()
    cum_vol = volumes.rolling(20).sum()
    return cum_tp_vol / cum_vol.replace(0, 1e-10)


# ═════════════════════════════════════════════════════════════════════════════
# ██  MAIN                                                                   ██
# ═════════════════════════════════════════════════════════════════════════════

def process_symbol(client, symbol, start_date, end_date):
    """Download data and generate backfill entries for one symbol."""
    
    print(f"\n{'─' * 60}")
    print(f"📥  Downloading {symbol} data...")
    print(f"    From: {start_date.strftime('%Y-%m-%d')} → To: {end_date.strftime('%Y-%m-%d')}")
    
    all_klines = []
    current = start_date
    
    while current < end_date:
        next_batch = min(current + timedelta(days=7), end_date)
        start_ms = int(current.timestamp() * 1000)
        end_ms = int(next_batch.timestamp() * 1000)
        
        try:
            klines = client.futures_klines(
                symbol=symbol, interval=INTERVAL,
                startTime=start_ms, endTime=end_ms, limit=1000
            )
            all_klines.extend(klines)
            print(f"    ✓ {current.strftime('%Y-%m-%d')} → {next_batch.strftime('%Y-%m-%d')} ({len(klines)} candles)")
        except Exception as e:
            print(f"    ⚠ Error: {e}. Retrying...")
            time.sleep(2)
            continue
        
        current = next_batch
        time.sleep(0.3)
    
    if not all_klines:
        print(f"    ❌ No data for {symbol}")
        return []
    
    # ── Build DataFrame ─────────────────────────────────────────────
    df = pd.DataFrame(all_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    for col in ["open", "high", "low", "close", "volume", "taker_buy_base"]:
        df[col] = df[col].astype(float)
    
    df = df.drop_duplicates(subset=["open_time"]).reset_index(drop=True)
    print(f"    📊 Total candles: {len(df)}")
    
    # ── Calculate indicators ────────────────────────────────────────
    print(f"    📈 Calculating indicators...")
    
    df["rsi"] = calculate_rsi(df["close"])
    df["atr"] = calculate_atr(df["high"], df["low"], df["close"])
    df["adx"] = calculate_adx(df["high"], df["low"], df["close"])
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["ema8"] = df["close"].ewm(span=8, adjust=False).mean()
    
    # Supertrend
    df["supertrend"], df["st_dir"] = calculate_supertrend(df["high"], df["low"], df["close"])
    
    # VWAP
    df["vwap"] = calculate_vwap(df["high"], df["low"], df["close"], df["volume"])
    
    # Bollinger Bands
    bb_sma = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_upper"] = bb_sma + 2 * bb_std
    df["bb_lower"] = bb_sma - 2 * bb_std
    df["bb_width"] = ((df["bb_upper"] - df["bb_lower"]) / bb_sma * 100)
    
    # Volume ratio
    df["vol_sma"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_sma"].replace(0, 1e-10)
    
    # Taker ratio  
    df["sell_vol"] = df["volume"] - df["taker_buy_base"]
    df["taker_ratio"] = df["taker_buy_base"] / df["sell_vol"].replace(0, 1e-10)
    
    # RSI slope
    df["rsi_slope"] = df["rsi"].diff(3)
    
    # EMA distances
    df["ema_cross_dist"] = (df["close"] - df["ema21"]) / df["close"] * 100
    df["h1_ema_dist"] = (df["close"] - df["ema200"]) / df["close"] * 100
    df["supertrend_dist"] = (df["close"] - df["supertrend"]) / df["close"] * 100
    df["vwap_dist"] = (df["close"] - df["vwap"]) / df["close"] * 100
    
    # ATR %
    df["atr_pct"] = df["atr"] / df["close"] * 100
    
    # Hour encoding
    df["hour"] = pd.to_datetime(df["open_time"], unit="ms").dt.hour
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
    
    # ── Generate signals ────────────────────────────────────────────
    print(f"    🎯 Generating signals...")
    
    backfill_entries = []
    skip_until = 0
    
    for i in range(250, len(df) - 50):
        if i < skip_until:
            continue
        
        row = df.iloc[i]
        
        if pd.isna(row["rsi"]) or pd.isna(row["atr"]) or pd.isna(row["adx"]):
            continue
        
        price = row["close"]
        rsi_val = row["rsi"]
        adx_val = row["adx"]
        atr_val = row["atr"]
        above_ema200 = price > row["ema200"]
        st_bullish = row["st_dir"] == 1
        vol_spike = row["vol_ratio"] > 1.3
        
        signal = None
        score = 0
        
        # ─── BUY conditions ─────────────────────────────────────
        if above_ema200:
            score += 3
            if st_bullish:
                score += 3
            if row["ema8"] > row["ema21"]:
                score += 1
                if row["ema21"] > row["ema50"]:
                    score += 2
            if price < row["vwap"]:
                score += 2
            if vol_spike:
                score += 3
            if row["taker_ratio"] > 1.2:
                score += 2
            if rsi_val < 65 and score >= 8:
                signal = "BUY"
        
        # ─── SELL conditions ────────────────────────────────────
        elif not above_ema200:
            score += 3
            if not st_bullish:
                score += 3
            if row["ema8"] < row["ema21"]:
                score += 1
                if row["ema21"] < row["ema50"]:
                    score += 2
            if price > row["vwap"]:
                score += 2
            if vol_spike:
                score += 3
            if row["taker_ratio"] < 0.8:
                score += 2
            if rsi_val > 35 and score >= 8:
                signal = "SELL"
        
        if signal is None:
            continue
        
        # ── Simulate stepped TP ─────────────────────────────────
        sl_dist = atr_val * SL_ATR_MULT
        tp_dist = atr_val * TP_ATR_MULT
        tp1_dist = atr_val * 1.0
        tp2_dist = atr_val * 2.0
        
        if signal == "BUY":
            sl_price = price - sl_dist
            tp1_price = price + tp1_dist
            tp2_price = price + tp2_dist
            tp3_price = price + tp_dist
        else:
            sl_price = price + sl_dist
            tp1_price = price - tp1_dist
            tp2_price = price - tp2_dist
            tp3_price = price - tp_dist
        
        outcome = 0
        tp1_hit = False
        tp2_hit = False
        
        for j in range(i + 1, min(i + 60, len(df))):
            future = df.iloc[j]
            
            if signal == "BUY":
                if future["low"] <= sl_price:
                    outcome = 1 if tp1_hit else 0
                    break
                if not tp1_hit and future["high"] >= tp1_price:
                    tp1_hit = True
                    sl_price = price
                if not tp2_hit and future["high"] >= tp2_price:
                    tp2_hit = True
                    sl_price = price + tp1_dist * 0.5
                if future["high"] >= tp3_price:
                    outcome = 1
                    break
            else:
                if future["high"] >= sl_price:
                    outcome = 1 if tp1_hit else 0
                    break
                if not tp1_hit and future["low"] <= tp1_price:
                    tp1_hit = True
                    sl_price = price
                if not tp2_hit and future["low"] <= tp2_price:
                    tp2_hit = True
                    sl_price = price - tp1_dist * 0.5
                if future["low"] <= tp3_price:
                    outcome = 1
                    break
        
        if tp1_hit and outcome == 0:
            outcome = 1
        
        # Build 22-feature vector
        regime_val = 1 if adx_val >= 18 else 0
        recent_buy = df["taker_buy_base"].iloc[max(0,i-20):i].sum()
        recent_sell = df["sell_vol"].iloc[max(0,i-20):i].sum()
        cvd_approx = 1 if recent_buy > recent_sell else (-1 if recent_sell > recent_buy * 1.1 else 0)
        
        def safe(val, default=0.0):
            return float(val) if not pd.isna(val) else default
        
        features = [
            safe(adx_val), safe(rsi_val), float(score),
            0.0, 0.0, safe(atr_val), float(regime_val),
            safe(row.get("rsi_slope", 0)), safe(row.get("atr_pct", 0)),
            safe(row.get("vol_ratio", 1)), safe(row.get("bb_width", 2)),
            safe(row.get("ema_cross_dist", 0)), float(cvd_approx),
            safe(row.get("taker_ratio", 1)), safe(row.get("supertrend_dist", 0)),
            safe(row.get("vwap_dist", 0)), safe(row.get("h1_ema_dist", 0)),
            0.0, 0.0, 0.0,
            safe(row.get("hour_sin", 0)), safe(row.get("hour_cos", 0)),
        ]
        
        backfill_entries.append({"features": features, "outcome": outcome})
        skip_until = i + 6
    
    wins = sum(1 for e in backfill_entries if e["outcome"] == 1)
    total = len(backfill_entries)
    wr = (wins / total * 100) if total > 0 else 0
    print(f"    ✅ {symbol}: {total} trades | Wins: {wins} ({wr:.1f}%)")
    
    return backfill_entries


def main():
    import sys
    
    # Accept symbol from command line: python ml_backfill.py ETHUSDT
    if len(sys.argv) > 1:
        target_symbols = [sys.argv[1].upper()]
    else:
        target_symbols = SYMBOLS
    
    print("=" * 60)
    print(f"  ML BACKFILL v3 — Processing: {', '.join(target_symbols)}")
    print("=" * 60)
    
    if not API_KEY or not API_SECRET:
        print("❌  Set BINANCE_API_KEY and BINANCE_API_SECRET in .env")
        return
    
    client = Client(API_KEY, API_SECRET, testnet=True)
    
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=30 * MONTHS_BACK)
    
    # Load existing backfill data (append mode)
    existing = []
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r") as f:
                existing = json.load(f)
            print(f"📦  Loaded {len(existing)} existing entries")
        except Exception:
            existing = []
    
    # Process target symbols
    new_entries = []
    for symbol in target_symbols:
        entries = process_symbol(client, symbol, start_date, end_date)
        new_entries.extend(entries)
    
    # Combine and save
    all_entries = existing + new_entries
    
    print(f"\n{'═' * 60}")
    print(f"📊  RESULTS:")
    print(f"    New trades: {len(new_entries)}")
    print(f"    Total trades: {len(all_entries)}")
    
    if all_entries:
        wins = sum(1 for e in all_entries if e["outcome"] == 1)
        losses = len(all_entries) - wins
        wr = wins / len(all_entries) * 100
        print(f"    Wins: {wins} ({wr:.1f}%)")
        print(f"    Losses: {losses} ({losses/len(all_entries)*100:.1f}%)")
        
        with open(OUTPUT_FILE, "w") as f:
            json.dump(all_entries, f)
        
        print(f"\n✅  Saved to: {OUTPUT_FILE}")
    else:
        print("⚠  No trades generated.")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()

