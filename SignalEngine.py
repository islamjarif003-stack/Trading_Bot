"""
================================================================================
  SignalEngine.py — Institutional Quant Signal Generator v11.0 (Smart Money Edition)
  Strategy: Confluence Scoring System + Market Regime Filter (ADX)
            Volume Profile (POC) + DOM Imbalance + Liquidity Magnet
            Liquidity Sweep + RSI Divergence + Volume Guard + MTF H1 + Fibo GP
            ★ Derivative Data (OI + Funding Rate Squeeze Detection)
            ★ AI Machine Learning Filter (XGBoost v2 — 22 Features)
            ★★ Freqtrade Community Patterns (BB Squeeze, MACD, StochRSI, EMA Triple, HA)
            ★★ Advanced Order Flow (CVD, Taker Ratio, Whale Detection, Absorption)
            ★★★ v11.0: Anti-FOMO Penalty + Exhaustion Filter + Sniper Zone Entries
  Asset: Multi-Pair | Exchange: Binance Futures Testnet
================================================================================
"""

import os
import json
import time
import math
import logging
import numpy as np
import pandas as pd
from collections import deque
from datetime import datetime, timezone
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException

# ─── ★★ ADVANCED MODULE IMPORTS ─────────────────────────────────────────────
from FreqtradePatterns import analyze_all_patterns
from OrderFlowEngine import analyze_order_flow
from MLModelV2 import MLFilterV2, build_feature_vector

log = logging.getLogger("InstitutionalBot")

# ─── CONFIGURATION ───────────────────────────────────────────────────────────
KLINE_INTERVAL = Client.KLINE_INTERVAL_5MINUTE    # ★ v23: Production mode — 5m entries for cleaner signals, less noise
KLINE_LIMIT = 200                                  # ★ v23: 200×5m ≈ 16.6 hours of data
H1_KLINE_INTERVAL = Client.KLINE_INTERVAL_15MINUTE  # ★ v23: 15m trend/flow (was 1H — now more responsive)
H1_KLINE_LIMIT = 250
ORDER_BOOK_DEPTH = 20
NUM_BINS = 50
POC_PROXIMITY_PCT = 0.05
IMBALANCE_RATIO = 1.5
RSI_PERIOD = 14
ADX_PERIOD = 14
ATR_PERIOD = 14
SWEEP_LOOKBACK = 24
VOLUME_SMA_PERIOD = 20
DIVERGENCE_LOOKBACK = 5
LIQ_MAGNET_PCT = 1.5  # 1.5% distance for magnet

# ─── SCORING THRESHOLDS ─────────────────────────────────────────────────────
SCORE_THRESHOLD = 12          # ★ v15: Raised to 12 (was 10) — better signal quality, fewer fake entries
MIN_SCORE_MARGIN = 3          # ★ v13: Winning side must lead by ≥3 pts (was 5, too strict)
POINTS_H1_TREND = 3           # +3 points for trend alignment + acts as veto
POINTS_FIB_GP = 3             # +3 points for Golden Pocket rejection
POINTS_LIQ_MAGNET = 2         # +2 points pointing toward Liquidation Magnet Zone
POINTS_VOLUME_SPIKE = 5       # ★ v13: +5 (Smart Money: volume burst is high-conviction)
POINTS_LIQUIDITY_SWEEP = 6    # ★ v13: +6 (Smart Money: liq sweep/S&R rejection is king)
POINTS_POC_REJECTION = 2
POINTS_RSI_DIVERGENCE = 2
POINTS_DOM_IMBALANCE = 2

# ─── WHALE TRACKING CONFIGURATION ───────────────────────────────────────────
WHALE_THRESHOLDS = {
    "BTCUSDT": {"qty": 15.0, "usd": 1_000_000},
    "ETHUSDT": {"qty": 200.0, "usd": 500_000},
    "BNBUSDT": {"qty": 2000.0, "usd": 500_000},
    "SOLUSDT": {"qty": 5000.0, "usd": 500_000},
    "XRPUSDT": {"qty": 500000.0, "usd": 500_000},
}
WHALE_RANGE_PCT = 0.2
POINTS_SUPERTREND = 1             # ★ v13: Downgraded from +3 → +1 (passive indicator, shouldn't inflate)
POINTS_VWAP_ZONE = 3              # ★ v13: Upgraded from +2 → +3 (VWAP+EMA alignment is key)
ADX_TREND_THRESHOLD = 18

# ─── ★★★ v10.0: BRAIN UPGRADES ──────────────────────────────────────────────
POINTS_TRAP_PENALTY = -5          # ★ v27: -5 PENALTY if trap detected (was -2, too soft)
POINTS_DELTA_DIVERGENCE = -1      # -1 PENALTY if CVD diverges (awareness, not blockade)
POINTS_LIQ_HUNT = 3               # +3 for trading towards liquidation cluster

# ─── ★★★ v11.0: SMART MONEY BRAIN UPGRADE ────────────────────────────────────
POINTS_FOMO_PENALTY = -4           # -4 PENALTY if price over-extended from baseline (buying the top)
POINTS_EXHAUSTION_PENALTY = -2     # ★ v28.5: Lowered -3 → -2 (was too aggressive, blocking normal pullback entries)
POINTS_SNIPER_ZONE = 3             # +3 BONUS for pullback entry to dynamic S/R (mean reversion)
FOMO_OVEREXT_ATR_MULT = 1.5        # Distance threshold: > 1.5× ATR from EMA9 = over-extended
EXHAUSTION_VOL_RATIO = 0.40        # ★ v28.5: Lowered 0.60 → 0.40 (only fire on truly dead volume, not normal retraces)

# ─── ★ DERIVATIVE DATA THRESHOLDS ───────────────────────────────────────────
POINTS_DERIVATIVE_SQUEEZE = 3       # +3 points for squeeze setup
FUNDING_RATE_STRONG_NEG = -0.0001   # -0.01% (strongly negative)
FUNDING_RATE_STRONG_POS = 0.0003    # +0.03% (strongly positive, above neutral 0.01%)
OI_ROLLING_WINDOW = 6               # Compare OI over last 6 snapshots

# ─── ★★ v8.0: FREQTRADE PATTERN SCORING ─────────────────────────────────────
POINTS_BB_SQUEEZE = 1             # ★ v13: Downgraded +2 → +1 (passive indicator)
POINTS_MACD_CROSS = 1             # ★ v13: Downgraded +2 → +1 (passive indicator)
POINTS_STOCH_RSI = 1              # ★ v13: Downgraded +2 → +1 (passive indicator)
POINTS_EMA_TRIPLE = 3             # +3 for Triple EMA alignment (FULL=3, PARTIAL=1) — kept (structural)
POINTS_HEIKIN_ASHI = 1            # ★ v13: Downgraded +2 → +1 (passive indicator)

# ─── ★★ v8.0: ORDER FLOW SCORING ────────────────────────────────────────────
POINTS_CVD_TREND = 5              # ★ v27: Upgraded +3 → +5 (Ultra-Quant: CVD is king of real money flow)
POINTS_TAKER_PRESSURE = 5         # ★ v27: Upgraded +3 → +5 (Ultra-Quant: aggressive whale buying/selling)
POINTS_WHALE_WALL = 5             # ★ v27: Upgraded +3 → +5 (Ultra-Quant: whale order wall = institutional)
POINTS_ABSORPTION = 5             # ★ v27: Upgraded +3 → +5 (Ultra-Quant: absorption = breakout coming)

# ─── ★ ML FILTER CONFIGURATION (v2 — XGBoost) ──────────────────────────────
ML_MIN_SAMPLES = 10           # Block trades if < 10 samples
ML_WIN_THRESHOLD = 0.40       # 40% predicted win probability required

# ─── ★ STRICT FILTERS ─────────────────────────────────────────────────────────
PENALTY_PRICE_CONTRADICTION = 3   # ★ v28.5: Lowered 10 → 3 (a -10 penalty blocks literally everything in a 15-point passing system)

# ─── ★★★ v16.1: VOLUME DELTA SCORE (Enhanced with Opposing Penalty) ─────────
POINTS_VOLUME_DELTA       = 4     # ★ v27: Upgraded +3 → +4 (real-time order flow data)
POINTS_VOLUME_DELTA_STRONG = 6    # ★ v27: Upgraded +4 → +6 (EXTREME alignment = ultra conviction)
PENALTY_VOLUME_DELTA_OPPOSE = -3  # -3 PENALTY when delta OPPOSES signal (buying into selling)
VOLUME_DELTA_THRESHOLD    = 0.55  # 55% taker buy = bullish, <45% = bearish
VOLUME_DELTA_STRONG_THRESH = 0.65 # 65% = STRONG alignment (extra bonus)

# ─── ★★★ v16.0: DYNAMIC SCORE THRESHOLD (ATR-ADAPTIVE) ─────────────────────
# Low volatility → lower threshold (more trades), High vol → higher (avoid fakeouts)
DYNAMIC_THRESHOLD_LOW  = 13       # ★ v27: Lowered 15 → 13 (more trades, Order Flow Floor protects quality)
DYNAMIC_THRESHOLD_MID  = 15       # ★ v27: Lowered 17 → 15 (balance frequency + quality)
DYNAMIC_THRESHOLD_HIGH = 17       # ★ v27: Lowered 19 → 17 (high vol still needs good score)
ATR_PERCENTILE_LOW     = 30       # Below 30th percentile = low volatility
ATR_PERCENTILE_HIGH    = 70       # Above 70th percentile = high volatility


# ═════════════════════════════════════════════════════════════════════════════
# ██  UTILITY: VOLUME DELTA & DYNAMIC THRESHOLD (NumPy Optimized)           ██
# ═════════════════════════════════════════════════════════════════════════════

