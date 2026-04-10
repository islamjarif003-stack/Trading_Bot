"""
================================================================================
  FreqtradePatterns.py — Community Strategy Patterns (Freqtrade-Inspired)
  
  Proven entry/exit patterns extracted from top-performing Freqtrade strategies.
  Zero external dependencies — pure numpy + pandas math.
  
  Patterns:
    1. Bollinger Band Squeeze → Volatility breakout detection
    2. MACD Histogram Crossover → Momentum shift
    3. Stochastic RSI → Oversold/Overbought with momentum
    4. EMA Triple Cross (8/21/50) → Multi-timeframe trend alignment
    5. Heikin-Ashi Trend Confirmation → Noise-filtered candle direction
================================================================================
"""

import numpy as np
import pandas as pd
import logging

log = logging.getLogger("InstitutionalBot")


# ═════════════════════════════════════════════════════════════════════════════
# ██  1. BOLLINGER BAND SQUEEZE                                             ██
# ═════════════════════════════════════════════════════════════════════════════

def detect_bb_squeeze(closes: pd.Series, period: int = 20, 
                      std_mult: float = 2.0, squeeze_pct: float = 0.02) -> dict:
    """
    Detect Bollinger Band Squeeze — a period of ultra-low volatility that
    typically precedes a large directional breakout.
    
    Logic (from Freqtrade BBRSIStrategy):
      - BB Width < squeeze_pct (2%) of price → Squeeze is ON
      - Price breaks above upper band + close > SMA → BUY breakout
      - Price breaks below lower band + close < SMA → SELL breakout
    
    Returns:
        dict with keys: squeeze_on, direction ("BUY"/"SELL"/"NONE"), bb_width
    """
    if len(closes) < period + 2:
        return {"squeeze_on": False, "direction": "NONE", "bb_width": 0.0}
    
    sma = closes.rolling(window=period).mean()
    std = closes.rolling(window=period).std()
    
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    
    # BB Width as percentage of middle band
    bb_width = ((upper - lower) / sma * 100.0).iloc[-1]
    prev_bb_width = ((upper - lower) / sma * 100.0).iloc[-2]
    
    current_close = closes.iloc[-1]
    current_sma = sma.iloc[-1]
    current_upper = upper.iloc[-1]
    current_lower = lower.iloc[-1]
    
    # Squeeze = BB Width < threshold
    squeeze_on = float(bb_width) < squeeze_pct * 100
    
    # Breakout detection: squeeze was on, now expanding
    direction = "NONE"
    if prev_bb_width < squeeze_pct * 100 and bb_width >= prev_bb_width:
        # Squeeze releasing — determine direction
        if current_close > current_upper and current_close > current_sma:
            direction = "BUY"
        elif current_close < current_lower and current_close < current_sma:
            direction = "SELL"
    elif not squeeze_on:
        # Not in squeeze — check for trend continuation
        if current_close > current_upper:
            direction = "BUY"
        elif current_close < current_lower:
            direction = "SELL"
    
    return {
        "squeeze_on": squeeze_on,
        "direction": direction,
        "bb_width": round(float(bb_width), 4),
    }


# ═════════════════════════════════════════════════════════════════════════════
# ██  2. MACD HISTOGRAM CROSSOVER                                           ██
# ═════════════════════════════════════════════════════════════════════════════

def detect_macd_cross(closes: pd.Series, fast: int = 12, slow: int = 26, 
                      signal_period: int = 9) -> dict:
    """
    MACD Histogram Crossover — detects momentum shift when histogram
    crosses the zero line.
    
    Logic (from Freqtrade MACDStrategy):
      - MACD histogram crosses from negative to positive → BUY
      - MACD histogram crosses from positive to negative → SELL
      - Stronger signal if histogram is accelerating (growing bars)
    
    Returns:
        dict with keys: direction, histogram, strength (0-1)
    """
    if len(closes) < slow + signal_period + 2:
        return {"direction": "NONE", "histogram": 0.0, "strength": 0.0}
    
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    histogram = macd_line - signal_line
    
    current_hist = float(histogram.iloc[-1])
    prev_hist = float(histogram.iloc[-2])
    prev2_hist = float(histogram.iloc[-3]) if len(histogram) > 2 else prev_hist
    
    direction = "NONE"
    strength = 0.0
    
    # Zero-line crossover
    if prev_hist <= 0 and current_hist > 0:
        direction = "BUY"
        # Strength = how fast histogram is growing
        strength = min(abs(current_hist - prev_hist) / (abs(prev_hist) + 1e-10), 1.0)
    elif prev_hist >= 0 and current_hist < 0:
        direction = "SELL"
        strength = min(abs(current_hist - prev_hist) / (abs(prev_hist) + 1e-10), 1.0)
    else:
        # No crossover, but check for histogram acceleration (trend strength)
        if current_hist > 0 and current_hist > prev_hist and prev_hist > prev2_hist:
            direction = "BUY"
            strength = 0.5  # Weaker — just acceleration, not crossover
        elif current_hist < 0 and current_hist < prev_hist and prev_hist < prev2_hist:
            direction = "SELL"
            strength = 0.5
    
    return {
        "direction": direction,
        "histogram": round(current_hist, 4),
        "strength": round(strength, 4),
    }


