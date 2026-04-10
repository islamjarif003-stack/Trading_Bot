"""
================================================================================
  OrderFlowEngine.py — Advanced Order Flow & Volume Profile Analysis
  
  Whale tracking, Volume Delta analysis, and Institutional-grade order flow
  intelligence using existing Binance klines + order book data.
  
  Components:
    1. Cumulative Volume Delta (CVD) — Buyer vs Seller pressure trend
    2. Taker Buy/Sell Ratio — Real-time aggressor detection
    3. Whale Order Detection — Large orders > 2σ from mean
    4. Absorption Detection — Big wall being absorbed = reversal signal
    5. Enhanced Volume-at-Price (VAP) — Cluster-based S/R levels
================================================================================
"""

import numpy as np
import pandas as pd
import logging

log = logging.getLogger("InstitutionalBot")


# ═════════════════════════════════════════════════════════════════════════════
# ██  1. CUMULATIVE VOLUME DELTA (CVD)                                       ██
# ═════════════════════════════════════════════════════════════════════════════

def calculate_cvd(klines_df: pd.DataFrame, lookback: int = 20) -> dict:
    """
    Cumulative Volume Delta — measures the net difference between buying
    and selling volume over time. Uses taker_buy_base from Binance klines.
    
    Logic:
      - Buy Volume = taker_buy_base (already in klines!)
      - Sell Volume = total volume - taker_buy_base
      - Delta = Buy Volume - Sell Volume (per candle)
      - CVD = cumulative sum of deltas
      - CVD Rising + Price Rising = Strong trend continuation
      - CVD Falling + Price Rising = Bearish divergence (smart money selling)
    
    Returns:
        dict: cvd_trend ("RISING"/"FALLING"/"FLAT"), delta_last, 
              divergence ("BULLISH_DIV"/"BEARISH_DIV"/"NONE")
    """
    if len(klines_df) < lookback + 2:
        return {"cvd_trend": "FLAT", "delta_last": 0.0, "divergence": "NONE",
                "cvd_value": 0.0}
    
    df = klines_df.copy()
    
    # Ensure taker_buy_base is float
    if "taker_buy_base" in df.columns:
        df["taker_buy_base"] = df["taker_buy_base"].astype(float)
        buy_vol = df["taker_buy_base"]
    else:
        # Fallback: estimate from candle direction (less accurate)
        green = df["close"] > df["open"]
        buy_vol = df["volume"].where(green, df["volume"] * 0.4)
    
    sell_vol = df["volume"] - buy_vol
    delta = buy_vol - sell_vol
    
    # CVD over lookback period
    recent_delta = delta.iloc[-lookback:]
    cvd = recent_delta.cumsum()
    
    current_cvd = float(cvd.iloc[-1])
    mid_cvd = float(cvd.iloc[len(cvd)//2])
    
    # CVD Trend (compare first half vs second half)
    first_half_avg = cvd.iloc[:len(cvd)//2].mean()
    second_half_avg = cvd.iloc[len(cvd)//2:].mean()
    
    diff = second_half_avg - first_half_avg
    threshold = abs(first_half_avg) * 0.1
    
    if diff > threshold:
        cvd_trend = "RISING"
    elif diff < -threshold:
        cvd_trend = "FALLING"
    else:
        cvd_trend = "FLAT"
    
    # Price trend for divergence detection
    price_start = float(df["close"].iloc[-lookback])
    price_end = float(df["close"].iloc[-1])
    price_rising = price_end > price_start * 1.001
    price_falling = price_end < price_start * 0.999
    
    # Divergence
    divergence = "NONE"
    if price_rising and cvd_trend == "FALLING":
        divergence = "BEARISH_DIV"  # Price up but smart money selling
    elif price_falling and cvd_trend == "RISING":
        divergence = "BULLISH_DIV"  # Price down but smart money buying
    
    return {
        "cvd_trend": cvd_trend,
        "delta_last": round(float(delta.iloc[-1]), 4),
        "divergence": divergence,
        "cvd_value": round(current_cvd, 4),
    }


# ═════════════════════════════════════════════════════════════════════════════
# ██  2. TAKER BUY/SELL RATIO                                                ██
# ═════════════════════════════════════════════════════════════════════════════

def calculate_taker_ratio(klines_df: pd.DataFrame, window: int = 10) -> dict:
    """
    Taker Buy/Sell Ratio — measures aggressive buying vs selling pressure.
    
    Logic:
      - Ratio > 1.2 → Strong buying aggression (whales buying)
      - Ratio < 0.8 → Strong selling aggression (whales selling)
      - Rising ratio → Increasing buy pressure
      - Falling ratio → Increasing sell pressure
    
    Returns:
        dict: ratio, direction ("BUY_PRESSURE"/"SELL_PRESSURE"/"NEUTRAL"),
              trend ("INCREASING"/"DECREASING"/"STABLE")
    """
    if len(klines_df) < window + 2:
        return {"ratio": 1.0, "direction": "NEUTRAL", "trend": "STABLE"}
    
    df = klines_df.copy()
    
    if "taker_buy_base" in df.columns:
        df["taker_buy_base"] = df["taker_buy_base"].astype(float)
        buy_vol = df["taker_buy_base"]
    else:
        green = df["close"] > df["open"]
        buy_vol = df["volume"].where(green, df["volume"] * 0.4)
    
    sell_vol = df["volume"] - buy_vol
    
    # Avoid division by zero
    sell_vol = sell_vol.replace(0, 1e-10)
    
    # Rolling ratio
    ratio_series = buy_vol / sell_vol
    avg_ratio = float(ratio_series.iloc[-window:].mean())
    current_ratio = float(ratio_series.iloc[-1])
    prev_ratio = float(ratio_series.iloc[-2])
    
    # Direction
    if avg_ratio > 1.15:
        direction = "BUY_PRESSURE"
    elif avg_ratio < 0.85:
        direction = "SELL_PRESSURE"
    else:
        direction = "NEUTRAL"
    
    # Trend (is ratio accelerating?)
    first_half = ratio_series.iloc[-window:-window//2].mean()
    second_half = ratio_series.iloc[-window//2:].mean()
    
    if second_half > first_half * 1.05:
        trend = "INCREASING"
    elif second_half < first_half * 0.95:
        trend = "DECREASING"
    else:
        trend = "STABLE"
    
    return {
        "ratio": round(avg_ratio, 4),
        "direction": direction,
        "trend": trend,
    }


# ═════════════════════════════════════════════════════════════════════════════
# ██  3. WHALE ORDER DETECTION                                               ██
# ═════════════════════════════════════════════════════════════════════════════

def detect_whale_orders(order_book: dict, depth_limit: int = 20, 
                        sigma_threshold: float = 2.0) -> dict:
    """
    Whale Detection — identifies abnormally large orders in the order book.
    
    Logic:
      - Calculate mean + std of all order sizes
      - Any order > mean + 2σ is a "whale order"
      - Multiple whale bids = buying wall (support)
      - Multiple whale asks = selling wall (resistance)
    
    Args:
        order_book: Raw order book dict with 'bids' and 'asks' lists
                   Each entry: [price, quantity]
    
    Returns:
        dict: whale_bias ("BUY_WALL"/"SELL_WALL"/"NEUTRAL"),
              whale_bid_count, whale_ask_count,
              largest_bid, largest_ask
    """
    bids = order_book.get("raw_bids", [])
    asks = order_book.get("raw_asks", [])
    
    if not bids or not asks:
        # Fallback: use aggregated values
        buy_wall = order_book.get("buy_wall", 0)
        sell_wall = order_book.get("sell_wall", 0)
        safe_sell = sell_wall if sell_wall > 0 else 1e-9
        safe_buy = buy_wall if buy_wall > 0 else 1e-9
        
        if buy_wall / safe_sell > 2.0:
            return {"whale_bias": "BUY_WALL", "whale_bid_count": 0,
                    "whale_ask_count": 0, "largest_bid": 0, "largest_ask": 0,
                    "imbalance_ratio": round(buy_wall / safe_sell, 2)}
        elif sell_wall / safe_buy > 2.0:
            return {"whale_bias": "SELL_WALL", "whale_bid_count": 0,
                    "whale_ask_count": 0, "largest_bid": 0, "largest_ask": 0,
                    "imbalance_ratio": round(sell_wall / safe_buy, 2)}
        else:
            return {"whale_bias": "NEUTRAL", "whale_bid_count": 0,
                    "whale_ask_count": 0, "largest_bid": 0, "largest_ask": 0,
                    "imbalance_ratio": 1.0}
    
    bid_sizes = np.array([q for _, q in bids])
    ask_sizes = np.array([q for _, q in asks])
    
    all_sizes = np.concatenate([bid_sizes, ask_sizes])
    mean_size = np.mean(all_sizes)
    std_size = np.std(all_sizes)
    
    whale_threshold = mean_size + sigma_threshold * std_size
    
    whale_bids = [(p, q) for p, q in bids if q > whale_threshold]
    whale_asks = [(p, q) for p, q in asks if q > whale_threshold]
    
    whale_bid_vol = sum(q for _, q in whale_bids)
    whale_ask_vol = sum(q for _, q in whale_asks)
    
    total_bid_vol = sum(bid_sizes)
    total_ask_vol = sum(ask_sizes)
    safe_ask = total_ask_vol if total_ask_vol > 0 else 1e-9
    safe_bid = total_bid_vol if total_bid_vol > 0 else 1e-9
    
    if whale_bid_vol > whale_ask_vol * 1.5:
        whale_bias = "BUY_WALL"
    elif whale_ask_vol > whale_bid_vol * 1.5:
        whale_bias = "SELL_WALL"
    else:
        whale_bias = "NEUTRAL"
    
    return {
        "whale_bias": whale_bias,
        "whale_bid_count": len(whale_bids),
        "whale_ask_count": len(whale_asks),
        "largest_bid": round(float(max(bid_sizes)), 4) if len(bid_sizes) > 0 else 0,
        "largest_ask": round(float(max(ask_sizes)), 4) if len(ask_sizes) > 0 else 0,
        "imbalance_ratio": round(total_bid_vol / safe_ask, 2),
    }


# ═════════════════════════════════════════════════════════════════════════════
# ██  4. ABSORPTION DETECTION                                                ██
# ═════════════════════════════════════════════════════════════════════════════

def detect_absorption(klines_df: pd.DataFrame, lookback: int = 5) -> dict:
    """
    Absorption Pattern — detects when a large wall (support/resistance) is
    being consumed by aggressive orders, suggesting a breakout.
    
    Logic:
      - High volume at a price level + price NOT moving = absorption
      - If price held at support with high sell volume → Bullish absorption
      - If price held at resistance with high buy volume → Bearish absorption (trapped)
    
    Pattern Recognition:
      - Multiple candles with high volume but small body = orders being absorbed
      - After absorption → explosive move in the opposite direction
    
    Returns:
        dict: pattern ("BULLISH_ABSORPTION"/"BEARISH_ABSORPTION"/"NONE"),
              strength (0-1)
    """
    if len(klines_df) < lookback + 2:
        return {"pattern": "NONE", "strength": 0.0}
    
    recent = klines_df.iloc[-lookback:]
    
    # Calculate average volume and body size
    avg_vol = klines_df["volume"].iloc[-(lookback*3):-lookback].mean()
    if avg_vol == 0:
        avg_vol = 1e-10
    
    absorption_candles = 0
    direction_bias = 0  # Positive = bullish, Negative = bearish
    
    for _, candle in recent.iterrows():
        body = abs(candle["close"] - candle["open"])
        full_range = candle["high"] - candle["low"]
        vol_ratio = candle["volume"] / avg_vol
        
        if full_range == 0:
            continue
        
        body_pct = body / full_range
        
        # Absorption = high volume + small body (doji-like)
        if vol_ratio > 1.3 and body_pct < 0.4:
            absorption_candles += 1
            
            # Determine direction by tail analysis
            lower_wick = min(candle["open"], candle["close"]) - candle["low"]
            upper_wick = candle["high"] - max(candle["open"], candle["close"])
            
            if lower_wick > upper_wick:
                direction_bias += 1   # Buyers absorbing at bottom
            else:
                direction_bias -= 1   # Sellers absorbing at top
    
    pattern = "NONE"
    strength = 0.0
    
    if absorption_candles >= lookback * 0.6:  # 60% or more candles show absorption
        strength = min(absorption_candles / lookback, 1.0)
        if direction_bias > 0:
            pattern = "BULLISH_ABSORPTION"
        elif direction_bias < 0:
            pattern = "BEARISH_ABSORPTION"
    
    return {
        "pattern": pattern,
        "strength": round(strength, 2),
    }


# ═════════════════════════════════════════════════════════════════════════════
# ██  5. ENHANCED VOLUME-AT-PRICE (VAP) CLUSTERS                            ██
# ═════════════════════════════════════════════════════════════════════════════

def calculate_vap_clusters(klines_df: pd.DataFrame, num_bins: int = 50,
                           cluster_threshold_pct: float = 0.7) -> dict:
    """
    Enhanced Volume-at-Price with Cluster Detection — identifies dense 
    volume zones that act as strong support/resistance.
    
    Improvement over basic POC:
      - Groups adjacent high-volume bins into "clusters" (like HVN zones)
      - Separates Buy Volume vs Sell Volume at each level
      - Calculates Value Area (70% of total volume range)
    
    Returns:
        dict: poc_price, value_area_high (VAH), value_area_low (VAL),
              buy_dominated_zones, sell_dominated_zones,
              nearest_support, nearest_resistance
    """
    if len(klines_df) < 10:
        return {"poc_price": 0, "vah": 0, "val": 0,
                "nearest_support": 0, "nearest_resistance": 0}
    
    df = klines_df.copy()
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    
    price_min = float(df["low"].min())
    price_max = float(df["high"].max())
    
    if price_max == price_min:
        return {"poc_price": price_min, "vah": price_max, "val": price_min,
                "nearest_support": price_min, "nearest_resistance": price_max}
    
    bin_edges = np.linspace(price_min, price_max, num_bins + 1)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
    
    volume_profile = np.zeros(num_bins)
    buy_volume_profile = np.zeros(num_bins)
    sell_volume_profile = np.zeros(num_bins)
    
    # Build profiles
    bin_indices = np.clip(np.digitize(typical_price.values, bin_edges) - 1, 0, num_bins - 1)
    
    for idx, (vol, tp, o, c) in enumerate(zip(
        df["volume"].values, typical_price.values,
        df["open"].values, df["close"].values
    )):
        bin_idx = bin_indices[idx]
        volume_profile[bin_idx] += vol
        
        # Separate buy/sell volume by candle direction
        if c > o:
            buy_volume_profile[bin_idx] += vol * 0.7
            sell_volume_profile[bin_idx] += vol * 0.3
        else:
            buy_volume_profile[bin_idx] += vol * 0.3
            sell_volume_profile[bin_idx] += vol * 0.7
    
    # POC
    poc_idx = np.argmax(volume_profile)
    poc_price = float(bin_centers[poc_idx])
    
    # Value Area (70% of total volume)
    total_vol = volume_profile.sum()
    target_vol = total_vol * cluster_threshold_pct
    
    # Expand from POC outward until we capture 70%
    va_low_idx = poc_idx
    va_high_idx = poc_idx
    accumulated = volume_profile[poc_idx]
    
    while accumulated < target_vol and (va_low_idx > 0 or va_high_idx < num_bins - 1):
        add_low = volume_profile[va_low_idx - 1] if va_low_idx > 0 else 0
        add_high = volume_profile[va_high_idx + 1] if va_high_idx < num_bins - 1 else 0
        
        if add_low >= add_high and va_low_idx > 0:
            va_low_idx -= 1
            accumulated += add_low
        elif va_high_idx < num_bins - 1:
            va_high_idx += 1
            accumulated += add_high
        else:
            va_low_idx -= 1
            accumulated += add_low
    
    vah = float(bin_centers[va_high_idx])
    val = float(bin_centers[va_low_idx])
    
    # Current price context
    current_price = float(df["close"].iloc[-1])
    
    # Find nearest support (highest volume zone below current price)
    support_candidates = [(bin_centers[i], volume_profile[i]) 
                          for i in range(num_bins) if bin_centers[i] < current_price]
    nearest_support = max(support_candidates, key=lambda x: x[1])[0] if support_candidates else val
    
    # Find nearest resistance (highest volume zone above current price)
    resistance_candidates = [(bin_centers[i], volume_profile[i]) 
                             for i in range(num_bins) if bin_centers[i] > current_price]
    nearest_resistance = max(resistance_candidates, key=lambda x: x[1])[0] if resistance_candidates else vah
    
    return {
        "poc_price": round(poc_price, 2),
        "vah": round(vah, 2),
        "val": round(val, 2),
        "nearest_support": round(float(nearest_support), 2),
        "nearest_resistance": round(float(nearest_resistance), 2),
    }


# ═════════════════════════════════════════════════════════════════════════════
# ██  COMBINED ORDER FLOW ANALYSIS                                           ██
# ═════════════════════════════════════════════════════════════════════════════

def analyze_order_flow(klines_df: pd.DataFrame, order_book: dict) -> dict:
    """
    Run all Order Flow analyses at once.
    
    Args:
        klines_df: DataFrame with OHLCV + taker data
        order_book: Order book dict from Binance
    
    Returns:
        dict with all order flow results
    """
    return {
        "cvd": calculate_cvd(klines_df),
        "taker_ratio": calculate_taker_ratio(klines_df),
        "whale": detect_whale_orders(order_book),
        "absorption": detect_absorption(klines_df),
        "vap": calculate_vap_clusters(klines_df),
    }
