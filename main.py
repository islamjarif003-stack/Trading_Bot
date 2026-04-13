"""
================================================================================
  main.py — Institutional Execution Engine v7.1 (Backtest-Optimized Edition)
  ★ FIX 1: ATR SL/TP with 1:2 R:R (SL=1.5×ATR, TP=3.0×ATR)
  ★ FIX 2: Limit Order Entries (Maker Fees — saves ~80% on fees)
  ★ BTC Correlation Filter (Skip/Reduce correlated trades)
  ★ Stepped Profit Taking (30% TP1 + 30% TP2 + 40% Runner)
  ★ 3-Phase Smart Trailing SL (Break-Even → Profit Lock → Dynamic Trail)
  Asset: Multi-Pair | Exchange: Binance Futures Testnet | Leverage: 20x
================================================================================
"""

import os
import time
import math
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException, BinanceRequestException
import requests

from SignalEngine import get_quant_signal, ml_filter, WHALE_THRESHOLDS, WHALE_RANGE_PCT
from StateExporter import StateExporter

# ═════════════════════════════════════════════════════════════════════════════
# ██  BINANCE FUTURES TESTNET CREDENTIALS                  ██
# ██  Option 1: Set env vars  BINANCE_API_KEY / BINANCE_API_SECRET          ██
# ██  Option 2: Paste directly below                                        ██
# ═════════════════════════════════════════════════════════════════════════════
API_KEY    = os.environ.get("BINANCE_API_KEY",    "")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
# ═════════════════════════════════════════════════════════════════════════════


# ─── TRADING CONFIGURATION ───────────────────────────────────────────────────
ENABLE_DYNAMIC_WATCHLIST = True # ★ Fetch Top 50 Volatile USDT pairs dynamically
ENABLE_MICRO_SCALPING = False   # ★ v12: DISABLED — Pre-flight fail = NO TRADE (no more weak entries)
SYMBOLS         = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

# ★ v15: BLACKLIST — Dead coins (Price $0.0) + consistent losers from audit
BLACKLIST_COINS = {
    # Dead coins (Price $0.0 on testnet)
    "OMNIUSDT", "ALPHAUSDT", "BSWUSDT", "HIFIUSDT", "NEIROETHUSDT",
    "A2ZUSDT", "TANSSIUSDT", "NKNUSDT", "BDXNUSDT", "LITUSDT", "BAKEUSDT",
    # Consistent losers from audit (0% win rate, heavy losses)
    "MAGMAUSDT", "BULLAUSDT", "TNSRUSDT", "SIRENUSDT",
    # v15: Near-liquidation risk — SOONUSDT hit -50% / 61% margin ratio
    "SOONUSDT", "TRADOORUSDT",
}
LEVERAGE        = 20                # ★ FIXED 20x leverage
SL_ATR_MULT     = 1.5               # ★ v10: SL = 1.5 × ATR (gives trade room to breathe)
TP_ATR_MULT     = 3.0               # ★ v10: TP = 3.0 × ATR (R:R = 1:2)
HARD_LOCKOUT_S  = 1800              # 30-minute hard lockout after every trade
LOOP_INTERVAL_S = 10                # Seconds between each scan cycle
ENTRY_RISK_PCT  = 15.0              # ★ $50 Config: 15% of $50 ≈ $7.50 risk per trade
MAX_MARGIN_PCT  = 30.0              # ★ $50 Config: Max 30% of balance as margin ($15 max)
LIMIT_OFFSET_PCT = 0.005            # ★ FIX 2: 0.005% offset for limit order (ultra-tight fill)
ADX_ENTRY_MIN   = 5.0               # ★ v20: Lowered from 7.0 — SMC works in ranging markets, only filter dead markets
SCAN_DELAY_S    = 1                 # ★ v7.2: Delay between each coin scan (API rate-limit safety)
MAX_DAILY_LOSS_PCT = 10.0            # ★ $50 Config: 10% = $5 daily loss limit (2 SL trades)

# ─── ★ v7.4: CONSECUTIVE LOSS COOLDOWN ──────────────────────────────────────
MAX_CONSEC_LOSSES   = 3         # After 3 consecutive losses on a coin...
LOSS_COOLDOWN_S     = 7200      # ...add 2-hour extra cooldown for that coin

# ─── ★ v7.4: VOLATILITY SPIKE FILTER ────────────────────────────────────────
ATR_SPIKE_MULT      = 2.0       # Skip entry if current ATR > 2× recent average ATR

# ─── ★ v11.1: ANTI-FOMO ENTRY GATE (Hard Blocker) ──────────────────────────
ANTI_FOMO_EMA_DIST_PCT  = 0.30  # ★ v16.1: Widened to 0.30% to prevent blocking good setups (was 0.18%)
ANTI_FOMO_CANDLE_BODY   = 0.60  # Block if candle body is >60% of total range (impulsive candle)
ANTI_FOMO_ENABLED       = True  # Master switch for anti-FOMO gate

# ─── ★ PER-SYMBOL PRECISION MAPPING ─────────────────────────────────────────
SYMBOL_PRECISION = {
    "BTCUSDT": {"price": 1, "qty": 3},
    "ETHUSDT": {"price": 2, "qty": 2},
    "SOLUSDT": {"price": 2, "qty": 1},
    "BNBUSDT": {"price": 2, "qty": 2},
    "XRPUSDT": {"price": 4, "qty": 0},
}
DEFAULT_PRICE_PRECISION = 2
DEFAULT_QTY_PRECISION   = 3

# ─── ★ BTC CORRELATION FILTER ──────────────────────────────────────────────
CORRELATION_THRESHOLD = 0.80    # Skip/reduce if corr > 0.80
CORR_ACTION           = "SKIP"  # "SKIP" = skip trade, "REDUCE" = 50% size
CORR_REDUCE_SIZE_PCT  = 50      # Reduce to 50% if CORR_ACTION == "REDUCE"

# ─── ★ BREAK-EVEN & TRAILING STOP-LOSS ("Let Winners Run" Edition) ───────
# Note: These are RAW price % (Unleveraged). A 0.4% raw move = 8% on Binance at 20x leverage.
# ★ v17: TRUE BREAK-EVEN — uses dynamic R:R, not static %. BE triggers at 1R profit.
TRUE_BE_FEE_BUFFER_PCT   = 0.15   # ★ v17: Fee-adjusted BE — covers 0.05% entry + 0.05% exit + 0.05% safety
TRAILING_ACTIVATION_RR   = 1.5    # ★ v17: Trailing ONLY activates after 1:1.5 R:R is achieved
TRAILING_SL_DISTANCE_PCT = 0.70   # ★ v16.1: Trail SL 0.70% behind highest/lowest price (was 0.50% — giving more room)
TTP_CHECK_INTERVAL       = 3      # Check every 3 cycles
DISABLE_HARD_TP          = True    # ★ v12: NO fixed TP → let trailing SL manage exit
SMART_REVERSAL_EXIT      = True    # ★ v12: Close if 5m MA25 cross-under/over detected
STALE_TRADE_MIN_LOSS_PCT = -0.50   # ★ v17: Stale timeout only fires if PnL < -0.50% (prevents fee-draining flat closes)


# ─── LOGGING ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("trading_bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("InstitutionalBot")