def _calculate_volume_delta(klines_df: pd.DataFrame, lookback: int = 10) -> dict:
    """
    ★ v16.1: Volume Delta — compares Taker Buy Volume vs Total Volume.
    Now returns strength level for tiered scoring + opposing penalty.
    Pure NumPy vectorized for millisecond execution.
    """
    if len(klines_df) < lookback:
        return {"buy_ratio": 0.5, "direction": "NEUTRAL", "strength": "NONE"}
    
    recent = klines_df.iloc[-lookback:]
    total_vol = recent["volume"].values.astype(np.float64)
    taker_buy = recent["taker_buy_base"].values.astype(np.float64)
    
    total_sum = np.sum(total_vol)
    if total_sum < 1e-10:
        return {"buy_ratio": 0.5, "direction": "NEUTRAL", "strength": "NONE"}
    
    buy_ratio = np.sum(taker_buy) / total_sum
    
    # Direction classification
    if buy_ratio >= VOLUME_DELTA_STRONG_THRESH:
        direction = "BULLISH"
        strength = "STRONG"     # ≥65% buyers = strong conviction
    elif buy_ratio >= VOLUME_DELTA_THRESHOLD:
        direction = "BULLISH"
        strength = "NORMAL"     # 55-64% buyers = moderate conviction
    elif buy_ratio <= (1.0 - VOLUME_DELTA_STRONG_THRESH):
        direction = "BEARISH"
        strength = "STRONG"     # ≤35% buyers (65%+ sellers) = strong
    elif buy_ratio <= (1.0 - VOLUME_DELTA_THRESHOLD):
        direction = "BEARISH"
        strength = "NORMAL"     # 36-45% buyers = moderate sellers
    else:
        direction = "NEUTRAL"
        strength = "NONE"       # 45-55% = mixed, no edge
    
    return {
        "buy_ratio": round(float(buy_ratio), 4),
        "direction": direction,
        "strength": strength,
    }


def _calculate_dynamic_threshold(klines_df: pd.DataFrame) -> int:
    """
    ★ v16: Dynamic Score Threshold — adjusts entry strictness based on ATR volatility.
    Low vol → lower threshold (10) to maintain trade frequency.
    High vol → higher threshold (14) to avoid fakeouts.
    Pure NumPy percentile calculation for speed.
    """
    if len(klines_df) < ATR_PERIOD + 10:
        return DYNAMIC_THRESHOLD_MID  # Default if insufficient data
    
    highs = klines_df["high"].values.astype(np.float64)
    lows = klines_df["low"].values.astype(np.float64)
    closes = klines_df["close"].values.astype(np.float64)
    
    # Calculate True Range using NumPy (vectorized)
    prev_close = np.roll(closes, 1)
    prev_close[0] = closes[0]
    
    tr1 = highs - lows
    tr2 = np.abs(highs - prev_close)
    tr3 = np.abs(lows - prev_close)
    true_range = np.maximum(np.maximum(tr1, tr2), tr3)
    
    # Current ATR (last 14 candles average)
    current_atr = np.mean(true_range[-ATR_PERIOD:])
    
    # Historical ATR percentiles (over full dataset)
    rolling_atrs = np.array([np.mean(true_range[max(0,i-ATR_PERIOD):i]) for i in range(ATR_PERIOD, len(true_range))])
    
    if len(rolling_atrs) < 5:
        return DYNAMIC_THRESHOLD_MID
    
    p_low = np.percentile(rolling_atrs, ATR_PERCENTILE_LOW)
    p_high = np.percentile(rolling_atrs, ATR_PERCENTILE_HIGH)
    
    if current_atr <= p_low:
        return DYNAMIC_THRESHOLD_LOW   # Quiet market → more trades
    elif current_atr >= p_high:
        return DYNAMIC_THRESHOLD_HIGH  # Volatile market → strict filter
    else:
        return DYNAMIC_THRESHOLD_MID   # Normal conditions


# ═════════════════════════════════════════════════════════════════════════════
# ██  TECHNICAL INDICATORS                                                  ██
# ═════════════════════════════════════════════════════════════════════════════