# ═════════════════════════════════════════════════════════════════════════════
# ██  3. STOCHASTIC RSI                                                      ██
# ═════════════════════════════════════════════════════════════════════════════

def detect_stoch_rsi(closes: pd.Series, rsi_period: int = 14, 
                     stoch_period: int = 14, k_smooth: int = 3, 
                     d_smooth: int = 3) -> dict:
    """
    Stochastic RSI — applies Stochastic oscillator to RSI values for
    smoother oversold/overbought signals with momentum confirmation.
    
    Logic (from Freqtrade StochRSIStrategy):
      - StochRSI K crosses above D in oversold zone (< 20) → BUY
      - StochRSI K crosses below D in overbought zone (> 80) → SELL
    
    Returns:
        dict with keys: direction, k_value, d_value, zone
    """
    if len(closes) < rsi_period + stoch_period + k_smooth + d_smooth:
        return {"direction": "NONE", "k_value": 50.0, "d_value": 50.0, "zone": "NEUTRAL"}
    
    # Step 1: Calculate RSI
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/rsi_period, min_periods=rsi_period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/rsi_period, min_periods=rsi_period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    
    # Step 2: Apply Stochastic to RSI
    rsi_min = rsi.rolling(window=stoch_period).min()
    rsi_max = rsi.rolling(window=stoch_period).max()
    rsi_range = rsi_max - rsi_min
    
    stoch_rsi = ((rsi - rsi_min) / rsi_range.replace(0, 1e-10)) * 100.0
    
    # Step 3: Smooth K and D lines
    k_line = stoch_rsi.rolling(window=k_smooth).mean()
    d_line = k_line.rolling(window=d_smooth).mean()
    
    current_k = float(k_line.iloc[-1])
    current_d = float(d_line.iloc[-1])
    prev_k = float(k_line.iloc[-2])
    prev_d = float(d_line.iloc[-2])
    
    # Determine zone
    if current_k < 20:
        zone = "OVERSOLD"
    elif current_k > 80:
        zone = "OVERBOUGHT"
    else:
        zone = "NEUTRAL"
    
    direction = "NONE"
    
    # K crosses above D in oversold → BUY
    if prev_k <= prev_d and current_k > current_d and current_k < 30:
        direction = "BUY"
    # K crosses below D in overbought → SELL
    elif prev_k >= prev_d and current_k < current_d and current_k > 70:
        direction = "SELL"
    
    return {
        "direction": direction,
        "k_value": round(current_k, 2),
        "d_value": round(current_d, 2),
        "zone": zone,
    }


# ═════════════════════════════════════════════════════════════════════════════
# ██  4. EMA TRIPLE CROSS (8 / 21 / 50)                                     ██
# ═════════════════════════════════════════════════════════════════════════════

def detect_ema_triple_cross(closes: pd.Series) -> dict:
    """
    Triple EMA Cross — when 3 EMAs align in order, it's a strong trend signal.
    
    Logic (from Freqtrade MultiEMAStrategy):
      - EMA8 > EMA21 > EMA50 AND all rising → Strong BUY
      - EMA8 < EMA21 < EMA50 AND all falling → Strong SELL
      - Partial alignment → Weak signal
    
    Returns:
        dict with keys: direction, alignment ("FULL"/"PARTIAL"/"NONE"),
                        ema8, ema21, ema50
    """
    if len(closes) < 55:
        return {"direction": "NONE", "alignment": "NONE", 
                "ema8": 0.0, "ema21": 0.0, "ema50": 0.0}
    
    ema8 = closes.ewm(span=8, adjust=False).mean()
    ema21 = closes.ewm(span=21, adjust=False).mean()
    ema50 = closes.ewm(span=50, adjust=False).mean()
    
    c_ema8 = float(ema8.iloc[-1])
    c_ema21 = float(ema21.iloc[-1])
    c_ema50 = float(ema50.iloc[-1])
    
    p_ema8 = float(ema8.iloc[-2])
    p_ema21 = float(ema21.iloc[-2])
    p_ema50 = float(ema50.iloc[-2])
    
    # Check alignment
    bull_aligned = c_ema8 > c_ema21 > c_ema50
    bear_aligned = c_ema8 < c_ema21 < c_ema50
    
    # Check if all EMAs are accelerating in the same direction
    ema8_rising = c_ema8 > p_ema8
    ema21_rising = c_ema21 > p_ema21
    ema50_rising = c_ema50 > p_ema50
    
    ema8_falling = c_ema8 < p_ema8
    ema21_falling = c_ema21 < p_ema21
    ema50_falling = c_ema50 < p_ema50
    
    direction = "NONE"
    alignment = "NONE"
    
    if bull_aligned:
        if ema8_rising and ema21_rising and ema50_rising:
            direction = "BUY"
            alignment = "FULL"
        else:
            direction = "BUY"
            alignment = "PARTIAL"
    elif bear_aligned:
        if ema8_falling and ema21_falling and ema50_falling:
            direction = "SELL"
            alignment = "FULL"
        else:
            direction = "SELL"
            alignment = "PARTIAL"
    else:
        # Partial: EMA8 crossed EMA21 but not yet EMA50
        if c_ema8 > c_ema21 and p_ema8 <= p_ema21:
            direction = "BUY"
            alignment = "PARTIAL"
        elif c_ema8 < c_ema21 and p_ema8 >= p_ema21:
            direction = "SELL"
            alignment = "PARTIAL"
    
    return {
        "direction": direction,
        "alignment": alignment,
        "ema8": round(c_ema8, 2),
        "ema21": round(c_ema21, 2),
        "ema50": round(c_ema50, 2),
    }