# ─── TELEGRAM HELPER ─────────────────────────────────────────────────────────
def send_telegram_alert(message: str):
    """Sends a text message to the configured Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message[:4000],
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=3)
    except Exception as e:
        log.warning(f"⚠  Telegram alert failed: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# ██  UTILITIES                                                             ██
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_symbol_precision(client: Client, symbol: str):
    """★ v12: Auto-fetch price and qty precision from Binance for any symbol."""
    try:
        info = client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == symbol:
                price_prec = s.get("pricePrecision", DEFAULT_PRICE_PRECISION)
                qty_prec = s.get("quantityPrecision", DEFAULT_QTY_PRECISION)
                SYMBOL_PRECISION[symbol] = {"price": price_prec, "qty": qty_prec}
                log.info(f"📐  [{symbol}] Auto-precision: price={price_prec}, qty={qty_prec}")
                return
    except Exception as e:
        log.warning(f"⚠  [{symbol}] Failed to fetch precision: {e}")


def _round_price(price: float, symbol: str = "BTCUSDT") -> float:
    prec = SYMBOL_PRECISION.get(symbol, {}).get("price", DEFAULT_PRICE_PRECISION)
    return round(price, prec)


def _round_qty(qty: float, symbol: str = "BTCUSDT") -> float:
    prec = SYMBOL_PRECISION.get(symbol, {}).get("qty", DEFAULT_QTY_PRECISION)
    return math.floor(qty * (10 ** prec)) / (10 ** prec)


def _get_account_balance(client: Client) -> float:
    balances = client.futures_account_balance()
    for b in balances:
        if b["asset"] == "USDT":
            return float(b["availableBalance"])
    return 0.0


def _has_open_position(client: Client, symbol: str) -> dict:
    """Returns position info dict or None."""
    positions = client.futures_position_information(symbol=symbol)
    for pos in positions:
        amt = float(pos["positionAmt"])
        if abs(amt) > 0:
            return {
                "side": "BUY" if amt > 0 else "SELL",
                "qty": abs(amt),
                "entry_price": float(pos["entryPrice"]),
                "unrealized_pnl": float(pos["unRealizedProfit"]),
                "mark_price": float(pos["markPrice"]),
            }
    return None


def _cancel_all_open_orders(client: Client, symbol: str):
    try:
        client.futures_cancel_all_open_orders(symbol=symbol)
    except BinanceAPIException as e:
        log.warning(f"⚠  [{symbol}] Could not cancel orders: {e.message}")


def _get_current_atr(client: Client, symbol: str) -> float:
    """Fetch fresh ATR(14) for adaptive trailing."""
    import pandas as pd
    raw = client.futures_klines(
        symbol=symbol,
        interval=Client.KLINE_INTERVAL_1HOUR,
        limit=30,
    )
    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    for col in ["high", "low", "close"]:
        df[col] = df[col].astype(float)

    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    return float(atr.iloc[-1])


# ═════════════════════════════════════════════════════════════════════════════
# ██  ★ BTC CORRELATION FILTER HELPERS                                      ██
# ═════════════════════════════════════════════════════════════════════════════

def _check_btc_same_direction(client: Client, signal: str) -> bool:
    """
    Check if BTC already has an open position in the same direction.
    Returns True if BTC has a position matching signal direction.
    """
    try:
        btc_pos = _has_open_position(client, "BTCUSDT")
        if btc_pos is None:
            return False
        if signal == "BUY" and btc_pos["side"] == "BUY":
            return True
        if signal == "SELL" and btc_pos["side"] == "SELL":
            return True
    except Exception as e:
        log.warning(f"⚠  BTC position check failed: {e}")
    return False


# ═════════════════════════════════════════════════════════════════════════════
# ██  SCORE DASHBOARD PRINTER                                              ██
# ═════════════════════════════════════════════════════════════════════════════

def _print_score_dashboard(signal_data: dict, scan_count: int, symbol: str):
    """Prints a rich, formatted score breakdown to the terminal."""
    signal = signal_data.get("signal", "NONE")
    score = signal_data.get("score", 0)
    threshold = signal_data.get("score_threshold", 9)
    max_score = signal_data.get("max_score", 12)
    breakdown = signal_data.get("score_breakdown", [])
    waiting_for = signal_data.get("waiting_for", [])
    regime = signal_data.get("regime", "UNKNOWN")
    adx = signal_data.get("adx", 0.0)
    atr = signal_data.get("atr", 0.0)
    rsi = signal_data.get("rsi", 0.0)
    price = signal_data.get("current_price", 0.0)
    poc = signal_data.get("poc_price", 0.0)
    poc_dist = signal_data.get("poc_distance_pct", 0.0)
    h1_trend = signal_data.get("h1_trend", "UNKNOWN")
    fibo_gp = "✅" if signal_data.get("fibo_gp_active", False) else "❌"
    liq_1 = signal_data.get("liq_magnet_1", 0.0)

    # ★ Derivative Data
    oi = signal_data.get("open_interest", 0.0)
    fr = signal_data.get("funding_rate", 0.0)
    oi_trend = signal_data.get("oi_trend", "FLAT")
    price_trend = signal_data.get("price_trend", "FLAT")

    # ★ ML Filter Data
    ml_prob = signal_data.get("ml_win_prob", 1.0)
    ml_vetoed = signal_data.get("ml_vetoed", False)
    ml_stats = signal_data.get("ml_stats", {})

    # ★ BTC Correlation
    btc_corr = signal_data.get("btc_correlation", 0.0)

    # Score bar visualization
    filled = min(int((score / max(max_score, 1)) * 20), 20)
    bar = "█" * filled + "░" * (20 - filled)

    # Status emoji
    if signal in ("BUY", "SELL"):
        status = f"🚀 {signal}"
    elif ml_vetoed:
        status = f"🤖 ML VETO"
    elif "Vetoed" in str(breakdown):
        status = f"🚫 VETO ({h1_trend})"
    elif score >= threshold - 2:
        status = "⚡ CLOSE"
    else:
        status = "⏳ WAIT"

    log.info("─" * 70)
    log.info(f"SCAN #{scan_count:04d} │ [{symbol}] │ {status}")
    log.info(f"  Price: ${price}  │  POC: ${poc} ({poc_dist:.3f}%)  │  Liq Zone: ${liq_1}")
    log.info(f"  Regime: {regime} (ADX: {adx})  │  H1 Trend: {h1_trend}  │  Fib GP: {fibo_gp}")
    log.info(f"  Score: [{bar}] {score}/{max_score} (need ≥{threshold})")

    # ★ Derivative Data Row
    oi_arrow = "↑" if oi_trend == "RISING" else ("↓" if oi_trend == "FALLING" else "→")
    fr_pct = fr * 100
    log.info(f"  ★ OI: {oi:.2f} ({oi_arrow}{oi_trend})  │  FR: {fr_pct:.4f}%  │  Price: {price_trend}")

    # ★ ML Filter Row
    ml_total = ml_stats.get("total", 0)
    ml_wr = ml_stats.get("win_rate", 0.0)
    ml_status = "VETO" if ml_vetoed else ("ACTIVE" if ml_total >= 10 else f"LEARNING ({ml_total}/10)")
    log.info(f"  ★ ML: {ml_status}  │  Win Prob: {ml_prob*100:.1f}%  │  Buffer: {ml_total} trades (WR: {ml_wr}%)")

    # ★ Correlation & Risk Row
    corr_status = "⚠ HIGH" if btc_corr > CORRELATION_THRESHOLD else "✅ OK"
    log.info(f"  ★ BTC Corr: {btc_corr:+.4f} ({corr_status})  │  Risk: $1 Sniper Margin  │  SL: {SL_ATR_MULT}×ATR")

    # Score breakdown
    if breakdown:
        log.info("  ┌─ SCORE BREAKDOWN:")
        for item in breakdown:
            log.info(f"  │  ✦ {item}")
        log.info("  └─")

    # What's missing
    if signal == "NONE" and waiting_for:
        log.info(f"  ⏳ Waiting for: {', '.join(waiting_for)}")

    log.info("─" * 70)



# ═════════════════════════════════════════════════════════════════════════════
# ██  ORDER EXECUTION (★ V7.1: Limit Entry + ATR SL/TP with 1:2 R:R)       ██
# ═════════════════════════════════════════════════════════════════════════════

def execute_trade(client: Client, symbol: str, signal: str, current_price: float,
                  atr: float, signal_data: dict, size_multiplier: float = 1.0, is_micro_scalp: bool = False) -> tuple:
    """
    ★ V7.1: Execute a LIMIT order (maker fee) with:
      - ATR-based Stop-Loss  (1.5 × ATR)
      - ATR-based Take-Profit (3.0 × ATR) → 1:2 Risk:Reward
    
    Falls back to MARKET order if LIMIT is not supported or errors.
    Single-shot entry (no pyramiding). Size can be reduced via size_multiplier.
    
    Returns:
        tuple: (success: bool, entry_qty: float)
    """
    try:
        # ★ v12: Auto-fetch precision for dynamic watchlist coins
        if symbol not in SYMBOL_PRECISION:
            _fetch_symbol_precision(client, symbol)

        # ★ FIX: Use 1-Hour ATR for SL/TP distances so they are large enough to breathe
        try:
            atr_1h = _get_current_atr(client, symbol)
            if atr_1h > 0:
                atr = atr_1h
        except Exception as atr_err:
            log.warning(f"⚠ [{symbol}] Could not fetch 1H ATR, using provided ATR: {atr_err}")

        # ★ Upgraded Position Sizing: Dynamic 10% of Account Balance with $1 Floor
        try:
            account = client.futures_account()
            total_balance = float(account.get("totalWalletBalance", 0.0))
            calc_margin = total_balance * 0.10
            dynamic_margin = max(calc_margin, 1.00)  # Floor at $1.00
        except Exception as bal_err:
            log.warning(f"⚠ [{symbol}] Could not fetch balance. Defaulting margin to $1.00. Error: {bal_err}")
            total_balance = 0.0
            dynamic_margin = 1.00
            
        position_value_usd = dynamic_margin * LEVERAGE
        raw_qty = position_value_usd / current_price
        quantity = _round_qty(raw_qty, symbol)

        # ATR-based SL/TP distances (fallback)
        sl_distance = atr * SL_ATR_MULT   # 1.5 × ATR
        tp_distance = atr * TP_ATR_MULT   # 3.0 × ATR (1:2 R:R)
        
        # ★ v20 SMC Override: Use deeply calculated structural SL behind Liquidity Sweep
        if signal_data and signal_data.get("smc_zone") and signal_data["smc_zone"].get("sl_distance", 0) > 0:
            sl_distance = signal_data["smc_zone"]["sl_distance"]
            tp_distance = sl_distance * 2.0  # Mathematically strict 1:2 R:R based on Structure
            
        risk_amount = dynamic_margin        # For logging only
        
        if is_micro_scalp:
            # Overwrite standard ATR with tight Micro-Scalp static targets
            tp_distance = current_price * 0.0025  # 0.25% raw target
            sl_distance = current_price * 0.0050  # 0.50% raw Stop Loss
            log.warning(f"⚡  [{symbol}] MICRO-SCALP Override Active! TP: 0.25% │ SL: 0.50%")

        if sl_distance <= 0:
            log.warning("⚠  ATR is zero. Cannot calculate SL/TP. Skipping.")
            return False, 0.0

        log.info(f"   ★ DYNAMIC MARGIN: ${dynamic_margin:.2f} (10% of Bal: ${total_balance:.2f}) × {LEVERAGE}x = ${position_value_usd:.2f} notional → qty: {quantity}")

        if quantity <= 0:
            log.warning("⚠  Calculated quantity is 0.")
            return False, 0.0

        if signal == "BUY":
            side = SIDE_BUY
            close_side = SIDE_SELL
        else:
            side = SIDE_SELL
            close_side = SIDE_BUY

        regime = signal_data["regime"]
        score = signal_data["score"]
        breakdown = signal_data["score_breakdown"]
        ml_prob = signal_data.get("ml_win_prob", 1.0)
        btc_corr = signal_data.get("btc_correlation", 0.0)

        size_note = f" (REDUCED {size_multiplier*100:.0f}%)" if size_multiplier < 1.0 else ""

        # ★ FIX 2: Calculate LIMIT price (tight offset for fast fill, maker fee)
        offset = current_price * (LIMIT_OFFSET_PCT / 100.0)
        if signal == "BUY":
            limit_price = _round_price(current_price - offset, symbol)  # Slightly below ask
        else:
            limit_price = _round_price(current_price + offset, symbol)  # Slightly above bid

        log.info("═" * 70)
        log.info(f"🚀  [{symbol}] EXECUTING {signal} │ Risk: $1 Sniper Margin{size_note} │ Score: {score}")
        log.info(f"   ★ LIMIT Entry : ${limit_price} (Maker Fee — offset {LIMIT_OFFSET_PCT}%)")
        log.info(f"   Market Price  : ${current_price}")
        log.info(f"   Quantity      : {quantity}")
        log.info(f"   ★ Risk Amt    : ${risk_amount:.2f} ({ENTRY_RISK_PCT}%{size_note})")
        log.info(f"   ★ ML Win Prob : {ml_prob*100:.1f}%")
        log.info(f"   ★ BTC Corr    : {btc_corr:+.4f}")
        log.info(f"   ★ SL Distance : ${sl_distance:.2f} ({SL_ATR_MULT}×ATR)")
        log.info(f"   ★ TP Distance : ${tp_distance:.2f} ({TP_ATR_MULT}×ATR) — R:R = 1:2")
        log.info(f"   Confluence    : {' | '.join(breakdown)}")
        log.info("═" * 70)

        # ★ FIX 2: Try LIMIT order first (maker fee), fallback to MARKET
        use_market = False
        try:
            entry_order = client.futures_create_order(
                symbol=symbol, side=side,
                type=ORDER_TYPE_LIMIT,
                price=str(limit_price),
                quantity=quantity,
                timeInForce=TIME_IN_FORCE_GTC,
            )
            e_id = entry_order.get('orderId', entry_order.get('order_id', 'UNKNOWN'))
            log.info(f"📋  [{symbol}] LIMIT ORDER PLACED — ${limit_price} │ OrderID: {e_id}")

            # Wait for fill (up to 45 seconds, check every 3s)
            filled = False
            for _wait in range(15):  # 15 × 3s = 45s max
                time.sleep(3)
                try:
                    order_status = client.futures_get_order(symbol=symbol, orderId=e_id)
                    status = order_status.get('status', '')
                    if status == 'FILLED':
                        log.info(f"✅  [{symbol}] LIMIT FILLED (Maker Fee!) — OrderID: {e_id}")
                        filled = True
                        break
                    elif status in ('CANCELED', 'EXPIRED', 'REJECTED'):
                        log.warning(f"⚠  [{symbol}] LIMIT {status}. Falling back to MARKET.")
                        use_market = True
                        break
                except Exception:
                    pass

            if not filled and not use_market:
                # Cancel unfilled limit and use market
                try:
                    client.futures_cancel_order(symbol=symbol, orderId=e_id)
                    log.warning(f"⏳  [{symbol}] LIMIT not filled in 45s. Cancelled → MARKET fallback.")
                except Exception:
                    pass
                use_market = True

        except BinanceAPIException as limit_err:
            log.warning(f"⚠  [{symbol}] LIMIT order failed: {limit_err.message}. Falling back to MARKET.")
            use_market = True

        if use_market:
            entry_order = client.futures_create_order(
                symbol=symbol, side=side,
                type=ORDER_TYPE_MARKET, quantity=quantity,
            )
            e_id = entry_order.get('orderId', entry_order.get('order_id', 'UNKNOWN'))
            log.info(f"✅  [{symbol}] MARKET FILLED (Fallback) — OrderID: {e_id}")

        # Fetch actual entry price from position
        pos = _has_open_position(client, symbol)
        if pos:
            avg_entry = pos["entry_price"]
            actual_qty = pos["qty"]
        else:
            avg_entry = current_price
            actual_qty = quantity
            
        # ★ FIX 1: Calculate SL and TP prices
        if signal == "BUY":
            sl_price = _round_price(avg_entry - sl_distance, symbol)
            tp_price = _round_price(avg_entry + tp_distance, symbol)
        else:
            sl_price = _round_price(avg_entry + sl_distance, symbol)
            tp_price = _round_price(avg_entry - tp_distance, symbol)

        # ── TELEGRAM ALERT: Entry ──
        whale_tag = ""
        if (signal == "BUY" and signal_data.get("buy_whale")) or \
           (signal == "SELL" and signal_data.get("sell_whale")):
            whale_tag = " [🐋 Supported]"

        alert_msg = (
            f"🟢 <b>NEW TRADE ENTERED{whale_tag}</b>\n"
            f"Coin: {symbol}\n"
            f"Side: {signal}\n"
            f"Price: ${avg_entry:.4f}\n"
            f"Qty: {actual_qty}\n"
            f"Est. SL: ${sl_price}\n"
            f"Est. TP: ${tp_price}"
        )
        try:
            send_telegram_alert(alert_msg)
        except Exception as tel_err:
            log.warning(f"⚠  [{symbol}] Telegram alert failed: {tel_err}")

        # Place STOP-LOSS order (use quantity+reduceOnly to avoid closePosition conflict)
        try:
            sl_order = client.futures_create_order(
                symbol=symbol, side=close_side,
                type=FUTURE_ORDER_TYPE_STOP_MARKET,
                stopPrice=str(sl_price),
                quantity=actual_qty,
                reduceOnly="true",
                timeInForce=TIME_IN_FORCE_GTC,
                workingType="MARK_PRICE",
            )
            s_id = sl_order.get('orderId', sl_order.get('order_id', 'UNKNOWN'))
            log.info(f"🛡  [{symbol}] INITIAL SL — ${sl_price} ({SL_ATR_MULT}×ATR = ${sl_distance:.2f}) │ OrderID: {s_id}")
        except BinanceAPIException as sl_err:
            log.error(f"🚨  [{symbol}] INITIAL SL placement failed: {sl_err.message}. System will drop Emergency SL on next cycle.")

        # ★ v12: Place TAKE-PROFIT order ONLY if DISABLE_HARD_TP is False
        if not DISABLE_HARD_TP:
            try:
                tp_order = client.futures_create_order(
                    symbol=symbol, side=close_side,
                    type="TAKE_PROFIT_MARKET",
                    stopPrice=str(tp_price),
                    closePosition="true",
                    timeInForce=TIME_IN_FORCE_GTC,
                    workingType="MARK_PRICE",
                )
                t_id = tp_order.get('orderId', tp_order.get('order_id', 'UNKNOWN'))
                log.info(f"🎯  [{symbol}] INITIAL TP — ${tp_price} ({TP_ATR_MULT}×ATR = ${tp_distance:.2f}) │ R:R 1:2 │ OrderID: {t_id}")
            except BinanceAPIException as tp_err:
                log.warning(f"⚠  [{symbol}] TP placement failed: {tp_err.message}. Trailing TP will manage exit.")
        else:
            log.info(f"🔓  [{symbol}] NO HARD TP — 'Let Winners Run' mode. Trailing SL will manage exit.")

        # ── TELEGRAM ALERT: Entry ──


        return True, actual_qty


    except BinanceAPIException as e:
        log.error(f"❌  API Error: {e.status_code} — {e.message}")
        return False, 0.0
    except BinanceRequestException as e:
        log.error(f"❌  Request Error: {e}")
        return False, 0.0
    except Exception as e:
        log.error(f"❌  Unexpected: {e}")
        return False, 0.0


# ═════════════════════════════════════════════════════════════════════════════
# ██  ★ PARTIAL CLOSE (STEPPED PROFIT TAKING)                               ██
# ═════════════════════════════════════════════════════════════════════════════

def execute_partial_close(client: Client, symbol: str, side: str,
                          close_pct: float, original_qty: float, reason: str) -> bool:
    """
    Close a percentage of the open position (reduce-only market order).
    
    Args:
        client: Binance client
        symbol: Trading pair
        side: Position side ("BUY" or "SELL")
        close_pct: Percentage to close (e.g. 30 for 30%)
        original_qty: Original full position quantity
        reason: Log label (e.g. "TP1", "TP2")
    
    Returns:
        bool: True if partial close succeeded
    """
    try:
        pos = _has_open_position(client, symbol)
        if pos is None:
            log.warning(f"⚠  [{symbol}] No position found for partial close.")
            return False
        
        current_qty = pos["qty"]
        close_qty = _round_qty(original_qty * (close_pct / 100.0), symbol)
        
        # Safety: don't close more than what's open
        if close_qty > current_qty:
            close_qty = current_qty
        
        if close_qty <= 0:
            log.warning(f"⚠  [{symbol}] Partial close qty is 0. Skipping.")
            return False
        
        close_side = SIDE_SELL if side == "BUY" else SIDE_BUY
        
        order = client.futures_create_order(
            symbol=symbol,
            side=close_side,
            type=ORDER_TYPE_MARKET,
            quantity=close_qty,
            reduceOnly="true",
        )
        o_id = order.get('orderId', order.get('order_id', 'UNKNOWN'))
        remaining = _round_qty(current_qty - close_qty, symbol)
        log.info(
            f"💰  [{symbol}] {reason} PARTIAL CLOSE — {close_pct}% ({close_qty}) "
            f"│ Remaining: {remaining} │ OrderID: {o_id}"
        )
        
        # ── TELEGRAM ALERT: Partial Close ──
        send_telegram_alert(f"💰 <b>Partial Close ({reason})</b>\nCoin: {symbol}\nAction: Closed {close_qty}\nRemaining: {remaining}")
        
        return True
        
    except BinanceAPIException as e:
        log.error(f"❌  [{symbol}] Partial close failed: {e.message}")
        return False
    except Exception as e:
        log.error(f"❌  [{symbol}] Partial close error: {e}")
        return False


# ═════════════════════════════════════════════════════════════════════════════
# ██  ★ SMART TRAILING TP MANAGER (3-PHASE + STEPPED PROFIT TAKING)        ██
# ═════════════════════════════════════════════════════════════════════════════

def _cancel_stop_orders_only(client: Client, symbol: str) -> int:
    """Cancel only STOP_MARKET orders, preserving TAKE_PROFIT_MARKET orders.
    Returns the number of STOP orders that were successfully cancelled."""
    cancelled = 0
    try:
        orders = client.futures_get_open_orders(symbol=symbol)
        for order in orders:
            order_type = (order.get("type", "") or order.get("origType", "")).upper()
            if "STOP" in order_type and "TAKE_PROFIT" not in order_type:
                try:
                    client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])
                    cancelled += 1
                except Exception as e:
                    log.warning(f"⚠  [{symbol}] Failed to cancel stop order {order.get('orderId')}: {e}")
        if cancelled > 0:
            log.info(f"🗑  [{symbol}] Cancelled {cancelled} old STOP orders.")
    except Exception as e:
        log.warning(f"⚠  [{symbol}] Failed to fetch open orders to cancel: {e}")
    return cancelled


def _cancel_tp_orders(client: Client, symbol: str) -> int:
    """★ v12: Cancel TAKE_PROFIT_MARKET orders (used when switching to trailing-only mode).
    Returns the number of TP orders cancelled."""
    cancelled = 0
    try:
        orders = client.futures_get_open_orders(symbol=symbol)
        for order in orders:
            order_type = (order.get("type", "") or order.get("origType", "")).upper()
            if "TAKE_PROFIT" in order_type:
                try:
                    client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])
                    cancelled += 1
                except Exception as e:
                    log.warning(f"⚠  [{symbol}] Failed to cancel TP order {order.get('orderId')}: {e}")
        if cancelled > 0:
            log.info(f"🔓  [{symbol}] Cancelled {cancelled} TP orders → Trailing SL now manages exit.")
    except Exception as e:
        log.warning(f"⚠  [{symbol}] Failed to cancel TP orders: {e}")
    return cancelled


def _check_smart_reversal(client: Client, symbol: str, side: str) -> tuple:
    """★ v12: Smart Reversal Exit — Check if 5m MA25 has been decisively crossed.
    
    For LONG: Close if price crosses BELOW 5m MA25 with confirmation.
    For SHORT: Close if price crosses ABOVE 5m MA25 with confirmation.
    
    Returns: (should_exit: bool, reason: str)
    """
    if not SMART_REVERSAL_EXIT:
        return False, ""
    
    try:
        raw = client.futures_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_5MINUTE, limit=30)
        if not raw or len(raw) < 26:
            return False, ""
        
        closes = [float(k[4]) for k in raw]
        volumes = [float(k[5]) for k in raw]
        current_price = closes[-1]
        prev_price = closes[-2]
        
        # Calculate MA25 from 5m candles
        ma25 = sum(closes[-25:]) / 25
        ma25_prev = sum(closes[-26:-1]) / 25
        
        # Volume confirmation: current volume > average
        avg_vol = sum(volumes[-10:]) / 10 if len(volumes) >= 10 else 1.0
        current_vol = volumes[-1]
        vol_confirm = current_vol > avg_vol * 1.2  # 20% above average
        
        if side == "BUY":
            # LONG reversal: price crossed BELOW MA25
            if prev_price > ma25_prev and current_price < ma25 and vol_confirm:
                return True, (
                    f"SMART REVERSAL: Price ${current_price:.2f} crossed below 5m MA25 ${ma25:.2f} "
                    f"with volume confirmation ({current_vol:.0f} > avg {avg_vol:.0f})"
                )
        else:  # SELL
            # SHORT reversal: price crossed ABOVE MA25
            if prev_price < ma25_prev and current_price > ma25 and vol_confirm:
                return True, (
                    f"SMART REVERSAL: Price ${current_price:.2f} crossed above 5m MA25 ${ma25:.2f} "
                    f"with volume confirmation ({current_vol:.0f} > avg {avg_vol:.0f})"
                )
        
        return False, ""
    except Exception as e:
        log.warning(f"⚠  [{symbol}] Smart reversal check failed: {e}")
        return False, ""


def _count_stop_orders(client: Client, symbol: str) -> int:
    """Count how many STOP_MARKET orders are currently open for a symbol."""
    try:
        orders = client.futures_get_open_orders(symbol=symbol)
        count = 0
        for order in orders:
            order_type = (order.get("type", "") or order.get("origType", "")).upper()
            if "STOP" in order_type and "TAKE_PROFIT" not in order_type:
                count += 1
        return count
    except Exception:
        return 0


def _force_cancel_all_stops(client: Client, symbol: str) -> bool:
    """Nuclear option: cancel ALL open orders for the symbol, then re-check.
    Used when normal cancel fails and we hit max stop order limit."""
    try:
        client.futures_cancel_all_open_orders(symbol=symbol)
        time.sleep(1.0)
        remaining = _count_stop_orders(client, symbol)
        if remaining == 0:
            log.info(f"🧹  [{symbol}] Force-cancelled ALL orders. Order book clean.")
            return True
        else:
            log.warning(f"⚠  [{symbol}] Force-cancel done but {remaining} stops remain.")
            return False
    except Exception as e:
        log.error(f"🚨  [{symbol}] Force-cancel failed: {e}")
        return False


def _update_sl_order(client: Client, symbol: str, side: str, new_sl: float) -> bool:
    """Cancel existing SL orders (preserve TP!) and place a new STOP_MARKET at new_sl.
    
    ★ CRITICAL FIX v3: 
    1. Cancels old STOP orders first, then VERIFIES they're gone before placing new one.
    2. On 'max stop order limit' error, does a force-cancel-all and retries.
    3. Uses quantity+reduceOnly to avoid closePosition conflicts.
    """
    close_side = SIDE_SELL if side == "BUY" else SIDE_BUY
    
    # Get position quantity for reduceOnly approach
    pos = _has_open_position(client, symbol)
    if pos is None:
        log.info(f"ℹ️  [{symbol}] No position found — SL update skipped.")
        return False
    
    qty = pos["qty"]
    if qty <= 0:
        return False
    
    try:
        # ★ Step 1: Cancel old STOP orders (preserve TP!)
        _cancel_stop_orders_only(client, symbol)
        
        # ★ Step 2: VERIFY cancellation — wait and check order book is clean
        time.sleep(1.5)
        remaining_stops = _count_stop_orders(client, symbol)
        if remaining_stops > 0:
            log.warning(f"⚠  [{symbol}] {remaining_stops} stop orders still alive after cancel. Force-clearing...")
            _force_cancel_all_stops(client, symbol)
            time.sleep(1.0)
        
        # ★ Step 3: Place new SL with quantity + reduceOnly
        resp = client.futures_create_order(
            symbol=symbol, side=close_side,
            type=FUTURE_ORDER_TYPE_STOP_MARKET,
            stopPrice=str(new_sl),
            quantity=qty,
            reduceOnly="true",
            timeInForce=TIME_IN_FORCE_GTC,
            workingType="MARK_PRICE",
        )
        
        if resp:
            return True
        return False
    except BinanceAPIException as e:
        if e.code == -4130:
            # Price already past SL — adjust to safe distance and retry ONCE
            log.warning(f"⚠  [{symbol}] SL at ${new_sl} would trigger immediately. Adjusting...")
            try:
                pos = _has_open_position(client, symbol)
                if pos is None:
                    log.info(f"ℹ️  [{symbol}] Position already closed by exchange SL/TP.")
                    return False
                
                mark = pos["mark_price"]
                qty = pos["qty"]
                if side == "BUY":
                    adjusted_sl = _round_price(mark * 0.995, symbol)
                else:
                    adjusted_sl = _round_price(mark * 1.005, symbol)
                
                _cancel_stop_orders_only(client, symbol)
                time.sleep(1.5)
                
                resp = client.futures_create_order(
                    symbol=symbol, side=close_side,
                    type=FUTURE_ORDER_TYPE_STOP_MARKET,
                    stopPrice=str(adjusted_sl),
                    quantity=qty,
                    reduceOnly="true",
                    timeInForce=TIME_IN_FORCE_GTC,
                    workingType="MARK_PRICE",
                )
                
                if resp:
                    log.info(f"🛡  [{symbol}] Adjusted SL placed at ${adjusted_sl} (0.5% from mark ${mark})")
                    return True
                return False
            except Exception as retry_err:
                log.warning(f"⚠  [{symbol}] SL retry also failed: {retry_err}. Position stays open WITHOUT SL.")
                return False
        elif e.code in (-4131, -4045) or "max stop order" in str(e.message).lower():
            # ★ v15: MAX STOP ORDER LIMIT HIT — ACCOUNT-WIDE on testnet
            # Must cancel stop orders across ALL symbols, not just this one
            log.warning(f"⚠  [{symbol}] Max stop order limit (code {e.code})! Nuclear-clearing ALL symbols...")
            try:
                # Get ALL symbols with open positions and cancel their orders
                positions = client.futures_position_information()
                for p in positions:
                    if float(p.get('positionAmt', 0)) != 0:
                        psym = p['symbol']
                        try:
                            client.futures_cancel_all_open_orders(symbol=psym)
                        except Exception:
                            pass
                time.sleep(3.0)  # ★ v15: Longer delay for testnet propagation
                
                resp = client.futures_create_order(
                    symbol=symbol, side=close_side,
                    type=FUTURE_ORDER_TYPE_STOP_MARKET,
                    stopPrice=str(new_sl),
                    quantity=qty,
                    reduceOnly="true",
                    timeInForce=TIME_IN_FORCE_GTC,
                    workingType="MARK_PRICE",
                )
                if resp:
                    log.info(f"🛡  [{symbol}] SL placed after global nuclear-clear at ${new_sl}")
                    return True
            except Exception as retry2:
                log.error(f"🚨  [{symbol}] SL retry after global nuclear-clear failed: {retry2}")
            return False
        else:
            log.error(f"🚨  [{symbol}] SL placement failed: {e.message}")
            return False
    except Exception as e:
        log.error(f"🚨  [{symbol}] SL update error: {e}")
        return False


def _force_market_close(client: Client, symbol: str, side: str) -> bool:
    """Emergency market close for the entire position. Returns True if successfully closed."""
    close_side = SIDE_SELL if side == "BUY" else SIDE_BUY
    try:
        _cancel_all_open_orders(client, symbol)
        
        # We must get exact position quantity; MARKET with closePosition is not supported
        pos = _has_open_position(client, symbol)
        if pos and pos["qty"] > 0:
            resp = client.futures_create_order(
                symbol=symbol, side=close_side,
                type=ORDER_TYPE_MARKET,
                quantity=pos["qty"],
                reduceOnly="true",
            )
            
            # ★ TRUE STATE SYNC: Wait and verify position is actually closed
            time.sleep(1.0)
            check_pos = _has_open_position(client, symbol)
            
            if check_pos is None and resp:
                log.info(f"✅  [{symbol}] MARKET CLOSE CONFIRMED — Force close ({pos['qty']} units)")
                return True
            else:
                log.error(f"🚨  [{symbol}] Close order sent but position might still be active!")
                return False
        else:
            log.info(f"ℹ️  [{symbol}] MARKET CLOSE skipped — No active position found")
            return True
            
            
    except Exception as e:
        log.error(f"🚨  [{symbol}] FORCE MARKET CLOSE FAILED: {e}")
        return False


def _reset_bot_state(bot_state: dict):
    """Reset all trailing/phase state fields after trade closes."""
    import time
    bot_state["phase"] = "INITIAL"
    bot_state["best_price"] = 0.0
    bot_state["current_trail_sl"] = 0.0
    bot_state["current_atr"] = 0.0
    bot_state["atr_refresh_counter"] = 0
    bot_state["original_qty"] = 0.0
    bot_state["trade_open_time"] = 0.0  # Reset open time
    bot_state["initial_risk_pct"] = 0.0  # ★ v17: Reset dynamic R:R risk baseline


def manage_trailing_tp(client: Client, symbol: str, bot_state: dict, visualizer=None):
    """
    ★ Clean 2-Phase Break-Even & Trailing Stop-Loss Manager.
    
    Phases:
        INITIAL    → SL = Entry ± ATR*1.5 (set at entry by execute_trade)
                     TP = Entry ± ATR*3.0 (set at entry by execute_trade)
                     Waiting for price to move +1.0% in profit.
        
        BREAKEVEN  → PnL ≥ +1.0% → SL moves to Entry Price (+0.04% fee buffer)
                     This guarantees ZERO RISK — the trade can no longer lose money.
        
        TRAILING   → Price moves beyond break-even → SL trails 0.5% behind best price.
                     SL only moves FORWARD (never backward), locking more profit as
                     price extends. The Binance TP order stays intact for full R:R exit.
    
    SAFEGUARDS:
        - _update_sl_order uses quantity+reduceOnly (avoids closePosition conflicts)
        - On -4130 error, SL is adjusted and retried (never force-closes)
        - Only STOP orders are cancelled; TP orders are always preserved
        - If SL update fails, position stays open (no panic close)
    """
    try:
        pos = _has_open_position(client, symbol)

        if pos is None:
            was_active = bot_state["phase"] != "INITIAL" or bot_state["current_trail_sl"] != 0
            if was_active:
                log.info(f"📊  [{symbol}] Position closed (by SL/TP). Manager exiting.")
                
                # ★ v12: Enhanced Telegram with close REASON
                try:
                    outcome, pnl = _determine_trade_outcome(client, symbol)
                    res_emoji = "✅ WIN" if pnl > 0 else "❌ LOSS"
                    
                    # Determine HOW it closed based on PnL and phase
                    phase = bot_state.get("phase", "INITIAL")
                    trail_sl = bot_state.get("current_trail_sl", 0.0)
                    last_side = bot_state.get("last_side", "UNKNOWN")
                    
                    if pnl > 0:
                        if phase == "TRAILING":
                            close_reason = f"🎯 Trailing SL Hit (Profit locked at ${trail_sl:.4f})"
                        elif phase == "BREAKEVEN":
                            close_reason = "🛡 Break-Even SL Hit (Zero loss exit)"
                        else:
                            close_reason = "🎯 Exchange TP Hit"
                    else:
                        if phase in ("TRAILING", "BREAKEVEN"):
                            close_reason = f"🛡 Trailing SL Hit @ ${trail_sl:.4f}"
                        else:
                            close_reason = "🔻 Initial SL Hit (ATR-based)"
                    
                    send_telegram_alert(
                        f"{res_emoji} <b>Trade Closed</b>\n"
                        f"Coin: {symbol}\n"
                        f"Side: {last_side}\n"
                        f"Close Reason: {close_reason}\n"
                        f"Phase: {phase}\n"
                        f"Realized PnL: ${pnl:.2f}"
                    )
                    log.info(f"📲  [{symbol}] Telegram Trade Closed alert sent. PnL: ${pnl:.2f} | Reason: {close_reason}")
                except Exception as tel_err:
                    log.warning(f"⚠  [{symbol}] Telegram closure alert failed: {tel_err}")
                
            _reset_bot_state(bot_state)
            if visualizer is not None:
                visualizer.clear_position_data()
                visualizer.set_bot_status("SCANNING")
            return

        if visualizer is not None:
            visualizer.set_bot_status("IN TRADE")

        entry_price = pos["entry_price"]
        mark_price = pos["mark_price"]
        side = pos["side"]
        current_qty = pos["qty"]

        # ── Calculate PnL % ──
        if side == "BUY":
            pnl_pct = ((mark_price - entry_price) / entry_price) * 100.0
        else:
            pnl_pct = ((entry_price - mark_price) / entry_price) * 100.0

        pnl_usdt = pos.get("unrealized_pnl", 0.0)
        if pnl_pct >= 0:
            pnl_str = f"🤑 +{pnl_pct:.2f}% (${pnl_usdt:+.2f})"
        else:
            pnl_str = f"🩸 {pnl_pct:.2f}% (${pnl_usdt:+.2f})"

        import time
        now = time.time()

        # ══════════════════════════════════════════════════════════════
        #  ★ v15: TIME-BASED EXIT (Stale Trade Protocol)
        #  Close if trade is <= 0 PnL after 45 mins AND in INITIAL phase.
        #  Profitable trades (Phase: BREAKEVEN/TRAILING) are NEVER closed here.
        # ══════════════════════════════════════════════════════════════
        trade_open_time = bot_state.get("trade_open_time", 0)
        if trade_open_time > 0:
            trade_duration_s = now - trade_open_time
            if trade_duration_s >= 2700 and pnl_pct <= STALE_TRADE_MIN_LOSS_PCT and bot_state.get("phase", "INITIAL") == "INITIAL":  # 45 mins, only if genuinely losing
                log.warning(f"⏳  [{symbol}] STALE TRADE: Open for {int(trade_duration_s/60)} mins with PnL {pnl_pct:.2f}% (genuine loss < {STALE_TRADE_MIN_LOSS_PCT}%). Closing early.")
                if _force_market_close(client, symbol, side):
                    _reset_bot_state(bot_state)
                    send_telegram_alert(f"⏳ <b>Stale Trade Closed</b>\nCoin: {symbol}\nSide: {side}\nReason: No momentum after {int(trade_duration_s/60)} mins\nPnL: {pnl_pct:+.2f}%")
                    if visualizer:
                        visualizer.clear_position_data()
                        visualizer.set_bot_status("SCANNING")
                    return

        # ── ATR Refresh (every 10 cycles) ──
        bot_state["atr_refresh_counter"] += 1
        if bot_state["atr_refresh_counter"] >= 10:
            bot_state["current_atr"] = _get_current_atr(client, symbol)
            bot_state["atr_refresh_counter"] = 0
        current_atr = bot_state.get("current_atr", 0.0)
        if current_atr == 0.0:
            current_atr = _get_current_atr(client, symbol)
            bot_state["current_atr"] = current_atr

        # ══════════════════════════════════════════════════════════════
        #  ★ LIVE DOM RADAR: Dynamic Whale Trade Management v2
        #    - True Profit Check (TP only if PnL > +0.15%)
        #    - Whale Reversal Counter-Attack (flip position)
        #    - 5-minute cooldown per coin
        # ══════════════════════════════════════════════════════════════
        import time
        now = time.time()
        if now - bot_state.get("last_dom_check", 0) >= 15:
            bot_state["last_dom_check"] = now

            # ── 2-minute Grace Period ──
            trade_age = now - bot_state.get("trade_open_time", 0)
            if trade_age < 120:
                pass  # Trade too young, skip DOM radar
            elif WHALE_THRESHOLDS.get(symbol):
                whale_cfg = WHALE_THRESHOLDS[symbol]

                try:
                    book = client.futures_order_book(symbol=symbol, limit=20)
                    bids = [[float(p), float(q)] for p, q in book["bids"]]
                    asks = [[float(p), float(q)] for p, q in book["asks"]]

                    price_range = mark_price * (WHALE_RANGE_PCT / 100.0)
                    high_bound = mark_price + price_range
                    low_bound = mark_price - price_range

                    in_profit = pnl_pct >= 0.80  # ★ v10: Min +0.80% before Dynamic TP (stop penny-taking)

                    # ── LONG: Dynamic TP ONLY when in profit ──
                    if side == "BUY" and in_profit:
                        max_sell_qty, max_sell_usd = 0.0, 0.0
                        for p, q in asks:
                            if mark_price <= p <= high_bound:
                                max_sell_qty = max(max_sell_qty, q)
                                max_sell_usd = max(max_sell_usd, p * q)
                        if max_sell_qty >= whale_cfg["qty"] or max_sell_usd >= whale_cfg["usd"]:
                            log.warning(f"🐋  [{symbol}] Dynamic TP! In Profit + Resistance. Exiting LONG.")
                            if _force_market_close(client, symbol, side):
                                _reset_bot_state(bot_state)
                                send_telegram_alert(f"🎯 <b>Dynamic TP! Resistance Wall ahead!</b>\nCoin: {symbol}\nSide: LONG\nPrice: ${mark_price}\nPnL: {pnl_pct:+.2f}% ✅")
                                if visualizer: visualizer.clear_position_data(); visualizer.set_bot_status("SCANNING")
                                return

                    # ── SHORT: Dynamic TP ONLY when in profit ──
                    elif side == "SELL" and in_profit:
                        max_buy_qty, max_buy_usd = 0.0, 0.0
                        for p, q in bids:
                            if low_bound <= p <= mark_price:
                                max_buy_qty = max(max_buy_qty, q)
                                max_buy_usd = max(max_buy_usd, p * q)
                        if max_buy_qty >= whale_cfg["qty"] or max_buy_usd >= whale_cfg["usd"]:
                            log.warning(f"🐋  [{symbol}] Dynamic TP! In Profit + Support. Exiting SHORT.")
                            if _force_market_close(client, symbol, side):
                                _reset_bot_state(bot_state)
                                send_telegram_alert(f"🎯 <b>Dynamic TP! Support Wall ahead!</b>\nCoin: {symbol}\nSide: SHORT\nPrice: ${mark_price}\nPnL: {pnl_pct:+.2f}% ✅")
                                if visualizer: visualizer.clear_position_data(); visualizer.set_bot_status("SCANNING")
                                return

                except Exception as e:
                    log.warning(f"⚠  [{symbol}] DOM check failed: {e}")

        # ══════════════════════════════════════════════════════════════
        #  ★ SAFETY NET: Local SL enforcement (backup if exchange SL missed)
        # ══════════════════════════════════════════════════════════════
        trail_sl_val = bot_state.get("current_trail_sl", 0.0)
        if trail_sl_val > 0:
            sl_breached = False
            if side == "BUY" and mark_price <= trail_sl_val:
                sl_breached = True
            elif side == "SELL" and mark_price >= trail_sl_val:
                sl_breached = True

            if sl_breached:
                log.info(f"🚨  [{symbol}] ★ SL BREACHED (LOCAL) │ Mark: ${mark_price} vs SL: ${trail_sl_val}")
                is_closed = _force_market_close(client, symbol, side)
                
                # ★ TRUE STATE SYNC: Only reset if confirmed closed
                if is_closed:
                    _reset_bot_state(bot_state)
                    send_telegram_alert(f"🚨 <b>LOCAL SL TRIGGERED</b>\nCoin: {symbol}\nSide: {side}\nPrice: ${mark_price}\n<i>Position forcefully closed!</i>")
                    if visualizer is not None:
                        visualizer.clear_position_data()
                        visualizer.set_bot_status("SCANNING")
                    return
                else:
                    log.warning(f"⚠  [{symbol}] Local SL close unconfirmed. Will retry next cycle.")

        # ══════════════════════════════════════════════════════════════
        #  ★ TRACK BEST PRICE (for trailing SL calculation)
        # ══════════════════════════════════════════════════════════════
        if side == "BUY":
            if mark_price > bot_state["best_price"] or bot_state["best_price"] == 0:
                bot_state["best_price"] = mark_price
        else:  # SELL
            if mark_price < bot_state["best_price"] or bot_state["best_price"] == 0:
                bot_state["best_price"] = mark_price

        # ══════════════════════════════════════════════════════════════
        #  ★ v17: PHASE 1: TRUE BREAK-EVEN (Dynamic R:R Based)
        #    Triggers at 1R profit. SL → Entry + 0.15% fee buffer.
        #    Covers Taker fees on BOTH sides so net PnL is always ≥ $0.
        # ══════════════════════════════════════════════════════════════
        # Calculate dynamic initial risk % (based on ATR at entry)
        initial_risk_pct = bot_state.get("initial_risk_pct", 0.0)
        if initial_risk_pct == 0.0 and current_atr > 0 and entry_price > 0:
            initial_risk_pct = (current_atr * SL_ATR_MULT / entry_price) * 100.0
            bot_state["initial_risk_pct"] = initial_risk_pct
            log.info(f"📐  [{symbol}] Dynamic Risk Baseline: {initial_risk_pct:.3f}% (ATR: ${current_atr:.2f} × {SL_ATR_MULT})")

        # BE triggers when profit reaches 0.75R (earlier Break-Even activation to lock safety)
        dynamic_be_trigger = max(initial_risk_pct * 0.75, 0.30)  # Floor at 0.30% to avoid micro-triggers

        if bot_state["phase"] == "INITIAL" and pnl_pct >= dynamic_be_trigger:
            # ★ v17: TRUE BREAK-EVEN — hardcoded 0.15% fee buffer (covers entry + exit taker fees)
            if side == "BUY":
                be_sl = _round_price(entry_price * (1.0 + TRUE_BE_FEE_BUFFER_PCT / 100.0), symbol)
            else:
                be_sl = _round_price(entry_price * (1.0 - TRUE_BE_FEE_BUFFER_PCT / 100.0), symbol)
            
            # ★ v15: GLOBAL nuclear clear — cancel ALL stop orders across ALL symbols
            # Testnet has account-wide stop order limit, not per-symbol
            try:
                all_positions = client.futures_position_information()
                for p in all_positions:
                    if float(p.get('positionAmt', 0)) != 0:
                        psym = p['symbol']
                        try:
                            client.futures_cancel_all_open_orders(symbol=psym)
                        except Exception:
                            pass
                time.sleep(2.0)
            except Exception:
                pass
            
            success = _update_sl_order(client, symbol, side, be_sl)
            if not success:
                # ★ v15: Second attempt — nuclear clear + retry
                log.warning(f"⚠  [{symbol}] BE SL attempt 1 failed. Nuclear retry...")
                try:
                    client.futures_cancel_all_open_orders(symbol=symbol)
                    time.sleep(1.5)
                    success = _update_sl_order(client, symbol, side, be_sl)
                except Exception as e2:
                    log.error(f"🚨  [{symbol}] Nuclear BE retry failed: {e2}")
            
            if success:
                bot_state["current_trail_sl"] = be_sl
                bot_state["phase"] = "BREAKEVEN"
                log.info(
                    f"🛡  [{symbol}] ★ TRUE BREAK-EVEN │ {pnl_str} │ SL → ${be_sl} (entry +{TRUE_BE_FEE_BUFFER_PCT}% fee cover) │ 1R={initial_risk_pct:.2f}%"
                )
                
                # ★ v12: Cancel hard TP orders → let trailing SL manage exit ("Let Winners Run")
                try:
                    _cancel_tp_orders(client, symbol)
                except Exception:
                    pass
                
                # ── TELEGRAM ALERT ──
                send_telegram_alert(
                    f"🛡 <b>TRUE BREAK-EVEN (v17)</b>\nCoin: {symbol}\nSide: {side}\nEntry: ${entry_price}\n"
                    f"SL → ${be_sl} (+{TRUE_BE_FEE_BUFFER_PCT}% fee buffer)\nPnL: {pnl_pct:+.2f}% (1R={initial_risk_pct:.2f}%)\n"
                    f"<i>Trade is now FEE-PROOF risk-free! Trailing activates at {TRAILING_ACTIVATION_RR}R 🚀</i>"
                )
            else:
                # ★ v15: FALLBACK — SL order failed but we still activate software break-even
                log.warning(
                    f"⚠  [{symbol}] Exchange SL failed — activating SOFTWARE break-even at ${be_sl} "
                    f"(will monitor & market-close if breached)"
                )
                bot_state["current_trail_sl"] = be_sl
                bot_state["phase"] = "BREAKEVEN"
                
                try:
                    _cancel_tp_orders(client, symbol)
                except Exception:
                    pass

        # ══════════════════════════════════════════════════════════════
        #  ★ v17: PHASE 2: DYNAMIC TRAILING (after 1:1.5 R:R reached)
        #    Trail SL 0.5% behind highest/lowest price
        #    ONLY activates when PnL >= initial_risk * TRAILING_ACTIVATION_RR
        # ══════════════════════════════════════════════════════════════
        trailing_activation_pct = max(initial_risk_pct * TRAILING_ACTIVATION_RR, 0.45)  # Floor 0.45%
        if bot_state["phase"] in ("BREAKEVEN", "TRAILING") and pnl_pct >= trailing_activation_pct:
            if bot_state["phase"] == "BREAKEVEN":
                log.info(f"🚀  [{symbol}] ★ TRAILING UNLOCKED │ PnL {pnl_pct:.2f}% ≥ {TRAILING_ACTIVATION_RR}R ({trailing_activation_pct:.2f}%) │ Now trailing!")
            bot_state["phase"] = "TRAILING"
            
            # ★ v12: SMART REVERSAL CHECK — Exit if momentum has definitively shifted
            if SMART_REVERSAL_EXIT and pnl_pct >= 0.50:  # Only check after decent profit
                reversal_exit, reversal_reason = _check_smart_reversal(client, symbol, side)
                if reversal_exit:
                    log.warning(f"🔄  [{symbol}] {reversal_reason}")
                    is_closed = _force_market_close(client, symbol, side)
                    if is_closed:
                        # Fetch final PnL
                        try:
                            outcome, pnl = _determine_trade_outcome(client, symbol)
                            res_emoji = "✅ WIN" if pnl > 0 else "❌ LOSS"
                            send_telegram_alert(
                                f"🔄 <b>SMART REVERSAL EXIT</b>\n"
                                f"Coin: {symbol}\nSide: {side}\n"
                                f"{reversal_reason}\n"
                                f"Realized PnL: ${pnl:.2f}"
                            )
                        except Exception:
                            send_telegram_alert(f"🔄 <b>SMART REVERSAL EXIT</b>\nCoin: {symbol}\nSide: {side}\n{reversal_reason}")
                        _reset_bot_state(bot_state)
                        if visualizer: visualizer.clear_position_data(); visualizer.set_bot_status("SCANNING")
                        return
            
            # Calculate trailing SL based on best price
            trail_distance = bot_state["best_price"] * (TRAILING_SL_DISTANCE_PCT / 100.0)
            
            if side == "BUY":
                new_trail_sl = _round_price(bot_state["best_price"] - trail_distance, symbol)
            else:
                new_trail_sl = _round_price(bot_state["best_price"] + trail_distance, symbol)

            # ★ GOLDEN RULE: SL only moves FORWARD, never backward
            current_sl_val = bot_state.get("current_trail_sl", 0.0)
            should_update = False
            if current_sl_val > 0:
                if side == "BUY" and new_trail_sl > current_sl_val:
                    should_update = True
                elif side == "SELL" and new_trail_sl < current_sl_val:
                    should_update = True
            else:
                should_update = True

            if should_update:
                success = _update_sl_order(client, symbol, side, new_trail_sl)
                if success:
                    bot_state["current_trail_sl"] = new_trail_sl
                    log.info(
                        f"📈  [{symbol}] ★ TRAILING SL │ SL → ${new_trail_sl} │ "
                        f"Best: ${bot_state['best_price']} │ Trail: {TRAILING_SL_DISTANCE_PCT}% (${trail_distance:.2f}) │ {pnl_str}"
                    )
                else:
                    # ★ v15: FALLBACK FOR TRAILING SL (Testnet Bug Fix)
                    bot_state["current_trail_sl"] = new_trail_sl
                    log.warning(
                        f"📈⚠ [{symbol}] Exchange Trailing SL Failed. Using SOFTWARE TRAIL SL → ${new_trail_sl} │ "
                        f"Best: ${bot_state['best_price']} │ {pnl_str}"
                    )
            else:
                log.info(
                    f"    [{symbol}] Trailing │ Best: ${bot_state['best_price']} │ "
                    f"SL: ${current_sl_val} │ {pnl_str} │ Phase: {bot_state['phase']}"
                )

        # ══════════════════════════════════════════════════════════════
        #  ★ INITIAL PHASE: Waiting for Break-Even trigger
        # ══════════════════════════════════════════════════════════════
        elif bot_state["phase"] == "INITIAL":
            # Progress bar toward dynamic BE trigger (1R)
            progress = max(0, pnl_pct) / dynamic_be_trigger if dynamic_be_trigger > 0 else 0
            filled_blocks = min(int(progress * 15), 15)
            bar = "█" * filled_blocks + "░" * (15 - filled_blocks)

            # Ensure we have a SL registered (recover from restart)
            if bot_state["current_trail_sl"] == 0.0:
                try:
                    _orders = client.futures_get_open_orders(symbol=symbol)
                    for _ord in _orders:
                        _otype = _ord.get("type", "") or _ord.get("origType", "")
                        if "STOP" in _otype.upper() and "TAKE_PROFIT" not in _otype.upper():
                            bot_state["current_trail_sl"] = float(_ord["stopPrice"])
                            break
                    
                    if bot_state["current_trail_sl"] == 0.0:
                        # Naked position — place emergency SL using 1H ATR
                        sl_distance = current_atr * SL_ATR_MULT
                        if side == "BUY":
                            calc_sl = _round_price(entry_price - sl_distance, symbol)
                        else:
                            calc_sl = _round_price(entry_price + sl_distance, symbol)
                        
                        success = _update_sl_order(client, symbol, side, calc_sl)
                        if success:
                            bot_state["current_trail_sl"] = calc_sl
                            log.warning(f"🚨  [{symbol}] NAKED POSITION — Emergency SL at ${calc_sl}")
                except Exception as e:
                    if "4130" not in str(e):
                        log.warning(f"⚠  [{symbol}] SL check failed: {e}")

            log.info(
                f"   [{symbol}] [{bar}] {pnl_str} │ "
                f"Phase: INITIAL → True BE at +{dynamic_be_trigger:.2f}% (1R) │ Trail at {trailing_activation_pct:.2f}% (1.5R) │ "
                f"SL: ${bot_state['current_trail_sl']} │ ATR: ${current_atr:.2f}"
            )

        elif bot_state["phase"] == "BREAKEVEN":
            # ★ v17: Show waiting for trailing activation
            log.info(
                f"   [{symbol}] 🛡 {pnl_str} │ "
                f"Phase: BREAKEVEN → Trail activates at +{trailing_activation_pct:.2f}% (1.5R) │ "
                f"SL: ${bot_state['current_trail_sl']} │ Best: ${bot_state['best_price']}"
            )

        # ── Update visualizer ──
        if visualizer is not None:
            try:
                trail_distance = bot_state["best_price"] * (TRAILING_SL_DISTANCE_PCT / 100.0) if bot_state["best_price"] > 0 else 0
                visualizer.set_position_data({
                    "side": side,
                    "entry_price": round(entry_price, 2),
                    "mark_price": round(mark_price, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "pnl_usdt": round(pnl_usdt, 2),
                    "phase": bot_state["phase"],
                    "best_price": round(bot_state["best_price"], 2),
                    "trail_sl": round(bot_state["current_trail_sl"], 2),
                    "trail_distance": round(trail_distance, 2),
                    "current_atr": round(current_atr, 2),
                })
                visualizer.update()
            except Exception:
                pass

    except BinanceAPIException as e:
        if e.status_code == 429:
            log.error(f"⏳  [{symbol}] Rate limited in TTP. Waiting 10s...")
            time.sleep(10)
        else:
            log.error(f"❌  [{symbol}] TTP API Error: {e.message}")
    except ConnectionError:
        log.error(f"🌐  [{symbol}] Connection lost in TTP.")
    except Exception as e:
        log.error(f"❌  [{symbol}] TTP Error: {e}.")


# ═════════════════════════════════════════════════════════════════════════════
# ██  ★ v11.1: ANTI-FOMO ENTRY GATE — "Buy the Dip, Not the Peak"          ██
# ═════════════════════════════════════════════════════════════════════════════

def _anti_fomo_entry_check(client: Client, symbol: str, direction: str) -> tuple:
    """
    ★ v11.1: HARD BLOCKER — Prevents FOMO entries at tops/bottoms.
    
    Two independent checks:
    1. Mean Reversion Distance: Price must NOT be extended >0.30% from EMA9
       in the trade direction. If it is, you're buying the top / selling the bottom.
    2. Pullback Candle: The current 1m candle must NOT be a large impulsive candle
       in the trade direction (big green for LONG, big red for SHORT). We want
       to enter on a micro-pullback, not chase a pump/dump.
    
    Returns:
        tuple: (passed: bool, reason: str)
    """
    if not ANTI_FOMO_ENABLED:
        return True, "Anti-FOMO disabled"
    
    try:
        # Fetch last 20 1-minute candles for EMA9 calculation
        raw = client.futures_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_1MINUTE, limit=20)
        if not raw or len(raw) < 10:
            return True, "Insufficient data for FOMO check"
        
        closes = [float(k[4]) for k in raw]
        current_price = closes[-1]
        
        # ── CHECK 1: Mean Reversion Distance from EMA9 ──
        # Calculate EMA9 from the 1m closes
        ema_period = 9
        ema_mult = 2.0 / (ema_period + 1)
        ema = closes[0]
        for c in closes[1:]:
            ema = (c - ema) * ema_mult + ema
        ema9 = ema
        
        if ema9 > 0:
            distance_pct = ((current_price - ema9) / ema9) * 100.0
            
            if direction == "BUY" and distance_pct > ANTI_FOMO_EMA_DIST_PCT:
                return False, (
                    f"FOMO BLOCKED (Mean Reversion): Price ${current_price:.4f} is "
                    f"+{distance_pct:.3f}% above EMA9 ${ema9:.4f} (limit: {ANTI_FOMO_EMA_DIST_PCT}%)"
                )
            elif direction == "SELL" and distance_pct < -ANTI_FOMO_EMA_DIST_PCT:
                return False, (
                    f"FOMO BLOCKED (Mean Reversion): Price ${current_price:.4f} is "
                    f"{distance_pct:.3f}% below EMA9 ${ema9:.4f} (limit: {ANTI_FOMO_EMA_DIST_PCT}%)"
                )
        
        # ── CHECK 2: Pullback Candle Requirement ──
        # Current (latest closed) candle must NOT be a large impulsive candle
        # in the trade direction. We want micro-pullbacks.
        last_kline = raw[-1]  # Current open candle
        candle_open = float(last_kline[1])
        candle_high = float(last_kline[2])
        candle_low = float(last_kline[3])
        candle_close = float(last_kline[4])
        
        candle_range = candle_high - candle_low
        if candle_range > 0:
            candle_body = abs(candle_close - candle_open)
            body_ratio = candle_body / candle_range
            
            is_green = candle_close > candle_open
            is_red = candle_close < candle_open
            
            # For LONG: Block if current candle is a big green candle
            # (body >60% of range AND close is in top 20% of range)
            if direction == "BUY" and is_green and body_ratio > ANTI_FOMO_CANDLE_BODY:
                close_position_in_range = (candle_close - candle_low) / candle_range
                if close_position_in_range > 0.80:  # Close is near the high
                    return False, (
                        f"FOMO BLOCKED (No Pullback): Large green candle "
                        f"(body {body_ratio*100:.0f}% of range, close at {close_position_in_range*100:.0f}% of range). "
                        f"Wait for pullback."
                    )
            
            # For SHORT: Block if current candle is a big red candle
            if direction == "SELL" and is_red and body_ratio > ANTI_FOMO_CANDLE_BODY:
                close_position_in_range = (candle_high - candle_close) / candle_range
                if close_position_in_range > 0.80:  # Close is near the low
                    return False, (
                        f"FOMO BLOCKED (No Pullback): Large red candle "
                        f"(body {body_ratio*100:.0f}% of range, close at {close_position_in_range*100:.0f}% of range). "
                        f"Wait for pullback."
                    )
        
        # ── CHECK 3: Consecutive Pump/Dump Protection ──
        # If the price has surged >0.35% vertically in the last 4 minutes, block entry to prevent buying the local peak.
        if len(closes) >= 5:
            price_4_mins_ago = closes[-5]
            pct_change_4m = ((current_price - price_4_mins_ago) / price_4_mins_ago) * 100.0
            
            if direction == "BUY" and pct_change_4m > 0.35:
                return False, f"FOMO Peak Blocked: Price pumped +{pct_change_4m:.2f}% in last 4 mins. Waiting."
            elif direction == "SELL" and pct_change_4m < -0.35:
                return False, f"FOMO Bottom Blocked: Price dumped {pct_change_4m:.2f}% in last 4 mins. Waiting."
        return True, "Anti-FOMO checks passed"
        
    except Exception as e:
        log.warning(f"⚠  [{symbol}] Anti-FOMO check failed: {e}. Allowing entry.")
        return True, f"Check error: {e}"


# ═════════════════════════════════════════════════════════════════════════════
# ██  ★ v12: S/R REJECTION QUALITY GATE                                     ██
# ═════════════════════════════════════════════════════════════════════════════

def _check_rejection_quality(client: Client, symbol: str, direction: str) -> tuple:
    """
    ★ v12: S/R Rejection Quality — Based on institutional rejection patterns.
    
    Only blocks CLEARLY BAD rejections (indecision doji + wrong color).
    Decent/good candles always pass — keeps trade frequency up.
    
    BAD (NO TRADE):
      - BUY: Red doji candle (body <30% of range) → no buyer conviction
      - BUY: Red candle closing below previous candle midpoint → sellers in control
      - SELL: Green doji candle (body <30% of range) → no seller conviction
      - SELL: Green candle closing above previous candle midpoint → buyers in control
    
    GOOD/DECENT (ALLOWED):
      - Correct color candle with decent body = PASS
      - Correct color even with small body = PASS (profit-taking wick is OK)
    
    Returns: (passed: bool, reason: str)
    """
    try:
        raw = client.futures_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_5MINUTE, limit=5)
        if not raw or len(raw) < 3:
            return True, "Insufficient data for rejection check"
        
        # Last CLOSED candle [-2] and the one before [-3]
        prev_k = raw[-3]
        curr_k = raw[-2]  # Last closed candle
        
        curr_open = float(curr_k[1])
        curr_high = float(curr_k[2])
        curr_low = float(curr_k[3])
        curr_close = float(curr_k[4])
        
        prev_high = float(prev_k[2])
        prev_low = float(prev_k[3])
        
        curr_range = curr_high - curr_low
        curr_body = abs(curr_close - curr_open)
        
        if curr_range <= 0:
            return True, "Zero range candle — skipping check"
        
        body_ratio = curr_body / curr_range
        prev_midpoint = (prev_high + prev_low) / 2.0
        
        if direction == "BUY":
            is_green = curr_close > curr_open
            
            # BAD: Red doji = zero buyer conviction → NO TRADE
            if not is_green and body_ratio < 0.30:
                return False, (
                    f"Bad S/R Rejection (LONG): Red doji candle "
                    f"(body {body_ratio*100:.0f}% of range). Need green body."
                )
            
            # BAD: Red candle closing below prev midpoint = sellers dominating
            if not is_green and curr_close < prev_midpoint:
                return False, (
                    f"Bad S/R Rejection (LONG): Red close ${curr_close:.4f} "
                    f"below prev mid ${prev_midpoint:.4f}. No bounce."
                )
        
        else:  # SELL
            is_red = curr_close < curr_open
            
            # BAD: Green doji = zero seller conviction → NO TRADE
            if not is_red and body_ratio < 0.30:
                return False, (
                    f"Bad S/R Rejection (SHORT): Green doji candle "
                    f"(body {body_ratio*100:.0f}% of range). Need red body."
                )
            
            # BAD: Green candle closing above prev midpoint = buyers dominating
            if not is_red and curr_close > prev_midpoint:
                return False, (
                    f"Bad S/R Rejection (SHORT): Green close ${curr_close:.4f} "
                    f"above prev mid ${prev_midpoint:.4f}. No rejection."
                )
        
        return True, "S/R rejection quality OK"
        
    except Exception as e:
        log.warning(f"⚠  [{symbol}] Rejection quality check error: {e}")
        return True, f"Check error: {e}"  # Fail-open — don't block on errors


# ═════════════════════════════════════════════════════════════════════════════
# ██  INITIALIZATION & EXECUTION TRIGGERS                                   ██
# ═════════════════════════════════════════════════════════════════════════════

FUTURES_TESTNET_URL = "https://testnet.binancefuture.com"

def _check_volume_burst(client: Client, symbol: str, direction: str) -> bool:
    """
    Volume Burst Trigger: ★ v14 Middle-ground — 0.8x average volume.
    Ensures SOME activity without requiring a massive breakout.
    Direction check restored to filter noise candles.
    """
    try:
        raw = client.futures_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_1MINUTE, limit=6)
        if not raw or len(raw) < 6:
            return True  # ★ v14: If can't get data, allow trade anyway
        
        closed_vols = [float(k[5]) for k in raw[:5]]
        avg_vol = sum(closed_vols) / len(closed_vols) if closed_vols else 1.0
        
        # If average volume is essentially zero (testnet dead coin), skip the check
        if avg_vol < 1e-8:
            return True
        
        current_kline = raw[-1]
        current_vol = float(current_kline[5])
        open_price = float(current_kline[1])
        close_price = float(current_kline[4])
        
        # ★ v15: 0.9x — slightly more lenient than 1.0x, but tighter than original 0.8x
        vol_ok = current_vol >= (avg_vol * 0.9)
        
        if vol_ok:
            # Soft direction check: allow flat candles too (close == open)
            if direction == "BUY" and close_price >= open_price:
                return True
            elif direction == "SELL" and close_price <= open_price:
                return True
            # If direction doesn't match but volume is 1.5x+, still allow (strong burst)
            if current_vol >= (avg_vol * 1.5):
                return True
                
        return False
    except Exception as e:
        log.warning(f"⚠  [{symbol}] Volume burst check failed: {e}")
        return True  # ★ v14: On error, allow trade


# ═════════════════════════════════════════════════════════════════════════════
# ██  ★ v18: MULTI-TIMEFRAME ANALYSIS (MTFA) — 1H Trend Filter            ██
# ═════════════════════════════════════════════════════════════════════════════

MTFA_ENABLED = True          # ★ v18: Master switch for 1H trend filter
MTFA_EMA_PERIOD = 50         # ★ v18: 50-period EMA on the 1H chart

def _check_1h_trend_ema50(client: Client, symbol: str, direction: str) -> tuple:
    """
    ★ v18: Multi-Timeframe Analysis — 1H 50 EMA Trend Filter.
    
    Fetches 1H klines, calculates 50 EMA using pure NumPy.
    Returns (aligned: bool, reason: str).
      - LONG only if 1H Price > 1H 50 EMA (macro uptrend)
      - SHORT only if 1H Price < 1H 50 EMA (macro downtrend)
    """
    try:
        import numpy as np
        
        h1 = client.futures_klines(
            symbol=symbol,
            interval=Client.KLINE_INTERVAL_1HOUR,
            limit=60,  # 60 candles gives plenty of data for 50 EMA
        )
        if not h1 or len(h1) < MTFA_EMA_PERIOD:
            return True, "MTFA SKIP: Not enough 1H data"  # Allow on insufficient data
        
        # Extract close prices as numpy array
        close_1h = np.array([float(k[4]) for k in h1])
        
        # Calculate 50 EMA using pure NumPy
        alpha = 2.0 / (MTFA_EMA_PERIOD + 1)
        ema_50 = np.zeros_like(close_1h)
        ema_50[0] = close_1h[0]
        for i in range(1, len(close_1h)):
            ema_50[i] = alpha * close_1h[i] + (1.0 - alpha) * ema_50[i - 1]
        
        current_price_1h = close_1h[-1]
        current_ema_50 = ema_50[-1]
        trend = "UP" if current_price_1h > current_ema_50 else "DOWN"
        dist_pct = ((current_price_1h - current_ema_50) / current_ema_50) * 100.0
        
        # Alignment check
        if direction == "BUY" and trend == "UP":
            return True, f"MTFA ALIGNED: 1H Trend UP (Price ${current_price_1h:.4f} > EMA50 ${current_ema_50:.4f}, +{dist_pct:.2f}%)"
        elif direction == "SELL" and trend == "DOWN":
            return True, f"MTFA ALIGNED: 1H Trend DOWN (Price ${current_price_1h:.4f} < EMA50 ${current_ema_50:.4f}, {dist_pct:.2f}%)"
        else:
            return False, (
                f"Signal Rejected: MTFA 1H Trend mismatch. "
                f"Signal={direction} but 1H Trend={trend} "
                f"(Price ${current_price_1h:.4f} vs EMA50 ${current_ema_50:.4f}, {dist_pct:+.2f}%)"
            )
    except Exception as e:
        log.warning(f"⚠  [{symbol}] MTFA check failed: {e}")
        return True, f"MTFA SKIP: Error ({e})"  # Allow on error (fail-open)


# ═════════════════════════════════════════════════════════════════════════════
# ██  ★ v20: SMART MONEY CONCEPTS (SMC) ENTRY ENGINE                       ██
# ██  Replaces v19 3-Layer + old AdvancedExecutionValidator                 ██
# ██  Pure Price Action: CHOCH/BOS + Liquidity Sweep + OB/FVG Retest       ██
# ═════════════════════════════════════════════════════════════════════════════

import numpy as np

ENTRY_VALIDATION_ENABLED = True    # ★ v20: Master switch for SMC entry validation
WAIT_QUEUE_MAX_CANDLES   = 8       # ★ WAIT queue: max candles before expiry
SMC_SWING_LOOKBACK       = 3       # ★ Swing detection: ±3 bar window
SMC_SWEEP_TOLERANCE_ATR  = 0.15    # ★ Sweep: wick must exceed level by at least 0.15× ATR
SMC_RR_MIN_RATIO         = 1.5     # ★ R:R floor for OB/FVG entries
SMC_SL_ATR_MULT          = 2.0     # ★ SL distance = 2.0× ATR beyond OB/FVG edge
SMC_STRUCTURE_LOOKBACK   = 50      # ★ How many candles to scan for structure


# ─── SWING POINT DETECTION ──────────────────────────────────────────────────
def _detect_swing_points(highs: np.ndarray, lows: np.ndarray, lookback: int = 3) -> tuple:
    """
    ★ v20 SMC: Detect Swing Highs and Swing Lows using pure OHLC data + NumPy.
    A Swing High at index i means high[i] is the maximum in the window [i-lookback, i+lookback].
    A Swing Low  at index i means low[i]  is the minimum in the window [i-lookback, i+lookback].
    
    Returns:
        swing_highs: list of (index, price) tuples
        swing_lows:  list of (index, price) tuples
    """
    n = len(highs)
    swing_highs = []
    swing_lows = []
    
    for i in range(lookback, n - lookback):
        window_high = highs[i - lookback : i + lookback + 1]
        window_low  = lows[i - lookback : i + lookback + 1]
        
        # Swing High: this bar's high is the highest in the window
        if highs[i] == np.max(window_high):
            # Avoid duplicates — must be strictly the peak (not a plateau)
            if highs[i] > highs[i - 1] or highs[i] > highs[i + 1]:
                swing_highs.append((i, float(highs[i])))
        
        # Swing Low: this bar's low is the lowest in the window
        if lows[i] == np.min(window_low):
            if lows[i] < lows[i - 1] or lows[i] < lows[i + 1]:
                swing_lows.append((i, float(lows[i])))
    
    return swing_highs, swing_lows


# ─── MARKET STRUCTURE MAPPING (BOS / CHOCH) ─────────────────────────────────
def _detect_market_structure(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                              swing_highs: list, swing_lows: list) -> tuple:
    """
    ★ v20 SMC: Map Market Structure using swing points.
    
    Detects:
      - CHOCH (Change of Character): Trend reversal signal.
        • Bullish CHOCH: In a downtrend (LH/LL), price breaks above the last Swing High.
        • Bearish CHOCH: In an uptrend (HH/HL), price breaks below the last Swing Low.
      - BOS (Break of Structure): Trend continuation.
        • Bullish BOS: In an uptrend, price makes a new Higher High.
        • Bearish BOS: In a downtrend, price makes a new Lower Low.
    
    Returns:
        (structure_type: str, break_index: int, break_level: float, detail: str)
        structure_type: "CHOCH_BULL" | "CHOCH_BEAR" | "BOS_BULL" | "BOS_BEAR" | "NONE"
    """
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "NONE", -1, 0.0, "Not enough swing points for structure mapping"
    
    current_price = closes[-1]
    n = len(closes)
    
    # Build recent swing sequence (last 6 swings of each type)
    recent_sh = swing_highs[-6:]
    recent_sl = swing_lows[-6:]
    
    # Determine prevailing trend from the last 2 swing highs and 2 swing lows
    # Uptrend: HH + HL pattern (each successive swing is higher)
    # Downtrend: LH + LL pattern (each successive swing is lower)
    last_2_sh = recent_sh[-2:]
    last_2_sl = recent_sl[-2:]
    
    sh_rising = last_2_sh[-1][1] > last_2_sh[-2][1]   # Higher High?
    sl_rising = last_2_sl[-1][1] > last_2_sl[-2][1]    # Higher Low?
    
    if sh_rising and sl_rising:
        trend = "UP"
    elif not sh_rising and not sl_rising:
        trend = "DOWN"
    else:
        trend = "MIXED"
    
    # --- CHOCH Detection ---
    # Look at the LAST FEW CANDLES (not historical) to find a live break
    check_window = min(8, n)  # Check last 8 candles for a fresh break
    
    # Bullish CHOCH: Downtrend + price breaks above last swing high
    if trend in ("DOWN", "MIXED"):
        last_sh_idx, last_sh_price = recent_sh[-1]
        # Check if any of the last `check_window` candles broke above the swing high
        for i in range(n - check_window, n):
            if closes[i] > last_sh_price and i > last_sh_idx:
                detail = (
                    f"Bullish CHOCH: Trend was {trend}, price ${closes[i]:.4f} broke above "
                    f"Swing High ${last_sh_price:.4f} (bar {last_sh_idx})"
                )
                return "CHOCH_BULL", i, last_sh_price, detail
    
    # Bearish CHOCH: Uptrend + price breaks below last swing low
    if trend in ("UP", "MIXED"):
        last_sl_idx, last_sl_price = recent_sl[-1]
        for i in range(n - check_window, n):
            if closes[i] < last_sl_price and i > last_sl_idx:
                detail = (
                    f"Bearish CHOCH: Trend was {trend}, price ${closes[i]:.4f} broke below "
                    f"Swing Low ${last_sl_price:.4f} (bar {last_sl_idx})"
                )
                return "CHOCH_BEAR", i, last_sl_price, detail
    
    # --- BOS Detection ---
    # Bullish BOS: Uptrend + new Higher High in last few candles
    if trend == "UP":
        prev_sh_price = last_2_sh[-2][1]
        last_sh_idx = last_2_sh[-1][0]
        for i in range(n - check_window, n):
            if highs[i] > last_2_sh[-1][1] and i > last_sh_idx:
                detail = (
                    f"Bullish BOS: Uptrend continuation. High ${highs[i]:.4f} broke "
                    f"Swing High ${last_2_sh[-1][1]:.4f}"
                )
                return "BOS_BULL", i, last_2_sh[-1][1], detail
    
    # Bearish BOS: Downtrend + new Lower Low in last few candles
    if trend == "DOWN":
        last_sl_idx = last_2_sl[-1][0]
        for i in range(n - check_window, n):
            if lows[i] < last_2_sl[-1][1] and i > last_sl_idx:
                detail = (
                    f"Bearish BOS: Downtrend continuation. Low ${lows[i]:.4f} broke "
                    f"Swing Low ${last_2_sl[-1][1]:.4f}"
                )
                return "BOS_BEAR", i, last_2_sl[-1][1], detail
    
    return "NONE", -1, 0.0, f"No structure break detected (trend={trend})"


# ─── LIQUIDITY SWEEP DETECTOR ───────────────────────────────────────────────
def _detect_liquidity_sweep(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                             opens: np.ndarray, vols: np.ndarray, swing_highs: list, swing_lows: list,
                             direction: str, atr: float) -> tuple:
    """
    ★ v20 Advanced SMC: Detect Liquidity Sweeps with strict Volume & wick size markers.
    """
    n = len(closes)
    min_sweep = atr * 0.5  # ★ Strict: wick must be at least 0.5x ATR
    
    # Calculate Volume MA(20) for the last few candles
    vol_ma = np.mean(vols[-20:]) if len(vols) >= 20 else np.mean(vols)
    
    if direction == "SELL" and swing_highs:
        # Bearish Sweep (Bull Trap)
        for sh_idx, sh_price in reversed(swing_highs[-5:]):
            if sh_idx >= n - 2:
                continue
            for i in range(max(n - 4, sh_idx + 1), n):
                wick_above = highs[i] - sh_price
                if wick_above >= min_sweep and closes[i] < sh_price and vols[i] > 1.5 * vol_ma:
                    detail = (
                        f"Bearish Trap: Candle {i} swept high ${sh_price:.4f} (+${wick_above:.4f}), "
                        f"vol {vols[i]:.1f} > 1.5x MA. Closed below at ${closes[i]:.4f}"
                    )
                    return True, sh_price, highs[i], detail
                    
    elif direction == "BUY" and swing_lows:
        # Bullish Sweep (Bear Trap)
        for sl_idx, sl_price in reversed(swing_lows[-5:]):
            if sl_idx >= n - 2:
                continue
            for i in range(max(n - 4, sl_idx + 1), n):
                wick_below = sl_price - lows[i]
                if wick_below >= min_sweep and closes[i] > sl_price and vols[i] > 1.5 * vol_ma:
                    detail = (
                        f"Bullish Trap: Candle {i} swept low ${sl_price:.4f} (+${wick_below:.4f}), "
                        f"vol {vols[i]:.1f} > 1.5x MA. Closed above at ${closes[i]:.4f}"
                    )
                    return True, sl_price, lows[i], detail
                    
    return False, 0.0, 0.0, f"No {direction}-side high-volume sweep detected"


# ─── ORDER BLOCK / FAIR VALUE GAP DETECTOR ──────────────────────────────────
def _detect_ob_fvg(highs: np.ndarray, lows: np.ndarray, opens: np.ndarray,
                    closes: np.ndarray, break_index: int, direction: str, atr: float) -> tuple:
    """
    ★ v20 Advanced SMC: Strict Order Block sizing.
    OB must be between 0.5x and 3.0x ATR to be considered institutional.
    OB top/bottom strictly defined by candle body (open/close).
    """
    if break_index < 3:
        return "NONE", 0.0, 0.0, "Break index too early for OB/FVG detection"
    
    search_start = max(0, break_index - 10)
    
    if direction == "BUY":
        # Bullish setup: Find the last BEARISH candle before the bullish break
        for i in range(break_index - 1, search_start - 1, -1):
            if closes[i] < opens[i]:  # Bearish candle
                ob_high = float(opens[i])   # body top
                ob_low = float(closes[i])   # body bottom
                size = ob_high - ob_low
                
                # Strict size filter
                if size < 0.5 * atr or size > 3.0 * atr:
                    continue
                    
                qual = min(1.0, size / atr)
                detail = f"Bullish OB: Bar {i}, Zone ${ob_low:.4f}–${ob_high:.4f} (Qual: {qual:.2f})"
                log.info(f"    🧱 {detail}")
                return "OB", ob_high, ob_low, detail
                
    elif direction == "SELL":
        # Bearish setup: Find the last BULLISH candle before the bearish break
        for i in range(break_index - 1, search_start - 1, -1):
            if closes[i] > opens[i]:  # Bullish candle
                ob_high = float(closes[i])  # body top
                ob_low = float(opens[i])    # body bottom
                size = ob_high - ob_low
                
                if size < 0.5 * atr or size > 3.0 * atr:
                    continue
                    
                qual = min(1.0, size / atr)
                detail = f"Bearish OB: Bar {i}, Zone ${ob_low:.4f}–${ob_high:.4f} (Qual: {qual:.2f})"
                log.info(f"    🧱 {detail}")
                return "OB", ob_high, ob_low, detail
                
    # Fallback to FVG
    for i in range(break_index - 1, max(search_start, 1), -1):
        if i + 1 < len(highs):
            if direction == "BUY":
                if highs[i - 1] < lows[i + 1]:
                    fvg_low = float(highs[i - 1])
                    fvg_high = float(lows[i + 1])
                    detail = f"Bullish FVG: Bar {i}, Zone ${fvg_low:.4f}–${fvg_high:.4f}"
                    return "FVG", fvg_high, fvg_low, detail
            else:
                if lows[i - 1] > highs[i + 1]:
                    fvg_high = float(lows[i - 1])
                    fvg_low = float(highs[i + 1])
                    detail = f"Bearish FVG: Bar {i}, Zone ${fvg_low:.4f}–${fvg_high:.4f}"
                    return "FVG", fvg_high, fvg_low, detail
                    
    return "NONE", 0.0, 0.0, "No Strict Order Block or FVG found"
    
# ─── INDUCEMENT DETECTOR ────────────────────────────────────────────────────
def _detect_inducement(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, opens: np.ndarray,
                       break_idx: int, sweep_extreme: float, direction: str) -> bool:
    """
    ★ v20 Advanced SMC: Identifies fake pullbacks (Inducements) that trap early traders
    before reaching the true OB.
    """
    if break_idx < 0:
        return False
        
    n = len(closes)
    choch_candle_mid = (opens[break_idx] + closes[break_idx]) / 2.0
    
    for i in range(break_idx + 1, n):
        if direction == "BUY":
            # Pulled back below 50% of the breakout candle, but did NOT break the sweep low
            if lows[i] < choch_candle_mid and closes[i] > sweep_extreme:
                log.info(f"    🪤 Inducement found! Pullback to ${lows[i]:.4f} traps shorts.")
                return True
            if lows[i] <= sweep_extreme:
                return False  # Structure broken, trade invalidated
        else:
            if highs[i] > choch_candle_mid and closes[i] < sweep_extreme:
                log.info(f"    🪤 Inducement found! Pullback to ${highs[i]:.4f} traps longs.")
                return True
            if highs[i] >= sweep_extreme:
                return False
                
    return False


# ─── SMC ENTRY ORCHESTRATOR (replaces _3layer_entry_validate) ────────────────
def _smc_entry_validate(client: Client, symbol: str, direction: str) -> tuple:
    """
    ★ v20 SMC: Smart Money Concepts Entry Validation.
    
    Orchestrates all SMC checks in sequence:
      1. Fetch M5 data + compute ATR14
      2. Detect Swing Points
      3. Detect Market Structure (BOS / CHOCH)
      4. Detect Liquidity Sweep
      5. Detect OB / FVG zone
      6. Check if price is in the zone → PASS / WAIT
      7. Validate R:R against next liquidity pool → PASS / NEUTRAL
    
    Returns: (result: str, reason: str, zone_data: dict or None)
      - "PASS"    → SMC criteria met, entry is valid. zone_data has zone info.
      - "NEUTRAL" → No valid setup. Kill signal.
      - "WAIT"    → Valid setup but price not in OB/FVG zone yet. Queue for retest.
    """
    try:
        m5 = client.futures_klines(symbol=symbol, interval="5m", limit=100)
        if not m5 or len(m5) < 40:
            return "PASS", "SMC SKIP: Not enough M5 data", None
        
        high  = np.array([float(k[2]) for k in m5])
        low   = np.array([float(k[3]) for k in m5])
        opn   = np.array([float(k[1]) for k in m5])
        close = np.array([float(k[4]) for k in m5])
        vols  = np.array([float(k[5]) for k in m5])
        
        current_price = close[-1]
        n = len(close)
        
        # ── ATR14 Calculation ─────────────────────────────────────────────
        prev_close = close[:-1]
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close))
        )
        atr_arr = np.zeros(len(tr))
        if len(tr) >= 14:
            atr_arr[13] = np.mean(tr[:14])
            for i in range(14, len(tr)):
                atr_arr[i] = (atr_arr[i - 1] * 13 + tr[i]) / 14.0
        atr14 = atr_arr[-1]
        if atr14 <= 0:
            return "PASS", "SMC SKIP: ATR14 is zero", None
        
        # ── Step 1: Swing Points ──────────────────────────────────────────
        swing_highs, swing_lows = _detect_swing_points(high, low, SMC_SWING_LOOKBACK)
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return "PASS", "SMC SKIP: Not enough swing points", None
        
        log.info(f"    [{symbol}] SMC: {len(swing_highs)} Swing Highs, {len(swing_lows)} Swing Lows detected")
        
        # ── Step 2: Liquidity Sweep ───────────────────────────────────────
        swept, sweep_level, sweep_extreme, sweep_detail = _detect_liquidity_sweep(
            high, low, close, opn, vols, swing_highs, swing_lows, direction, atr14
        )
        if swept:
            log.info(f"    [{symbol}] 💧 {sweep_detail}")
        else:
            log.info(f"    [{symbol}] SMC: {sweep_detail}")
            
        # ── Step 3: Market Structure (BOS / CHOCH) ────────────────────────
        struct_type, break_idx, break_level, struct_detail = _detect_market_structure(
            high, low, close, swing_highs, swing_lows
        )
        log.info(f"    [{symbol}] SMC Structure: {struct_type} | {struct_detail}")
        
        valid_structure = False
        if direction == "BUY" and struct_type in ("CHOCH_BULL", "BOS_BULL"):
            valid_structure = True
        elif direction == "SELL" and struct_type in ("CHOCH_BEAR", "BOS_BEAR"):
            valid_structure = True
        
        # ── Decision Gate: Need Valid Structure ──────────────
        if not valid_structure:
            reason = f"SMC NEUTRAL: No valid structure shift ({struct_type}) for {direction}."
            return "NEUTRAL", reason, None
            
        # ── Step 4: Inducement Detection ──────────────────────────────────
        inducement_found = _detect_inducement(
            high, low, close, opn, break_idx, sweep_extreme if swept else 0.0, direction
        )
        
        # ── Step 5: Find Strict OB / FVG Zone ─────────────────────────────
        ob_search_idx = break_idx if break_idx > 0 else n - 3
        zone_type, zone_high, zone_low, zone_detail = _detect_ob_fvg(
            high, low, opn, close, ob_search_idx, direction, atr14
        )
        
        if zone_type == "NONE":
            reason = f"SMC NEUTRAL: Structure valid but no Strict OB/FVG zone found. {zone_detail}"
            return "NEUTRAL", reason, None
        
        log.info(f"    [{symbol}] SMC Zone: {zone_type} @ ${zone_low:.4f}–${zone_high:.4f}")
        
        zone_data = {
            "zone_type": zone_type,
            "zone_high": zone_high,
            "zone_low": zone_low,
            "structure": struct_type,
            "swept": swept,
            "inducement": inducement_found,
            "sweep_level": sweep_level,
        }
        
        # ── Step 6: Is price IN the zone now? ─────────────────────────────
        in_zone = False
        if direction == "BUY":
            in_zone = current_price <= zone_high and current_price >= zone_low - (0.2 * atr14)
        elif direction == "SELL":
            in_zone = current_price >= zone_low and current_price <= zone_high + (0.2 * atr14)
        
        if not in_zone:
            reason = (
                f"SMC WAIT: {zone_type} zone ${zone_low:.4f}–${zone_high:.4f} found, "
                f"but price ${current_price:.4f} not in zone yet. Queued for retest."
            )
            log.info(f"    [{symbol}] ⏳ {reason}")
            return "WAIT", reason, zone_data
        
        # ── Step 7: Strict R:R Validation (SL behind Sweep) ───────────────
        sl_distance = SMC_SL_ATR_MULT * atr14
        if swept and sweep_extreme > 0:
            # Place SL mathematically behind the exact sweep extreme
            if direction == "BUY":
                sl_distance = current_price - (sweep_extreme - (0.2 * atr14))
            else:
                sl_distance = (sweep_extreme + (0.2 * atr14)) - current_price
                
        # Fallback safeguard
        if sl_distance <= 0:
            sl_distance = SMC_SL_ATR_MULT * atr14
            
        # ★ HARD REJECTION: Cap SL at 4.0% max raw move to protect against extreme volatility (Medium Tolerance)
        sl_pct = (sl_distance / current_price) * 100.0
        if sl_pct > 4.0:
            reason = f"SMC NEUTRAL: SL distance {sl_pct:.2f}% is too wide (>4.0%). Skipping highly volatile setup."
            log.warning(f"    [{symbol}] 🚫 {reason}")
            return "NEUTRAL", reason, zone_data
            
        tp_distance = 0.0
        if direction == "BUY":
            tp_targets = [sh_price for sh_idx, sh_price in swing_highs if sh_price > current_price + (0.1 * atr14)]
            tp_distance = min(tp_targets) - current_price if tp_targets else sl_distance * 2.0
        else:
            tp_targets = [sl_price for sl_idx, sl_price in swing_lows if sl_price < current_price - (0.1 * atr14)]
            tp_distance = current_price - max(tp_targets) if tp_targets else sl_distance * 2.0
        
        rr_ratio = tp_distance / sl_distance if sl_distance > 0 else 0
        
        if rr_ratio < SMC_RR_MIN_RATIO:
            reason = (
                f"SMC NEUTRAL: R:R too low. TP ${tp_distance:.4f} / SL ${sl_distance:.4f} = "
                f"{rr_ratio:.2f}R (need ≥ {SMC_RR_MIN_RATIO}R)."
            )
            return "NEUTRAL", reason, zone_data
        
        # ── ALL CHECKS PASSED ─────────────────────────────────────────────
        triggers = []
        if valid_structure:
            triggers.append(struct_type)
        if swept:
            triggers.append("LIQ_SWEEP")
        if inducement_found:
            triggers.append("INDUCEMENT_TRAP")
        
        final_reason = (
            f"SMC PASS ✅: [{'+'.join(triggers)}] → {zone_type} Zone "
            f"${zone_low:.4f}–${zone_high:.4f}, Price ${current_price:.4f} IN ZONE, "
            f"R:R = {rr_ratio:.2f}"
        )
        zone_data["sl_distance"] = sl_distance  # ★ v20 SMC SL Integration: Pass mathematical SL distance down pipe
        log.info(f"    [{symbol}] {final_reason}")
        return "PASS", final_reason, zone_data
    
    except Exception as e:
        log.warning(f"⚠  [{symbol}] SMC validation error: {e}")
        return "PASS", f"SMC SKIP: Error ({e})", None  # Fail-open


class AdvancedExecutionValidator:
    """
    ★ v20: SMC-based Pre-Flight Check.
    Replaces the old indicator-based validator (ADX/BB/RSI/VolumeDelta).
    Now validates using pure market structure + price action.
    The v17 Exit Engine is completely untouched.
    """
    @staticmethod
    def validate(client: Client, symbol: str, direction: str) -> tuple[bool, str]:
        """
        Thin wrapper over _smc_entry_validate for API compatibility.
        The main loop calls .validate() → (passed: bool, reason: str).
        SMC returns PASS/NEUTRAL/WAIT — here we map PASS → True, else False.
        (WAIT is handled separately in the v19 entry gate section of the main loop.)
        """
        result, reason, zone_data = _smc_entry_validate(client, symbol, direction)
        if result == "PASS":
            return True, f"SMC Pre-Flight PASSED: {reason}"
        else:
            # NEUTRAL or WAIT — both block immediate execution at the pre-flight stage
            return False, f"SMC Pre-Flight BLOCKED ({result}): {reason}"

def initialize_client() -> Client:
    """Create and configure the Binance Futures Testnet client."""
    log.info("🔌  Connecting to Binance Futures TESTNET (DEMO ACCOUNT)...")
    log.info(f"    API Key: {API_KEY[:8]}...{API_KEY[-4:]} ({len(API_KEY)} chars)")

    # Connect to Testnet
    client = Client(API_KEY, API_SECRET, testnet=True, ping=False)
    # Ensure Testnet Futures URL is used
    client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'

    # ★ AUTO TIME-SYNC: Fix "Timestamp ahead of server" errors (VPN latency)
    try:
        server_time = client.futures_time()
        local_time = int(time.time() * 1000)
        client.timestamp_offset = server_time['serverTime'] - local_time
        log.info(f"⏱  Time sync: offset = {client.timestamp_offset}ms")
    except Exception as e:
        log.warning(f"⚠  Time sync failed: {e}. Continuing with local clock.")

    try:
        client.futures_ping()
        log.info("✅  Futures Mainnet connection verified (REAL MONEY ACTIVE).")
    except BinanceAPIException as e:
        log.error(f"❌  Ping failed: {e.message}")
        raise

    global SYMBOLS
    if ENABLE_DYNAMIC_WATCHLIST:
        log.info("📡 Fetching Top Volatile Pairs dynamically (limit=50)...")
        try:
            tickers = client.futures_ticker()
            valid = [
                t for t in tickers 
                if t['symbol'].endswith('USDT') 
                and '_' not in t['symbol']
                and float(t.get('lastPrice', 0)) > 0
                and float(t.get('volume', 0)) > 0
            ]
            valid.sort(key=lambda x: float(x.get('quoteVolume', 0)), reverse=True)
            top_symbols = [t['symbol'] for t in valid[:50]]
            if top_symbols:
                if "BTCUSDT" not in top_symbols: top_symbols.insert(0, "BTCUSDT")
                if "ETHUSDT" not in top_symbols: top_symbols.insert(1, "ETHUSDT")
                # ★ v15: Remove blacklisted dead/losing coins
                top_symbols = [s for s in top_symbols if s not in BLACKLIST_COINS]
                SYMBOLS = top_symbols[:50]
                log.info(f"🎯 Dynamic Watchlist Active: {len(SYMBOLS)} pairs (blacklisted {len(BLACKLIST_COINS)} coins).")
        except Exception as e:
            log.error(f"Failed to fetch dynamic symbols: {e}")

    for sym in SYMBOLS:
        try:
            client.futures_change_leverage(symbol=sym, leverage=LEVERAGE)
            log.info(f"⚙  [{sym}] Leverage: {LEVERAGE}x")
        except BinanceAPIException as e:
            log.warning(f"⚠  [{sym}] Leverage: {e.message}")

        try:
            client.futures_change_margin_type(symbol=sym, marginType="ISOLATED")
            log.info(f"⚙  [{sym}] Margin: ISOLATED")
        except BinanceAPIException as e:
            if "No need to change margin type" in str(e.message):
                log.info(f"⚙  [{sym}] Margin already ISOLATED")
            else:
                log.warning(f"⚠  [{sym}] Margin: {e.message}")

    try:
        balance = _get_account_balance(client)
        log.info(f"💰  Balance: ${balance:.2f} USDT")
    except BinanceAPIException as e:
        log.error(f"❌  Balance fetch failed: {e.message}")
        raise

    return client


# ═════════════════════════════════════════════════════════════════════════════
# ██  MAIN EXECUTION LOOP                                                   ██
# ═════════════════════════════════════════════════════════════════════════════

def main():
    """
    The Ironclad Execution Loop v6.0 (Smart Risk Edition).

    - Confluence Scoring with visual dashboard.
    - ★ Derivative Data (OI + Funding Rate Squeeze Detection).
    - ★ AI ML Filter (Random Forest win probability gate).
    - ★ BTC Correlation Filter (skip/reduce correlated trades).
    - ★ Stepped Profit Taking (30% + 30% + 40% runner).
    - ★ 3-Phase Smart Trailing SL (Break-Even → Profit Lock → Dynamic Trail).
    - Market regime awareness (ADX).
    - Multi-pair Round-Robin scanner.
    """
    print()
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║   INSTITUTIONAL MULTI-PAIR BOT v9.0 — BREAK-EVEN + TRAIL      ║")
    print("║   ★ 5 Pairs │ Telegram Alerts │ Daily Kill Switch (5%)         ║")
    print("║   ★ SL 1.5×ATR + TP 3.0×ATR │ BE at +0.30% │ Trail 0.15%       ║")
    print("║   MTF+Price │ RSI Zone │ Candle Confirm │ Score≥18             ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print()

    if not API_KEY or not API_SECRET:
        log.error("❌  API credentials not set!")
        log.error("    Set env vars BINANCE_API_KEY & BINANCE_API_SECRET")
        return

    client = initialize_client()

    # Isolate Bot state mappings per symbol
    bot_state = {}
    visualizers = {}
    for sym in SYMBOLS:
        bot_state[sym] = {
            # ★ Break-Even & Trailing state
            "phase": "INITIAL",         # INITIAL, BREAKEVEN, TRAILING
            "best_price": 0.0,
            "current_trail_sl": 0.0,
            "atr_refresh_counter": 0,
            "current_atr": 0.0,
            "original_qty": 0.0,        # Full entry qty for SL order sizing
            "initial_risk_pct": 0.0,    # ★ v17: Dynamic R:R baseline (ATR × SL_MULT / entry)
            # Core state
            "last_trade_time": 0,
            "last_signal_data": None,
            "last_side": "UNKNOWN",
            "_trade_logged": True,      # Default true to prevent logging random starts
            # ★ v7.4: Consecutive loss tracking
            "consec_losses": 0,
            "loss_cooldown_until": 0,
            # ★ v7.4: ATR history for spike detection
            "atr_history": [],
            # ★ v11.1: Micro-Momentum Armed State
            "armed_signal": "NONE",
            "armed_time": 0,
            "armed_signal_data": None,
            # ★ v20: SMC WAIT Queue (replaces v19 3-Layer WAIT)
            "wait_queue_signal": "NONE",       # Signal direction in WAIT
            "wait_queue_data": None,            # Signal data snapshot
            "wait_queue_candle_count": 0,        # Candles elapsed since WAIT
            "wait_queue_start_time": 0,          # When WAIT started
            "wait_queue_zone": None,             # ★ v20: OB/FVG zone data for retest
        }
        visualizers[sym] = StateExporter(client, sym)

    scan_count = 0

    log.info("🟢  Bot is LIVE — Institutional Edition v8.0")
    log.info(f"    Pairs: {', '.join(SYMBOLS)} ({len(SYMBOLS)} coins)")
    log.info(f"    Loop: {LOOP_INTERVAL_S}s │ Lockout: {HARD_LOCKOUT_S}s │ Scan Delay: {SCAN_DELAY_S}s/coin")
    log.info(f"    ★ SL: {SL_ATR_MULT}×ATR │ TP: {TP_ATR_MULT}×ATR │ R:R = 1:2")
    log.info(f"    ★ LIMIT Orders (Maker Fee) │ Offset: {LIMIT_OFFSET_PCT}%")
    log.info(f"    ★ Dynamic Risk: $7 Fixed Margin │ ADX Filter: ≥{ADX_ENTRY_MIN}")
    log.info(f"    ★ Correlation: >{CORRELATION_THRESHOLD} → {CORR_ACTION}")
    log.info(f"    ★ v17 True Break-Even: +1R dynamic → SL to entry (+{TRUE_BE_FEE_BUFFER_PCT}% fee cover)")
    log.info(f"    ★ Trailing SL: {TRAILING_SL_DISTANCE_PCT}% behind best price (activates at {TRAILING_ACTIVATION_RR}R)")
    log.info(f"    ★ Stale Trade: timeout only if PnL < {STALE_TRADE_MIN_LOSS_PCT}%")
    log.info(f"    ★ ML Filter: ACTIVE │ Penalty: DISABLED (retraining on clean data)")
    print()

    # ── v7.3: Set up Daily Drawdown Trackers ──
    daily_stats_date = datetime.now(timezone.utc).date()
    daily_start_balance = _get_account_balance(client)
    daily_pnl = 0.0
    daily_wins = 0
    daily_losses = 0
    kill_switch_active = False
    last_report_date = daily_stats_date  # ★ v7.4: Track daily report

    send_telegram_alert(f"🚀 <b>Bot Started - v16.0 ($1 Sniper Challenge)</b>\nPairs: {len(SYMBOLS)}\nStarting Balance: ${daily_start_balance:.2f}")

    while True:
        try:
            today = datetime.now(timezone.utc).date()
            if today != daily_stats_date:
                # ★ v7.4: Send daily summary BEFORE resetting
                if daily_wins + daily_losses > 0:
                    current_bal = _get_account_balance(client)
                    wr = (daily_wins / (daily_wins + daily_losses) * 100) if (daily_wins + daily_losses) > 0 else 0
                    send_telegram_alert(
                        f"📊 <b>DAILY REPORT — {daily_stats_date}</b>\n"
                        f"Trades: {daily_wins + daily_losses}\n"
                        f"Wins: {daily_wins} | Losses: {daily_losses}\n"
                        f"Win Rate: {wr:.1f}%\n"
                        f"Net PnL: ${daily_pnl:+.2f}\n"
                        f"Balance: ${current_bal:.2f}"
                    )

                daily_stats_date = today
                daily_start_balance = _get_account_balance(client)
                daily_pnl = 0.0
                daily_wins = 0
                daily_losses = 0
                kill_switch_active = False
                # Reset per-coin loss streaks
                for sym in SYMBOLS:
                    bot_state[sym]["consec_losses"] = 0
                    bot_state[sym]["loss_cooldown_until"] = 0
                send_telegram_alert(f"🌅 <b>New Trading Day Started!</b>\nStarting Balance: ${daily_start_balance:.2f}")

            if kill_switch_active:
                log.warning("⚠  KILL SWITCH ACTIVE. Trading suspended for today.")
                time.sleep(60)
                continue

            scan_count += 1
            now = time.time()

            for symbol in SYMBOLS:
                state = bot_state[symbol]
                visualizer = visualizers[symbol]

                # ── Hard Lockout ─────────────────────────────────────────────
                elapsed = now - state["last_trade_time"]
                if state["last_trade_time"] > 0 and elapsed < HARD_LOCKOUT_S:
                    remaining = int(HARD_LOCKOUT_S - elapsed)
                    if scan_count % 6 == 0:
                        log.info(f"🔒  [{symbol}] LOCKOUT — {remaining}s remaining.")
                    visualizer.set_bot_status(f"LOCKOUT ({remaining}s)")
                    visualizer.update()
                    continue

                # ── ★ v7.4: Consecutive Loss Cooldown ────────────────────────
                if state["loss_cooldown_until"] > now:
                    remaining = int(state["loss_cooldown_until"] - now)
                    if scan_count % 6 == 0:
                        log.info(f"🧊  [{symbol}] LOSS STREAK COOLDOWN — {remaining}s remaining ({state['consec_losses']} consecutive losses).")
                    visualizer.set_bot_status(f"LOSS COOL ({remaining}s)")
                    visualizer.update()
                    continue

                # ── Position check → Manage Trailing TP ──────────────────────
                pos = _has_open_position(client, symbol)
                if pos is not None:
                    # Mark trade as ready to log outcome upon exit
                    if state["_trade_logged"]:
                        log.info(f"📊  [{symbol}] Open {pos['side']} position detected.")
                        if state["last_signal_data"] is None:
                            state["_trade_logged"] = False
                            # Set original_qty for partial close math on reconnect
                            if state["original_qty"] == 0.0:
                                state["original_qty"] = pos["qty"]
                            state["last_side"] = pos["side"]
                            
                    try:
                        signal_data = get_quant_signal(client, symbol)
                        visualizer.set_signal_data(signal_data)
                        visualizer.set_scan_count(scan_count)
                        visualizer.update()
                    except Exception as e:
                        log.warning(f"⚠  [{symbol}] Signal fetch failed: {e}")

                    # Manage TTP (one synchronous check)
                    manage_trailing_tp(client, symbol, state, visualizer)
                    
                    time.sleep(2)  # Spacing
                    continue
                
                # ── Trade Closure check ──────────────────────────────────────
                if not state["_trade_logged"]:
                    try:
                        outcome, pnl = _determine_trade_outcome(client, symbol)
                        daily_pnl += pnl

                        # ★ v7.4: Track consecutive losses per coin
                        if pnl > 0:
                            state["consec_losses"] = 0
                            daily_wins += 1
                        else:
                            state["consec_losses"] += 1
                            daily_losses += 1
                            if state["consec_losses"] >= MAX_CONSEC_LOSSES:
                                state["loss_cooldown_until"] = time.time() + LOSS_COOLDOWN_S
                                log.warning(f"🧊  [{symbol}] {MAX_CONSEC_LOSSES} CONSECUTIVE LOSSES → Extra {LOSS_COOLDOWN_S}s cooldown activated.")
                        
                        # ── TELEGRAM ALERT: Trade Exit (with REASON) ──
                        streak_info = f"\nLoss Streak: {state['consec_losses']}/{MAX_CONSEC_LOSSES}" if state["consec_losses"] > 0 else ""
                        res_emoji = "✅ WIN" if pnl > 0 else "❌ LOSS"
                        
                        # ★ v12: Determine close reason from state
                        phase = state.get("phase", "INITIAL")
                        trail_sl = state.get("current_trail_sl", 0.0)
                        last_side = state.get("last_side", "UNKNOWN")
                        
                        if pnl > 0:
                            if phase == "TRAILING":
                                close_reason = f"🎯 Trailing SL Hit (Profit locked)"
                            elif phase == "BREAKEVEN":
                                close_reason = "🛡 Break-Even SL Hit"
                            else:
                                close_reason = "🎯 Exchange TP Hit"
                        else:
                            if phase in ("TRAILING", "BREAKEVEN"):
                                close_reason = f"🛡 SL Hit @ ${trail_sl:.4f}"
                            else:
                                close_reason = "🔻 Initial SL Hit (ATR-based)"
                        
                        send_telegram_alert(
                            f"{res_emoji} <b>Trade Closed</b>\n"
                            f"Coin: {symbol}\n"
                            f"Side: {last_side}\n"
                            f"Close Reason: {close_reason}\n"
                            f"Phase: {phase}\n"
                            f"Realized PnL: ${pnl:.2f}\n"
                            f"Today's Net PnL: ${daily_pnl:.2f}{streak_info}"
                        )

                        # ── KILL SWITCH EVALUATION ──
                        if daily_pnl < 0 and abs(daily_pnl) >= daily_start_balance * (MAX_DAILY_LOSS_PCT / 100.0):
                            kill_switch_active = True
                            log.error(f"🚨 KILL SWITCH ACTIVATED! Daily Loss = ${daily_pnl:.2f}")
                            send_telegram_alert(
                                f"⚠️ <b>KILL SWITCH ACTIVATED</b> ⚠️\n"
                                f"Limit Hit: {MAX_DAILY_LOSS_PCT}% Daily Loss\n"
                                f"Loss amount: ${daily_pnl:.2f}\n"
                                f"Trading suspended until tomorrow."
                            )

                        if state["last_signal_data"] is not None:
                            ml_features = state["last_signal_data"].get("ml_features", {})
                            ml_filter.log_trade(ml_features, outcome)
                        else:
                            default_features = {
                                "adx": 25.0, "rsi": 50.0, "score": 9,
                                "oi_trend": "FLAT", "funding_rate": 0.0,
                                "atr": 100.0, "regime": "TRENDING",
                            }
                            ml_filter.log_trade(default_features, outcome)
                            log.info(f"🤖  [{symbol}] ML: Logged reconnect trade with default features")
                        
                        # Record PnL for dashboard (using aggregated pnl from above)
                        try:
                            visualizer.record_trade_result(pnl, state.get("last_side", "UNKNOWN"))
                        except Exception:
                            pass
                        
                        state["_trade_logged"] = True
                        state["last_signal_data"] = None
                        _reset_bot_state(state)
                        state["last_trade_time"] = time.time()
                        visualizer.clear_position_data()
                        log.info(f"🔒  [{symbol}] LOCKOUT ENGAGED — {HARD_LOCKOUT_S}s cooldown.")
                    except Exception as e:
                        log.warning(f"⚠  [{symbol}] ML outcome logging failed: {e}")
                    
                    continue  # Skip right into lockout

                # ── Fetch signal (forced refresh) ────────────────────────────
                try:
                    signal_data = get_quant_signal(client, symbol)
                    visualizer.set_signal_data(signal_data)
                    visualizer.set_bot_status("SCANNING")
                    visualizer.set_scan_count(scan_count)
                    visualizer.update()
                except Exception as e:
                    log.warning(f"⚠  [{symbol}] StateExporter/Signal failed: {e}")
                    time.sleep(2)
                    continue

                # ── Print score dashboard ────────────────────────────────────
                _print_score_dashboard(signal_data, scan_count, symbol)

                signal = signal_data["signal"]
                current_price = signal_data["current_price"]
                atr = signal_data["atr"]
                current_adx = signal_data.get("adx", 0)

                # ── ★ v7.2: ADX HARD FILTER — Block trades in choppy markets ─
                if signal in ("BUY", "SELL") and current_adx < ADX_ENTRY_MIN:
                    log.info(
                        f"🚫  [{symbol}] ADX FILTER — ADX {current_adx:.1f} < {ADX_ENTRY_MIN} │ "
                        f"Market too choppy. Skipping {signal}."
                    )
                    signal = "WAIT"  # Override signal

                # ── ★ v7.4: VOLATILITY SPIKE FILTER ──────────────────────────
                if signal in ("BUY", "SELL") and atr > 0:
                    state["atr_history"].append(atr)
                    if len(state["atr_history"]) > 20:
                        state["atr_history"] = state["atr_history"][-20:]
                    if len(state["atr_history"]) >= 5:
                        avg_atr = sum(state["atr_history"]) / len(state["atr_history"])
                        if atr > avg_atr * ATR_SPIKE_MULT:
                            log.info(
                                f"🌋  [{symbol}] ATR SPIKE — Current: {atr:.2f} > {ATR_SPIKE_MULT}× Avg: {avg_atr:.2f} │ "
                                f"Dangerous volatility. Skipping {signal}."
                            )
                            signal = "WAIT"

                # ── ★ v20: SMC WAIT QUEUE PROCESSOR (re-check OB/FVG zone retest) ──
                if state["wait_queue_signal"] != "NONE" and state["armed_signal"] == "NONE":
                    wq_age_s = now - state["wait_queue_start_time"]
                    wq_candles = int(wq_age_s / 300)  # Each M5 candle = 300s
                    state["wait_queue_candle_count"] = wq_candles
                    
                    if wq_candles >= WAIT_QUEUE_MAX_CANDLES:
                        log.warning(f"⌛  [{symbol}] WAIT QUEUE EXPIRED: {WAIT_QUEUE_MAX_CANDLES} candles elapsed. Signal killed.")
                        state["wait_queue_signal"] = "NONE"
                        state["wait_queue_data"] = None
                        state["wait_queue_candle_count"] = 0
                        state["wait_queue_start_time"] = 0
                        state["wait_queue_zone"] = None
                    else:
                        wq_dir = state["wait_queue_signal"]
                        # ★ v20: Re-run full SMC validation (check if price entered OB/FVG zone)
                        smc_result, smc_reason, smc_zone = _smc_entry_validate(client, symbol, wq_dir)
                        
                        if smc_result == "PASS":
                            log.info(f"🔄  [{symbol}] WAIT QUEUE → SMC PASS after {wq_candles} candles! Re-arming {wq_dir}...")
                            state["armed_signal"] = wq_dir
                            state["armed_time"] = now
                            state["armed_signal_data"] = state["wait_queue_data"]
                            # Clear WAIT queue
                            state["wait_queue_signal"] = "NONE"
                            state["wait_queue_data"] = None
                            state["wait_queue_candle_count"] = 0
                            state["wait_queue_start_time"] = 0
                            state["wait_queue_zone"] = None
                        elif smc_result == "NEUTRAL":
                            log.warning(f"🚫  [{symbol}] WAIT QUEUE → NEUTRAL on re-check. Signal killed.")
                            state["wait_queue_signal"] = "NONE"
                            state["wait_queue_data"] = None
                            state["wait_queue_candle_count"] = 0
                            state["wait_queue_start_time"] = 0
                            state["wait_queue_zone"] = None
                        else:
                            if scan_count % 3 == 0:
                                log.info(f"⏳  [{symbol}] WAIT QUEUE: Still waiting ({wq_candles}/{WAIT_QUEUE_MAX_CANDLES} candles) for OB/FVG retest...")

                # ── ★ ARM the Signal instead of immediate execution ────────────
                if signal in ("BUY", "SELL") and state["armed_signal"] == "NONE":
                    log.info(f"🔫  [{symbol}] SIGNAL ARMED ({signal}) │ Waiting max 4 mins for Vol Burst...")
                    state["armed_signal"] = signal
                    state["armed_time"] = now
                    state["armed_signal_data"] = signal_data

                # ── Process Armed State (Wait for Micro-Momentum) ────────────
                if state["armed_signal"] != "NONE":
                    armed_age = now - state["armed_time"]
                    
                    if armed_age > 240:  # 4 minutes timeout
                        log.warning(f"⌛  [{symbol}] ARMED CANCELLED │ No volume burst within 4 mins. Setup stale.")
                        state["armed_signal"] = "NONE"
                        state["armed_time"] = 0
                        state["armed_signal_data"] = None
                    else:
                        armed_dir = state["armed_signal"]
                        burst_detected = _check_volume_burst(client, symbol, armed_dir)
                        
                        if burst_detected:
                            log.info(f"💥  [{symbol}] VOLUME BURST TRIGGERED! Running Pre-Flight Check...")
                            
                            # ── ★ ADVANCED PRE-FLIGHT CHECK ──
                            passed, reason = AdvancedExecutionValidator.validate(client, symbol, armed_dir)
                            is_micro_scalp = False
                            
                            if not passed:
                                if ENABLE_MICRO_SCALPING:
                                    log.warning(f"⚡  [{symbol}] PRE-FLIGHT WEAK ({reason}) — Converting to MICRO-SCALP!")
                                    is_micro_scalp = True
                                else:
                                    log.warning(f"🚫  [{symbol}] PRE-FLIGHT REJECTED: {reason}")
                                    visualizer.record_rejection(f"PRE-FLIGHT: {reason}")
                                    state["armed_signal"] = "NONE"
                                    state["armed_time"] = 0
                                    state["armed_signal_data"] = None
                                    continue
                                    
                            if not is_micro_scalp:
                                log.info(f"✅  [{symbol}] PRE-FLIGHT PASSED. Executing {armed_dir}...")
                            
                            # ── ★ v11.1: ANTI-FOMO GATE (Final Entry Filter) ──
                            # Legacy anti-fomo gate commented out. Master Logic (AdvancedExecutionValidator) now handles all Anti-FOMO rules.
                            # fomo_passed, fomo_reason = _anti_fomo_entry_check(client, symbol, armed_dir)
                            # if not fomo_passed:
                            #     log.warning(f"🚫  [{symbol}] {fomo_reason} — ENTRY BLOCKED.")
                            #     state["armed_signal"] = "NONE"
                            #     state["armed_time"] = 0
                            #     state["armed_signal_data"] = None
                            #     continue

                            # ── ★ v12: S/R REJECTION QUALITY GATE ──
                            # ★ v15: DISABLED for testnet — was blocking 75% of valid trades
                            # rej_passed, rej_reason = _check_rejection_quality(client, symbol, armed_dir)
                            # if not rej_passed:
                            #     log.warning(f"🚫  [{symbol}] {rej_reason} — ARMED CANCELLED.")
                            #     state["armed_signal"] = "NONE"
                            #     state["armed_time"] = 0
                            #     state["armed_signal_data"] = None
                            #     continue
                            log.info(f"✅  [{symbol}] S/R Gate: BYPASSED (v15 testnet mode)")

                            # ── ★ v18: MULTI-TIMEFRAME ANALYSIS (MTFA) GATE ──
                            if MTFA_ENABLED:
                                mtfa_aligned, mtfa_reason = _check_1h_trend_ema50(client, symbol, armed_dir)
                                if not mtfa_aligned:
                                    log.warning(f"🚫  [{symbol}] {mtfa_reason}")
                                    visualizer.record_rejection(mtfa_reason)
                                    state["armed_signal"] = "NONE"
                                    state["armed_time"] = 0
                                    state["armed_signal_data"] = None
                                    continue
                                else:
                                    log.info(f"🌍  [{symbol}] {mtfa_reason}")

                            # ── ★ v20: SMC ENTRY VALIDATION GATE ──
                            if ENTRY_VALIDATION_ENABLED:
                                smc_result, smc_reason, smc_zone = _smc_entry_validate(client, symbol, armed_dir)
                                log.info(f"📊  [{symbol}] SMC Result: {smc_result} | {smc_reason}")
                                
                                if smc_result == "NEUTRAL":
                                    # Kill the signal entirely
                                    log.warning(f"🚫  [{symbol}] SMC NEUTRAL — Signal killed.")
                                    visualizer.record_rejection(smc_reason)
                                    state["armed_signal"] = "NONE"
                                    state["armed_time"] = 0
                                    state["armed_signal_data"] = None
                                    continue
                                elif smc_result == "WAIT":
                                    # Move to WAIT queue (don't cancel, don't execute)
                                    log.info(f"⏳  [{symbol}] SMC WAIT — Signal queued for OB/FVG retest. {WAIT_QUEUE_MAX_CANDLES} candles max.")
                                    state["wait_queue_signal"] = armed_dir
                                    state["wait_queue_data"] = state["armed_signal_data"]
                                    state["wait_queue_candle_count"] = 0
                                    state["wait_queue_start_time"] = time.time()
                                    state["wait_queue_zone"] = smc_zone  # ★ v20: Store zone for retest
                                    # Disarm so it doesn't re-trigger burst logic
                                    state["armed_signal"] = "NONE"
                                    state["armed_time"] = 0
                                    state["armed_signal_data"] = None
                                    continue
                                # else: "PASS" — continue to execution

                            # Restore data for execution
                            exc_signal = armed_dir
                            exc_data = state["armed_signal_data"]
                            if smc_zone: exc_data["smc_zone"] = smc_zone  # ★ Inject SMC mathematical SL geometry into execution data
                            exc_price = current_price  # Use latest price, not the one from 2 mins ago
                            exc_atr = exc_data["atr"]
                            
                            # ★ CORRELATION FILTER
                            btc_corr = exc_data.get("btc_correlation", 0.0)
                            size_multiplier = 1.0

                            if abs(btc_corr) > CORRELATION_THRESHOLD and symbol != "BTCUSDT":
                                btc_same_dir = _check_btc_same_direction(client, exc_signal)
                                if btc_same_dir:
                                    if CORR_ACTION == "SKIP":
                                        log.info(f"🚫  [{symbol}] CORR FILTER SKIP — BTC Corr: {btc_corr:+.4f} > {CORRELATION_THRESHOLD} & BTC {exc_signal} running")
                                        state["armed_signal"] = "NONE"
                                        time.sleep(2)
                                        continue
                                    else:  # REDUCE
                                        size_multiplier = CORR_REDUCE_SIZE_PCT / 100.0
                                        log.info(f"⚠  [{symbol}] CORR FILTER REDUCE — Size: {CORR_REDUCE_SIZE_PCT}% │ BTC Corr: {btc_corr:+.4f}")

                            success, entry_qty = execute_trade(
                                client, symbol, exc_signal, exc_price, exc_atr, exc_data,
                                size_multiplier=size_multiplier, is_micro_scalp=is_micro_scalp
                            )
                            if success:
                                state["last_signal_data"] = exc_data
                                state["last_side"] = exc_signal
                                state["_trade_logged"] = False
                                state["original_qty"] = entry_qty
                                state["phase"] = "INITIAL"
                                state["best_price"] = 0.0
                                state["current_trail_sl"] = 0.0
                                state["trade_open_time"] = time.time()  # ★ Exact entry time
                            
                            # Reset armed state immediately after processing
                            state["armed_signal"] = "NONE"
                            state["armed_time"] = 0
                            state["armed_signal_data"] = None
                        else:
                            if scan_count % 2 == 0:
                                log.info(f"🔎  [{symbol}] ARMED ({armed_dir}) │ Waiting for volume burst... ({int(240 - armed_age)}s lock)")

                time.sleep(SCAN_DELAY_S)  # ★ v7.2: API rate-limit safety between coins
                
            # Scan delay spacing
            time.sleep(LOOP_INTERVAL_S)

        except BinanceAPIException as e:
            if e.status_code == 429:
                log.error("⏳  RATE LIMITED. Backing off 60s...")
                time.sleep(60)
            elif e.code == -1001:
                log.error("🌐  Timeout. Retrying 30s...")
                time.sleep(30)
            else:
                log.error(f"❌  API Error [{e.status_code}]: {e.message}")
                time.sleep(15)

        except BinanceRequestException as e:
            log.error(f"🌐  Request Error: {e}. Retrying 30s...")
            time.sleep(30)

        except ConnectionError:
            log.error("🌐  CONNECTION LOST. Retrying 30s...")
            time.sleep(30)

        except KeyboardInterrupt:
            log.info("🛑  Bot stopped by user.")
            print("\n🛑  Graceful shutdown complete.\n")
            break

        except Exception as e:
            log.error(f"❌  UNHANDLED: {e}. Retrying 15s...")
            time.sleep(15)


def _determine_trade_outcome(client: Client, symbol: str) -> tuple:
    """
    Determine if the last closed trade was a win (1) or loss (0).
    
    ★ FIX: Aggregates ALL partial fills from the same closing event.
    Binance often fills a close order in multiple chunks (partial fills),
    each generating a separate REALIZED_PNL entry. We fetch the last 10
    entries and sum all that belong to the same close event (within 60s window).
    
    Also fetches COMMISSION entries for accurate fee reporting.
    
    Returns (outcome, total_pnl).
    """
    try:
        # ★ Fetch last 10 realized PnL entries (enough to capture all partial fills)
        income = client.futures_income_history(
            symbol=symbol,
            incomeType="REALIZED_PNL",
            limit=10,
        )
        if not income or len(income) == 0:
            return 0, 0.0
        
        # ★ Find the latest entry's timestamp, then aggregate all entries
        # within a 60-second window (same closing event)
        latest_ts = int(income[-1].get("time", 0))
        AGGREGATION_WINDOW_MS = 60_000  # 60 seconds in milliseconds
        
        total_pnl = 0.0
        fill_count = 0
        
        for entry in reversed(income):
            entry_ts = int(entry.get("time", 0))
            # Only aggregate entries within the time window of the latest fill
            if abs(latest_ts - entry_ts) <= AGGREGATION_WINDOW_MS:
                total_pnl += float(entry.get("income", 0.0))
                fill_count += 1
            else:
                break  # Older entries belong to a different trade
        
        # ★ Fetch commission/fees for accurate reporting
        total_fees = 0.0
        try:
            fees = client.futures_income_history(
                symbol=symbol,
                incomeType="COMMISSION",
                limit=10,
            )
            if fees:
                for fee_entry in reversed(fees):
                    fee_ts = int(fee_entry.get("time", 0))
                    if abs(latest_ts - fee_ts) <= AGGREGATION_WINDOW_MS:
                        total_fees += abs(float(fee_entry.get("income", 0.0)))
                    else:
                        break
        except Exception:
            pass  # Fees are nice-to-have, don't fail if unavailable
        
        outcome = 1 if total_pnl > 0 else 0
        result_emoji = "✅" if outcome == 1 else "❌"
        
        if fill_count > 1:
            log.info(
                f"🤖  [{symbol}] Trade Result: {result_emoji} "
                f"Aggregated PnL: ${total_pnl:.2f} ({fill_count} fills) │ Fees: ${total_fees:.2f}"
            )
        else:
            log.info(f"🤖  [{symbol}] Trade Result: {result_emoji} PnL: ${total_pnl:.2f} │ Fees: ${total_fees:.2f}")
        
        return outcome, total_pnl
    except Exception as e:
        log.warning(f"⚠  [{symbol}] Could not determine trade outcome: {e}")
    
    return 0, 0.0  # Default to loss if unknown (conservative)


if __name__ == "__main__":
    main()
