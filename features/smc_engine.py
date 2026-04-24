import os
import logging
from dataclasses import dataclass, field
import pandas as pd
import numpy as np

logger = logging.getLogger("InstitutionalBot")

@dataclass
class SMCSignal:
    signal: str  # "LONG", "SHORT", "WAIT", "NEUTRAL"
    confidence: float
    entry_zone: tuple = (0.0, 0.0)
    sl_price: float = 0.0
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    setup_type: str = ""
    components_found: list = field(default_factory=list)

def _calc_atr(df: pd.DataFrame, idx: int, period: int = 14) -> float:
    """Helper to calculate ATR if not present."""
    if idx < period:
        return 0.0
    sub = df.iloc[max(0, idx - period - 1) : idx + 1]
    if len(sub) < 2:
        return 0.0
    
    high = sub['high'].values
    low = sub['low'].values
    close = sub['close'].values
    
    tr = np.zeros(len(high) - 1)
    for i in range(1, len(high)):
        tr[i-1] = max(
            high[i] - low[i],
            abs(high[i] - close[i-1]),
            abs(low[i] - close[i-1])
        )
    return float(np.mean(tr[-period:]))

def _get_vol_ma(df: pd.DataFrame, idx: int, period: int = 20) -> float:
    """Helper to calculate Volume MA."""
    sub = df.iloc[max(0, idx - period):idx]
    if len(sub) == 0:
        return 0.0
    return float(sub['volume'].mean())

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMPONENT 1: LIQUIDITY SWEEP DETECTOR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def detect_liquidity_sweep(df: pd.DataFrame, lookback: int = 30, max_age: int = 20) -> dict:
    """
    Finds the most recent liquidity sweep within the last max_age candles.
    """
    current_idx = len(df) - 1
    start_idx = max(1, current_idx - max_age)
    
    # Iterate backwards to find the most recent sweep
    for i in range(current_idx, start_idx - 1, -1):
        window = df.iloc[max(0, i - lookback) : i]
        if len(window) < 5:
            continue
            
        swing_high = float(window['high'].max())
        swing_low = float(window['low'].min())
        
        candle = df.iloc[i]
        
        atr14 = df['atr14'].iloc[i] if 'atr14' in df.columns else _calc_atr(df, i)
        vol_ma20 = _get_vol_ma(df, i)
        
        if atr14 == 0 or vol_ma20 == 0:
            continue
            
        # BEARISH SWEEP (Bull Trap -> Short)
        wick_above = candle['high'] - swing_high
        if wick_above > 0 and candle['close'] < swing_high:
            if wick_above >= 0.5 * atr14 and candle['volume'] > 1.5 * vol_ma20:
                return {
                    'sweep_detected': True,
                    'sweep_type': 'BEARISH_SWEEP',
                    'swept_level': swing_high,
                    'sweep_candle_index': i,
                    'sweep_extreme': float(candle['high']),
                    'sweep_strength': float(wick_above / atr14)
                }
                
        # BULLISH SWEEP (Bear Trap -> Long)
        wick_below = swing_low - candle['low']
        if wick_below > 0 and candle['close'] > swing_low:
            if wick_below >= 0.5 * atr14 and candle['volume'] > 1.5 * vol_ma20:
                return {
                    'sweep_detected': True,
                    'sweep_type': 'BULLISH_SWEEP',
                    'swept_level': swing_low,
                    'sweep_candle_index': i,
                    'sweep_extreme': float(candle['low']),
                    'sweep_strength': float(wick_below / atr14)
                }
                
    return {'sweep_detected': False}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMPONENT 2: CHOCH DETECTOR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def detect_choch(df: pd.DataFrame, sweep_result: dict) -> dict:
    if not sweep_result.get('sweep_detected', False):
        return {'choch_confirmed': False}
        
    sweep_idx = sweep_result['sweep_candle_index']
    current_idx = len(df) - 1
    
    if current_idx <= sweep_idx:
        return {'choch_confirmed': False}
        
    # Analyze target swing points just prior to the sweep
    if sweep_result['sweep_type'] == 'BEARISH_SWEEP':
        # Took out highs, now we look for a break below the recent minor swing low (BEARISH CHOCH)
        target_level = float(df.iloc[max(0, sweep_idx - 5) : sweep_idx]['low'].min())
        target_type = 'BEARISH_CHOCH'
    else:
        # Took out lows, now we look for a break above the recent minor swing high (BULLISH CHOCH)
        target_level = float(df.iloc[max(0, sweep_idx - 5) : sweep_idx]['high'].max())
        target_type = 'BULLISH_CHOCH'
        
    # Scan candles after sweep (max 15)
    max_idx = min(current_idx, sweep_idx + 15)
    for i in range(sweep_idx + 1, max_idx + 1):
        candle = df.iloc[i]
        atr14 = df['atr14'].iloc[i] if 'atr14' in df.columns else _calc_atr(df, i)
        body_size = abs(candle['close'] - candle['open'])
        
        # Must be a strong candle body
        if atr14 > 0 and body_size >= 0.6 * atr14:
            if target_type == 'BULLISH_CHOCH' and candle['close'] > target_level:
                return {
                    'choch_confirmed': True,
                    'choch_type': target_type,
                    'choch_level': target_level,
                    'choch_candle_index': i,
                    'candles_after_sweep': i - sweep_idx
                }
            elif target_type == 'BEARISH_CHOCH' and candle['close'] < target_level:
                return {
                    'choch_confirmed': True,
                    'choch_type': target_type,
                    'choch_level': target_level,
                    'choch_candle_index': i,
                    'candles_after_sweep': i - sweep_idx
                }
                
    return {'choch_confirmed': False}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMPONENT 3: INDUCEMENT DETECTOR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def detect_inducement(df: pd.DataFrame, sweep_result: dict, choch_result: dict) -> dict:
    if not choch_result.get('choch_confirmed', False):
        return {'inducement_detected': False}
        
    choch_idx = choch_result['choch_candle_index']
    current_idx = len(df) - 1
    
    if current_idx <= choch_idx:
        return {'inducement_detected': False}
        
    choch_candle = df.iloc[choch_idx]
    choch_mid = (choch_candle['open'] + choch_candle['close']) / 2
    sweep_extreme = sweep_result.get('sweep_extreme', 0.0)
    
    for i in range(choch_idx + 1, current_idx + 1):
        candle = df.iloc[i]
        
        if choch_result['choch_type'] == 'BULLISH_CHOCH':
            # Market pulls back down below 50% of CHOCH candle
            if candle['low'] < choch_mid and candle['close'] > sweep_extreme:
                return {
                    'inducement_detected': True,
                    'inducement_type': 'BULLISH_INDUCEMENT',
                    'inducement_level': float(candle['low']),
                    'inducement_complete': True
                }
            if candle['low'] <= sweep_extreme:  # Sweep level violated -> setup failed
                break
                
        elif choch_result['choch_type'] == 'BEARISH_CHOCH':
            # Market pulls back up above 50% of CHOCH candle
            if candle['high'] > choch_mid and candle['close'] < sweep_extreme:
                return {
                    'inducement_detected': True,
                    'inducement_type': 'BEARISH_INDUCEMENT',
                    'inducement_level': float(candle['high']),
                    'inducement_complete': True
                }
            if candle['high'] >= sweep_extreme:
                break
                
    return {'inducement_detected': False}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMPONENT 4: ORDER BLOCK FINDER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def find_order_block(df: pd.DataFrame, sweep_result: dict, choch_result: dict) -> dict:
    if not choch_result.get('choch_confirmed', False):
        return {'ob_found': False}
        
    choch_idx = choch_result['choch_candle_index']
    search_start = max(0, choch_idx - 30)
    
    for i in range(choch_idx - 1, search_start - 1, -1):
        candle = df.iloc[i]
        atr14 = df['atr14'].iloc[i] if 'atr14' in df.columns else _calc_atr(df, i)
        if atr14 == 0:
            continue
            
        if choch_result['choch_type'] == 'BULLISH_CHOCH':
            # Look for last RED (bearish) candle before CHOCH
            if candle['close'] < candle['open']:
                ob_top = float(candle['open'])
                ob_bottom = float(candle['close'])
                ob_size = ob_top - ob_bottom
                
                if 0.5 * atr14 <= ob_size <= 3 * atr14:
                    return {
                        'ob_found': True,
                        'ob_top': ob_top,
                        'ob_bottom': ob_bottom,
                        'ob_mid': (ob_top + ob_bottom) / 2,
                        'ob_type': 'BULLISH_OB',
                        'ob_quality': min(1.0, ob_size / atr14)
                    }
                    
        elif choch_result['choch_type'] == 'BEARISH_CHOCH':
            # Look for last GREEN (bullish) candle before CHOCH
            if candle['close'] > candle['open']:
                ob_top = float(candle['close'])
                ob_bottom = float(candle['open'])
                ob_size = ob_top - ob_bottom
                
                if 0.5 * atr14 <= ob_size <= 3 * atr14:
                    return {
                        'ob_found': True,
                        'ob_top': ob_top,
                        'ob_bottom': ob_bottom,
                        'ob_mid': (ob_top + ob_bottom) / 2,
                        'ob_type': 'BEARISH_OB',
                        'ob_quality': min(1.0, ob_size / atr14)
                    }
                    
    return {'ob_found': False}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN FUNCTION: get_smc_signal
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_smc_signal(df: pd.DataFrame) -> SMCSignal:
    """
    Evaluates the full SMC pipeline and returns a standardized SMCSignal.
    """
    components = []
    
    # Step 1: Sweep
    sweep = detect_liquidity_sweep(df)
    if not sweep.get('sweep_detected', False):
        return SMCSignal(signal="NEUTRAL", confidence=0.0)
        
    components.append(f"SWEEP: {sweep['sweep_type']} @ {sweep['swept_level']:.2f}")
    confidence = 0.3
    
    # Step 2: CHOCH
    choch = detect_choch(df, sweep)
    if not choch.get('choch_confirmed', False):
        return SMCSignal(signal="NEUTRAL", confidence=0.0)
        
    components.append(f"CHOCH: {choch['choch_type']} @ {choch['choch_level']:.2f}")
    confidence = 0.5
    
    # Step 3: Inducement
    inducement = detect_inducement(df, sweep, choch)
    if inducement.get('inducement_detected', False):
        components.append(f"INDUCEMENT: {inducement['inducement_type']} @ {inducement['inducement_level']:.2f}")
        confidence = 0.65
        
    # Step 4: Order Block
    ob = find_order_block(df, sweep, choch)
    if not ob.get('ob_found', False):
        return SMCSignal(signal="NEUTRAL", confidence=0.0)
        
    components.append(f"OB: {ob['ob_type']} [{ob['ob_bottom']:.2f}-{ob['ob_top']:.2f}] (Qual: {ob['ob_quality']:.2f})")
    
    # Step 5: Entry Check
    current_price = float(df.iloc[-1]['close'])
    atr_current = _calc_atr(df, len(df) - 1)
    signal = "WAIT"
    
    if ob['ob_type'] == 'BULLISH_OB':
        if ob['ob_bottom'] <= current_price <= ob['ob_top']:
            signal = "LONG"
            confidence = 0.80 if ob['ob_quality'] <= 0.8 else 0.90
            
        sl_price = sweep['sweep_extreme'] - (0.2 * atr_current)
        tp1_price = choch['choch_level'] + (choch['choch_level'] - ob['ob_mid'])
        tp2_price = tp1_price + (tp1_price - ob['ob_mid'])
        
    else:  # BEARISH_OB
        if ob['ob_bottom'] <= current_price <= ob['ob_top']:
            signal = "SHORT"
            confidence = 0.80 if ob['ob_quality'] <= 0.8 else 0.90
            
        sl_price = sweep['sweep_extreme'] + (0.2 * atr_current)
        tp1_price = choch['choch_level'] - (choch['choch_level'] - ob['ob_mid'])
        tp2_price = tp1_price - (tp1_price - ob['ob_mid'])

    setup_type = f"{sweep['sweep_type']} + {choch['choch_type']}"

    # Logging
    logger.info(f"[SMC] SWEEP detected: {sweep['sweep_type']} at {sweep['swept_level']:.2f} (strength: {sweep['sweep_strength']:.1f}x)")
    logger.info(f"[SMC] CHOCH confirmed: {choch['choch_type']} broke {choch['choch_level']:.2f} ({choch['candles_after_sweep']} candles after sweep)")
    if inducement.get('inducement_detected', False):
        logger.info(f"[SMC] INDUCEMENT: Pullback to {inducement['inducement_level']:.2f} — trap complete")
    logger.info(f"[SMC] ORDER BLOCK: {ob['ob_bottom']:.2f}-{ob['ob_top']:.2f} (quality: {ob['ob_quality']:.2f})")
    logger.info(f"[SMC] SIGNAL: {signal} | Entry: {ob['ob_bottom']:.2f}-{ob['ob_top']:.2f} | SL: {sl_price:.2f} | TP1: {tp1_price:.2f}")

    return SMCSignal(
        signal=signal,
        confidence=confidence,
        entry_zone=(ob['ob_bottom'], ob['ob_top']),
        sl_price=sl_price,
        tp1_price=tp1_price,
        tp2_price=tp2_price,
        setup_type=setup_type,
        components_found=components
    )