# ═════════════════════════════════════════════════════════════════════════════
# ██  5. HEIKIN-ASHI TREND CONFIRMATION                                      ██
# ═════════════════════════════════════════════════════════════════════════════

def detect_heikin_ashi_trend(opens: pd.Series, highs: pd.Series, 
                              lows: pd.Series, closes: pd.Series,
                              lookback: int = 3) -> dict:
    """
    Heikin-Ashi candle trend confirmation — smoothed candles that filter
    noise and show cleaner trends.
    
    Logic (from Freqtrade HAStrategy):
      - N consecutive green HA candles with no lower wick → Strong BUY
      - N consecutive red HA candles with no upper wick → Strong SELL
      - Mixed → NONE
    
    Returns:
        dict with keys: direction, consecutive_count, strength
    """
    if len(closes) < lookback + 2:
        return {"direction": "NONE", "consecutive_count": 0, "strength": 0.0}
    
    # Build Heikin-Ashi candles
    ha_close = (opens + highs + lows + closes) / 4.0
    
    ha_open = pd.Series(0.0, index=opens.index)
    ha_open.iloc[0] = (opens.iloc[0] + closes.iloc[0]) / 2.0
    
    for i in range(1, len(opens)):
        ha_open.iloc[i] = (ha_open.iloc[i-1] + ha_close.iloc[i-1]) / 2.0
    
    ha_high = pd.concat([highs, ha_open, ha_close], axis=1).max(axis=1)
    ha_low = pd.concat([lows, ha_open, ha_close], axis=1).min(axis=1)
    
    # Analyze last N candles
    bullish_count = 0
    bearish_count = 0
    
    for i in range(-lookback, 0):
        ha_o = ha_open.iloc[i]
        ha_c = ha_close.iloc[i]
        ha_h = ha_high.iloc[i]
        ha_l = ha_low.iloc[i]
        
        is_green = ha_c > ha_o
        is_red = ha_c < ha_o
        
        # Strong bullish: green candle with no lower wick (ha_low == ha_open)
        if is_green and abs(ha_l - ha_o) < (ha_c - ha_o) * 0.1:
            bullish_count += 1
        elif is_green:
            bullish_count += 0.5  # Weak green
        
        # Strong bearish: red candle with no upper wick (ha_high == ha_open)
        if is_red and abs(ha_h - ha_o) < (ha_o - ha_c) * 0.1:
            bearish_count += 1
        elif is_red:
            bearish_count += 0.5  # Weak red
    
    direction = "NONE"
    strength = 0.0
    consecutive = 0
    
    if bullish_count >= lookback * 0.7:
        direction = "BUY"
        consecutive = int(bullish_count)
        strength = min(bullish_count / lookback, 1.0)
    elif bearish_count >= lookback * 0.7:
        direction = "SELL"
        consecutive = int(bearish_count)
        strength = min(bearish_count / lookback, 1.0)
    
    return {
        "direction": direction,
        "consecutive_count": consecutive,
        "strength": round(strength, 2),
    }


# ═════════════════════════════════════════════════════════════════════════════
# ██  COMBINED ANALYSIS (convenience wrapper)                                ██
# ═════════════════════════════════════════════════════════════════════════════

def analyze_all_patterns(klines_df: pd.DataFrame) -> dict:
    """
    Run all 5 Freqtrade-inspired pattern detectors at once.
    
    Args:
        klines_df: DataFrame with columns: open, high, low, close, volume
    
    Returns:
        dict with all pattern results keyed by pattern name
    """
    closes = klines_df["close"]
    opens = klines_df["open"]
    highs = klines_df["high"]
    lows = klines_df["low"]
    
    return {
        "bb_squeeze": detect_bb_squeeze(closes),
        "macd_cross": detect_macd_cross(closes),
        "stoch_rsi": detect_stoch_rsi(closes),
        "ema_triple": detect_ema_triple_cross(closes),
        "heikin_ashi": detect_heikin_ashi_trend(opens, highs, lows, closes),
    }