def _calculate_rsi(closes: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100.0 - (100.0 / (1.0 + rs))

def _calculate_atr(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = ATR_PERIOD) -> pd.Series:
    prev_close = closes.shift(1)
    tr1 = highs - lows
    tr2 = (highs - prev_close).abs()
    tr3 = (lows - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

def _calculate_adx(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = ADX_PERIOD) -> pd.Series:
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
    atr_smooth = true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    plus_di_smooth = plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    minus_di_smooth = minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    plus_di = 100.0 * (plus_di_smooth / atr_smooth.replace(0, 1e-10))
    minus_di = 100.0 * (minus_di_smooth / atr_smooth.replace(0, 1e-10))
    di_sum = plus_di + minus_di
    di_diff = (plus_di - minus_di).abs()
    dx = 100.0 * (di_diff / di_sum.replace(0, 1e-10))
    return dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

def _calculate_ema(closes: pd.Series, period: int = 50) -> pd.Series:
    return closes.ewm(span=period, adjust=False).mean()


def _calculate_supertrend(highs: pd.Series, lows: pd.Series, closes: pd.Series,
                          period: int = 10, multiplier: float = 3.0) -> dict:
    """
    Calculate Supertrend indicator.
    Returns dict with 'direction' (1=BULL, -1=BEAR) and 'value' (supertrend line).
    """
    atr = _calculate_atr(highs, lows, closes, period)
    hl2 = (highs + lows) / 2.0
    
    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr
    
    supertrend = pd.Series(0.0, index=closes.index)
    direction = pd.Series(1, index=closes.index)  # 1=BULL, -1=BEAR
    
    for i in range(1, len(closes)):
        if closes.iloc[i] > upper_band.iloc[i - 1]:
            direction.iloc[i] = 1
        elif closes.iloc[i] < lower_band.iloc[i - 1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]
            if direction.iloc[i] == 1 and lower_band.iloc[i] < lower_band.iloc[i - 1]:
                lower_band.iloc[i] = lower_band.iloc[i - 1]
            if direction.iloc[i] == -1 and upper_band.iloc[i] > upper_band.iloc[i - 1]:
                upper_band.iloc[i] = upper_band.iloc[i - 1]
        
        supertrend.iloc[i] = lower_band.iloc[i] if direction.iloc[i] == 1 else upper_band.iloc[i]
    
    return {
        "direction": int(direction.iloc[-1]),  # 1=BULL, -1=BEAR
        "value": float(supertrend.iloc[-1]),
    }


def _calculate_vwap(highs: pd.Series, lows: pd.Series, closes: pd.Series,
                    volumes: pd.Series) -> float:
    """
    Calculate session VWAP (Volume Weighted Average Price).
    Uses last 50 candles as session window.
    """
    window = min(50, len(closes))
    typical_price = (highs[-window:] + lows[-window:] + closes[-window:]) / 3.0
    vol = volumes[-window:]
    cum_tp_vol = (typical_price * vol).sum()
    cum_vol = vol.sum()
    if cum_vol == 0:
        return float(closes.iloc[-1])
    return float(cum_tp_vol / cum_vol)


# ═════════════════════════════════════════════════════════════════════════════
# ██  FIBONACCI GOLDEN POCKET                                               ██
# ═════════════════════════════════════════════════════════════════════════════

def _detect_fibonacci_gp(klines_df: pd.DataFrame) -> dict:
    highs = klines_df["high"]
    lows = klines_df["low"]
    macro_high = highs.max()
    macro_low = lows.min()
    current = klines_df.iloc[-1]
    
    diff = macro_high - macro_low
    if diff == 0:
        return {"gp_active": False, "gp_dir": "NONE"}

    # Golden pocket levels: Expanded from [0.5, 0.618] to [0.5, 0.786] range
    lvl_05 = macro_low + 0.5 * diff
    lvl_0618 = macro_low + (1.0 - 0.786) * diff  # 0.786 from top is 0.214 from bottom
    gp_upper = max(lvl_05, lvl_0618)
    gp_lower = min(lvl_05, lvl_0618)

    # Rejection candle check
    open_p, close_p, high_p, low_p = current["open"], current["close"], current["high"], current["low"]
    body = abs(open_p - close_p)
    tail = min(open_p, close_p) - low_p
    head = high_p - max(open_p, close_p)
    
    is_in_gp = (low_p <= gp_upper) and (high_p >= gp_lower)
    if not is_in_gp or body == 0:
        return {"gp_active": False, "gp_dir": "NONE", "lvl_05": lvl_05, "lvl_0618": lvl_0618}

    if tail > 1.5 * body and low_p <= gp_upper:
        return {"gp_active": True, "gp_dir": "BUY", "lvl_05": lvl_05, "lvl_0618": lvl_0618}
    elif head > 1.5 * body and high_p >= gp_lower:
        return {"gp_active": True, "gp_dir": "SELL", "lvl_05": lvl_05, "lvl_0618": lvl_0618}

    return {"gp_active": False, "gp_dir": "NONE", "lvl_05": lvl_05, "lvl_0618": lvl_0618}


# ═════════════════════════════════════════════════════════════════════════════
# ██  RSI DIVERGENCE                                                        ██
# ═════════════════════════════════════════════════════════════════════════════

def _detect_rsi_divergence(closes: pd.Series, rsi: pd.Series, lookback: int = DIVERGENCE_LOOKBACK) -> str:
    if len(closes) < lookback * 2 + 1 or len(rsi) < lookback * 2 + 1: return "NONE"
    recent_close = closes.iloc[-(lookback * 2 + 1):].values
    recent_rsi = rsi.iloc[-(lookback * 2 + 1):].values
    first_half_close, second_half_close = recent_close[:lookback], recent_close[lookback:]
    first_half_rsi, second_half_rsi = recent_rsi[:lookback], recent_rsi[lookback:]
    if np.min(second_half_close) < np.min(first_half_close) and np.min(second_half_rsi) > np.min(first_half_rsi):
        return "BULLISH"
    if np.max(second_half_close) > np.max(first_half_close) and np.max(second_half_rsi) < np.max(first_half_rsi):
        return "BEARISH"
    return "NONE"


# ═════════════════════════════════════════════════════════════════════════════
# ██  LIQUIDITY SWEEP                                                       ██
# ═════════════════════════════════════════════════════════════════════════════

def _detect_liquidity_sweep(klines_df: pd.DataFrame, lookback: int = SWEEP_LOOKBACK) -> dict:
    if len(klines_df) < lookback + 1:
        return {"buy_sweep": False, "sell_sweep": False, "range_high": 0.0, "range_low": 0.0}
    range_data = klines_df.iloc[-(lookback + 1):-1]
    current = klines_df.iloc[-1]
    range_high = range_data["high"].max()
    range_low = range_data["low"].min()
    buy_sweep = (current["low"] < range_low) and (current["close"] > range_low)
    sell_sweep = (current["high"] > range_high) and (current["close"] < range_high)
    return {"buy_sweep": buy_sweep, "sell_sweep": sell_sweep, "range_high": round(float(range_high), 2), "range_low": round(float(range_low), 2)}


# ═════════════════════════════════════════════════════════════════════════════
# ██  VOLUME GUARD                                                          ██
# ═════════════════════════════════════════════════════════════════════════════

def _check_volume_guard(klines_df: pd.DataFrame, sma_period: int = VOLUME_SMA_PERIOD) -> dict:
    if len(klines_df) < sma_period + 1:
        return {"volume_ok": False, "current_vol": 0.0, "vol_sma": 0.0}
    vol_sma = klines_df["volume"].iloc[-(sma_period + 1):-1].mean()
    current_vol = klines_df["volume"].iloc[-1]
    # ★ v23: Volume Gate raised to 2.0× SMA — only enter on solid institutional volume
    return {"volume_ok": current_vol > (vol_sma * 2.0), "current_vol": round(float(current_vol), 2), "vol_sma": round(float(vol_sma), 2)}


# ═════════════════════════════════════════════════════════════════════════════
# ██  VOLUME PROFILE & LIQUIDITY MAGNETS                                    ██
# ═════════════════════════════════════════════════════════════════════════════

def _build_volume_profile(klines_df: pd.DataFrame, num_bins: int = NUM_BINS) -> dict:
    df = klines_df.copy()
    df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3.0
    bin_edges = np.linspace(df["low"].min(), df["high"].max(), num_bins + 1)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
    bin_indices = np.clip(np.digitize(df["typical_price"].values, bin_edges) - 1, 0, num_bins - 1)
    volume_profile = np.zeros(num_bins)
    for idx, vol in zip(bin_indices, df["volume"].values):
        volume_profile[idx] += vol
    
    # Sort bins by volume to find top nodes (POC + magnets)
    sorted_idx = np.argsort(volume_profile)[::-1]
    poc_idx = sorted_idx[0]
    
    # Magnet zones are the top 2 highest volume nodes
    magnet_zone_1 = float(bin_centers[sorted_idx[0]])
    magnet_zone_2 = float(bin_centers[sorted_idx[1]]) if len(sorted_idx) > 1 else magnet_zone_1

    return {
        "poc_price": float(bin_centers[poc_idx]),
        "magnet_zone_1": magnet_zone_1,
        "magnet_zone_2": magnet_zone_2
    }


# ═════════════════════════════════════════════════════════════════════════════
# ██  ★ DERIVATIVE DATA FETCHERS (Open Interest + Funding Rate)             ██
# ═════════════════════════════════════════════════════════════════════════════

# Rolling OI cache — tracks OI snapshots across scan cycles for trend detection per symbol
_oi_history_db = {}

def _fetch_open_interest(client: Client, symbol: str) -> float:
    """Fetch live Open Interest from Binance Futures API."""
    if symbol not in _oi_history_db:
        _oi_history_db[symbol] = deque(maxlen=OI_ROLLING_WINDOW)
    try:
        oi_data = client.futures_open_interest(symbol=symbol)
        oi_value = float(oi_data.get("openInterest", 0.0))
        # Cache for trend detection
        _oi_history_db[symbol].append(oi_value)
        return oi_value
    except (BinanceAPIException, BinanceRequestException) as e:
        log.warning(f"⚠  OI fetch failed: {e}. Using cached data.")
        return _oi_history_db[symbol][-1] if _oi_history_db[symbol] else 0.0
    except Exception as e:
        log.warning(f"⚠  OI fetch error: {e}")
        return _oi_history_db[symbol][-1] if _oi_history_db[symbol] else 0.0


def _fetch_funding_rate(client: Client, symbol: str) -> float:
    """Fetch the latest Funding Rate from Binance Futures API."""
    try:
        fr_data = client.futures_funding_rate(symbol=symbol, limit=1)
        if fr_data and len(fr_data) > 0:
            return float(fr_data[-1].get("fundingRate", 0.0))
        return 0.0
    except (BinanceAPIException, BinanceRequestException) as e:
        log.warning(f"⚠  Funding Rate fetch failed: {e}")
        return 0.0
    except Exception as e:
        log.warning(f"⚠  Funding Rate error: {e}")
        return 0.0


def _detect_oi_trend(symbol: str) -> str:
    """
    Detect OI trend from cached history for a specific symbol.
    Returns: "RISING", "FALLING", or "FLAT"
    """
    hist = _oi_history_db.get(symbol, [])
    if len(hist) < 3:
        return "FLAT"
    recent = list(hist)
    oldest = np.mean(recent[:len(recent)//2])
    newest = np.mean(recent[len(recent)//2:])
    
    pct_change = ((newest - oldest) / oldest * 100.0) if oldest > 0 else 0.0
    
    if pct_change > 0.5:
        return "RISING"
    elif pct_change < -0.5:
        return "FALLING"
    return "FLAT"


def _detect_price_trend(klines_df: pd.DataFrame, lookback: int = 3) -> str:
    """
    Check if price has been consistently rising or dropping over last N candles.
    Returns: "RISING", "DROPPING", or "FLAT"
    """
    if len(klines_df) < lookback:
        return "FLAT"
    
    recent = klines_df.iloc[-lookback:]
    bullish_candles = sum(1 for _, row in recent.iterrows() if row["close"] > row["open"])
    bearish_candles = sum(1 for _, row in recent.iterrows() if row["close"] < row["open"])
    
    if bearish_candles >= lookback - 1:  # At least 2 of 3 candles bearish
        return "DROPPING"
    elif bullish_candles >= lookback - 1:  # At least 2 of 3 candles bullish
        return "RISING"
    return "FLAT"


# ═════════════════════════════════════════════════════════════════════════════
# ██  ★ BTC CORRELATION COEFFICIENT                                         ██
# ═════════════════════════════════════════════════════════════════════════════

CORRELATION_PERIOD = 20  # 20-period correlation window


_btc_klines_cache = {'time': 0, 'data': None}

def _calculate_btc_correlation(client: Client, symbol: str, target_klines_df: pd.DataFrame, period: int = CORRELATION_PERIOD) -> float:
    """
    Calculate Pearson correlation coefficient between a target coin and BTCUSDT.
    Uses close price percentage returns over the specified period.
    
    Returns:
        float: Correlation coefficient (-1.0 to +1.0).
               Returns 1.0 if symbol IS BTCUSDT.
               Returns 0.0 on error (fail-open — allows trade).
    """
    if symbol == "BTCUSDT":
        return 1.0  # Perfect self-correlation
    
    try:
        # Fetch klines for both the target symbol and BTC
        limit = period + 5  # Extra buffer for return calculation
        
        # Pull from target_klines_df (which is already recent)
        if target_klines_df is None or len(target_klines_df) < limit:
            return 0.0
        
        target_closes = target_klines_df["close"].iloc[-limit:].reset_index(drop=True)
        
        # Use cached BTC klines if available
        now = time.time()
        if _btc_klines_cache['data'] is None or now - _btc_klines_cache['time'] > 60:
            btc_raw = client.futures_klines(
                symbol="BTCUSDT", interval=KLINE_INTERVAL, limit=limit,
            )
            btc_closes = pd.Series([float(k[4]) for k in btc_raw])
            _btc_klines_cache['data'] = btc_closes
            _btc_klines_cache['time'] = now
        else:
            btc_closes = _btc_klines_cache['data'].iloc[-limit:].reset_index(drop=True)
        
        if len(target_closes) < period + 1 or len(btc_closes) < period + 1:
            log.warning(f"⚠  Correlation: Insufficient data for {symbol}. Bypassing.")
            return 0.0
        
        # Align lengths (use the shorter one)
        min_len = min(len(target_closes), len(btc_closes))
        target_closes = target_closes.iloc[-min_len:]
        btc_closes = btc_closes.iloc[-min_len:]
        
        # Calculate percentage returns
        target_returns = target_closes.pct_change().dropna()
        btc_returns = btc_closes.pct_change().dropna()
        
        # Align again after pct_change
        min_len = min(len(target_returns), len(btc_returns))
        if min_len < period:
            log.warning(f"⚠  Correlation: Not enough return data for {symbol}. Bypassing.")
            return 0.0
        
        target_returns = target_returns.iloc[-period:]
        btc_returns = btc_returns.iloc[-period:]
        
        # Pearson correlation
        correlation = float(target_returns.corr(btc_returns))
        
        if pd.isna(correlation):
            return 0.0
            
        return round(correlation, 4)
        
    except (BinanceAPIException, BinanceRequestException) as e:
        log.warning(f"⚠  Correlation fetch failed for {symbol}: {e}. Bypassing filter.")
        return 0.0
    except Exception as e:
        log.warning(f"⚠  Correlation calculation error for {symbol}: {e}. Bypassing filter.")
        return 0.0


# ═════════════════════════════════════════════════════════════════════════════
# ██  ★ AI MACHINE LEARNING FILTER (scikit-learn Random Forest)             ██
# ═════════════════════════════════════════════════════════════════════════════




# ─── GLOBAL ML FILTER INSTANCE (v2 — XGBoost) ──────────────────────────────
ml_filter = MLFilterV2(
    max_memory=2000,
    min_samples=ML_MIN_SAMPLES,
    win_threshold=ML_WIN_THRESHOLD,
    retrain_interval=25,
)

# Try to load backfill data if available
_backfill_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml_backfill_data.json")
if os.path.exists(_backfill_path) and len(ml_filter.memory) < 50:
    ml_filter.load_backfill(_backfill_path)


# ═════════════════════════════════════════════════════════════════════════════
# ██  DATA FETCHERS                                                         ██
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_klines(client: Client, symbol: str, interval: str, limit: int) -> pd.DataFrame:
    raw = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    for col in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base", "taker_buy_quote"]:
        df[col] = df[col].astype(float)
    return df

def _fetch_order_book(client: Client, symbol: str) -> dict:
    book = client.futures_order_book(symbol=symbol, limit=ORDER_BOOK_DEPTH)
    bids = [[float(p), float(q)] for p, q in book["bids"]]
    asks = [[float(p), float(q)] for p, q in book["asks"]]
    
    best_bid = bids[0][0] if bids else 0.0
    best_ask = asks[0][0] if asks else 0.0
    mid_price = (best_bid + best_ask) / 2.0
    
    has_buy_whale = False
    has_sell_whale = False
    
    whale_cfg = WHALE_THRESHOLDS.get(symbol)
    if whale_cfg and mid_price > 0:
        price_range = mid_price * (WHALE_RANGE_PCT / 100.0)
        low_bound = mid_price - price_range
        high_bound = mid_price + price_range
        
        # Check Bids (Support Wall)
        for p, q in bids:
            if low_bound <= p <= high_bound:
                if q >= whale_cfg["qty"] or (p * q) >= whale_cfg["usd"]:
                    has_buy_whale = True
                    break
        
        # Check Asks (Resistance Wall)
        for p, q in asks:
            if low_bound <= p <= high_bound:
                if q >= whale_cfg["qty"] or (p * q) >= whale_cfg["usd"]:
                    has_sell_whale = True
                    break

    return {
        "buy_wall": sum(q for _, q in bids), "sell_wall": sum(q for _, q in asks),
        "best_bid": best_bid, "best_ask": best_ask,
        "raw_bids": bids,  # ★ v8.0: Raw data for whale detection
        "raw_asks": asks,  # ★ v8.0: Raw data for whale detection
        "buy_whale": has_buy_whale,
        "sell_whale": has_sell_whale,
    }


# ═════════════════════════════════════════════════════════════════════════════
# ██  MASTER SIGNAL FUNCTION                                                ██
# ═════════════════════════════════════════════════════════════════════════════

def get_quant_signal(client: Client, symbol: str) -> dict:
    # ── 1. Fetch data ────────────────────────────────────────────────────
    klines_df = _fetch_klines(client, symbol, KLINE_INTERVAL, KLINE_LIMIT)
    h1_klines_df = _fetch_klines(client, symbol, H1_KLINE_INTERVAL, H1_KLINE_LIMIT)
    dom = _fetch_order_book(client, symbol)

    # ── FAIL-FAST: Eliminate Dead Testnet Coins ──────────────────────────
    current_price = (dom["best_bid"] + dom["best_ask"]) / 2.0
    if current_price <= 0.0 or klines_df.empty or float(klines_df["close"].iloc[-1]) <= 0.0:
        raise ValueError("Dead Coin (Price is 0.0)")

    # ── 1b. ★ Fetch Derivative Data ─────────────────────────────────────
    open_interest = _fetch_open_interest(client, symbol)
    funding_rate = _fetch_funding_rate(client, symbol)
    oi_trend = _detect_oi_trend(symbol)
    price_trend = _detect_price_trend(klines_df)

    # ── 1c. ★ BTC Correlation Coefficient ────────────────────────────────
    btc_correlation = _calculate_btc_correlation(client, symbol, klines_df)

    # ── 1d. ★★ v8.0: Freqtrade Patterns ─────────────────────────────────
    ft_patterns = analyze_all_patterns(klines_df)

    # ── 1e. ★★ v8.0: Order Flow Analysis ────────────────────────────────
    order_flow = analyze_order_flow(klines_df, dom)

    # ── 2. MTF H1 Analysis (200 EMA Filter) ──────────────────────────────
    h1_closes = h1_klines_df["close"]
    h1_ema_200 = _calculate_ema(h1_closes, period=200)
    current_h1_ema = float(h1_ema_200.iloc[-1]) if not h1_ema_200.empty else 0.0
    
    mtf_trend = "BULLISH" if current_price > current_h1_ema else "BEARISH"

    # ── 3. Core indicators & Magnet Zones ────────────────────────────────
    vp = _build_volume_profile(klines_df)
    poc_price = vp["poc_price"]
    mag_1 = vp["magnet_zone_1"]
    mag_2 = vp["magnet_zone_2"]

    poc_distance_pct = abs(current_price - poc_price) / current_price * 100.0
    near_poc = poc_distance_pct <= POC_PROXIMITY_PCT

    buy_wall, sell_wall = dom["buy_wall"], dom["sell_wall"]
    safe_sell = sell_wall if sell_wall > 0 else 1e-9
    safe_buy = buy_wall if buy_wall > 0 else 1e-9
    buy_dominant = buy_wall / safe_sell
    sell_dominant = sell_wall / safe_buy

    # ── 4. Golden Pocket (Fibonacci) ─────────────────────────────────────
    fibo_gp = _detect_fibonacci_gp(klines_df)

    # ── 5. RSI + Divergence + ADX + ATR + Sweep + Volume ─────────────────
    rsi_series = _calculate_rsi(klines_df["close"])
    current_rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty else 50.0
    divergence = _detect_rsi_divergence(klines_df["close"], rsi_series)

    adx_series = _calculate_adx(klines_df["high"], klines_df["low"], klines_df["close"])
    current_adx = float(adx_series.iloc[-1]) if not adx_series.empty else 20.0
    regime = "TRENDING" if current_adx >= ADX_TREND_THRESHOLD else "RANGING"

    atr_series = _calculate_atr(klines_df["high"], klines_df["low"], klines_df["close"])
    current_atr = float(atr_series.iloc[-1]) if not atr_series.empty else 0.0

    sweep = _detect_liquidity_sweep(klines_df)
    vol_guard = _check_volume_guard(klines_df)

    # ── 6. ★ v7.4: Supertrend + VWAP ─────────────────────────────────
    supertrend = _calculate_supertrend(klines_df["high"], klines_df["low"], klines_df["close"])
    vwap_price = _calculate_vwap(klines_df["high"], klines_df["low"], klines_df["close"], klines_df["volume"])

    # ── 7. ★★★ v10.0: BRAIN UPGRADES ────────────────────────────────
    # --- Trap Detector ---
    trap_signal = "NONE"
    closes = klines_df["close"].values
    highs = klines_df["high"].values
    lows = klines_df["low"].values
    if len(closes) >= 5:
        recent_high = max(highs[-5:-1])  # Previous 4 candles high
        recent_low = min(lows[-5:-1])    # Previous 4 candles low
        last_close = closes[-1]
        last_open = float(klines_df["open"].iloc[-1])
        # Bull Trap: Price broke above recent high but closed back below
        if highs[-1] > recent_high and last_close < recent_high and last_close < last_open:
            trap_signal = "BULL_TRAP"  # Don't BUY here!
        # Bear Trap: Price broke below recent low but closed back above
        elif lows[-1] < recent_low and last_close > recent_low and last_close > last_open:
            trap_signal = "BEAR_TRAP"  # Don't SELL here!

    # --- Delta Divergence (CVD vs Price) ---
    delta_div = "NONE"
    cvd_vals = order_flow["cvd"]
    if len(closes) >= 10:
        price_change_pct = ((closes[-1] - closes[-5]) / closes[-5] * 100) if closes[-5] > 0 else 0.0
        # Require a clear >0.5% move over 5 candles to consider it a "trend" for divergence
        price_rising_clear = price_change_pct >= 0.5
        price_falling_clear = price_change_pct <= -0.5
        
        cvd_rising = cvd_vals.get("cvd_trend") == "RISING"
        cvd_falling = cvd_vals.get("cvd_trend") == "FALLING"
        
        # Bearish divergence: Price clearly up but smart money selling (CVD down)
        if price_rising_clear and cvd_falling:
            delta_div = "BEARISH_DIV"  # Fake pump!
        # Bullish divergence: Price clearly down but smart money buying (CVD up)
        elif price_falling_clear and cvd_rising:
            delta_div = "BULLISH_DIV"  # Fake dump!

    # --- Liquidation Hunt Estimation ---
    liq_hunt_dir = "NONE"
    if open_interest > 0 and current_atr > 0:
        # Liquidation clusters form at ~2-3% from current price
        # If price is near a round number + lots of OI, liquidations are stacked there
        liq_up = current_price * 1.025   # Longs get liquidated above
        liq_down = current_price * 0.975  # Shorts get liquidated below
        oi_size = open_interest * current_price if open_interest < 100 else open_interest
        # If funding is negative, shorts are heavy → price hunts UP (liquidates shorts)
        if funding_rate < -0.0001:
            liq_hunt_dir = "HUNT_UP"    # Go LONG — shorts will be liquidated
        # If funding very positive, longs are heavy → price hunts DOWN
        elif funding_rate > 0.0003:
            liq_hunt_dir = "HUNT_DOWN"  # Go SHORT — longs will be liquidated

    # ═══════════════════════════════════════════════════════════════════
    #  CONFLUENCE SCORING ENGINE
    # ═══════════════════════════════════════════════════════════════════
    buy_score = 0
    sell_score = 0
    buy_breakdown = []
    sell_breakdown = []

    range_boost = 1.5 if regime == "RANGING" else 1.0
    trend_boost = 1.5 if regime == "TRENDING" else 1.0

    # ── MTF Trend Alignment (Veto + 3 pts) ───────────────────────────
    if mtf_trend == "BULLISH":
        buy_score += POINTS_H1_TREND
        buy_breakdown.append(f"H1 Trend Align (BULL > EMA): +{POINTS_H1_TREND}")
    else:
        sell_score += POINTS_H1_TREND
        sell_breakdown.append(f"H1 Trend Align (BEAR < EMA): +{POINTS_H1_TREND}")

    # ── Fibonacci Golden Pocket (3 pts) ──────────────────────────────
    if fibo_gp["gp_active"]:
        if fibo_gp["gp_dir"] == "BUY":
            buy_score += POINTS_FIB_GP
            buy_breakdown.append(f"Fib Golden Pocket Reject (BUY): +{POINTS_FIB_GP}")
        elif fibo_gp["gp_dir"] == "SELL":
            sell_score += POINTS_FIB_GP
            sell_breakdown.append(f"Fib Golden Pocket Reject (SELL): +{POINTS_FIB_GP}")

    # ── Liquidation Magnet Logic (2 pts) ─────────────────────────────
    mag1_dist = (mag_1 - current_price) / current_price * 100.0
    mag2_dist = (mag_2 - current_price) / current_price * 100.0
    
    # Trade towards magnet if within LIQ_MAGNET_PCT distance
    def check_magnet(dist, mag_price):
        b_score, s_score = 0, 0
        b_break, s_break = None, None
        if abs(dist) <= LIQ_MAGNET_PCT and abs(dist) > 0.1:
            if dist > 0:
                b_score += POINTS_LIQ_MAGNET
                b_break = f"Liq Magnet Above (${mag_price:.1f}): +{POINTS_LIQ_MAGNET}"
            elif dist < 0:
                s_score += POINTS_LIQ_MAGNET
                s_break = f"Liq Magnet Below (${mag_price:.1f}): +{POINTS_LIQ_MAGNET}"
        return b_score, s_score, b_break, s_break

    mz1_bs, mz1_ss, mz1_bb, mz1_sb = check_magnet(mag1_dist, mag_1)
    if mz1_bb:
        buy_score += mz1_bs
        buy_breakdown.append(mz1_bb)
    if mz1_sb:
        sell_score += mz1_ss
        sell_breakdown.append(mz1_sb)

    mz2_bs, mz2_ss, mz2_bb, mz2_sb = check_magnet(mag2_dist, mag_2)
    if mz2_bb and mag_2 != mag_1:
        buy_score += mz2_bs
        buy_breakdown.append(mz2_bb)
    if mz2_sb and mag_2 != mag_1:
        sell_score += mz2_ss
        sell_breakdown.append(mz2_sb)

    # ── Liquidity Sweep (2 pts) ──────────────────────────────────────
    if sweep["buy_sweep"]:
        pts = int(POINTS_LIQUIDITY_SWEEP * trend_boost)
        buy_score += pts
        buy_breakdown.append(f"Liq Sweep BUY: +{pts}")
    if sweep["sell_sweep"]:
        pts = int(POINTS_LIQUIDITY_SWEEP * trend_boost)
        sell_score += pts
        sell_breakdown.append(f"Liq Sweep SELL: +{pts}")

    # ── POC Rejection (2 pts) ────────────────────────────────────────
    if near_poc:
        pts = int(POINTS_POC_REJECTION * range_boost)
        if current_price > poc_price:
            buy_score += pts
            buy_breakdown.append(f"POC Rejection (above): +{pts}")
        else:
            sell_score += pts
            sell_breakdown.append(f"POC Rejection (below): +{pts}")

    # ── RSI Divergence (2 pts) ───────────────────────────────────────
    if divergence == "BULLISH":
        pts = int(POINTS_RSI_DIVERGENCE * range_boost)
        buy_score += pts
        buy_breakdown.append(f"RSI Div Bullish: +{pts}")
    elif divergence == "BEARISH":
        pts = int(POINTS_RSI_DIVERGENCE * range_boost)
        sell_score += pts
        sell_breakdown.append(f"RSI Div Bearish: +{pts}")

    # ── Volume Spike (3 pts) ─────────────────────────────────────────
    if vol_guard["volume_ok"]:
        pts = int(POINTS_VOLUME_SPIKE * trend_boost)
        last_close = klines_df["close"].iloc[-1]
        last_open = klines_df["open"].iloc[-1]
        if last_close > last_open:
            buy_score += pts
            buy_breakdown.append(f"Vol Spike (bullish): +{pts}")
        else:
            sell_score += pts
            sell_breakdown.append(f"Vol Spike (bearish): +{pts}")

    # ── DOM Imbalance (2 pts) ────────────────────────────────────────
    if buy_dominant >= IMBALANCE_RATIO:
        buy_score += POINTS_DOM_IMBALANCE
        buy_breakdown.append(f"DOM Buy Wall ({buy_dominant:.1f}x): +{POINTS_DOM_IMBALANCE}")
    if sell_dominant >= IMBALANCE_RATIO:
        sell_score += POINTS_DOM_IMBALANCE
        sell_breakdown.append(f"DOM Sell Wall ({sell_dominant:.1f}x): +{POINTS_DOM_IMBALANCE}")

    # ── ★ WHALE TRACKING ──────────────────────────────────────────────
    # (Scoring handled by Order Flow Whale Wall section below — no duplication)

    # ── ★ v7.4: Supertrend Confirmation (3 pts) ──────────────────
    if supertrend["direction"] == 1:  # BULL
        buy_score += POINTS_SUPERTREND
        buy_breakdown.append(f"Supertrend BULL (↑): +{POINTS_SUPERTREND}")
    else:  # BEAR
        sell_score += POINTS_SUPERTREND
        sell_breakdown.append(f"Supertrend BEAR (↓): +{POINTS_SUPERTREND}")

    # ── ★ v7.4: VWAP Value Zone (2 pts) ────────────────────────
    if current_price < vwap_price:
        buy_score += POINTS_VWAP_ZONE
        buy_breakdown.append(f"VWAP Value (Below ${vwap_price:.1f}): +{POINTS_VWAP_ZONE}")
    else:
        sell_score += POINTS_VWAP_ZONE
        sell_breakdown.append(f"VWAP Value (Above ${vwap_price:.1f}): +{POINTS_VWAP_ZONE}")

    # ══════════════════════════════════════════════════════════════════
    #  ★ DERIVATIVE SQUEEZE DETECTION (+3 pts)
    # ══════════════════════════════════════════════════════════════════
    
    # Short Squeeze: Price dropping + OI rising + Funding strongly negative → BUY
    if (price_trend == "DROPPING" and oi_trend == "RISING" and 
            funding_rate <= FUNDING_RATE_STRONG_NEG):
        buy_score += POINTS_DERIVATIVE_SQUEEZE
        buy_breakdown.append(
            f"★ Short Squeeze (OI↑ FR:{funding_rate*100:.4f}%): +{POINTS_DERIVATIVE_SQUEEZE}"
        )

    # Long Squeeze: Price rising + OI rising + Funding strongly positive → SELL
    if (price_trend == "RISING" and oi_trend == "RISING" and 
            funding_rate >= FUNDING_RATE_STRONG_POS):
        sell_score += POINTS_DERIVATIVE_SQUEEZE
        sell_breakdown.append(
            f"★ Long Squeeze (OI↑ FR:{funding_rate*100:.4f}%): +{POINTS_DERIVATIVE_SQUEEZE}"
        )

    # ══════════════════════════════════════════════════════════════════
    #  ★★ v8.0: FREQTRADE PATTERN SCORING (+11 max)
    # ══════════════════════════════════════════════════════════════════

    # ── Bollinger Band Squeeze Breakout (+2 pts) ──
    bb_data = ft_patterns["bb_squeeze"]
    if bb_data["direction"] == "BUY":
        buy_score += POINTS_BB_SQUEEZE
        buy_breakdown.append(f"★★ BB Squeeze BUY (W:{bb_data['bb_width']:.2f}): +{POINTS_BB_SQUEEZE}")
    elif bb_data["direction"] == "SELL":
        sell_score += POINTS_BB_SQUEEZE
        sell_breakdown.append(f"★★ BB Squeeze SELL (W:{bb_data['bb_width']:.2f}): +{POINTS_BB_SQUEEZE}")

    # ── MACD Histogram Crossover (+2 pts) ──
    macd_data = ft_patterns["macd_cross"]
    if macd_data["direction"] == "BUY":
        buy_score += POINTS_MACD_CROSS
        buy_breakdown.append(f"★★ MACD Cross BUY (H:{macd_data['histogram']:.4f}): +{POINTS_MACD_CROSS}")
    elif macd_data["direction"] == "SELL":
        sell_score += POINTS_MACD_CROSS
        sell_breakdown.append(f"★★ MACD Cross SELL (H:{macd_data['histogram']:.4f}): +{POINTS_MACD_CROSS}")

    # ── Stochastic RSI (+2 pts) ──
    stoch_data = ft_patterns["stoch_rsi"]
    if stoch_data["direction"] == "BUY":
        buy_score += POINTS_STOCH_RSI
        buy_breakdown.append(f"★★ StochRSI BUY (K:{stoch_data['k_value']:.1f}): +{POINTS_STOCH_RSI}")
    elif stoch_data["direction"] == "SELL":
        sell_score += POINTS_STOCH_RSI
        sell_breakdown.append(f"★★ StochRSI SELL (K:{stoch_data['k_value']:.1f}): +{POINTS_STOCH_RSI}")

    # ── Triple EMA Cross (+3 FULL / +1 PARTIAL) ──
    ema_data = ft_patterns["ema_triple"]
    if ema_data["direction"] == "BUY":
        ema_pts = POINTS_EMA_TRIPLE if ema_data["alignment"] == "FULL" else 1
        buy_score += ema_pts
        buy_breakdown.append(f"★★ EMA Triple BUY ({ema_data['alignment']}): +{ema_pts}")
    elif ema_data["direction"] == "SELL":
        ema_pts = POINTS_EMA_TRIPLE if ema_data["alignment"] == "FULL" else 1
        sell_score += ema_pts
        sell_breakdown.append(f"★★ EMA Triple SELL ({ema_data['alignment']}): +{ema_pts}")

    # ── Heikin-Ashi Trend Confirm (+2 pts) ──
    ha_data = ft_patterns["heikin_ashi"]
    if ha_data["direction"] == "BUY":
        buy_score += POINTS_HEIKIN_ASHI
        buy_breakdown.append(f"★★ Heikin-Ashi BUY ({ha_data['consecutive_count']} candles): +{POINTS_HEIKIN_ASHI}")
    elif ha_data["direction"] == "SELL":
        sell_score += POINTS_HEIKIN_ASHI
        sell_breakdown.append(f"★★ Heikin-Ashi SELL ({ha_data['consecutive_count']} candles): +{POINTS_HEIKIN_ASHI}")

    # ══════════════════════════════════════════════════════════════════
    #  ★★ v8.0: ORDER FLOW SCORING (+9 max)
    # ══════════════════════════════════════════════════════════════════

    # ── CVD Trend Alignment (+2 pts) ──
    cvd_data = order_flow["cvd"]
    if cvd_data["cvd_trend"] == "RISING":
        buy_score += POINTS_CVD_TREND
        buy_breakdown.append(f"★★ CVD Rising (Δ:{cvd_data['delta_last']:.2f}): +{POINTS_CVD_TREND}")
    elif cvd_data["cvd_trend"] == "FALLING":
        sell_score += POINTS_CVD_TREND
        sell_breakdown.append(f"★★ CVD Falling (Δ:{cvd_data['delta_last']:.2f}): +{POINTS_CVD_TREND}")

    # ── Taker Buy/Sell Pressure (+2 pts) ──
    taker_data = order_flow["taker_ratio"]
    if taker_data["direction"] == "BUY_PRESSURE":
        buy_score += POINTS_TAKER_PRESSURE
        buy_breakdown.append(f"★★ Taker BUY Press (R:{taker_data['ratio']:.2f}): +{POINTS_TAKER_PRESSURE}")
    elif taker_data["direction"] == "SELL_PRESSURE":
        sell_score += POINTS_TAKER_PRESSURE
        sell_breakdown.append(f"★★ Taker SELL Press (R:{taker_data['ratio']:.2f}): +{POINTS_TAKER_PRESSURE}")

    # ── Whale Wall Detection (+3 pts) ──
    whale_data = order_flow["whale"]
    if whale_data["whale_bias"] == "BUY_WALL":
        buy_score += POINTS_WHALE_WALL
        buy_breakdown.append(f"★★ Whale BUY Wall (IMB:{whale_data['imbalance_ratio']:.1f}x): +{POINTS_WHALE_WALL}")
    elif whale_data["whale_bias"] == "SELL_WALL":
        sell_score += POINTS_WHALE_WALL
        sell_breakdown.append(f"★★ Whale SELL Wall (IMB:{whale_data['imbalance_ratio']:.1f}x): +{POINTS_WHALE_WALL}")

    # ── Absorption Pattern (+2 pts) ──
    absorb_data = order_flow["absorption"]
    if absorb_data["pattern"] == "BULLISH_ABSORPTION":
        buy_score += POINTS_ABSORPTION
        buy_breakdown.append(f"★★ Bull Absorption (S:{absorb_data['strength']:.2f}): +{POINTS_ABSORPTION}")
    elif absorb_data["pattern"] == "BEARISH_ABSORPTION":
        sell_score += POINTS_ABSORPTION
        sell_breakdown.append(f"★★ Bear Absorption (S:{absorb_data['strength']:.2f}): +{POINTS_ABSORPTION}")

    # ══════════════════════════════════════════════════════════════════
    #  ★★★ v10.0: BRAIN UPGRADES SCORING
    # ══════════════════════════════════════════════════════════════════

    # ── 🪤 Trap Detector (PENALTY: blocks bad entries) ──
    if trap_signal == "BULL_TRAP":
        buy_score += POINTS_TRAP_PENALTY   # Negative! Reduces buy score
        buy_breakdown.append(f"🪤 BULL TRAP DETECTED: {POINTS_TRAP_PENALTY}")
    elif trap_signal == "BEAR_TRAP":
        sell_score += POINTS_TRAP_PENALTY   # Negative! Reduces sell score
        sell_breakdown.append(f"🪤 BEAR TRAP DETECTED: {POINTS_TRAP_PENALTY}")

    # ── 📊 Delta Divergence (PENALTY: fake moves) ──
    if delta_div == "BEARISH_DIV":
        buy_score += POINTS_DELTA_DIVERGENCE  # Negative! Price up but CVD down = fake pump
        buy_breakdown.append(f"📊 DELTA DIV: Price↑ CVD↓ (Fake Pump!): {POINTS_DELTA_DIVERGENCE}")
    elif delta_div == "BULLISH_DIV":
        sell_score += POINTS_DELTA_DIVERGENCE  # Negative! Price down but CVD up = fake dump
        sell_breakdown.append(f"📊 DELTA DIV: Price↓ CVD↑ (Fake Dump!): {POINTS_DELTA_DIVERGENCE}")

    # ── 💀 Liquidation Hunt (+3 bonus) ──
    if liq_hunt_dir == "HUNT_UP":
        buy_score += POINTS_LIQ_HUNT
        buy_breakdown.append(f"💀 LIQ HUNT: Shorts stacked! Price→UP: +{POINTS_LIQ_HUNT}")
    elif liq_hunt_dir == "HUNT_DOWN":
        sell_score += POINTS_LIQ_HUNT
        sell_breakdown.append(f"💀 LIQ HUNT: Longs stacked! Price→DOWN: +{POINTS_LIQ_HUNT}")

    # ══════════════════════════════════════════════════════════════════
    #  ★★★ v11.0: SMART MONEY BRAIN UPGRADE — Anti-FOMO + Sniper Entry
    # ══════════════════════════════════════════════════════════════════

    # ── 🛑 FOMO PENALTY: Over-Extension from Short-Term Baseline (-4) ──
    # If price is stretched too far from EMA9 in the trade direction,
    # you're buying the top / selling the bottom. PENALIZE hard.
    ema9 = _calculate_ema(klines_df["close"], period=9)
    ema9_val = float(ema9.iloc[-1]) if not ema9.empty else current_price
    price_to_ema9_dist = current_price - ema9_val  # Positive = price above EMA9
    fomo_threshold = current_atr * FOMO_OVEREXT_ATR_MULT  # 1.5× ATR

    if fomo_threshold > 0:
        # BUY FOMO: price is way ABOVE EMA9 → buying at the top of a pump
        if price_to_ema9_dist > fomo_threshold:
            buy_score += POINTS_FOMO_PENALTY  # -4
            buy_breakdown.append(
                f"🚨 FOMO Over-Extension: Price ${current_price:.1f} is "
                f"+{price_to_ema9_dist:.1f} above EMA9 ${ema9_val:.1f} "
                f"(>{fomo_threshold:.1f} = {FOMO_OVEREXT_ATR_MULT}×ATR): {POINTS_FOMO_PENALTY}"
            )
        # SELL FOMO: price is way BELOW EMA9 → selling at the bottom of a dump
        if price_to_ema9_dist < -fomo_threshold:
            sell_score += POINTS_FOMO_PENALTY  # -4
            sell_breakdown.append(
                f"🚨 FOMO Over-Extension: Price ${current_price:.1f} is "
                f"{price_to_ema9_dist:.1f} below EMA9 ${ema9_val:.1f} "
                f"(>{fomo_threshold:.1f} = {FOMO_OVEREXT_ATR_MULT}×ATR): {POINTS_FOMO_PENALTY}"
            )

    # ── 🩸 EXHAUSTION PENALTY: Declining Volume on Push (-3) ──
    # If the current candle is pushing higher/lower but volume is dying,
    # the move is running out of steam. Don't chase it.
    if len(klines_df) >= 3:
        curr_candle = klines_df.iloc[-1]
        prev_candle = klines_df.iloc[-2]
        curr_vol = float(curr_candle["volume"])
        prev_vol = float(prev_candle["volume"])

        if prev_vol > 0:
            vol_ratio_exhaust = curr_vol / prev_vol

            # Bullish exhaustion: green candle pushing up but on much weaker volume
            if (curr_candle["close"] > curr_candle["open"] and
                    curr_candle["close"] > prev_candle["close"] and
                    vol_ratio_exhaust < EXHAUSTION_VOL_RATIO):
                buy_score += POINTS_EXHAUSTION_PENALTY  # -3
                buy_breakdown.append(
                    f"🩸 EXHAUSTION: Pump on dying vol "
                    f"({vol_ratio_exhaust:.0%} of prev): {POINTS_EXHAUSTION_PENALTY}"
                )

            # Bearish exhaustion: red candle pushing down but on much weaker volume
            if (curr_candle["close"] < curr_candle["open"] and
                    curr_candle["close"] < prev_candle["close"] and
                    vol_ratio_exhaust < EXHAUSTION_VOL_RATIO):
                sell_score += POINTS_EXHAUSTION_PENALTY  # -3
                sell_breakdown.append(
                    f"🩸 EXHAUSTION: Dump on dying vol "
                    f"({vol_ratio_exhaust:.0%} of prev): {POINTS_EXHAUSTION_PENALTY}"
                )

    # ── 🎯 SNIPER ZONE BONUS: Pullback Entry to Dynamic S/R (+3) ──
    # Instead of chasing breakouts, reward entries when price pulls back
    # to key levels (VWAP, EMA21, or Fib Golden Pocket) and REJECTS.
    ema21 = _calculate_ema(klines_df["close"], period=21)
    ema21_val = float(ema21.iloc[-1]) if not ema21.empty else current_price
    sniper_atr_zone = current_atr * 0.5  # Must be within 0.5× ATR of the level

    sniper_buy_triggered = False
    sniper_sell_triggered = False
    sniper_level_name = ""

    if len(klines_df) >= 2 and sniper_atr_zone > 0:
        last_candle = klines_df.iloc[-1]
        candle_body_bullish = last_candle["close"] > last_candle["open"]
        candle_body_bearish = last_candle["close"] < last_candle["open"]
        lower_wick = min(last_candle["open"], last_candle["close"]) - last_candle["low"]
        upper_wick = last_candle["high"] - max(last_candle["open"], last_candle["close"])
        candle_body = abs(last_candle["close"] - last_candle["open"])
        has_rejection_wick_buy = lower_wick > candle_body * 0.8   # Long lower wick = buyers stepping in
        has_rejection_wick_sell = upper_wick > candle_body * 0.8  # Long upper wick = sellers stepping in

        # Check each dynamic S/R level
        sniper_levels = [
            ("VWAP", vwap_price),
            ("EMA21", ema21_val),
        ]
        # Add Fib GP level if active
        if fibo_gp.get("lvl_05", 0) > 0:
            sniper_levels.append(("Fib GP 0.5", fibo_gp["lvl_05"]))

        for level_name, level_price in sniper_levels:
            dist_to_level = abs(current_price - level_price)

            if dist_to_level <= sniper_atr_zone:
                # BUY Sniper: Price pulled back DOWN to support and is rejecting UP
                if (current_price >= level_price and
                        (candle_body_bullish or has_rejection_wick_buy) and
                        not sniper_buy_triggered):
                    sniper_buy_triggered = True
                    sniper_level_name = level_name

                # SELL Sniper: Price pulled back UP to resistance and is rejecting DOWN
                if (current_price <= level_price and
                        (candle_body_bearish or has_rejection_wick_sell) and
                        not sniper_sell_triggered):
                    sniper_sell_triggered = True
                    sniper_level_name = level_name

    if sniper_buy_triggered:
        buy_score += POINTS_SNIPER_ZONE  # +3
        buy_breakdown.append(
            f"🎯 SNIPER ZONE: Pullback to {sniper_level_name} "
            f"+ rejection → BUY: +{POINTS_SNIPER_ZONE}"
        )
    if sniper_sell_triggered:
        sell_score += POINTS_SNIPER_ZONE  # +3
        sell_breakdown.append(
            f"🎯 SNIPER ZONE: Pullback to {sniper_level_name} "
            f"+ rejection → SELL: +{POINTS_SNIPER_ZONE}"
        )
    # ══════════════════════════════════════════════════════════════════
    #  ★★ v12: STRICT DIRECTIONAL CHECK
    # ══════════════════════════════════════════════════════════════════
    if price_trend == "DROPPING":
        buy_score -= PENALTY_PRICE_CONTRADICTION
        buy_breakdown.append(f"🚨 Price DROPPING vs BUY: -{PENALTY_PRICE_CONTRADICTION}")
    elif price_trend == "RISING":
        sell_score -= PENALTY_PRICE_CONTRADICTION
        sell_breakdown.append(f"🚨 Price RISING vs SELL: -{PENALTY_PRICE_CONTRADICTION}")

    buy_score = max(0, buy_score)
    sell_score = max(0, sell_score)

    # ══════════════════════════════════════════════════════════════════
    #  ★★★ v16.1: VOLUME DELTA SCORING (Tiered + Opposing Penalty)
    #  STRONG alignment (≥65%): +4, Normal (≥55%): +3
    #  OPPOSING delta: -3 penalty (buying into selling = suicide)
    # ══════════════════════════════════════════════════════════════════
    vol_delta = _calculate_volume_delta(klines_df, lookback=10)
    vd_ratio_str = f"{vol_delta['buy_ratio']*100:.1f}%"

    if vol_delta["direction"] == "BULLISH":
        if vol_delta["strength"] == "STRONG":
            buy_score += POINTS_VOLUME_DELTA_STRONG
            buy_breakdown.append(f"📊 Vol Delta STRONG BULL (Buy%: {vd_ratio_str}): +{POINTS_VOLUME_DELTA_STRONG}")
        else:
            buy_score += POINTS_VOLUME_DELTA
            buy_breakdown.append(f"📊 Vol Delta BULLISH (Buy%: {vd_ratio_str}): +{POINTS_VOLUME_DELTA}")
        # ★ OPPOSING PENALTY: If we're considering a SELL but volume is bullish → penalize
        sell_score += PENALTY_VOLUME_DELTA_OPPOSE
        sell_breakdown.append(f"📊 Vol Delta OPPOSES SELL (Buy%: {vd_ratio_str}): {PENALTY_VOLUME_DELTA_OPPOSE}")

    elif vol_delta["direction"] == "BEARISH":
        if vol_delta["strength"] == "STRONG":
            sell_score += POINTS_VOLUME_DELTA_STRONG
            sell_breakdown.append(f"📊 Vol Delta STRONG BEAR (Buy%: {vd_ratio_str}): +{POINTS_VOLUME_DELTA_STRONG}")
        else:
            sell_score += POINTS_VOLUME_DELTA
            sell_breakdown.append(f"📊 Vol Delta BEARISH (Buy%: {vd_ratio_str}): +{POINTS_VOLUME_DELTA}")
        # ★ OPPOSING PENALTY: If we're considering a BUY but volume is bearish → penalize
        buy_score += PENALTY_VOLUME_DELTA_OPPOSE
        buy_breakdown.append(f"📊 Vol Delta OPPOSES BUY (Buy%: {vd_ratio_str}): {PENALTY_VOLUME_DELTA_OPPOSE}")

    else:
        # NEUTRAL delta — no edge, log it but don't add/subtract
        buy_breakdown.append(f"📊 Vol Delta NEUTRAL (Buy%: {vd_ratio_str}): +0")
        sell_breakdown.append(f"📊 Vol Delta NEUTRAL (Buy%: {vd_ratio_str}): +0")

    # ══════════════════════════════════════════════════════════════════
    #  ★★★ v16.0: DYNAMIC SCORE THRESHOLD (ATR-Adaptive)
    #  Low vol → 10 (more trades), Mid → 12, High vol → 14 (strict)
    # ══════════════════════════════════════════════════════════════════
    dynamic_threshold = _calculate_dynamic_threshold(klines_df)

    # ═══════════════════════════════════════════════════════════════════
    #  SIGNAL DECISION (WITH HARD VETO + STRICT ENTRY RULES)
    # ═══════════════════════════════════════════════════════════════════
    signal = "NONE"
    final_score = 0
    score_breakdown = []
    
    # ★ v8.0: Updated max_possible with all modules (including Volume Delta)
    max_possible = (POINTS_H1_TREND + POINTS_FIB_GP + POINTS_LIQ_MAGNET + 
                    POINTS_LIQUIDITY_SWEEP + POINTS_POC_REJECTION +
                    POINTS_RSI_DIVERGENCE + POINTS_VOLUME_SPIKE +
                    POINTS_DOM_IMBALANCE + POINTS_DERIVATIVE_SQUEEZE +
                    POINTS_SUPERTREND + POINTS_VWAP_ZONE +
                    # ★★ Freqtrade patterns
                    POINTS_BB_SQUEEZE + POINTS_MACD_CROSS + POINTS_STOCH_RSI +
                    POINTS_EMA_TRIPLE + POINTS_HEIKIN_ASHI +
                    # ★★ Order Flow
                    POINTS_CVD_TREND + POINTS_TAKER_PRESSURE +
                    POINTS_WHALE_WALL + POINTS_ABSORPTION +
                    # ★★★ v11.0: Sniper Zone (bonus only; penalties don't count in max)
                    POINTS_SNIPER_ZONE +
                    # ★★★ v16.1: Volume Delta (use STRONG variant for max display)
                    POINTS_VOLUME_DELTA_STRONG)
    max_display = int(max_possible * max(range_boost, trend_boost))

    # ── Ranging Market RSI Veto ──────────────────────────────────────
    if regime == "RANGING":
        if current_rsi < 40:
            sell_score = 0
            if "★ VETO: Ranging Oversold (RSI < 40)" not in sell_breakdown:
                sell_breakdown.append("★ VETO: Ranging Oversold (RSI < 40)")
        if current_rsi > 60:
            buy_score = 0
            if "★ VETO: Ranging Overbought (RSI > 60)" not in buy_breakdown:
                buy_breakdown.append("★ VETO: Ranging Overbought (RSI > 60)")

    # ★ v12: Apply MTF Alignment as SOFT PENALTY (not hard VETO)
    # Counter-trend trades are penalized but NOT completely blocked.
    # This allows strong 1m pullback/reversal setups to still fire.
    MTF_COUNTER_TREND_PENALTY = 5  # Deduct 5 points for counter-trend

    if mtf_trend == "BULLISH":
        sell_score = max(0, sell_score - MTF_COUNTER_TREND_PENALTY)
        if sell_score > 0:
            sell_breakdown.append(f"★ Counter-Trend Penalty (H1 BULL): -{MTF_COUNTER_TREND_PENALTY}")
        if buy_score >= dynamic_threshold:
            signal = "BUY"
            final_score = buy_score
            score_breakdown = buy_breakdown
        elif sell_score >= dynamic_threshold:
            signal = "SELL"
            final_score = sell_score
            score_breakdown = sell_breakdown
        else:
            final_score = max(buy_score, sell_score)
            score_breakdown = buy_breakdown if buy_score >= sell_score else sell_breakdown
    else:
        buy_score = max(0, buy_score - MTF_COUNTER_TREND_PENALTY)
        if buy_score > 0:
            buy_breakdown.append(f"★ Counter-Trend Penalty (H1 BEAR): -{MTF_COUNTER_TREND_PENALTY}")
        if sell_score >= dynamic_threshold:
            signal = "SELL"
            final_score = sell_score
            score_breakdown = sell_breakdown
        elif buy_score >= dynamic_threshold:
            signal = "BUY"
            final_score = buy_score
            score_breakdown = buy_breakdown
        else:
            final_score = max(buy_score, sell_score)
            score_breakdown = buy_breakdown if buy_score >= sell_score else sell_breakdown

    # ★★★ v12: NET SCORE MARGIN FILTER — Block weak/confused signals
    # The winning side must lead by at least MIN_SCORE_MARGIN points.
    # This prevents "always-on" indicators from inflating mediocre setups.
    if signal in ("BUY", "SELL"):
        score_diff = abs(buy_score - sell_score)
        if score_diff < MIN_SCORE_MARGIN:
            score_breakdown.append(
                f"🚫 MARGIN FILTER: Score gap {score_diff} < {MIN_SCORE_MARGIN} "
                f"(BUY:{buy_score} vs SELL:{sell_score}) — Signal too weak/confused"
            )
            signal = "NONE"

    # ★★★ v27: ORDER FLOW FLOOR — At least 1 institutional signal MUST fire
    # Without real money flow confirmation, no trade passes. This is the Ultra-Quant rule.
    if signal in ("BUY", "SELL"):
        has_order_flow = False
        # Check if ANY order flow signal fired for the winning side
        of_keywords = ["CVD", "Taker", "Whale", "Absorption", "Vol Delta BULLISH", "Vol Delta BEARISH", "Vol Delta STRONG"]
        for item in score_breakdown:
            for kw in of_keywords:
                if kw in item and "OPPOSES" not in item and "NEUTRAL" not in item:
                    has_order_flow = True
                    break
            if has_order_flow:
                break
        if not has_order_flow:
            score_breakdown.append(
                f"🚫 ORDER FLOW FLOOR: No CVD/Taker/Whale/Absorption/VolDelta signal — "
                f"Ultra-Quant requires at least 1 institutional confirmation"
            )
            signal = "NONE"

    # ══════════════════════════════════════════════════════════════════
    #  ★ RULE 3: CANDLE CLOSE CONFIRMATION
    #    BUY: Last closed candle (iloc[-2]) must be Green (close > open)
    #    SELL: Last closed candle (iloc[-2]) must be Red (close < open)
    # ══════════════════════════════════════════════════════════════════
    if signal in ("BUY", "SELL"):
        # Use the second-to-last candle (the last CLOSED candle, not the live one)
        last_closed = klines_df.iloc[-2]
        candle_is_green = last_closed["close"] > last_closed["open"]
        candle_is_red = last_closed["close"] < last_closed["open"]

        if signal == "BUY" and not candle_is_green:
            final_score = max(0, final_score - 1)  # ★ v28.5: -2 → -1 (candle color is secondary to SMC structure)
            score_breakdown.append("★ Candle NOT green (−1)")
            if final_score < dynamic_threshold:
                signal = "NONE"
        elif signal == "SELL" and not candle_is_red:
            final_score = max(0, final_score - 1)  # ★ v28.5: -2 → -1
            score_breakdown.append("★ Candle NOT red (−1)")
            if final_score < dynamic_threshold:
                signal = "NONE"

    if signal == "NONE" and not score_breakdown:
        score_breakdown = ["No confluence (Vetoed)"]

    # ═══════════════════════════════════════════════════════════════════
    #  ★★ v8.0: AI MACHINE LEARNING FILTER (XGBoost — 22 Features)
    # ═══════════════════════════════════════════════════════════════════
    
    # ── Build 22-feature ML input ──
    now_utc = datetime.now(timezone.utc)
    
    # RSI slope (rate of change over last 3 candles)
    rsi_slope = 0.0
    if len(rsi_series) >= 4:
        rsi_slope = float(rsi_series.iloc[-1] - rsi_series.iloc[-4])
    
    # Volume ratio
    vol_ratio = vol_guard.get("current_vol", 0) / max(vol_guard.get("vol_sma", 1), 1e-10)
    
    # EMA21 distance
    ema21_val = float(_calculate_ema(klines_df["close"], 21).iloc[-1])
    ema_cross_dist = (current_price - ema21_val) / current_price * 100.0
    
    # Supertrend distance
    supertrend_dist = (current_price - supertrend["value"]) / current_price * 100.0
    
    # VWAP distance
    vwap_dist = (current_price - vwap_price) / current_price * 100.0
    
    # H1 EMA distance
    h1_ema_dist = (current_price - current_h1_ema) / current_price * 100.0 if current_h1_ema > 0 else 0.0
    
    # Fib proximity
    fib_proximity = 0.0
    if fibo_gp.get("lvl_05", 0) > 0:
        fib_proximity = abs(current_price - fibo_gp["lvl_05"]) / current_price * 100.0
    
    ml_features = {
        # Original 7
        "adx": current_adx,
        "rsi": current_rsi,
        "score": final_score,
        "oi_trend": oi_trend,
        "funding_rate": funding_rate,
        "atr": current_atr,
        "regime": regime,
        # New 15
        "rsi_slope": rsi_slope,
        "current_price": current_price,
        "volume_ratio": vol_ratio,
        "bb_width": bb_data.get("bb_width", 2.0),
        "ema_cross_dist": ema_cross_dist,
        "cvd_trend": cvd_data.get("cvd_trend", "FLAT"),
        "taker_ratio": taker_data.get("ratio", 1.0),
        "supertrend_dist": supertrend_dist,
        "vwap_dist": vwap_dist,
        "h1_ema_dist": h1_ema_dist,
        "oi_pct_change": 0.0,  # Will be enriched by OI history if available
        "funding_z_score": funding_rate * 10000,  # Scaled
        "fib_proximity": fib_proximity,
        "hour": now_utc.hour,
    }
    
    ml_win_prob = 1.0
    ml_vetoed = False
    
    if signal in ("BUY", "SELL"):
        ml_win_prob = ml_filter.predict_win_probability(ml_features)
        
        # ★ ML PENALTY DISABLED (As per user request)
        if len(ml_filter.memory) >= ML_MIN_SAMPLES:
            if ml_win_prob < ML_WIN_THRESHOLD:
                # We log it in breakdown but DO NOT deduct points anymore
                score_breakdown.append(f"⚠️ ML Low Confidence (WR:{ml_win_prob*100:.1f}%) — [Penalty Disabled]")
            log.info(f"🤖  ML V2 Info │ Win Prob: {ml_win_prob*100:.1f}% │ Memory: {len(ml_filter.memory)} trades")
        else:
            log.info(f"🤖  ML V2 Filter: LEARNING ({len(ml_filter.memory)}/{ML_MIN_SAMPLES} samples) │ Win Prob: {ml_win_prob*100:.1f}%")

    # ── Missing factors ──────────────────────────────────────────────
    waiting_for = []
    if "BULLISH" not in mtf_trend and "BEARISH" not in mtf_trend: waiting_for.append("H1 Alignment")
    if not fibo_gp["gp_active"]: waiting_for.append("Fib GP")
    if not vol_guard["volume_ok"]: waiting_for.append("Volume spike")
    if divergence == "NONE": waiting_for.append("RSI divergence")

    ml_stats = ml_filter.get_stats()

    return {
        "signal": signal,
        "score": final_score,
        "score_threshold": dynamic_threshold,
        "max_score": max_display,
        "score_breakdown": score_breakdown,
        "waiting_for": waiting_for,
        "regime": regime,
        "adx": round(current_adx, 2),
        "rsi": round(current_rsi, 2),
        "atr": round(current_atr, 2),
        "poc_price": round(poc_price, 2),
        "poc_distance_pct": round(poc_distance_pct, 3),
        "current_price": round(current_price, 2),
        "h1_trend": mtf_trend,
        "fibo_gp_active": fibo_gp["gp_active"],
        "buy_whale": dom.get("buy_whale", False),
        "sell_whale": dom.get("sell_whale", False),
        "fibo_gp_05": fibo_gp.get("lvl_05", 0),
        "fibo_gp_0618": fibo_gp.get("lvl_0618", 0),
        "liq_magnet_1": round(mag_1, 2),
        "liq_magnet_2": round(mag_2, 2),
        "range_high": sweep["range_high"],
        "range_low": sweep["range_low"],
        "klines_df": klines_df,
        # ★ New derivative data fields
        "open_interest": round(open_interest, 4),
        "funding_rate": round(funding_rate, 6),
        "oi_trend": oi_trend,
        "price_trend": price_trend,
        # ★ BTC Correlation
        "btc_correlation": btc_correlation,
        # ★ New ML filter fields
        "ml_win_prob": round(ml_win_prob, 4),
        "ml_vetoed": ml_vetoed,
        "ml_features": ml_features,
        "ml_stats": ml_stats,
        # ★ v7.4: Supertrend + VWAP
        "supertrend_dir": supertrend["direction"],
        "supertrend_val": round(supertrend["value"], 2),
        "vwap": round(vwap_price, 2),
        # ★★ v8.0: Freqtrade Patterns
        "ft_patterns": ft_patterns,
        # ★★ v8.0: Order Flow
        "order_flow": order_flow,
        # ★★★ v16.1: Volume Delta data
        "vol_delta": vol_delta,
    }
