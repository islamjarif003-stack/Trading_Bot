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
import io
import time
import math
import logging
import threading
from datetime import datetime, timezone

# ★ v28: SMC Image Engine — headless matplotlib for VPS
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend (no display needed)
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D
import pandas as pd

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
DRY_RUN              = False    # 🚀 LIVE TRADING ACTIVATED
USE_TESTNET          = False    # ★ v20.1: True = Binance Testnet, False = Binance Mainnet
ENABLE_DYNAMIC_WATCHLIST = True # ★ Fetch Top 50 Volatile USDT pairs dynamically
ENABLE_MICRO_SCALPING = False   # ★ v12: DISABLED — Pre-flight fail = NO TRADE (no more weak entries)
SYMBOLS         = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

# ★ v26: BLACKLIST — Dead coins + Meme/Low-cap + Consistent losers + Manipulated pairs
BLACKLIST_COINS = {
    # Dead coins (Price $0.0 on testnet/mainnet)
    "OMNIUSDT", "ALPHAUSDT", "BSWUSDT", "HIFIUSDT", "NEIROETHUSDT",
    "A2ZUSDT", "TANSSIUSDT", "NKNUSDT", "BDXNUSDT", "LITUSDT", "BAKEUSDT",
    "UXLINKUSDT", "VIDTUSDT", "SXPUSDT", "AGIXUSDT", "PORT3USDT",
    # Consistent losers from audit (0% win rate, heavy losses)
    "MAGMAUSDT", "BULLAUSDT", "TNSRUSDT", "SIRENUSDT",
    # Near-liquidation risk
    "SOONUSDT", "TRADOORUSDT",
    # ★ v26: Meme coins — pump & dump, no real price action, SL gets hunted
    "GIGGLEUSDT", "MEMEFIUSDT", "RAVEUSDT", "BLESSUSDT",
    "FARTCOINUSDT", "1000PEPEUSDT", "DOGEUSDT",
    # ★ v26: Low-cap/micro-cap — extremely thin orderbook, easy to manipulate
    "INUSDT", "BRUSDT", "BZUSDT", "CLUSDT", "BIOUSDT",
    "APRUSDT", "MYXUSDT", "RIVERUSDT", "WLFIUSDT",
    "BASEDUSDT", "ALPACAUSDT", "LINAUSDT", "BNXUSDT",
    "ARIAUSDT", "ENAUSDT", "WETUSDT",
}

# ★ v34-fix3: TradFi coins — markets closed on weekends (Sat/Sun), skip those days
TRADFI_SYMBOLS = {"XAUUSDT", "XAGUSDT", "CRCLUSDT", "TSLAUSDT", "INTCUSDT"}

LEVERAGE        = 20                # ★ FIXED 20x leverage
SL_ATR_MULT     = 3.0               # ★ v34-fix3: SL = 3.0 × ATR (was 1.5 — too tight, noise killed winning trades)
TP_ATR_MULT     = 6.0               # ★ v34-fix3: TP = 6.0 × ATR (R:R = 1:2 maintained with SL=3.0)
HARD_LOCKOUT_S  = 300               # ★ v22: 5-minute hard lockout after every trade (was 30m)
LOOP_INTERVAL_S = 10                # Seconds between each scan cycle
ENTRY_RISK_PCT  = 15.0              # ★ $50 Config: 15% of $50 ≈ $7.50 risk per trade
MAX_MARGIN_PCT  = 30.0              # ★ $50 Config: Max 30% of balance as margin ($15 max)
LIMIT_OFFSET_PCT = 0.05             # ★ Buffer (Tolerance Zone): 0.05% offset to ensure limit orders get filled
ADX_ENTRY_MIN   = 5.0               # ★ v20: Lowered from 7.0 — SMC works in ranging markets, only filter dead markets
SCAN_DELAY_S    = 1                 # ★ v7.2: Delay between each coin scan (API rate-limit safety)
MAX_DAILY_LOSS_PCT = 10.0            # ★ $50 Config: 10% = $5 daily loss limit (2 SL trades)

# ─── ★ v7.4: CONSECUTIVE LOSS COOLDOWN ──────────────────────────────────────
MAX_CONSEC_LOSSES   = 3         # After 3 consecutive losses on a coin...
LOSS_COOLDOWN_S     = 14400     # ...add 4-hour extra cooldown for that coin (strict SMC rule)

# ─── ★ v7.4: VOLATILITY SPIKE FILTER ────────────────────────────────────────
ATR_SPIKE_MULT      = 2.0       # Skip entry if current ATR > 2× recent average ATR

# ─── ★ v11.1: ANTI-FOMO ENTRY GATE (Hard Blocker) ──────────────────────────
ANTI_FOMO_EMA_DIST_PCT  = 0.30  # ★ v16.1: Widened to 0.30% to prevent blocking good setups (was 0.18%)
ANTI_FOMO_CANDLE_BODY   = 0.60  # Block if candle body is >60% of total range (impulsive candle)
# NOTE: ANTI_FOMO_ENABLED master switch is set below at L2289 (currently False, SMC handles this)

# ─── ★ PER-SYMBOL PRECISION MAPPING ─────────────────────────────────────────
SYMBOL_PRECISION = {
    "BTCUSDT": {"price": 1, "qty": 3},
    "ETHUSDT": {"price": 2, "qty": 2},
    "SOLUSDT": {"price": 2, "qty": 1},
    "BNBUSDT": {"price": 2, "qty": 2},
    "XRPUSDT": {"price": 4, "qty": 0},
}

# ─── ★ LIVE ACCOUNT SETUP (MICRO-PARAMETERS) ─────────────────────────────────
VIRTUAL_ACCOUNT         = False    # IMPORTANT: False activates LIVE Binance Execution, True = Virtual Balance Mocks
INITIAL_BALANCE         = 25.00    # Hard Account Balance Baseline
PROFIT_TARGET           = 5.00     # Optional profit ceiling
DAILY_LOSS_LIMIT        = 5.00     # 🚨 Global Hard Stop mechanism ($5)
MAX_TOTAL_LOSS          = 2.00     # 🚨 Complete System Lockout at $2 cumulative loss

# ─── ★ STRICT POSITION SIZING & RISK ─────────────────────────────────────────
LEVERAGE                = 20       # 20x Leverage (Ensures Notional Value scales correctly)
ENTRY_RISK_PCT          = 6.0      # 6% of $25 = $1.50 Margin per trade (prevents Binance notional limits)
MAX_MARGIN_PCT          = 6.0      # Hard-caps max permitted margin to $1.50
RISK_PER_TRADE_PERCENT  = 6.0      # Redundancy constraint for internal Kelly mappings
DEFAULT_PRICE_PRECISION = 2
DEFAULT_QTY_PRECISION   = 3

# ─── ★ BTC CORRELATION FILTER ──────────────────────────────────────────────
CORRELATION_THRESHOLD = 0.80    # Skip/reduce if corr > 0.80
CORR_ACTION           = "SKIP"  # "SKIP" = skip trade, "REDUCE" = 50% size
CORR_REDUCE_SIZE_PCT  = 50      # Reduce to 50% if CORR_ACTION == "REDUCE"

# ─── ★ BREAK-EVEN & TRAILING STOP-LOSS ("Let Winners Run" Edition) ───────
# Note: These are RAW price % (Unleveraged). A 0.4% raw move = 8% on Binance at 20x leverage.
# ★ v17: TRUE BREAK-EVEN — uses dynamic R:R, not static %. BE triggers at 1R profit.
TRUE_BE_FEE_BUFFER_PCT   = 0.25   # ★ v37: Increased 0.15 → 0.25 to cover taker fees + slippage on BE exits
TRAILING_ACTIVATION_RR   = 1.5    # ★ v37: BE triggers at 1.5R (was 1.0R — too early for 15m candles)
TRAILING_SL_DISTANCE_PCT = 1.55   # ★ v38: Trail 1.55% behind best price (was 1.80% / 1.20%)
TTP_CHECK_INTERVAL       = 3      # Check every 3 cycles
DISABLE_HARD_TP          = True    # ★ v26: DISABLED fixed TP to allow dynamic Trailing SL for 'Let Winners Run' mode
SMART_REVERSAL_EXIT      = True    # ★ v12: Close if 5m MA25 cross-under/over detected
STALE_TRADE_MIN_LOSS_PCT = -0.50   # ★ v17: Stale timeout only fires if PnL < -0.50% (prevents fee-draining flat closes)


# ─── ★ v22: HALF-KELLY CRITERION POSITION SIZING ────────────────────────────
KELLY_DEFAULT_WIN_RATE = 0.40     # Baseline assumption: 40% win rate
KELLY_DEFAULT_AVG_RR   = 2.0      # Baseline assumption: 1:2 Reward-to-Risk
KELLY_MAX_RISK_PCT     = 0.10     # Hard cap: never risk more than 10% of balance
KELLY_MIN_TRADES_FOR_LIVE = 10    # Use live stats only after 10+ trades

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

import json as _json

# Global Kelly performance tracker (shared across all symbols) — FILE-PERSISTENT
KELLY_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kelly_state.json")

def _load_kelly_state() -> dict:
    """Load Kelly performance data from disk."""
    default = {
        "total_wins": 0,
        "total_losses": 0,
        "total_win_pnl": 0.0,
        "total_loss_pnl": 0.0,
    }
    try:
        if os.path.exists(KELLY_STATE_FILE):
            with open(KELLY_STATE_FILE, "r") as f:
                data = _json.load(f)
                log.info(f"📂  [KELLY] Loaded state: W={data.get('total_wins',0)} L={data.get('total_losses',0)} | Win$={data.get('total_win_pnl',0):.2f} Loss$={data.get('total_loss_pnl',0):.2f}")
                return {**default, **data}
    except Exception as e:
        log.warning(f"⚠  [KELLY] Failed to load state file: {e}")
    return default

def _save_kelly_state():
    """Save Kelly performance data to disk."""
    try:
        with open(KELLY_STATE_FILE, "w") as f:
            _json.dump(kelly_state, f, indent=2)
    except Exception as e:
        log.warning(f"⚠  [KELLY] Failed to save state file: {e}")

kelly_state = _load_kelly_state()

# ★ v22.1: DRY RUN Position Simulator (FILE-PERSISTENT)
# Tracks simulated positions so the bot doesn't think trades are closed immediately
# Persisted to JSON file so positions survive PM2 restarts
DRY_RUN_POSITIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dry_run_positions.json")

def _load_dry_run_positions() -> dict:
    """Load simulated positions from disk."""
    try:
        if os.path.exists(DRY_RUN_POSITIONS_FILE):
            with open(DRY_RUN_POSITIONS_FILE, "r") as f:
                data = _json.load(f)
                log.info(f"📂  [DRY RUN] Loaded {len(data)} simulated positions from disk.")
                return data
    except Exception as e:
        log.warning(f"⚠  [DRY RUN] Failed to load positions file: {e}")
    return {}

def _save_dry_run_positions():
    """Save simulated positions to disk."""
    try:
        with open(DRY_RUN_POSITIONS_FILE, "w") as f:
            _json.dump(dry_run_positions, f, indent=2)
    except Exception as e:
        log.warning(f"⚠  [DRY RUN] Failed to save positions file: {e}")

dry_run_positions = _load_dry_run_positions()

# ★ v22.2: Store last closed sim PnL so _determine_trade_outcome can use it
dry_run_last_pnl = {}

# ─── ★ PROP STATE MANAGER ───────────────────────────────────────────────────
PROP_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prop_state.json")

def _load_prop_state() -> dict:
    import datetime
    today_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    default = {
        "current_balance": INITIAL_BALANCE,
        "today_start_balance": INITIAL_BALANCE,
        "date_str": today_str
    }
    
    if not VIRTUAL_ACCOUNT:
        return default
        
    try:
        if os.path.exists(PROP_STATE_FILE):
            with open(PROP_STATE_FILE, "r") as f:
                data = _json.load(f)
                
            # If UTC day flipped, reset the daily starting balance
            if data.get("date_str") != today_str:
                data["today_start_balance"] = data.get("current_balance", INITIAL_BALANCE)
                data["date_str"] = today_str
                log.info(f"🔄  [PROP] New UTC Day! Daily Reset. Starting Balance: ${data['today_start_balance']:.2f}")
                
            log.info(f"📂  [PROP] Loaded Virtual State: Balance=${data.get('current_balance', INITIAL_BALANCE):.2f}")
            return {**default, **data}
    except Exception as e:
        log.warning(f"⚠  [PROP] Failed to load prop state: {e}")
    return default

def _save_prop_state():
    if not VIRTUAL_ACCOUNT: return
    try:
        with open(PROP_STATE_FILE, "w") as f:
            _json.dump(prop_state, f, indent=2)
    except Exception as e:
        log.warning(f"⚠  [PROP] Failed to save prop state: {e}")

prop_state = _load_prop_state()

def check_prop_rules():
    """
    Evaluates current balance against daily and max drawdown limits.
    Returns True if trading is allowed, False if HALTED.
    """
    if not VIRTUAL_ACCOUNT:
        return True
        
    daily_pnl = prop_state["current_balance"] - prop_state["today_start_balance"]
    total_pnl = prop_state["current_balance"] - INITIAL_BALANCE
    
    if total_pnl <= -MAX_TOTAL_LOSS:
        log.error(f"🚨🚨 [PROP] MAX DRAWDOWN REACHED! Virtual Account Failed. (${total_pnl:.2f})")
        return False
        
    if daily_pnl <= -DAILY_LOSS_LIMIT:
        log.error(f"🛡️  [PROP] DAILY LOSS LIMIT REACHED! Trading Halted for Today. (${daily_pnl:.2f})")
        return False
        
    if total_pnl >= PROFIT_TARGET:
        log.info(f"🏆  [PROP] VIRTUAL CHALLENGE PASSED! Target Reached: ${prop_state['current_balance']:.2f}")
    
    return True

def calculate_kelly_risk_pct(win_rate: float, avg_rr: float) -> float:
    """
    ★ v22: Half-Kelly Criterion Calculator.
    
    Full Kelly: K = W - ((1 - W) / R)
    Half Kelly: K / 2 (more conservative, reduces variance by ~50%)
    
    Args:
        win_rate: Historical win rate (0.0 to 1.0)
        avg_rr:   Average Reward-to-Risk ratio (e.g., 2.0 = 1:2 R:R)
    
    Returns:
        float: Fraction of balance to risk (0.0 to KELLY_MAX_RISK_PCT)
    """
    if avg_rr <= 0:
        return 0.0
    
    full_kelly = win_rate - ((1.0 - win_rate) / avg_rr)
    
    if full_kelly <= 0.0:
        return 0.0  # No statistical edge → do not trade
    
    half_kelly = full_kelly / 2.0
    
    # Safety cap: never exceed KELLY_MAX_RISK_PCT
    return min(half_kelly, KELLY_MAX_RISK_PCT)


def _get_kelly_params() -> tuple:
    """
    Returns (win_rate, avg_rr) from live tracking if enough data,
    otherwise falls back to conservative defaults.
    """
    total_trades = kelly_state["total_wins"] + kelly_state["total_losses"]
    
    if total_trades >= KELLY_MIN_TRADES_FOR_LIVE:
        live_wr = kelly_state["total_wins"] / total_trades
        
        # Average R:R = avg_win / avg_loss
        if kelly_state["total_losses"] > 0 and kelly_state["total_loss_pnl"] > 0:
            avg_win = kelly_state["total_win_pnl"] / max(kelly_state["total_wins"], 1)
            avg_loss = kelly_state["total_loss_pnl"] / kelly_state["total_losses"]
            live_rr = avg_win / avg_loss if avg_loss > 0 else KELLY_DEFAULT_AVG_RR
        else:
            live_rr = KELLY_DEFAULT_AVG_RR
        
        return live_wr, live_rr
    else:
        return KELLY_DEFAULT_WIN_RATE, KELLY_DEFAULT_AVG_RR




# ─── TELEGRAM HELPER ─────────────────────────────────────────────────────────
def send_telegram_alert(message: str):
    """Sends a text message to the configured Telegram chat(s)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # Support multiple comma-separated chat IDs
    chat_ids = [cid.strip() for cid in TELEGRAM_CHAT_ID.split(",") if cid.strip()]
    
    for chat_id in chat_ids:
        payload = {
            "chat_id": chat_id,
            "text": message[:4000],
            "parse_mode": "HTML"
        }
        try:
            requests.post(url, json=payload, timeout=3)
        except Exception as e:
            log.warning(f"⚠  Telegram alert failed for {chat_id}: {e}")


def send_telegram_photo(image_stream: io.BytesIO, caption: str = ""):
    """★ v28: Send an image (BytesIO stream) to Telegram with optional caption."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    
    chat_ids = [cid.strip() for cid in TELEGRAM_CHAT_ID.split(",") if cid.strip()]
    
    for i, chat_id in enumerate(chat_ids):
        # We need to seek to 0 for each chat ID
        image_stream.seek(0)
        files = {"photo": ("smc_chart.png", image_stream, "image/png")}
        data = {
            "chat_id": chat_id,
            "caption": caption[:1024],  # Telegram photo caption limit
            "parse_mode": "HTML"
        }
        try:
            resp = requests.post(url, files=files, data=data, timeout=10)
            if resp.status_code != 200:
                log.warning(f"⚠  Telegram photo send failed for {chat_id}: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            log.warning(f"⚠  Telegram photo send failed for {chat_id}: {e}")


def generate_smc_chart(client: Client, symbol: str, signal: str, avg_entry: float,
                       sl_price: float, tp_price: float, signal_data: dict) -> io.BytesIO:
    """
    ★ v28: SMC Image Engine — Generate institutional-grade TA chart.
    
    Features:
      - Dark theme candlestick chart (5m timeframe)
      - Order Block zone (faded blue box)
      - Entry / SL / TP horizontal lines with shaded risk/reward zones
      - BOS/CHOCH structure annotations
      - Score & confluence info overlay
    
    Returns: io.BytesIO PNG image stream (no disk writes)
    """
    try:
        # ── Fetch 100 candles of 5m data ──
        raw_klines = client.futures_klines(symbol=symbol, interval='5m', limit=100)
        if not raw_klines or len(raw_klines) < 20:
            log.warning(f"⚠ [{symbol}] SMC Chart: Not enough data ({len(raw_klines) if raw_klines else 0} candles)")
            return None
        
        # ── Build DataFrame ──
        df = pd.DataFrame(raw_klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_vol', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        
        # ── Dark Theme Setup ──
        fig, ax = plt.subplots(1, 1, figsize=(14, 7), facecolor='#0d1117')
        ax.set_facecolor('#0d1117')
        ax.tick_params(colors='#8b949e', labelsize=8)
        ax.spines['top'].set_color('#21262d')
        ax.spines['bottom'].set_color('#21262d')
        ax.spines['left'].set_color('#21262d')
        ax.spines['right'].set_color('#21262d')
        ax.yaxis.label.set_color('#c9d1d9')
        ax.xaxis.label.set_color('#c9d1d9')
        ax.grid(True, alpha=0.1, color='#30363d')
        
        # ── Draw Candlesticks ──
        n = len(df)
        x_indices = range(n)
        
        for i in x_indices:
            o, h, l, c = df['open'].iloc[i], df['high'].iloc[i], df['low'].iloc[i], df['close'].iloc[i]
            color = '#26a69a' if c >= o else '#ef5350'  # Green / Red
            
            # Wick
            ax.plot([i, i], [l, h], color=color, linewidth=0.8, alpha=0.8)
            # Body
            body_bottom = min(o, c)
            body_height = abs(c - o)
            if body_height == 0:
                body_height = (h - l) * 0.01  # Doji
            rect = mpatches.FancyBboxPatch(
                (i - 0.35, body_bottom), 0.7, body_height,
                boxstyle="round,pad=0.02", facecolor=color, edgecolor=color, alpha=0.9
            )
            ax.add_patch(rect)
        
        # ── Order Block Zone (Faded Blue Box) ──
        smc_zone = signal_data.get('smc_zone') if signal_data else None
        if smc_zone and smc_zone.get('zone_high') and smc_zone.get('zone_low'):
            zone_high = float(smc_zone['zone_high'])
            zone_low = float(smc_zone['zone_low'])
            zone_type = smc_zone.get('zone_type', 'OB')
            ob_rect = mpatches.Rectangle(
                (0, zone_low), n, zone_high - zone_low,
                facecolor='#1f6feb', alpha=0.15, edgecolor='#58a6ff',
                linewidth=1.0, linestyle='--', label=f'{zone_type} Zone'
            )
            ax.add_patch(ob_rect)
            ax.text(2, zone_high + (zone_high - zone_low) * 0.1,
                    f'📦 {zone_type}', color='#58a6ff', fontsize=8,
                    fontweight='bold', alpha=0.9)
        
        # ── Entry Line (White) ──
        ax.axhline(y=avg_entry, color='#e6edf3', linewidth=1.5, linestyle='-', alpha=0.9)
        ax.text(n - 1, avg_entry, f' Entry ${avg_entry:.2f}', color='#e6edf3',
                fontsize=8, fontweight='bold', va='bottom',
                bbox=dict(boxstyle='round,pad=0.2', facecolor='#0d1117', edgecolor='#e6edf3', alpha=0.8))
        
        # ── SL Line (Red) + Shaded Risk Zone ──
        ax.axhline(y=sl_price, color='#f85149', linewidth=1.5, linestyle='-', alpha=0.9)
        ax.text(n - 1, sl_price, f' SL ${sl_price:.2f}', color='#f85149',
                fontsize=8, fontweight='bold', va='top',
                bbox=dict(boxstyle='round,pad=0.2', facecolor='#0d1117', edgecolor='#f85149', alpha=0.8))
        # Red shaded zone (Entry → SL)
        sl_bottom = min(avg_entry, sl_price)
        sl_top = max(avg_entry, sl_price)
        ax.axhspan(sl_bottom, sl_top, facecolor='#f85149', alpha=0.08)
        
        # ── TP Line (Green) + Shaded Reward Zone ──
        if tp_price and tp_price > 0:
            ax.axhline(y=tp_price, color='#3fb950', linewidth=1.5, linestyle='-', alpha=0.9)
            ax.text(n - 1, tp_price, f' TP ${tp_price:.2f}', color='#3fb950',
                    fontsize=8, fontweight='bold',
                    va='bottom' if signal == 'BUY' else 'top',
                    bbox=dict(boxstyle='round,pad=0.2', facecolor='#0d1117', edgecolor='#3fb950', alpha=0.8))
            # Green shaded zone (Entry → TP)
            tp_bottom = min(avg_entry, tp_price)
            tp_top = max(avg_entry, tp_price)
            ax.axhspan(tp_bottom, tp_top, facecolor='#3fb950', alpha=0.08)
        
        # ── BOS / CHOCH Structure Annotations ──
        if smc_zone and smc_zone.get('structure'):
            struct_type = smc_zone['structure']
            struct_color = '#3fb950' if 'BULL' in struct_type else '#f85149'
            struct_label = struct_type.replace('_', ' ')
            # Place annotation near last 1/3 of chart
            ann_x = int(n * 0.65)
            ann_y = df['high'].iloc[ann_x] if ann_x < n else df['high'].max()
            ax.annotate(
                f'⚡ {struct_label}', xy=(ann_x, ann_y),
                xytext=(ann_x + 3, ann_y + (df['high'].max() - df['low'].min()) * 0.05),
                fontsize=9, fontweight='bold', color=struct_color,
                arrowprops=dict(arrowstyle='->', color=struct_color, lw=1.5),
                bbox=dict(boxstyle='round,pad=0.3', facecolor='#0d1117',
                          edgecolor=struct_color, alpha=0.9)
            )
        
        # ── Title & Info Overlay ──
        score = signal_data.get('score', 0) if signal_data else 0
        signal_emoji = '🟢 LONG' if signal == 'BUY' else '🔴 SHORT'
        title_text = f'{symbol} │ {signal_emoji} │ Score: {score}'
        ax.set_title(title_text, color='#e6edf3', fontsize=13, fontweight='bold', pad=12)
        
        # ── Legend ──
        legend_elements = [
            Line2D([0], [0], color='#e6edf3', linewidth=2, label='Entry'),
            Line2D([0], [0], color='#f85149', linewidth=2, label='Stop Loss'),
            Line2D([0], [0], color='#3fb950', linewidth=2, label='Take Profit'),
        ]
        if smc_zone:
            legend_elements.append(
                mpatches.Patch(facecolor='#1f6feb', alpha=0.3, label='OB Zone')
            )
        ax.legend(handles=legend_elements, loc='upper left', fontsize=7,
                  facecolor='#161b22', edgecolor='#30363d', labelcolor='#c9d1d9')
        
        # ── Watermark ──
        fig.text(0.5, 0.01, '🧠 SMC Image Engine v28 │ Ultra-Quant Bot',
                 ha='center', fontsize=8, color='#484f58', alpha=0.7)
        
        # ── X-axis formatting ──
        ax.set_xlabel('Candles (5m)', fontsize=9, color='#8b949e')
        ax.set_ylabel('Price ($)', fontsize=9, color='#8b949e')
        
        # ── Auto-scale Y with padding ──
        prices_all = [avg_entry, sl_price]
        if tp_price and tp_price > 0:
            prices_all.append(tp_price)
        price_range = max(df['high'].max(), max(prices_all)) - min(df['low'].min(), min(prices_all))
        y_pad = price_range * 0.08
        ax.set_ylim(
            min(df['low'].min(), min(prices_all)) - y_pad,
            max(df['high'].max(), max(prices_all)) + y_pad
        )
        
        plt.tight_layout()
        
        # ── Save to BytesIO (no disk writes) ──
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                    facecolor=fig.get_facecolor(), edgecolor='none')
        plt.close(fig)  # Free memory
        buf.seek(0)
        
        log.info(f"📸  [{symbol}] SMC Chart generated ({buf.getbuffer().nbytes / 1024:.0f} KB)")
        return buf
    
    except Exception as e:
        log.warning(f"⚠  [{symbol}] SMC Chart generation failed: {e}")
        try:
            plt.close('all')  # Clean up on error
        except:
            pass
        return None


def _send_chart_async(client, symbol, signal, avg_entry, sl_price, tp_price, signal_data, caption):
    """★ v28: Non-blocking chart generation & send (runs in background thread)."""
    try:
        chart_buf = generate_smc_chart(client, symbol, signal, avg_entry, sl_price, tp_price, signal_data)
        if chart_buf:
            full_caption = caption + "\n\n🧠 SMC logic visualized!"
            send_telegram_photo(chart_buf, full_caption)
            log.info(f"📸  [{symbol}] SMC Chart sent to Telegram ✅")
        else:
            # Fallback: send text-only alert
            send_telegram_alert(caption)
    except Exception as e:
        log.warning(f"⚠  [{symbol}] Chart send failed: {e}. Sending text alert.")
        send_telegram_alert(caption)


# ═════════════════════════════════════════════════════════════════════════════
# ██  UTILITIES                                                             ██
# ═════════════════════════════════════════════════════════════════════════════

# ★ AUDIT FIX: Global cache for exchange info (avoids redundant API calls per symbol)
_exchange_info_cache = None
_exchange_info_cache_time = 0

def _fetch_symbol_precision(client: Client, symbol: str):
    """★ v12: Auto-fetch price and qty precision from Binance for any symbol.
    ★ v35 FIX: Also extracts tickSize from PRICE_FILTER for proper price rounding."""
    global _exchange_info_cache, _exchange_info_cache_time
    try:
        # Cache exchange info for 1 hour to avoid API spam
        now = time.time()
        if _exchange_info_cache is None or (now - _exchange_info_cache_time) > 3600:
            _exchange_info_cache = client.futures_exchange_info()
            _exchange_info_cache_time = now
            log.info(f"📐  Exchange info cached ({len(_exchange_info_cache.get('symbols', []))} symbols)")
        
        for s in _exchange_info_cache["symbols"]:
            if s["symbol"] == symbol:
                price_prec = s.get("pricePrecision", DEFAULT_PRICE_PRECISION)
                qty_prec = s.get("quantityPrecision", DEFAULT_QTY_PRECISION)
                
                # ★ v35 FIX: Extract tickSize from PRICE_FILTER
                tick_size = None
                for f in s.get("filters", []):
                    if f["filterType"] == "PRICE_FILTER":
                        tick_size = float(f["tickSize"])
                        break
                
                SYMBOL_PRECISION[symbol] = {
                    "price": price_prec, 
                    "qty": qty_prec,
                    "tick_size": tick_size
                }
                log.info(f"📐  [{symbol}] Auto-precision: price={price_prec}, qty={qty_prec}, tickSize={tick_size}")
                return
    except Exception as e:
        log.warning(f"⚠  [{symbol}] Failed to fetch precision: {e}")


def _round_price(price: float, symbol: str = "BTCUSDT") -> float:
    """★ v35 FIX: Round price to valid tick size increment.
    Uses tickSize from PRICE_FILTER (not just pricePrecision) to ensure
    Binance accepts the order without 'Price not increased by tick size' error."""
    sym_info = SYMBOL_PRECISION.get(symbol, {})
    tick_size = sym_info.get("tick_size")
    
    if tick_size and tick_size > 0:
        # Round DOWN to nearest tick size increment
        rounded = round(round(price / tick_size) * tick_size, 10)
        # Also apply decimal precision to avoid floating point artifacts
        prec = sym_info.get("price", DEFAULT_PRICE_PRECISION)
        return round(rounded, prec)
    else:
        # Fallback: use pricePrecision only
        prec = sym_info.get("price", DEFAULT_PRICE_PRECISION)
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
    """Returns position info dict or None.
    ★ v22: In DRY RUN mode, uses simulated positions and checks SL/TP hits."""
    
    # ★ v22: DRY RUN — use simulated position tracker
    if DRY_RUN and symbol in dry_run_positions:
        sim = dry_run_positions[symbol]
        # Check if SL or TP hit by fetching current price
        try:
            ticker = client.futures_symbol_ticker(symbol=symbol)
            current_price = float(ticker["price"])
            
            # Check SL hit
            sl_hit = False
            tp_hit = False
            if sim["side"] == "BUY":
                sl_hit = current_price <= sim["sl_price"]
                tp_hit = current_price >= sim["tp_price"] if sim["tp_price"] > 0 else False
            else:  # SELL
                sl_hit = current_price >= sim["sl_price"]
                tp_hit = current_price <= sim["tp_price"] if sim["tp_price"] > 0 else False
            
            if sl_hit:
                # ★ v31.3 FIX: Do not hardcode * -1. Trailing SL hits can be in green!
                if sim["side"] == "BUY":
                    pnl = (sim["sl_price"] - sim["entry_price"]) * sim["qty"]
                else:
                    pnl = (sim["entry_price"] - sim["sl_price"]) * sim["qty"]
                
                log.info(f"🔻  [DRY RUN] [{symbol}] SL HIT @ ${current_price:.4f} (SL: ${sim['sl_price']:.4f}) | Sim PnL: ${pnl:.4f}")
                dry_run_last_pnl[symbol] = pnl
                del dry_run_positions[symbol]
                _save_dry_run_positions()
                return None  # Position closed
            elif tp_hit:
                if sim["side"] == "BUY":
                    pnl = (sim["tp_price"] - sim["entry_price"]) * sim["qty"]
                else:
                    pnl = (sim["entry_price"] - sim["tp_price"]) * sim["qty"]
                log.info(f"🎯  [DRY RUN] [{symbol}] TP HIT @ ${current_price:.4f} (TP: ${sim['tp_price']:.4f}) | Sim PnL: +${pnl:.4f}")
                dry_run_last_pnl[symbol] = pnl
                del dry_run_positions[symbol]
                _save_dry_run_positions()
                return None  # Position closed
            else:
                # Position still open
                unrealized = (current_price - sim["entry_price"]) * sim["qty"]
                if sim["side"] == "SELL":
                    unrealized = -unrealized
                return {
                    "side": sim["side"],
                    "qty": sim["qty"],
                    "entry_price": sim["entry_price"],
                    "mark_price": current_price,
                    "unrealized_pnl": unrealized,
                }
        except Exception:
            # Can't get price, assume position still open
            return {
                "side": sim["side"],
                "qty": sim["qty"],
                "entry_price": sim["entry_price"],
                "mark_price": sim["entry_price"],
                "unrealized_pnl": 0.0,
            }
    elif DRY_RUN:
        return None  # No simulated position for this symbol
    
    # LIVE mode — check Binance API
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
    log.info(f"  ★ BTC Corr: {btc_corr:+.4f} ({corr_status})  │  Risk: Dynamic 10% Margin  │  SL: {SL_ATR_MULT}×ATR")

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
# ██  ★ v35: DYNAMIC SESSION MANAGER                                       ██
# ═════════════════════════════════════════════════════════════════════════════

def get_current_session() -> str:
    """
    ★ v35: Detect current trading session based on UTC time.
    Returns: 'ASIA', 'LONDON', or 'NEW_YORK'
    
    Session Times (UTC):
      - ASIA:     00:00 – 08:00 UTC  (BD: 06:00 – 14:00)
      - LONDON:   08:00 – 13:00 UTC  (BD: 14:00 – 19:00)
      - NEW_YORK: 13:00 – 22:00 UTC  (BD: 19:00 – 04:00)
      - ASIA:     22:00 – 00:00 UTC  (BD: 04:00 – 06:00) — late night = Asia
    """
    utc_hour = datetime.now(timezone.utc).hour
    
    if 0 <= utc_hour < 8:
        return "ASIA"
    elif 8 <= utc_hour < 13:
        return "LONDON"
    elif 13 <= utc_hour < 22:
        return "NEW_YORK"
    else:  # 22-24
        return "ASIA"


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

        # ★ v21: Save 5m ATR for dynamic limit offset only
        atr_1m_for_offset = atr

        # ★ v36 FIX: Use 1H ATR for SL/TP — MUST match trailing manager's _get_current_atr()
        # The 5m ATR from signal engine is too small (~$0.01) causing tiny SL that gets
        # immediately replaced by Emergency SL (~7.8%). Using 1H ATR ensures consistency.
        try:
            atr_1h = _get_current_atr(client, symbol)
            if atr_1h > 0:
                log.info(f"   ★ ATR Sync: 5m={atr:.4f} → 1H={atr_1h:.4f} (using 1H for SL/TP)")
                atr = atr_1h
        except Exception as atr_err:
            log.warning(f"⚠ [{symbol}] Could not fetch 1H ATR, using signal ATR: {atr_err}")

        # ★ v22: HALF-KELLY CRITERION POSITION SIZING
        try:
            account = client.futures_account()
            total_balance = float(account.get("totalWalletBalance", 0.0))
        except Exception as bal_err:
            log.warning(f"⚠ [{symbol}] Could not fetch balance. Defaulting margin to $1.00. Error: {bal_err}")
            total_balance = 0.0
        
        # Calculate Kelly-optimal risk fraction
        kelly_wr, kelly_rr = _get_kelly_params()
        half_kelly_pct = calculate_kelly_risk_pct(kelly_wr, kelly_rr)
        kelly_trades = kelly_state["total_wins"] + kelly_state["total_losses"]
        kelly_source = "LIVE" if kelly_trades >= KELLY_MIN_TRADES_FOR_LIVE else "DEFAULT"
        
        if half_kelly_pct <= 0.0:
            log.warning(f"🚨  [{symbol}] NEGATIVE KELLY: WR={kelly_wr*100:.1f}% RR={kelly_rr:.2f} → No statistical edge. Using Minimum Risk Floor.")
            half_kelly_pct = 0.001  # Use a tiny fraction so floor_margin activates

        
        # Apply Kelly sizing with $1 floor (or $10 for realistic DRY RUN)
        calc_margin = total_balance * half_kelly_pct
        floor_margin = 10.00 if DRY_RUN else 2.50  # ★ v42: $2.50 margin cap per trade
        dynamic_margin = max(calc_margin, floor_margin)
        dynamic_margin = min(dynamic_margin, 2.50)  # ★ v42: HARD CAP — never exceed $2.50 margin per trade
            
        position_value_usd = dynamic_margin * LEVERAGE
        raw_qty = position_value_usd / current_price
        quantity = _round_qty(raw_qty * size_multiplier, symbol)  # ★ FIX: Apply correlation size_multiplier

        # SL calculation will happen below after limit_price is determined.
        risk_amount = dynamic_margin        # For logging only

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

        # ★ v24: 50% EQUILIBRIUM ORDER BLOCK LIMIT ENTRY
        # Instead of a tiny ATR offset, we target the OB midpoint for better fill & less drawdown
        smc_zone = signal_data.get("smc_zone") if signal_data else None
        ob_entry_used = False
        if smc_zone and smc_zone.get("zone_high") and smc_zone.get("zone_low"):
            zone_high = float(smc_zone["zone_high"])
            zone_low = float(smc_zone["zone_low"])
            equilibrium_price = (zone_high + zone_low) / 2.0
            if signal == "BUY":
                # Safety: If price already pulled back below equilibrium, use current price (better fill)
                limit_price = _round_price(min(equilibrium_price, current_price), symbol)
            else:
                # Safety: If price already rallied above equilibrium, use current price (better fill)
                limit_price = _round_price(max(equilibrium_price, current_price), symbol)
            ob_entry_used = True
            log.info(f"   🧱 OB Equilibrium: zone ${zone_low:.4f}–${zone_high:.4f} → 50% = ${equilibrium_price:.4f}")
        else:
            # Fallback: Dynamic ATR offset (original logic for non-OB setups)
            dynamic_offset = atr_1m_for_offset * 0.10
            offset = max(dynamic_offset, current_price * 0.00001)
            if signal == "BUY":
                limit_price = _round_price(current_price - offset, symbol)
            else:
                limit_price = _round_price(current_price + offset, symbol)

        # ════════════════════════════════════════════════════════════════
        #  ★ v25: STRUCTURAL OB SL (Zone-Based Stop Loss)
        # ════════════════════════════════════════════════════════════════
        if ob_entry_used:
            buffer = atr * 0.30  # ★ Use 1H ATR (not 5m) for proper buffer
            if signal == "BUY":
                sl_distance = limit_price - (zone_low - buffer)
            else:
                sl_distance = (zone_high + buffer) - limit_price
            
            # Constraints — enough room to survive noise
            sl_distance = max(sl_distance, current_price * 0.003) # Min 0.3% (survive noise)
            sl_distance = min(sl_distance, current_price * 0.015) # Max 1.5%
            tp_distance = sl_distance * 3.0  # ★ 1:3 R:R (bigger wins)
        else:
            # ★ Strict SMC Fallback: Tighter ATR SL when no OB zone available
            sl_distance = atr * 1.5   # 1.5 × ATR (tighter than old 3.0x)
            sl_distance = max(sl_distance, current_price * 0.002)  # Min 0.2%
            sl_distance = min(sl_distance, current_price * 0.04)   # Max 4.0%
            tp_distance = sl_distance * 3.0  # ★ 1:3 R:R (bigger wins)
            
            # ★ Minimum SL floor = 0.15% of price (prevents instant SL from noise)
            min_sl = current_price * 0.0015
            if sl_distance < min_sl:
                log.warning(f"   ⚠ SL Floor: ${sl_distance:.4f} < 0.15% min ${min_sl:.4f} — Raising to floor")
                sl_distance = min_sl
                tp_distance = sl_distance * 2.0
                
        if is_micro_scalp:
            tp_distance = current_price * 0.0025
            sl_distance = current_price * 0.0050
            log.warning(f"⚡  [{symbol}] MICRO-SCALP Override Active! TP: 0.25% │ SL: 0.50%")

        if sl_distance <= 0:
            log.warning("⚠  SL Distance is <= 0. Cannot compute. Skipping.")
            return False, 0.0

        # ★ PROP FIRM POSITION SIZING UPDATE ★
        if VIRTUAL_ACCOUNT:
            risk_usd = prop_state["current_balance"] * (RISK_PER_TRADE_PERCENT / 100.0)
            
            # ★ v34 FIX: Strict Dollar Risk Cap (The Hard Lock)
            MAX_RISK_PER_TRADE_USD = 20.0
            if risk_usd > MAX_RISK_PER_TRADE_USD:
                log.info(f"🛡  [{symbol}] Capping risk_usd from ${risk_usd:.2f} to strict maximum ${MAX_RISK_PER_TRADE_USD:.2f}")
                risk_usd = MAX_RISK_PER_TRADE_USD
            raw_qty_prop = risk_usd / sl_distance
            max_qty_allowed = (prop_state["current_balance"] * LEVERAGE_SIM) / current_price
            if raw_qty_prop > max_qty_allowed:
                log.warning(f"⚠  [{symbol}] Prop SL Sizing exceeds max {LEVERAGE_SIM}x leverage limit! Capping size.")
                raw_qty_prop = max_qty_allowed
            
            quantity = _round_qty(raw_qty_prop * size_multiplier, symbol)
            dynamic_margin = (quantity * current_price) / LEVERAGE_SIM
            risk_amount = risk_usd
            
        if quantity <= 0:
            log.warning("⚠  Calculated quantity is 0 after rounding.")
            return False, 0.0

        entry_method = "50% OB Equilibrium" if ob_entry_used else "ATR Offset"
        log.info("═" * 70)
        mode_str = "PROP CHALLENGE" if VIRTUAL_ACCOUNT else "REAL ACCOUNT"
        log.info(f"🚀  [{symbol}] EXECUTING {signal} ({mode_str}) │ Score: {score}")
        # ★ v34-fix3: Track entry time for spam guard (safe — bot_state may not be in scope)
        try:
            bot_state["last_entry_attempt_time"] = time.time()
        except NameError:
            pass  # bot_state not available in execute_trade scope, skip spam guard tracking
        log.info(f"   ★ LIMIT Entry : ${limit_price} ({entry_method})")
        log.info(f"   Market Price  : ${current_price}")
        log.info(f"   Quantity      : {quantity}")
        if VIRTUAL_ACCOUNT:
            log.info(f"   ★ Risk Amt    : ${risk_amount:.2f} ({RISK_PER_TRADE_PERCENT}% Fixed SL Risk){size_note}")
        else:
            log.info(f"   ★ Risk Margin : ${risk_amount:.2f} ({ENTRY_RISK_PCT}%){size_note}")
        log.info(f"   ★ ML Win Prob : {ml_prob*100:.1f}%")
        log.info(f"   ★ BTC Corr    : {btc_corr:+.4f}")
        log.info(f"   ★ SL Distance : ${sl_distance:.2f} ({SL_ATR_MULT}×ATR)")
        log.info(f"   ★ TP Distance : ${tp_distance:.2f} ({TP_ATR_MULT}×ATR) — R:R = 1:2")
        log.info(f"   Confluence    : {' | '.join(breakdown)}")
        log.info("═" * 70)

        # ★ v24: DRY-RUN or GTC LIMIT ENTRY (50% OB Equilibrium)
        if DRY_RUN:
            # ★ DRY RUN: Simulate entry — no API call
            notional = dynamic_margin * LEVERAGE
            est_fee = notional * 0.0002  # Maker fee estimate (0.02%)
            log.info(f"🟢  [DRY RUN] ENTRY {signal} at ${limit_price} │ Qty: {quantity} │ Notional: ${notional:.2f} │ Est. Fee: ${est_fee:.4f}")
            e_id = f"DRY-{symbol}-{int(time.time())}"
            avg_entry = limit_price  # Use limit price as simulated fill
            actual_qty = quantity
        else:
            # ★ v25.2: Smart OB Zone Entry — LOG only, don't block (was blocking all real trades!)
            # ★ v34-fix: Changed from hard block → soft warning (dry run didn't have this gate)
            if smc_zone and smc_zone.get("zone_high") and smc_zone.get("zone_low"):
                zone_high = float(smc_zone["zone_high"])
                zone_low = float(smc_zone["zone_low"])
                zone_buffer = atr * 3.0  # ★ v34-fix: Widened 1.0x → 3.0x ATR
                if signal == "BUY" and current_price > zone_high + zone_buffer:
                    log.info(f"⚠️  [{symbol}] OB NOTE — BUY ${current_price:.4f} above OB ${zone_low:.4f}-${zone_high:.4f} (buffer: ${zone_buffer:.4f}) — proceeding anyway")
                elif signal == "SELL" and current_price < zone_low - zone_buffer:
                    log.info(f"⚠️  [{symbol}] OB NOTE — SELL ${current_price:.4f} below OB ${zone_low:.4f}-${zone_high:.4f} (buffer: ${zone_buffer:.4f}) — proceeding anyway")
                else:
                    log.info(f"✅  [{symbol}] OB ZONE OK — ${current_price:.4f} near OB ${zone_low:.4f}-${zone_high:.4f}")
            try:
                entry_order = client.futures_create_order(
                    symbol=symbol, side=side,
                    type='MARKET',
                    # price=str(limit_price),  # v25: Not needed for MARKET
                    quantity=quantity,
                    # timeInForce='GTC',  # v25: Not needed for MARKET
                )
                e_id = entry_order.get('orderId', entry_order.get('order_id', 'UNKNOWN'))
                log.info(f"🚀  [{symbol}] MARKET ORDER FILLED — Qty: {quantity} │ OrderID: {e_id}")

                # ★ v24: Quick fill check (5 seconds) — if already at price, fills instantly
                filled = True  # v25: MARKET orders always fill instantly
                time.sleep(5)
                try:
                    order_status = client.futures_get_order(symbol=symbol, orderId=e_id)
                    status = order_status.get('status', '')
                    if status == 'FILLED':
                        log.info(f"✅  [{symbol}] GTC FILLED instantly! (Maker Fee) — OrderID: {e_id}")
                        filled = True
                    elif status in ('CANCELED', 'EXPIRED', 'REJECTED'):
                        log.warning(f"🚫  [{symbol}] GTC {status} by exchange. Trade SKIPPED.")
                        return False, 0.0
                except Exception:
                    pass

                if not filled:
                    # ★ v24: Order is PENDING — return entry info for auto-cancel manager
                    # Store pending order data so main loop can monitor & cancel
                    pending_info = {
                        "order_id": e_id,
                        "side": signal,
                        "limit_price": limit_price,
                        "quantity": quantity,
                        "placed_time": time.time(),
                        "zone": {"zone_high": smc_zone["zone_high"], "zone_low": smc_zone["zone_low"]} if smc_zone else None,
                        "signal_data": signal_data,
                        "sl_distance": sl_distance,
                        "tp_distance": tp_distance,
                    }
                    log.info(f"⏳  [{symbol}] GTC PENDING — Waiting for pullback to ${limit_price}. Auto-cancel in 12 candles (1h).")
                    send_telegram_alert(
                        f"\u23f3 <b>PENDING LIMIT ORDER</b>\n"
                        f"Coin: {symbol}\n"
                        f"Side: {signal}\n"
                        f"Target Entry: ${limit_price:.4f}\n"
                        f"Status: Waiting for price pullback."
                    )
                    return "PENDING", pending_info  # ★ New return type for pending orders

            except BinanceAPIException as gtx_err:
                if gtx_err.code == -5022 or 'post only' in str(gtx_err.message).lower() or 'would immediately' in str(gtx_err.message).lower():
                    log.warning(f"🚫  [{symbol}] LIMIT REJECTED — {gtx_err.message}. Trade SKIPPED.")
                else:
                    log.error(f"❌  [{symbol}] Limit order error: {gtx_err.message}")
                return False, 0.0

        # Fetch actual entry price from position (skip in DRY RUN — already set above)
        if not DRY_RUN:
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
        # ★ v28: SMC Image Engine — Generate chart + send as Telegram photo (non-blocking)
        try:
            chart_thread = threading.Thread(
                target=_send_chart_async,
                args=(client, symbol, signal, avg_entry, sl_price, tp_price, signal_data, alert_msg),
                daemon=True
            )
            chart_thread.start()
            log.info(f"📸  [{symbol}] SMC Chart generation started (background thread)")
        except Exception as tel_err:
            log.warning(f"⚠  [{symbol}] Chart/Telegram failed: {tel_err}")
            send_telegram_alert(alert_msg)  # Fallback text-only

        # Place STOP-LOSS order (use quantity+reduceOnly to avoid closePosition conflict)
        if DRY_RUN:
            log.info(f"🟢  [DRY RUN] SL ORDER — ${sl_price} ({SL_ATR_MULT}×ATR = ${sl_distance:.2f}) │ Side: {close_side} │ Qty: {actual_qty}")
        else:
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
            if DRY_RUN:
                log.info(f"🟢  [DRY RUN] TP ORDER — ${tp_price} ({TP_ATR_MULT}×ATR = ${tp_distance:.2f}) │ R:R 1:2")
            else:
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

        # ★ v22: Register DRY RUN position for simulated tracking
        if DRY_RUN:
            dry_run_positions[symbol] = {
                "side": signal,
                "qty": actual_qty,
                "entry_price": avg_entry,
                "sl_price": sl_price,
                "tp_price": tp_price if not DISABLE_HARD_TP else 0.0,
            }
            _save_dry_run_positions()
            log.info(f"📍  [DRY RUN] [{symbol}] Position registered: {signal} @ ${avg_entry} | SL: ${sl_price} | TP: ${tp_price}")

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
        
        if DRY_RUN:
            remaining = _round_qty(current_qty - close_qty, symbol)
            log.info(f"🟢  [DRY RUN] {reason} PARTIAL CLOSE — {close_pct}% ({close_qty}) │ Remaining: {remaining}")
            return True
        
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
    
    # ★ v20.1: DRY RUN — simulate SL update
    if DRY_RUN:
        log.info(f"🟢  [DRY RUN] SL UPDATE — ${new_sl} │ Side: {close_side} │ Qty: {qty}")
        if symbol in dry_run_positions:
            dry_run_positions[symbol]["sl_price"] = new_sl
            _save_dry_run_positions()
        return True
    
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
        if DRY_RUN:
            log.info(f"🟢  [DRY RUN] FORCE MARKET CLOSE — {symbol} │ Side: {close_side}")
            if symbol in dry_run_positions:
                try:
                    # Calculate final PnL at close
                    sim = dry_run_positions[symbol]
                    cp = float(client.futures_symbol_ticker(symbol=symbol)["price"])
                    pnl = (cp - sim["entry_price"]) * sim["qty"] if sim["side"] == "BUY" else (sim["entry_price"] - cp) * sim["qty"]
                    dry_run_last_pnl[symbol] = pnl
                    del dry_run_positions[symbol]
                    _save_dry_run_positions()
                except Exception as e:
                    log.warning(f"Failed to calculate dry run PnL on force close: {e}")
            return True
        
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
    bot_state["close_reason_override"] = None


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
                
                # ★ AUDIT FIX: Set _trade_logged=True HERE so main loop does NOT
                # call _determine_trade_outcome a second time (prevents double PnL + double Telegram)
                bot_state["_trade_logged_by_manager"] = True
                
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
                        elif phase == "MANUAL_CLOSE":
                            close_reason = "🛡 Local SL or Stale Trigger (Profit Secured V2)"
                        else:
                            close_reason = "🎯 Exchange TP Hit"
                    else:
                        if phase in ("TRAILING", "BREAKEVEN"):
                            close_reason = f"🛡 Trailing SL Hit @ ${trail_sl:.4f}"
                        elif phase == "MANUAL_CLOSE":
                            close_reason = "🔻 Local SL or Stale Timeout Trigger (Loss)"
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

        # (time already imported at module level)
        now = time.time()

        # ══════════════════════════════════════════════════════════════
        #  ★ v15: TIME-BASED EXIT (Stale Trade Protocol)
        #  Close if trade is <= 0 PnL after 45 mins AND in INITIAL phase.
        #  Profitable trades (Phase: BREAKEVEN/TRAILING) are NEVER closed here.
        # ══════════════════════════════════════════════════════════════
        trade_open_time = bot_state.get("trade_open_time", 0)
        if trade_open_time > 0:
            trade_duration_s = now - trade_open_time
            if trade_duration_s >= 1800 and pnl_pct <= STALE_TRADE_MIN_LOSS_PCT and bot_state.get("phase", "INITIAL") == "INITIAL":  # ★ v26: 30 mins (was 45 — dead trades sit too long)
                log.warning(f"⏳  [{symbol}] STALE TRADE: Open for {int(trade_duration_s/60)} mins with PnL {pnl_pct:.2f}% (genuine loss < {STALE_TRADE_MIN_LOSS_PCT}%). Closing early.")
                if _force_market_close(client, symbol, side):
                    bot_state["phase"] = "MANUAL_CLOSE"  # Let next cycle handle cleanup naturally
                    bot_state["close_reason_override"] = f"⏳ Stale Trade: No momentum after {int(trade_duration_s/60)} mins"
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
        # (time already imported at module level)
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
                                bot_state["phase"] = "MANUAL_CLOSE"
                                bot_state["close_reason_override"] = "🎯 Dynamic TP! Resistance Wall ahead!"
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
                                bot_state["phase"] = "MANUAL_CLOSE"
                                bot_state["close_reason_override"] = "🎯 Dynamic TP! Support Wall ahead!"
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
                
                if is_closed:
                    bot_state["phase"] = "MANUAL_CLOSE"  # So next cycle can classify it
                    bot_state["close_reason_override"] = "🚨 LOCAL SL TRIGGERED"
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
        # Calculate dynamic initial risk % (based on ACTUAL SL if available, else ATR)
        initial_risk_pct = bot_state.get("initial_risk_pct", 0.0)
        
        # ★ v41 FIX: Fetch actual SL to determine True 1R instead of relying on generic 3.0x ATR multiplier
        if initial_risk_pct == 0.0 and current_atr > 0 and entry_price > 0:
            actual_sl_dist = 0.0
            if bot_state.get("current_trail_sl", 0.0) > 0:
                actual_sl_dist = abs(entry_price - bot_state["current_trail_sl"])
            else:
                try:
                    if DRY_RUN and symbol in dry_run_positions:
                        actual_sl_dist = abs(entry_price - dry_run_positions[symbol]["sl_price"])
                    else:
                        _orders = client.futures_get_open_orders(symbol=symbol)
                        for _ord in _orders:
                            _otype = _ord.get("type", "") or _ord.get("origType", "")
                            if "STOP" in _otype.upper() and "TAKE_PROFIT" not in _otype.upper():
                                actual_sl_dist = abs(entry_price - float(_ord["stopPrice"]))
                                bot_state["current_trail_sl"] = float(_ord["stopPrice"]) # Cache it
                                break
                except Exception:
                    pass

            if actual_sl_dist > 0:
                initial_risk_pct = (actual_sl_dist / entry_price) * 100.0
                bot_state["initial_risk_pct"] = initial_risk_pct
                log.info(f"📐  [{symbol}] True Risk Baseline (1R): {initial_risk_pct:.3f}% (Actual SL Dist: ${actual_sl_dist:.4f})")
            else:
                initial_risk_pct = (current_atr * SL_ATR_MULT / entry_price) * 100.0
                bot_state["initial_risk_pct"] = initial_risk_pct
                log.info(f"📐  [{symbol}] Dynamic Risk Baseline: {initial_risk_pct:.3f}% (ATR: ${current_atr:.2f} × {SL_ATR_MULT})")

        # ★ v42 FIX: BE triggers exactly when profit hits $0.40 USDT (calculated dynamically as a %)
        position_notional = entry_price * current_qty
        if position_notional > 0:
            target_usdt_profit = 0.40
            dynamic_be_trigger = (target_usdt_profit / position_notional) * 100.0
            # Safety floor: never trigger before 0.15% to avoid fee burn
            dynamic_be_trigger = max(dynamic_be_trigger, 0.15)
        else:
            dynamic_be_trigger = 0.50  # Fallback

        if bot_state["phase"] == "INITIAL" and pnl_pct >= dynamic_be_trigger:
            # ★ v17: TRUE BREAK-EVEN — hardcoded 0.15% fee buffer (covers entry + exit taker fees)
            if side == "BUY":
                be_sl = _round_price(entry_price * (1.0 + TRUE_BE_FEE_BUFFER_PCT / 100.0), symbol)
            else:
                be_sl = _round_price(entry_price * (1.0 - TRUE_BE_FEE_BUFFER_PCT / 100.0), symbol)
            
            # ★ AUDIT FIX: Cancel orders for THIS symbol only (was global nuclear cancel
            # that stripped SL/TP from ALL other positions — dangerous on mainnet)
            try:
                client.futures_cancel_all_open_orders(symbol=symbol)
                time.sleep(1.0)
                log.info(f"🧹  [{symbol}] Cleared existing orders for BE SL placement")
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
        trailing_activation_pct = min(max(initial_risk_pct * TRAILING_ACTIVATION_RR, 0.45), 1.20)  # Floor 0.45%, CAP 1.20%
        if bot_state["phase"] in ("BREAKEVEN", "TRAILING") and pnl_pct >= trailing_activation_pct:
            if bot_state["phase"] == "BREAKEVEN":
                log.info(f"🚀  [{symbol}] ★ TRAILING UNLOCKED │ PnL {pnl_pct:.2f}% ≥ {TRAILING_ACTIVATION_RR}R ({trailing_activation_pct:.2f}%) │ Now trailing!")
            bot_state["phase"] = "TRAILING"
            
            # ★ v12: SMART REVERSAL CHECK — Exit if momentum has definitively shifted
            if SMART_REVERSAL_EXIT and pnl_pct >= 0.50:  # Only check after decent profit
                reversal_exit, reversal_reason = _check_smart_reversal(client, symbol, side)
                if reversal_exit:
                    log.warning(f"🔄  [{symbol}] {reversal_reason}")
                    if _force_market_close(client, symbol, side):
                        bot_state["phase"] = "MANUAL_CLOSE"
                        bot_state["close_reason_override"] = f"🔄 SMART REVERSAL: {reversal_reason}"
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
                    
                    # ★ v31.2: Calculate locked profit if SL gets hit
                    if side == "BUY":
                        locked_pnl_pct = ((new_trail_sl - entry_price) / entry_price) * 100.0
                    else:
                        locked_pnl_pct = ((entry_price - new_trail_sl) / entry_price) * 100.0
                    locked_pnl_usd = locked_pnl_pct / 100.0 * entry_price * current_qty * LEVERAGE / entry_price
                    # Simpler: locked_pnl_usd ≈ (pnl_pct_at_sl / 100) * margin * leverage
                    
                    log.info(
                        f"📈  [{symbol}] ★ TRAILING SL │ SL → ${new_trail_sl} │ "
                        f"Best: ${bot_state['best_price']} │ Trail: {TRAILING_SL_DISTANCE_PCT}% (${trail_distance:.2f}) │ {pnl_str}"
                    )
                    
                    # ★ v31.2: Telegram SL Movement Alert
                    send_telegram_alert(
                        f"📈 <b>Trailing SL Moved</b>\n"
                        f"Coin: {symbol}\n"
                        f"Side: {side}\n"
                        f"Entry: ${entry_price:.4f}\n"
                        f"SL → ${new_trail_sl:.4f}\n"
                        f"Best Price: ${bot_state['best_price']:.4f}\n"
                        f"Current PnL: {pnl_str}\n"
                        f"🔒 If SL hits: ~{locked_pnl_pct:+.2f}% locked"
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
                    if DRY_RUN and symbol in dry_run_positions:
                        bot_state["current_trail_sl"] = dry_run_positions[symbol]["sl_price"]
                    else:
                        _orders = client.futures_get_open_orders(symbol=symbol)
                        for _ord in _orders:
                            _otype = _ord.get("type", "") or _ord.get("origType", "")
                            if "STOP" in _otype.upper() and "TAKE_PROFIT" not in _otype.upper():
                                bot_state["current_trail_sl"] = float(_ord["stopPrice"])
                                break
                    
                    if bot_state["current_trail_sl"] == 0.0:
                        # Naked position — place emergency SL (tight: max 2% from entry)
                        sl_distance = current_atr * 1.5  # Use tighter 1.5x ATR
                        max_sl_distance = entry_price * 0.01  # ★ Hard cap: 1% max (tight SL)
                        sl_distance = min(sl_distance, max_sl_distance)
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

            # ★ SL STEP-UP: Tighten SL as price moves in our favor (before break-even)
            # When profit reaches 0.15%, move SL halfway closer to entry
            current_sl = bot_state.get("current_trail_sl", 0.0)
            if current_sl > 0 and pnl_pct >= 0.15 and pnl_pct < dynamic_be_trigger:
                if side == "BUY":
                    sl_gap = entry_price - current_sl  # How far SL is below entry
                    if sl_gap > entry_price * 0.003:  # Only if gap > 0.3%
                        new_sl = _round_price(entry_price - (sl_gap * 0.5), symbol)  # Halve the gap
                        if new_sl > current_sl:
                            success = _update_sl_order(client, symbol, side, new_sl)
                            if success:
                                bot_state["current_trail_sl"] = new_sl
                                log.info(f"📐  [{symbol}] SL STEP-UP: Profit {pnl_pct:.2f}% → SL tightened ${current_sl} → ${new_sl} (gap halved)")
                elif side == "SELL":
                    sl_gap = current_sl - entry_price  # How far SL is above entry
                    if sl_gap > entry_price * 0.003:  # Only if gap > 0.3%
                        new_sl = _round_price(entry_price + (sl_gap * 0.5), symbol)  # Halve the gap
                        if new_sl < current_sl:
                            success = _update_sl_order(client, symbol, side, new_sl)
                            if success:
                                bot_state["current_trail_sl"] = new_sl
                                log.info(f"📐  [{symbol}] SL STEP-UP: Profit {pnl_pct:.2f}% → SL tightened ${current_sl} → ${new_sl} (gap halved)")

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
        
        closed_klines = raw[:-1]
        prev_4_vols = [float(k[5]) for k in closed_klines[-5:-1]]
        avg_vol = sum(prev_4_vols) / len(prev_4_vols) if prev_4_vols else 1.0
        
        # If average volume is essentially zero, skip the check
        if avg_vol < 1e-8:
            return True
        
        last_closed_kline = closed_klines[-1]
        last_closed_vol = float(last_closed_kline[5])
        last_close_price = float(last_closed_kline[4])
        last_open_price = float(last_closed_kline[1])

        current_kline = raw[-1]
        current_vol = float(current_kline[5])
        curr_close_price = float(current_kline[4])
        curr_open_price = float(current_kline[1])
        
        # ★ v22: A burst is valid if either the LAST COMPLETED candle spiked, 
        # OR the CURRENT INCOMPLETE candle already spiked
        vol_ok = last_closed_vol >= (avg_vol * 0.7) or current_vol >= (avg_vol * 0.7)  # ★ v37 Tuning: 0.7x (was 0.9x)
        
        if vol_ok:
            # Soft direction check: allow flat candles too
            if direction == "BUY" and (last_close_price >= last_open_price or curr_close_price >= curr_open_price):
                return True
            elif direction == "SELL" and (last_close_price <= last_open_price or curr_close_price <= curr_open_price):
                return True
            # If direction doesn't match but volume is 1.5x+, still allow (strong burst)
            if last_closed_vol >= (avg_vol * 1.5) or current_vol >= (avg_vol * 1.5):
                return True
                
        return False
    except Exception as e:
        log.warning(f"⚠  [{symbol}] Volume burst check failed: {e}")
        return True  # ★ v14: On error, allow trade


# ═════════════════════════════════════════════════════════════════════════════
# ██  ★ v25: MULTI-TIMEFRAME DIRECTIONAL CONFLUENCE (MTDC)                 ██
# ██  4H = HARD VETO │ 1H + 15m + OI + FR = Confidence Score               ██
# ═════════════════════════════════════════════════════════════════════════════

MTFA_ENABLED = False          # ★ v29.8: MTDC confidence gate disabled (SMC naturally handles this)
ANTI_FOMO_ENABLED = False     # ★ v29.8: Disabled (SMC naturally handles this)
MTDC_MIN_CONFIDENCE = 0.50    # ★ v26: Lowered from 70% → 50% (was blocking ALL trades, 4H Hard Veto still protects)
MTDC_4H_HARD_VETO = False     # ★ v29.1: Disabled per user request to allow 5m counter-trend scalps

# ── Weights for confidence sources (excluding 4H which is Hard Veto) ──
MTDC_WEIGHT_1H     = 0.35     # 1H EMA trend alignment (35%)
MTDC_WEIGHT_15M    = 0.30     # 15m structure break (30%)
MTDC_WEIGHT_OI     = 0.20     # OI + Price divergence (20%)
MTDC_WEIGHT_FR     = 0.15     # Funding rate sentiment (15%)


def _numpy_ema(data, period):
    """Pure NumPy EMA calculation."""
    import numpy as np
    alpha = 2.0 / (period + 1)
    ema = np.zeros_like(data, dtype=float)
    ema[0] = data[0]
    for i in range(1, len(data)):
        ema[i] = alpha * data[i] + (1.0 - alpha) * ema[i - 1]
    return ema


def _calculate_directional_confidence(client: Client, symbol: str, direction: str, signal_data: dict = None) -> tuple:
    """
    ★ v25: Multi-Timeframe Directional Confluence (MTDC) System.
    
    Aggregates 5 independent directional signals to determine if the market
    is truly heading in the proposed trade direction with high confidence.
    
    4H EMA Trend = HARD VETO (instant kill if opposing).
    1H + 15m + OI + Funding Rate = Weighted confidence score (0-100%).
    
    Returns:
        (confidence: float, breakdown: list, veto: bool, veto_reason: str)
        confidence → 0.0 to 1.0
        breakdown  → Human-readable list of each source
        veto       → True if 4H Hard Veto triggered
        veto_reason → Explanation if vetoed
    """
    import numpy as np
    
    confidence = 0.0
    breakdown = []
    
    # ════════════════════════════════════════════════════════════════
    #  SOURCE 1: 4H EMA TREND — HARD VETO (Pass/Kill)
    #  If 4H trend opposes the signal → trade is DEAD. No exceptions.
    # ════════════════════════════════════════════════════════════════
    try:
        h4 = client.futures_klines(symbol=symbol, interval="4h", limit=60)
        if h4 and len(h4) >= 50:
            h4_closes = np.array([float(k[4]) for k in h4])
            h4_ema50 = _numpy_ema(h4_closes, 50)
            h4_ema21 = _numpy_ema(h4_closes, 21)
            
            h4_price = h4_closes[-1]
            h4_ema50_val = h4_ema50[-1]
            h4_ema21_val = h4_ema21[-1]
            
            # 4H is BULLISH if: Price > EMA21 > EMA50 (perfect uptrend stack)
            # 4H is BEARISH if: Price < EMA21 < EMA50 (perfect downtrend stack)
            # Mixed = NEUTRAL (still allows trades but no bonus)
            h4_bull = h4_price > h4_ema21_val and h4_ema21_val > h4_ema50_val
            h4_bear = h4_price < h4_ema21_val and h4_ema21_val < h4_ema50_val
            
            dist_pct = ((h4_price - h4_ema50_val) / h4_ema50_val) * 100.0
            
            if direction == "BUY":
                if h4_bear and MTDC_4H_HARD_VETO:
                    veto_reason = (
                        f"🚫 4H HARD VETO: BUY signal killed! 4H is BEARISH "
                        f"(Price ${h4_price:.2f} < EMA21 ${h4_ema21_val:.2f} < EMA50 ${h4_ema50_val:.2f}, {dist_pct:+.2f}%)"
                    )
                    return 0.0, [veto_reason], True, veto_reason
                elif h4_bull:
                    breakdown.append(f"✅ 4H: BULLISH (Price > EMA21 > EMA50, {dist_pct:+.2f}%)")
                else:
                    breakdown.append(f"⚠️ 4H: NEUTRAL (mixed EMAs, {dist_pct:+.2f}%)")
            else:  # SELL
                if h4_bull and MTDC_4H_HARD_VETO:
                    veto_reason = (
                        f"🚫 4H HARD VETO: SELL signal killed! 4H is BULLISH "
                        f"(Price ${h4_price:.2f} > EMA21 ${h4_ema21_val:.2f} > EMA50 ${h4_ema50_val:.2f}, {dist_pct:+.2f}%)"
                    )
                    return 0.0, [veto_reason], True, veto_reason
                elif h4_bear:
                    breakdown.append(f"✅ 4H: BEARISH (Price < EMA21 < EMA50, {dist_pct:+.2f}%)")
                else:
                    breakdown.append(f"⚠️ 4H: NEUTRAL (mixed EMAs, {dist_pct:+.2f}%)")
        else:
            breakdown.append("⚠️ 4H: SKIP (insufficient data)")
    except Exception as e:
        log.warning(f"⚠  [{symbol}] MTDC 4H check failed: {e}")
        breakdown.append(f"⚠️ 4H: ERROR ({e})")
    
    # ════════════════════════════════════════════════════════════════
    #  SOURCE 2: 1H EMA TREND (35% weight)
    # ════════════════════════════════════════════════════════════════
    try:
        h1 = client.futures_klines(symbol=symbol, interval="1h", limit=60)
        if h1 and len(h1) >= 50:
            h1_closes = np.array([float(k[4]) for k in h1])
            h1_ema21 = _numpy_ema(h1_closes, 21)
            h1_ema50 = _numpy_ema(h1_closes, 50)
            
            h1_price = h1_closes[-1]
            h1_bull = h1_price > h1_ema21[-1] and h1_ema21[-1] > h1_ema50[-1]
            h1_bear = h1_price < h1_ema21[-1] and h1_ema21[-1] < h1_ema50[-1]
            
            if (direction == "BUY" and h1_bull) or (direction == "SELL" and h1_bear):
                confidence += MTDC_WEIGHT_1H
                breakdown.append(f"✅ 1H: ALIGNED (+{MTDC_WEIGHT_1H*100:.0f}%)")
            elif (direction == "BUY" and h1_bear) or (direction == "SELL" and h1_bull):
                breakdown.append(f"❌ 1H: OPPOSING (0%)")
            else:
                confidence += MTDC_WEIGHT_1H * 0.5  # Half credit for neutral
                breakdown.append(f"⚠️ 1H: NEUTRAL (+{MTDC_WEIGHT_1H*50:.0f}%)")
        else:
            confidence += MTDC_WEIGHT_1H * 0.5
            breakdown.append("⚠️ 1H: SKIP (insufficient data, +half)")
    except Exception as e:
        log.warning(f"⚠  [{symbol}] MTDC 1H check failed: {e}")
        confidence += MTDC_WEIGHT_1H * 0.5
        breakdown.append(f"⚠️ 1H: ERROR (+half)")
    
    # ════════════════════════════════════════════════════════════════
    #  SOURCE 3: 15m MARKET STRUCTURE — BOS/CHOCH (30% weight)
    # ════════════════════════════════════════════════════════════════
    try:
        m15 = client.futures_klines(symbol=symbol, interval="15m", limit=100)
        if m15 and len(m15) >= 30:
            m15_high = np.array([float(k[2]) for k in m15])
            m15_low = np.array([float(k[3]) for k in m15])
            m15_close = np.array([float(k[4]) for k in m15])
            
            # Simple structure: Higher Highs + Higher Lows = BULL
            #                   Lower Highs + Lower Lows = BEAR
            recent_highs = m15_high[-10:]
            recent_lows = m15_low[-10:]
            
            hh = recent_highs[-1] > recent_highs[-5] and recent_highs[-5] > recent_highs[-9]  # Higher Highs
            hl = recent_lows[-1] > recent_lows[-5] and recent_lows[-5] > recent_lows[-9]     # Higher Lows
            lh = recent_highs[-1] < recent_highs[-5] and recent_highs[-5] < recent_highs[-9]  # Lower Highs
            ll = recent_lows[-1] < recent_lows[-5] and recent_lows[-5] < recent_lows[-9]     # Lower Lows
            
            m15_bull = hh and hl
            m15_bear = lh and ll
            
            if (direction == "BUY" and m15_bull) or (direction == "SELL" and m15_bear):
                confidence += MTDC_WEIGHT_15M
                breakdown.append(f"✅ 15m: STRUCTURE ALIGNED (+{MTDC_WEIGHT_15M*100:.0f}%)")
            elif (direction == "BUY" and m15_bear) or (direction == "SELL" and m15_bull):
                breakdown.append(f"❌ 15m: STRUCTURE OPPOSING (0%)")
            else:
                confidence += MTDC_WEIGHT_15M * 0.5
                breakdown.append(f"⚠️ 15m: RANGING (+{MTDC_WEIGHT_15M*50:.0f}%)")
        else:
            confidence += MTDC_WEIGHT_15M * 0.5
            breakdown.append("⚠️ 15m: SKIP (insufficient data, +half)")
    except Exception as e:
        log.warning(f"⚠  [{symbol}] MTDC 15m check failed: {e}")
        confidence += MTDC_WEIGHT_15M * 0.5
        breakdown.append(f"⚠️ 15m: ERROR (+half)")
    
    # ════════════════════════════════════════════════════════════════
    #  SOURCE 4: OI + PRICE DIVERGENCE (20% weight)
    #  OI Rising + Price Rising = Strong Bullish (accumulation)
    #  OI Rising + Price Falling = Short Squeeze incoming (bullish)
    #  OI Falling + Price Rising = Distribution (bearish warning)
    #  OI Falling + Price Falling = Capitulation (bearish)
    # ════════════════════════════════════════════════════════════════
    try:
        oi_trend = signal_data.get("oi_trend", "FLAT") if signal_data else "FLAT"
        price_trend = signal_data.get("price_trend", "FLAT") if signal_data else "FLAT"
        
        oi_bullish = (oi_trend == "RISING" and price_trend == "RISING") or \
                     (oi_trend == "RISING" and price_trend == "DROPPING")  # squeeze
        oi_bearish = (oi_trend == "FALLING" and price_trend == "RISING") or \
                     (oi_trend == "FALLING" and price_trend == "DROPPING")  # capitulation
        
        if (direction == "BUY" and oi_bullish) or (direction == "SELL" and oi_bearish):
            confidence += MTDC_WEIGHT_OI
            breakdown.append(f"✅ OI: {oi_trend}+Price {price_trend} = ALIGNED (+{MTDC_WEIGHT_OI*100:.0f}%)")
        elif (direction == "BUY" and oi_bearish) or (direction == "SELL" and oi_bullish):
            breakdown.append(f"❌ OI: {oi_trend}+Price {price_trend} = OPPOSING (0%)")
        else:
            confidence += MTDC_WEIGHT_OI * 0.5
            breakdown.append(f"⚠️ OI: {oi_trend} (NEUTRAL, +{MTDC_WEIGHT_OI*50:.0f}%)")
    except Exception:
        confidence += MTDC_WEIGHT_OI * 0.5
        breakdown.append("⚠️ OI: ERROR (+half)")
    
    # ════════════════════════════════════════════════════════════════
    #  SOURCE 5: FUNDING RATE SENTIMENT (15% weight)
    #  Negative FR = market overshorted → bullish bias
    #  Positive FR = market overlonged → bearish bias
    # ════════════════════════════════════════════════════════════════
    try:
        fr = signal_data.get("funding_rate", 0.0) if signal_data else 0.0
        
        fr_bullish = fr < -0.0001   # Negative = shorts paying longs = bullish
        fr_bearish = fr > 0.0003    # Strongly positive = longs paying shorts = bearish
        
        if (direction == "BUY" and fr_bullish) or (direction == "SELL" and fr_bearish):
            confidence += MTDC_WEIGHT_FR
            breakdown.append(f"✅ FR: {fr*100:.4f}% = ALIGNED (+{MTDC_WEIGHT_FR*100:.0f}%)")
        elif (direction == "BUY" and fr_bearish) or (direction == "SELL" and fr_bullish):
            breakdown.append(f"❌ FR: {fr*100:.4f}% = OPPOSING (0%)")
        else:
            confidence += MTDC_WEIGHT_FR * 0.5  # Neutral funding
            breakdown.append(f"⚠️ FR: {fr*100:.4f}% (NEUTRAL, +{MTDC_WEIGHT_FR*50:.0f}%)")
    except Exception:
        confidence += MTDC_WEIGHT_FR * 0.5
        breakdown.append("⚠️ FR: ERROR (+half)")
    
    return confidence, breakdown, False, ""


# ★ v25: Wrapper for backward compatibility (replaces old _check_1h_trend_ema50)
def _check_1h_trend_ema50(client: Client, symbol: str, direction: str) -> tuple:
    """Backward-compatible wrapper. Now uses full MTDC system."""
    confidence, breakdown, veto, veto_reason = _calculate_directional_confidence(
        client, symbol, direction
    )
    if veto:
        return False, veto_reason
    if confidence >= MTDC_MIN_CONFIDENCE:
        return True, f"MTDC PASS: {confidence*100:.0f}% confidence │ {' │ '.join(breakdown)}"
    return False, f"MTDC LOW CONFIDENCE: {confidence*100:.0f}% < {MTDC_MIN_CONFIDENCE*100:.0f}% │ {' │ '.join(breakdown)}"


# ═════════════════════════════════════════════════════════════════════════════
# ██  ★ v20: SMART MONEY CONCEPTS (SMC) ENTRY ENGINE                       ██
# ██  Replaces v19 3-Layer + old AdvancedExecutionValidator                 ██
# ██  Pure Price Action: CHOCH/BOS + Liquidity Sweep + OB/FVG Retest       ██
# ═════════════════════════════════════════════════════════════════════════════

import numpy as np

ENTRY_VALIDATION_ENABLED = True    # ★ v20: Master switch for SMC entry validation
WAIT_QUEUE_MAX_CANDLES   = 30      # ★ WAIT queue: max 30 candles (2.5h on 5m) before expiry
SMC_SWING_LOOKBACK       = 3       # ★ Swing detection: ±3 bar window
SMC_SWEEP_TOLERANCE_ATR  = 0.15    # ★ Sweep: wick must exceed level by at least 0.15× ATR
SMC_RR_MIN_RATIO         = 1.2     # ★ v37 Tuning: R:R floor lowered (was 1.5) — allows 1.2R+ setups through
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
                              swing_highs: list, swing_lows: list, atr14: float = 0.0) -> tuple:
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
    # Look at ALL candles after the last swing point to see if structure broke during the pullback
    # ★ v34-fix2: Minimum break distance to filter noise/wick breaks
    min_break = atr14 * 0.1 if atr14 > 0 else 0  # Must break by at least 0.1×ATR
    
    # Bullish CHOCH: Downtrend + price breaks above last swing high
    if trend in ("DOWN", "MIXED"):
        # ★ v34-fix: Check BOTH last AND 2nd-last swing high (last swing often too recent to break)
        for sh_check_idx in range(len(recent_sh) - 1, max(len(recent_sh) - 3, -1), -1):
            if sh_check_idx < 0:
                break
            check_sh_idx, check_sh_price = recent_sh[sh_check_idx]
            start_idx = max(0, check_sh_idx)
            for i in range(start_idx, n):
                if closes[i] > (check_sh_price + min_break) and i > check_sh_idx:
                    detail = (
                        f"Bullish CHOCH: Trend was {trend}, price ${closes[i]:.4f} broke above "
                        f"Swing High ${check_sh_price:.4f} by ${closes[i]-check_sh_price:.4f} (min ${min_break:.4f}) (bar {check_sh_idx})"
                    )
                    return "CHOCH_BULL", i, check_sh_price, detail
    
    # Bearish CHOCH: Uptrend + price breaks below last swing low
    if trend in ("UP", "MIXED"):
        # ★ v34-fix: Check BOTH last AND 2nd-last swing low
        for sl_check_idx in range(len(recent_sl) - 1, max(len(recent_sl) - 3, -1), -1):
            if sl_check_idx < 0:
                break
            check_sl_idx, check_sl_price = recent_sl[sl_check_idx]
            start_idx = max(0, check_sl_idx)
            for i in range(start_idx, n):
                if closes[i] < (check_sl_price - min_break) and i > check_sl_idx:
                    detail = (
                        f"Bearish CHOCH: Trend was {trend}, price ${closes[i]:.4f} broke below "
                        f"Swing Low ${check_sl_price:.4f} by ${check_sl_price-closes[i]:.4f} (min ${min_break:.4f}) (bar {check_sl_idx})"
                    )
                    return "CHOCH_BEAR", i, check_sl_price, detail
    
    # --- BOS Detection ---
    # Bullish BOS: Uptrend + new Higher High since last swing high
    if trend == "UP":
        last_sh_idx = last_2_sh[-1][0]
        start_idx = max(0, last_sh_idx)
        for i in range(start_idx, n):
            if highs[i] > (last_2_sh[-1][1] + min_break) and i > last_sh_idx:
                detail = (
                    f"Bullish BOS: Uptrend continuation. High ${highs[i]:.4f} broke "
                    f"Swing High ${last_2_sh[-1][1]:.4f} by ${highs[i]-last_2_sh[-1][1]:.4f}"
                )
                return "BOS_BULL", i, last_2_sh[-1][1], detail
    
    # Bearish BOS: Downtrend + new Lower Low since last swing low
    if trend == "DOWN":
        last_sl_idx = last_2_sl[-1][0]
        start_idx = max(0, last_sl_idx)
        for i in range(start_idx, n):
            if lows[i] < (last_2_sl[-1][1] - min_break) and i > last_sl_idx:
                detail = (
                    f"Bearish BOS: Downtrend continuation. Low ${lows[i]:.4f} broke "
                    f"Swing Low ${last_2_sl[-1][1]:.4f} by ${last_2_sl[-1][1]-lows[i]:.4f}"
                )
                return "BOS_BEAR", i, last_2_sl[-1][1], detail
    
    # ── ★ v38: ANTICIPATED BOS for Trending Markets ──────────────────────
    # ★ v34-fix: Extended to MIXED trends (with tighter threshold)
    if atr14 > 0:
        if trend in ("UP", "MIXED") and len(last_2_sh) >= 2:
            last_sh_price = last_2_sh[-1][1]
            threshold = 0.2 if trend == "UP" else 0.4  # MIXED gets wider tolerance
            if current_price >= (last_sh_price - threshold * atr14):
                detail = (
                    f"Anticipated BOS BULL: price ${current_price:.4f} within "
                    f"{threshold}×ATR (${threshold*atr14:.4f}) of Swing High ${last_sh_price:.4f}"
                )
                return "BOS_BULL", n - 1, last_sh_price, detail
        
        if trend in ("DOWN", "MIXED") and len(last_2_sl) >= 2:
            last_sl_price = last_2_sl[-1][1]
            threshold = 0.2 if trend == "DOWN" else 0.4
            if current_price <= (last_sl_price + threshold * atr14):
                detail = (
                    f"Anticipated BOS BEAR: price ${current_price:.4f} within "
                    f"{threshold}×ATR (${threshold*atr14:.4f}) of Swing Low ${last_sl_price:.4f}"
                )
                return "BOS_BEAR", n - 1, last_sl_price, detail
    
    return "NONE", -1, 0.0, f"No structure break detected (trend={trend})"


# ─── LIQUIDITY SWEEP DETECTOR ───────────────────────────────────────────────
def _detect_liquidity_sweep(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                             opens: np.ndarray, vols: np.ndarray, swing_highs: list, swing_lows: list,
                             direction: str, atr: float) -> tuple:
    """
    ★ v20 Advanced SMC: Detect Liquidity Sweeps with strict Volume & wick size markers.
    """
    n = len(closes)
    min_sweep = atr * 0.2  # ★ v35 FIX: Relaxed from 0.5x to 0.2x ATR (0.5x was rejecting valid minor sweeps)
    
    # Calculate Volume MA(20) for the last few candles
    vol_ma = np.mean(vols[-20:]) if len(vols) >= 20 else np.mean(vols)
    
    if direction == "SELL" and swing_highs:
        # Bearish Sweep (Bull Trap)
        for sh_idx, sh_price in reversed(swing_highs[-5:]):
            if sh_idx >= n - 2:
                continue
            # ★ v42: FRESH SWEEP ONLY — only check last 3 candles (15 min on 5m)
            for i in range(max(sh_idx + 1, n - 3), n):
                wick_above = highs[i] - sh_price
                # ★ v25.2: Relaxed volume from 1.0x down to 0.7x (catch more valid sweeps)
                if wick_above >= min_sweep and closes[i] < sh_price and vols[i] >= vol_ma * 0.7:
                    detail = (
                        f"Bearish Trap: Candle {i} swept high ${sh_price:.4f} (+${wick_above:.4f}), "
                        f"vol {vols[i]:.1f} >= MA. Closed below at ${closes[i]:.4f}"
                    )
                    return True, sh_price, highs[i], detail
                    
    elif direction == "BUY" and swing_lows:
        # Bullish Sweep (Bear Trap)
        for sl_idx, sl_price in reversed(swing_lows[-5:]):
            if sl_idx >= n - 2:
                continue
            # ★ v42: FRESH SWEEP ONLY — only check last 3 candles (15 min on 5m)
            for i in range(max(sl_idx + 1, n - 3), n):
                wick_below = sl_price - lows[i]
                # ★ v25.2: Relaxed volume from 1.0x down to 0.5x (catch more valid sweeps)
                if wick_below >= min_sweep and closes[i] > sl_price and vols[i] >= vol_ma * 0.5:
                    detail = (
                        f"Bullish Trap: Candle {i} swept low ${sl_price:.4f} (+${wick_below:.4f}), "
                        f"vol {vols[i]:.1f} >= MA. Closed above at ${closes[i]:.4f}"
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
                if size < 0.2 * atr or size > 3.0 * atr:  # ★ v21 TESTING: Lowered min from 0.5 to 0.2 (accept more OB setups)
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
                
                if size < 0.2 * atr or size > 3.0 * atr:  # ★ v21 TESTING: Lowered min from 0.5 to 0.2 (accept more OB setups)
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
def _smc_entry_validate(client: Client, symbol: str, direction: str, score: int = 0) -> tuple:
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
        # ── ★ v42: GATE 0 — 15m EMA21 Trend Filter (FIRST CHECK) ──────────
        # If 15m price is on the wrong side of EMA21, reject immediately.
        try:
            m15_raw = client.futures_klines(symbol=symbol, interval="15m", limit=50)
            if m15_raw and len(m15_raw) >= 25:
                m15_closes = np.array([float(k[4]) for k in m15_raw])
                # Calculate EMA21 on 15m closes
                ema_period = 21
                m15_ema21 = m15_closes[0]
                for _c in m15_closes[1:]:
                    m15_ema21 = _c * (2.0 / (ema_period + 1)) + m15_ema21 * (1 - 2.0 / (ema_period + 1))
                m15_close = m15_closes[-1]

                if direction == "BUY" and m15_close < m15_ema21:
                    reason = (
                        f"15m TREND BEARISH: Price {m15_close:.4f} "
                        f"< EMA21 {m15_ema21:.4f}. NO BUY allowed."
                    )
                    log.info(f"    [{symbol}] ❌ {reason}")
                    return "NEUTRAL", reason, None

                if direction == "SELL" and m15_close > m15_ema21:
                    reason = (
                        f"15m TREND BULLISH: Price {m15_close:.4f} "
                        f"> EMA21 {m15_ema21:.4f}. NO SELL allowed."
                    )
                    log.info(f"    [{symbol}] ❌ {reason}")
                    return "NEUTRAL", reason, None

                log.info(f"    [{symbol}] ✅ 15m EMA21 Gate: PASSED (Price {m15_close:.4f} vs EMA21 {m15_ema21:.4f})")
        except Exception as e_m15:
            log.warning(f"    [{symbol}] ⚠ 15m EMA21 gate failed: {e_m15} — BLOCKED")
            return "NEUTRAL", f"15m EMA21 gate failed: {e_m15}", None

        m5 = client.futures_klines(symbol=symbol, interval="5m", limit=200)  # ★ v34-fix: Reverted 15m → 5m (original setup)
        if not m5 or len(m5) < 40:
            return "PASS", "SMC SKIP: Not enough M15 data", None
        
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
            # ★ v36: Sweep Confirmation is PREFERRED but not MANDATORY
            # Allow entry without sweep IF structure (BOS/CHOCH) + OB zone is valid
            log.info(f"    [{symbol}] ⚠ No Liquidity Sweep — Will require strong structure to compensate")
            
        # ── ★ v42: REVERSAL CONFIRMATION — Validate bounce after sweep ─────
        # After sweep, next 2 candles must confirm reversal direction.
        # This filters out sweeps that are actually continuation traps.
        if swept and n >= 3:
            c_1 = close[-2]  # candle after sweep (or recent)
            o_1 = opn[-2]
            c_2 = close[-1]  # current candle
            o_2 = opn[-1]
            v_2 = vols[-1]
            
            # Average volume for threshold
            avg_vol = np.mean(vols[-20:]) if len(vols) >= 20 else np.mean(vols)
            
            # The close of the sweep candle (approximate: sweep_level is the swing that was swept)
            sweep_close_ref = sweep_level
            
            if direction == "BUY":
                body_1 = c_1 - o_1  # positive = bullish
                body_2 = c_2 - o_2
                confirmation = (
                    body_1 > 0 and           # green candle
                    body_2 > 0 and           # green candle
                    c_1 > sweep_close_ref and
                    c_2 > c_1 and
                    v_2 > avg_vol * 0.8
                )
            else:  # SELL
                body_1 = o_1 - c_1  # positive = bearish
                body_2 = o_2 - c_2
                confirmation = (
                    body_1 > 0 and           # red candle
                    body_2 > 0 and           # red candle
                    c_1 < sweep_close_ref and
                    c_2 < c_1 and
                    v_2 > avg_vol * 0.8
                )
            
            if not confirmation:
                reason = (
                    f"REVERSAL NOT CONFIRMED: Bounce looks like a trap. "
                    f"Need 2 consecutive {'bullish' if direction == 'BUY' else 'bearish'} closes "
                    f"{'above' if direction == 'BUY' else 'below'} sweep level ${sweep_close_ref:.4f}."
                )
                log.info(f"    [{symbol}] ⚠ {reason}")
                return "WAIT", reason, None
            else:
                log.info(f"    [{symbol}] ✅ Reversal Confirmed: 2 consecutive {'bullish' if direction == 'BUY' else 'bearish'} candles after sweep")

        # ── Step 3: Market Structure (BOS / CHOCH) ────────────────────────
        struct_type, break_idx, break_level, struct_detail = _detect_market_structure(
            high, low, close, swing_highs, swing_lows, atr14=atr14  # ★ v38: Pass ATR for Anticipated BOS
        )
        log.info(f"    [{symbol}] SMC Structure: {struct_type} | {struct_detail}")
        
        # ★ v22: Direction-Agnostic Structure Validation
        # Accept ANY valid structure shift. If SMC direction opposes signal,
        # flip the trade direction to follow Smart Money instead of blocking.
        valid_structure = struct_type in ("CHOCH_BULL", "BOS_BULL", "CHOCH_BEAR", "BOS_BEAR")
        
        # ── ★ v26.3 UPGRADE 3: DISPLACEMENT CANDLE QUALITY CHECK ────────
        # Only trust structure breaks backed by strong institutional candles.
        # Weak breaks (small body, no momentum) are often fake breakouts.
        if valid_structure and break_idx > 0 and break_idx < len(close):
            brk_body = abs(close[break_idx] - opn[break_idx])
            brk_range = high[break_idx] - low[break_idx]
            body_pct = brk_body / brk_range if brk_range > 0 else 0
            is_strong = body_pct >= 0.25 and brk_body >= 0.10 * atr14  # ★ v37: Relaxed further (25%/0.10x ATR) — allow more SMC setups through
            if is_strong:
                log.info(f"    [{symbol}] 💪 Displacement: STRONG (body {body_pct*100:.0f}%, {brk_body/atr14:.1f}x ATR)")
            else:
                log.info(f"    [{symbol}] ⚠️ Displacement: WEAK (body {body_pct*100:.0f}%, {brk_body/atr14:.1f}x ATR) → No structure claim")
                valid_structure = False
        
        if not valid_structure:
            # ★ v38.2: Dynamic Session Score Threshold for Bypass
            session = get_current_session()
            bypass_threshold = 26  # ★ HIGH QUALITY ONLY
            
            # --- ONE TIME FORCED BYPASS (DISABLED) ---
            import os
            flag_file = "/opt/trading-bot/.one_time_bypass.flag"
            # DISABLED — was causing low-quality entries
            if session == "LONDON":
                bypass_threshold = 24  # Slightly relaxed for London (good liquidity)
            elif session == "ASIA":
                bypass_threshold = 24  # Same for Asia
            else:
                bypass_threshold = 26  # Strict for NY (high volatility/fakeouts)
                
            if score >= bypass_threshold:  # ★ Dynamic threshold applied
                # ★ v38.1: H1 TREND DIRECTION CHECK — prevent counter-trend bypass
                # SELL bypass in BULLISH market = guaranteed loss (XRPUSDT, DASHUSDT losses)
                h1_trend_ok = True  # default pass
                try:
                    h1_klines = client.futures_klines(symbol=symbol, interval="1h", limit=55)
                    if h1_klines and len(h1_klines) >= 50:
                        h1c = np.array([float(k[4]) for k in h1_klines])
                        # Quick EMA21/EMA50
                        _m21 = 2.0 / 22; _e21 = float(h1c[0])
                        _m50 = 2.0 / 51; _e50 = float(h1c[0])
                        for _v in h1c[1:]:
                            _e21 = (float(_v) - _e21) * _m21 + _e21
                            _e50 = (float(_v) - _e50) * _m50 + _e50
                        h1_bull = float(h1c[-1]) > _e21 and _e21 > _e50
                        h1_bear = float(h1c[-1]) < _e21 and _e21 < _e50
                        
                        if direction == "BUY" and h1_bear:
                            h1_trend_ok = False
                            log.info(f"    [{symbol}] 🚫 BYPASS BLOCKED: BUY in H1 BEARISH (EMA21 < EMA50)")
                        elif direction == "SELL" and h1_bull:
                            h1_trend_ok = False
                            log.info(f"    [{symbol}] 🚫 BYPASS BLOCKED: SELL in H1 BULLISH (EMA21 > EMA50)")
                except Exception as h1_err:
                    log.warning(f"    [{symbol}] H1 trend check failed: {h1_err} — allowing bypass")
                
                if not h1_trend_ok:
                    reason = f"SMC NEUTRAL: Score {score} >= 22 but H1 trend opposes {direction}. Counter-trend blocked."
                    log.info(f"    [{symbol}] 🚫 {reason}")
                    return "NEUTRAL", reason, None
                
                # ★ v38: EMA20 safety — BUY must be above EMA20, SELL must be below
                ema20_val = close[-1]  # fallback
                if len(close) >= 20:
                    ema_mult_20 = 2.0 / (20 + 1)
                    _ema = float(close[0])
                    for _c in close[1:]:
                        _ema = (float(_c) - _ema) * ema_mult_20 + _ema
                    ema20_val = _ema
                ema_ok = (direction == "BUY" and current_price > ema20_val) or \
                         (direction == "SELL" and current_price < ema20_val)
                if ema_ok:
                    log.info(f"    [{symbol}] 🚀 HIGH SCORE BYPASS: Score ({score}) >= 22 + H1 Trend OK + EMA20 ${ema20_val:.4f} OK. Bypassing SMC!")
                    struct_type = "BYPASS"
                else:
                    reason = f"SMC NEUTRAL: Score {score} >= 22 but EMA20 ${ema20_val:.4f} opposes {direction}. Blocked."
                    log.info(f"    [{symbol}] 🚫 {reason}")
                    return "NEUTRAL", reason, None
            else:
                reason = f"SMC NEUTRAL (No Valid Structure): 5m has no confirmed BOS/CHOCH ({struct_type}). Waiting for structure."
                log.info(f"    [{symbol}] 🚫 {reason}")
                return "NEUTRAL", reason, None
        
        # ★ v22: Auto-flip direction to match 5m SMC structure
        if struct_type == "BYPASS":
            smc_direction = direction
        else:
            smc_direction = "BUY" if struct_type in ("CHOCH_BULL", "BOS_BULL") else "SELL"
            
        if smc_direction != direction:
            log.info(f"    [{symbol}] 🔄 SMC FLIP: Signal was {direction} but 5m structure is {struct_type} → Flipping to {smc_direction}")
            direction = smc_direction
        
        # ── ★ v26.3 UPGRADE 2: 15m HIGHER-TIMEFRAME CONFLUENCE (STRICT VETO) ──
        # If 15m structure opposes 5m direction, SKIP trade entirely.
        # DO NOT flip — a BUY OB cannot be traded as SELL (SL math breaks).
        try:
            m15 = client.futures_klines(symbol=symbol, interval="1h", limit=100)  # ★ v37: Higher TF confluence now 1H (since main is 15m)
            if m15 and len(m15) >= 40:
                h15 = np.array([float(k[2]) for k in m15])
                l15 = np.array([float(k[3]) for k in m15])
                c15 = np.array([float(k[4]) for k in m15])
                sh15, sl15 = _detect_swing_points(h15, l15, SMC_SWING_LOOKBACK)
                if len(sh15) >= 2 and len(sl15) >= 2:
                    struct15, _, _, detail15 = _detect_market_structure(h15, l15, c15, sh15, sl15)
                    htf_dir = "BUY" if struct15 in ("CHOCH_BULL", "BOS_BULL") else "SELL" if struct15 in ("CHOCH_BEAR", "BOS_BEAR") else "NONE"
                    if htf_dir != "NONE" and htf_dir != direction:
                        reason = f"⚠ WARNING: 15m HTF Mismatch — 5m wants {direction} but 15m is {struct15}. Passing anyway as Micro-Scalp!"
                        log.warning(f"    [{symbol}] {reason}")
                        # ★ v31: User wants instant execution, no HTF blocks!
                    elif htf_dir == direction:
                        log.info(f"    [{symbol}] 🔗 15m Confluence: ALIGNED ({struct15}) ✅")
                    else:
                        log.info(f"    [{symbol}] 🔗 15m Confluence: NEUTRAL (no 15m structure) — Passing on 5m alone.")
        except Exception as e15:
            log.warning(f"    [{symbol}] ⚠ 15m HTF check failed: {e15} — Continuing on 5m.")
        
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
            reason = f"SMC PASS: Valid {struct_type} but no Strict OB/FVG zone found. Proceeding as Momentum Breakout!"
            
            # Form partial zone_data just for the CHOCH chart plotting
            partial_data = {
                "structure": struct_type, "smc_direction": direction,
                "swept": swept, "sweep_level": sweep_level, 
                "zone_type": "NONE"
            }
            return "PASS", reason, partial_data
        
        log.info(f"    [{symbol}] SMC Zone: {zone_type} @ ${zone_low:.4f}–${zone_high:.4f}")
        
        # ── ★ v37: OB QUALITY VALIDATION — Confirm OB is institutional-grade ──
        # 3 confirmations: Displacement + Imbalance + Unmitigated
        ob_quality_score = 0
        ob_quality_details = []
        
        if zone_type == "OB" and ob_search_idx > 3:
            # 1️⃣ DISPLACEMENT CHECK: Was the move away from OB strong?
            # The candle(s) after the OB should have big body (≥ 1.0x ATR)
            disp_start = min(ob_search_idx, n - 1)
            disp_end = min(ob_search_idx + 3, n)
            max_body = 0.0
            for di in range(disp_start, disp_end):
                body = abs(float(close[di]) - float(opn[di]))
                if body > max_body:
                    max_body = body
            if max_body >= atr14 * 0.8:
                ob_quality_score += 1
                ob_quality_details.append(f"✅ Displacement: {max_body/atr14:.1f}x ATR (strong)")
            else:
                ob_quality_details.append(f"❌ Displacement: {max_body/atr14:.1f}x ATR (weak)")
            
            # 2️⃣ IMBALANCE CHECK: Is there a Fair Value Gap between OB and current price?
            has_imbalance = False
            scan_start = max(0, ob_search_idx - 2)
            scan_end = min(ob_search_idx + 5, n - 1)
            for fi in range(scan_start, scan_end):
                if fi + 2 < n:
                    if direction == "BUY" and float(high[fi]) < float(low[fi + 2]):
                        has_imbalance = True
                        break
                    elif direction == "SELL" and float(low[fi]) > float(high[fi + 2]):
                        has_imbalance = True
                        break
            if has_imbalance:
                ob_quality_score += 1
                ob_quality_details.append("✅ Imbalance: FVG found (price attracted)")
            else:
                ob_quality_details.append("⚠️ Imbalance: No FVG (weaker attraction)")
            
            # 3️⃣ UNMITIGATED CHECK: Has price retested OB zone already?
            mitigated = False
            for mi in range(ob_search_idx + 1, n):
                if direction == "BUY" and float(low[mi]) <= zone_high:
                    mitigated = True
                    break
                elif direction == "SELL" and float(high[mi]) >= zone_low:
                    mitigated = True
                    break
            if not mitigated:
                ob_quality_score += 1
                ob_quality_details.append("✅ Unmitigated: OB is FRESH (never retested)")
            else:
                ob_quality_details.append("⚠️ Mitigated: OB already tested (weaker)")
            
            log.info(f"    [{symbol}] 🏆 OB Quality: {ob_quality_score}/3 │ {' │ '.join(ob_quality_details)}")
            
            # ★ v34-fix: OB Quality is informational only — don't hard block
            if ob_quality_score < 1:
                log.info(f"    [{symbol}] ⚠️ OB Quality low ({ob_quality_score}/3) — proceeding anyway (score filter handles quality)")
        
        # ── ★ v26.3 UPGRADE 1: PREMIUM/DISCOUNT ZONE FILTER ──────────────
        # SMC Refactor: Only block extreme entries (top 25% for BUY, bottom 25% for SELL)
        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            pd_range_high = max(sh[1] for sh in swing_highs[-4:])
            pd_range_low = min(sl[1] for sl in swing_lows[-4:])
            range_size = pd_range_high - pd_range_low
            
            extreme_premium = pd_range_high - (range_size * 0.05)  # ★ v34-fix: Top 5% only (was 15%)
            extreme_discount = pd_range_low + (range_size * 0.05)  # ★ v34-fix: Bottom 5% only (was 15%)
            
            if direction == "BUY" and current_price > extreme_premium:
                reason = f"P/D REJECT: BUY in EXTREME PREMIUM (price ${current_price:.4f} > top 25% ${extreme_premium:.4f})."
                log.warning(f"    [{symbol}] {reason}")
                return "NEUTRAL", reason, None
            elif direction == "SELL" and current_price < extreme_discount:
                reason = f"P/D REJECT: SELL in EXTREME DISCOUNT (price ${current_price:.4f} < bottom 25% ${extreme_discount:.4f})."
                log.warning(f"    [{symbol}] {reason}")
                return "NEUTRAL", reason, None
            else:
                log.info(f"    [{symbol}] P/D Zone: OK (Not in extreme boundaries)")
        
        zone_data = {
            "zone_type": zone_type,
            "zone_high": zone_high,
            "zone_low": zone_low,
            "structure": struct_type,
            "swept": swept,
            "inducement": inducement_found,
            "sweep_level": sweep_level,
            "smc_direction": direction,  # ★ v22: May be flipped from original signal
        }
        
        # ── Step 6: Is price IN the zone now? (ATR-based Buffer) ──────────
        in_zone = False
        # ★ v37: ATR-based buffer instead of %. Prevents XAUUSDT-type $23 buffer bug.
        zone_buffer_abs = 0.5 * atr14  # ★ Strict SMC: 0.5 × ATR buffer (sniper touch entry)
        if direction == "BUY":
            buy_upper_bound = zone_high + zone_buffer_abs
            buy_lower_bound = zone_low - zone_buffer_abs
            in_zone = current_price <= buy_upper_bound and current_price >= buy_lower_bound
        elif direction == "SELL":
            sell_lower_bound = zone_low - zone_buffer_abs
            sell_upper_bound = zone_high + zone_buffer_abs
            in_zone = current_price >= sell_lower_bound and current_price <= sell_upper_bound
        
        # ★ CANDLE TOUCH CHECK: If current price missed the zone, check if the 
        # last 2 candles' wick touched it (catches fast bounces between scans).
        # BUT: only enter if current price is still NEAR the zone (within 1.0 ATR).
        if not in_zone and n >= 2:
            near_zone_limit = 1.0 * atr14  # Max distance from zone edge to allow late entry
            
            for lookback in range(1, 3):  # Check last 2 candles
                candle_low = float(low[-lookback])
                candle_high = float(high[-lookback])
                
                if direction == "BUY":
                    # Did the candle's low dip into/below the OB zone?
                    candle_touched = candle_low <= buy_upper_bound
                    # Is current price still close enough to zone top?
                    price_still_near = current_price <= (zone_high + near_zone_limit)
                    if candle_touched and price_still_near:
                        in_zone = True
                        log.info(f"    [{symbol}] 🎯 CANDLE TOUCH DETECTED! Candle[-{lookback}] Low ${candle_low:.4f} touched zone. Price ${current_price:.4f} still near (within 1.0 ATR).")
                        break
                        
                elif direction == "SELL":
                    # Did the candle's high reach into/above the OB zone?
                    candle_touched = candle_high >= sell_lower_bound
                    # Is current price still close enough to zone bottom?
                    price_still_near = current_price >= (zone_low - near_zone_limit)
                    if candle_touched and price_still_near:
                        in_zone = True
                        log.info(f"    [{symbol}] 🎯 CANDLE TOUCH DETECTED! Candle[-{lookback}] High ${candle_high:.4f} touched zone. Price ${current_price:.4f} still near (within 1.0 ATR).")
                        break
        
        if not in_zone:
            reason = (
                f"SMC WAIT: {zone_type} zone ${zone_low:.4f}–${zone_high:.4f} found, "
                f"but price ${current_price:.4f} not in zone yet. Queued for retest."
            )
            log.info(f"    [{symbol}] ⏳ {reason}")
            return "WAIT", reason, zone_data
        
        # ★ Strict SMC: SL = OB edge + 0.2 ATR (tight, zone-based)
        # Use zone edge as entry reference (not current_price which may have bounced away)
        if direction == "BUY":
            entry_ref = min(current_price, zone_high)  # Entry at zone or better
            sl_distance = entry_ref - (zone_low - (0.2 * atr14))
        else:
            entry_ref = max(current_price, zone_low)  # Entry at zone or better
            sl_distance = (zone_high + (0.2 * atr14)) - entry_ref
        
        # Min/Max safety guardrails
        sl_distance = max(sl_distance, current_price * 0.002)  # Min 0.2%
        sl_distance = min(sl_distance, current_price * 0.04)   # Max 4.0%
            
        # ★ HARD REJECTION: Cap SL at 4.0% max raw move to protect against extreme volatility
        sl_pct = (sl_distance / current_price) * 100.0
        if sl_pct > 4.0:
            reason = f"SMC NEUTRAL: SL distance {sl_pct:.2f}% is too wide (>4.0%). Skipping."
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
        zone_data["has_sweep"] = swept          # ★ v25: Liquidity Sweep Premium Flag
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
    def validate(client: Client, symbol: str, direction: str, score: int = 0) -> tuple[bool, str, dict]:
        """
        Thin wrapper over _smc_entry_validate for API compatibility.
        """
        result, reason, zone_data = _smc_entry_validate(client, symbol, direction, score)
        if result == "PASS":
            return True, f"SMC Pre-Flight PASSED: {reason}", zone_data
        elif result == "WAIT":
            # ★ FIX: Let WAIT pass the pre-flight so the downstream gate can add it to the wait_queue
            return True, f"SMC Pre-Flight WAIT (passing to queue): {reason}", zone_data
        else:
            # NEUTRAL blocks immediate execution
            return False, f"SMC Pre-Flight BLOCKED ({result}): {reason}", zone_data  # ★ v36: Pass zone_data for debugging

def initialize_client() -> Client:
    """Create and configure the Binance Futures client (Testnet or Mainnet)."""
    if USE_TESTNET:
        log.info("🔌  Connecting to Binance Futures TESTNET (DEMO ACCOUNT)...")
        client = Client(API_KEY, API_SECRET, testnet=True, ping=False)
        client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
    else:
        log.info("🔴  Connecting to Binance Futures MAINNET (REAL MONEY)...")
        if DRY_RUN:
            log.info("🟢  DRY RUN MODE — No real orders will be placed.")
        else:
            log.warning("⚠️  LIVE TRADING MODE — Real orders WILL be placed!")
        client = Client(API_KEY, API_SECRET, testnet=False, ping=False)

    # ★ AUDIT FIX: Redact API key from logs (was exposing 12 chars)
    log.info(f"    API Key: {API_KEY[:4]}****{API_KEY[-2:]} ({len(API_KEY)} chars)")
    log.info(f"    Mode: {'TESTNET' if USE_TESTNET else 'MAINNET'} │ DRY_RUN: {DRY_RUN}")

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
                and float(t.get('lastPrice', 0)) > 0.50             # ★ v29.8 min $0.50
                and float(t.get('quoteVolume', 0)) > 30_000_000     # ★ v29.8 min $30M volume
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
# ██  ★ v24: PENDING LIMIT ORDER AUTO-CANCEL MANAGER                       ██
# ═════════════════════════════════════════════════════════════════════════════

PENDING_ENTRY_MAX_CANDLES = 12   # Cancel unfilled limit after 12 × 5m = 1 hour
PENDING_ENTRY_CANDLE_S    = 300  # 5-minute candle in seconds

def _check_pending_entry_order(client: Client, symbol: str, state: dict, current_price: float) -> str:
    """
    ★ v24: Auto-Cancel Manager for unfilled GTC Limit Entry orders.
    
    Rules:
      1. TIME EXPIRY: If unfilled for > 12 candles (1 hour on 5m), cancel.
      2. OB INVALIDATION: If price breaks below zone_low (BUY) or above zone_high (SELL), cancel.
      3. FILLED CHECK: If order was filled while we were scanning other coins, register the position.
    
    Returns:
      "FILLED"    — Order was filled, position is active.
      "PENDING"   — Still waiting for fill, order is alive.
      "CANCELLED" — Order was cancelled (expired or invalidated).
      "NONE"      — No pending order for this symbol.
    """
    order_id = state.get("pending_entry_order_id")
    if not order_id:
        return "NONE"
    
    placed_time = state.get("pending_entry_time", 0)
    entry_side = state.get("pending_entry_side", "NONE")
    zone = state.get("pending_entry_zone")
    limit_price = state.get("pending_entry_price", 0.0)
    
    elapsed_s = time.time() - placed_time
    elapsed_candles = int(elapsed_s / PENDING_ENTRY_CANDLE_S)
    
    # ── Rule 1: Check if order was FILLED while we were busy ──
    if not DRY_RUN:
        try:
            order_status = client.futures_get_order(symbol=symbol, orderId=order_id)
            status = order_status.get('status', '')
            if status == 'FILLED':
                log.info(f"✅  [{symbol}] PENDING LIMIT FILLED! (Maker Fee) — ${limit_price} │ OrderID: {order_id}")
                
                # Place SL and TP orders now
                pending_data = state.get("pending_entry_data", {})
                sl_dist = pending_data.get("sl_distance", 0)
                tp_dist = pending_data.get("tp_distance", 0)
                
                if sl_dist > 0:
                    close_side = SIDE_SELL if entry_side == "BUY" else SIDE_BUY
                    if entry_side == "BUY":
                        sl_price = _round_price(limit_price - sl_dist, symbol)
                        tp_price = _round_price(limit_price + tp_dist, symbol)
                    else:
                        sl_price = _round_price(limit_price + sl_dist, symbol)
                        tp_price = _round_price(limit_price - tp_dist, symbol)
                    
                    # Place SL
                    try:
                        pos = _has_open_position(client, symbol)
                        qty = pos["qty"] if pos else pending_data.get("quantity", 0)
                        client.futures_create_order(
                            symbol=symbol, side=close_side,
                            type=FUTURE_ORDER_TYPE_STOP_MARKET,
                            stopPrice=str(sl_price),
                            quantity=qty,
                            reduceOnly="true",
                            timeInForce=TIME_IN_FORCE_GTC,
                            workingType="MARK_PRICE",
                        )
                        log.info(f"🛡  [{symbol}] POST-FILL SL — ${sl_price}")
                    except Exception as sl_e:
                        log.error(f"🚨  [{symbol}] Post-fill SL failed: {sl_e}")
                    
                    # Place TP (if enabled)
                    if not DISABLE_HARD_TP:
                        try:
                            client.futures_create_order(
                                symbol=symbol, side=close_side,
                                type="TAKE_PROFIT_MARKET",
                                stopPrice=str(tp_price),
                                closePosition="true",
                                timeInForce=TIME_IN_FORCE_GTC,
                                workingType="MARK_PRICE",
                            )
                            log.info(f"🎯  [{symbol}] POST-FILL TP — ${tp_price}")
                        except Exception as tp_e:
                            log.warning(f"⚠  [{symbol}] Post-fill TP failed: {tp_e}")
                
                # Telegram alert
                try:
                    send_telegram_alert(
                        f"✅ <b>LIMIT FILLED</b>\n"
                        f"Coin: {symbol}\nSide: {entry_side}\n"
                        f"Entry: ${limit_price}\n"
                        f"Method: 50% OB Equilibrium"
                    )
                except Exception:
                    pass
                
                # Clear pending state
                state["pending_entry_order_id"] = None
                state["pending_entry_time"] = 0
                state["pending_entry_side"] = "NONE"
                state["pending_entry_zone"] = None
                state["pending_entry_data"] = None
                state["pending_entry_price"] = 0.0
                return "FILLED"
            
            elif status in ('CANCELED', 'EXPIRED', 'REJECTED'):
                log.info(f"ℹ️  [{symbol}] Pending order already {status} by exchange.")
                state["pending_entry_order_id"] = None
                state["pending_entry_time"] = 0
                state["pending_entry_side"] = "NONE"
                state["pending_entry_zone"] = None
                state["pending_entry_data"] = None
                state["pending_entry_price"] = 0.0
                return "CANCELLED"
                
        except Exception as e:
            log.warning(f"⚠  [{symbol}] Pending order status check failed: {e}")
    else:
        # DRY RUN: Simulate fill check — if current price hits limit, simulate fill
        if entry_side == "BUY" and current_price <= limit_price:
            log.info(f"✅  [DRY RUN] [{symbol}] PENDING LIMIT FILLED! Price ${current_price} ≤ Limit ${limit_price}")
            pending_data = state.get("pending_entry_data", {})
            sl_dist = pending_data.get("sl_distance", 0)
            tp_dist = pending_data.get("tp_distance", 0)
            qty = pending_data.get("quantity", 0)
            
            # ★ AUDIT FIX: We're inside `if entry_side == "BUY"` block at L3290,
            # so the else branch was dead code. Calculate SL/TP for BUY only here.
            sl_price = _round_price(limit_price - sl_dist, symbol) if sl_dist > 0 else 0.0
            tp_price = _round_price(limit_price + tp_dist, symbol) if tp_dist > 0 else 0.0
            
            dry_run_positions[symbol] = {
                "side": entry_side,
                "qty": qty,
                "entry_price": limit_price,
                "sl_price": sl_price,
                "tp_price": tp_price if not DISABLE_HARD_TP else 0.0,
            }
            _save_dry_run_positions()
            log.info(f"📍  [DRY RUN] [{symbol}] Position registered: {entry_side} @ ${limit_price} | SL: ${sl_price} | TP: ${tp_price}")
            
            state["pending_entry_order_id"] = None
            state["pending_entry_time"] = 0
            state["pending_entry_side"] = "NONE"
            state["pending_entry_zone"] = None
            state["pending_entry_data"] = None
            state["pending_entry_price"] = 0.0
            return "FILLED"
        
        elif entry_side == "SELL" and current_price >= limit_price:
            log.info(f"✅  [DRY RUN] [{symbol}] PENDING LIMIT FILLED! Price ${current_price} ≥ Limit ${limit_price}")
            pending_data = state.get("pending_entry_data", {})
            sl_dist = pending_data.get("sl_distance", 0)
            tp_dist = pending_data.get("tp_distance", 0)
            qty = pending_data.get("quantity", 0)
            
            sl_price = _round_price(limit_price + sl_dist, symbol) if sl_dist > 0 else 0.0
            tp_price = _round_price(limit_price - tp_dist, symbol) if tp_dist > 0 else 0.0
            
            dry_run_positions[symbol] = {
                "side": entry_side,
                "qty": qty,
                "entry_price": limit_price,
                "sl_price": sl_price,
                "tp_price": tp_price if not DISABLE_HARD_TP else 0.0,
            }
            _save_dry_run_positions()
            log.info(f"📍  [DRY RUN] [{symbol}] Position registered: {entry_side} @ ${limit_price} | SL: ${sl_price} | TP: ${tp_price}")
            
            state["pending_entry_order_id"] = None
            state["pending_entry_time"] = 0
            state["pending_entry_side"] = "NONE"
            state["pending_entry_zone"] = None
            state["pending_entry_data"] = None
            state["pending_entry_price"] = 0.0
            return "FILLED"
    
    # ── Rule 2: TIME EXPIRY — Cancel if > 12 candles (1 hour) ──
    if elapsed_candles >= PENDING_ENTRY_MAX_CANDLES:
        log.warning(f"⏰  [{symbol}] PENDING LIMIT EXPIRED — {elapsed_candles} candles ({elapsed_s/60:.0f}min). Cancelling.")
        if not DRY_RUN:
            try:
                client.futures_cancel_order(symbol=symbol, orderId=order_id)
            except Exception as e:
                log.warning(f"⚠  [{symbol}] Cancel failed (may be already filled): {e}")
        try:
            send_telegram_alert(f"⏰ <b>LIMIT EXPIRED</b>\nCoin: {symbol}\nSide: {entry_side}\nWaited: {elapsed_candles} candles")
        except Exception:
            pass
        state["pending_entry_order_id"] = None
        state["pending_entry_time"] = 0
        state["pending_entry_side"] = "NONE"
        state["pending_entry_zone"] = None
        state["pending_entry_data"] = None
        state["pending_entry_price"] = 0.0
        return "CANCELLED"
    
    # ── Rule 3: OB INVALIDATION — Price broke the zone boundary ──
    if zone:
        zone_low = float(zone.get("zone_low", 0))
        zone_high = float(zone.get("zone_high", 0))
        
        if entry_side == "BUY" and current_price < zone_low:
            log.warning(f"🚫  [{symbol}] OB INVALIDATED — Price ${current_price:.4f} broke below zone_low ${zone_low:.4f}. Cancelling BUY limit.")
            if not DRY_RUN:
                try:
                    client.futures_cancel_order(symbol=symbol, orderId=order_id)
                except Exception as e:
                    log.warning(f"⚠  [{symbol}] Cancel failed: {e}")
            try:
                send_telegram_alert(f"🚫 <b>OB INVALIDATED</b>\nCoin: {symbol}\nPrice ${current_price:.4f} < Zone Low ${zone_low:.4f}")
            except Exception:
                pass
            state["pending_entry_order_id"] = None
            state["pending_entry_time"] = 0
            state["pending_entry_side"] = "NONE"
            state["pending_entry_zone"] = None
            state["pending_entry_data"] = None
            state["pending_entry_price"] = 0.0
            return "CANCELLED"
        
        elif entry_side == "SELL" and current_price > zone_high:
            log.warning(f"🚫  [{symbol}] OB INVALIDATED — Price ${current_price:.4f} broke above zone_high ${zone_high:.4f}. Cancelling SELL limit.")
            if not DRY_RUN:
                try:
                    client.futures_cancel_order(symbol=symbol, orderId=order_id)
                except Exception as e:
                    log.warning(f"⚠  [{symbol}] Cancel failed: {e}")
            try:
                send_telegram_alert(f"🚫 <b>OB INVALIDATED</b>\nCoin: {symbol}\nPrice ${current_price:.4f} > Zone High ${zone_high:.4f}")
            except Exception:
                pass
            state["pending_entry_order_id"] = None
            state["pending_entry_time"] = 0
            state["pending_entry_side"] = "NONE"
            state["pending_entry_zone"] = None
            state["pending_entry_data"] = None
            state["pending_entry_price"] = 0.0
            return "CANCELLED"
    
    # Still pending, log status
    if elapsed_candles % 3 == 0:  # Log every 3 candles to avoid spam
        log.info(f"⏳  [{symbol}] PENDING LIMIT: ${limit_price} ({entry_side}) │ {elapsed_candles}/12 candles │ {elapsed_s/60:.0f}min")
    
    return "PENDING"


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
            "trade_open_time": 0.0,
            "last_trade_time": 0,
            "last_entry_attempt_time": 0,  # ★ v34-fix3: Spam guard timestamp
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
            # ★ v24: Pending Limit Entry Tracking (50% OB Equilibrium)
            "pending_entry_order_id": None,      # Binance orderId for unfilled limit entry
            "pending_entry_time": 0,             # Timestamp when limit order was placed
            "pending_entry_side": "NONE",        # BUY or SELL
            "pending_entry_zone": None,          # {zone_high, zone_low} for invalidation check
            "pending_entry_data": None,          # Signal data snapshot for post-fill SL/TP
            "pending_entry_price": 0.0,          # The limit price placed
            # ★ v28.1: VETO COOLDOWN (prevents re-arming same direction after 4H veto)
            "veto_cooldown_until": 0,             # Timestamp when cooldown expires
            "veto_cooldown_dir": "NONE",          # Direction that was vetoed
        }
        visualizers[sym] = StateExporter(client, sym)

    scan_count = 0

    log.info("🟢  Bot is LIVE — Institutional Edition v8.0")
    log.info(f"    Pairs: {', '.join(SYMBOLS)} ({len(SYMBOLS)} coins)")
    log.info(f"    Loop: {LOOP_INTERVAL_S}s │ Lockout: {HARD_LOCKOUT_S}s │ Scan Delay: {SCAN_DELAY_S}s/coin")
    log.info(f"    ★ SL: {SL_ATR_MULT}×ATR │ TP: {TP_ATR_MULT}×ATR │ R:R = 1:2")
    log.info(f"    ★ {'GTX Post-Only (Maker)' if not DRY_RUN else 'DRY RUN (Simulated)'} │ Offset: {LIMIT_OFFSET_PCT}%")
    log.info(f"    ★ Dynamic 10% Margin │ DRY_RUN: {DRY_RUN} │ ADX Filter: ≥{ADX_ENTRY_MIN}")
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

    send_telegram_alert(f"🚀 <b>Bot Started - v20.1 (Dynamic 10% Margin + SMC Engine)</b>\nPairs: {len(SYMBOLS)}\nStarting Balance: ${daily_start_balance:.2f}")

    while True:
        try:
            today = datetime.now(timezone.utc).date()
            if today != daily_stats_date:
                # ★ v7.4: Send daily summary BEFORE resetting
                if daily_wins + daily_losses > 0:
                    current_bal = _get_account_balance(client)
                    wr = (daily_wins / (daily_wins + daily_losses) * 100) if (daily_wins + daily_losses) > 0 else 0
                    
                    if VIRTUAL_ACCOUNT:
                        current_bal = prop_state["current_balance"]
                        daily_pnl_str = f"${(prop_state['current_balance'] - prop_state['today_start_balance']):+.2f}"
                    else:
                        daily_pnl_str = f"${daily_pnl:+.2f}"
                        
                    send_telegram_alert(
                        f"📊 <b>DAILY REPORT — {daily_stats_date}</b>\n"
                        f"Trades: {daily_wins + daily_losses}\n"
                        f"Wins: {daily_wins} | Losses: {daily_losses}\n"
                        f"Win Rate: {wr:.1f}%\n"
                        f"Net PnL: {daily_pnl_str}\n"
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
                
            if not check_prop_rules():
                time.sleep(60)
                continue

            scan_count += 1
            now = time.time()
            
            # ★ v29.7: Evaluate Global Market Session (UTC)
            utc_hour = datetime.now(timezone.utc).hour
            is_high_volume_session = 12 <= utc_hour < 21  # NY Open (12-21 UTC) = Peak Volume. BD Time: 6PM-3AM
            
            if scan_count % 30 == 0:  # Periodically log the session state
                sess_desc = "HIGH LIQUIDITY (NY+London Overlap) | Strict SMC Rules Active" if is_high_volume_session else "LOW LIQUIDITY (Asian/Early London) | Filters Relaxed"
                log.info(f"🌍 [MARKET SESSION DETECTED] {sess_desc} | UTC Hour: {utc_hour}")

            # ── ★ GLOBAL CONCURRENCY LIMITER (v37: Max 1 position) ──
            global_open_trades_count = 0
            global_pending_orders = 0
            if DRY_RUN:
                global_open_trades_count = sum(1 for s in SYMBOLS if s in dry_run_positions)
            else:
                try:
                    all_pos = client.futures_position_information()
                    global_open_trades_count = sum(1 for p in all_pos if float(p.get('positionAmt', 0)) != 0 and p['symbol'] in SYMBOLS)
                    # ★ v37: Also count pending limit orders to prevent spam
                    all_orders = client.futures_get_open_orders()
                    global_pending_orders = len([o for o in all_orders if o['symbol'] in SYMBOLS])
                except Exception as e:
                    log.warning(f"Failed to fetch global positions: {e}")

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
                # ★ v31.2 FIX: Cooldown must NOT skip position management!
                # We set a flag here and block NEW entries later, but still manage open trades.
                in_loss_cooldown = state["loss_cooldown_until"] > now
                if in_loss_cooldown:
                    remaining = int(state["loss_cooldown_until"] - now)
                    if scan_count % 6 == 0:
                        log.info(f"🧊  [{symbol}] LOSS STREAK COOLDOWN — {remaining}s remaining ({state['consec_losses']} consecutive losses). Open positions still managed.")
                    visualizer.set_bot_status(f"LOSS COOL ({remaining}s)")

                # ── Position check → Manage Trailing TP ──────────────────────
                pos = _has_open_position(client, symbol)
                if pos is not None:
                    # Mark trade as ready to log outcome upon exit
                    if state["_trade_logged"]:
                        log.info(f"📊  [{symbol}] Open {pos['side']} position detected.")
                        state["last_side"] = pos["side"]  # Always track side
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
                # ★ AUDIT FIX: Skip if manage_trailing_tp already handled this closure
                if state.get("_trade_logged_by_manager"):
                    state["_trade_logged_by_manager"] = False  # Reset flag
                    state["_trade_logged"] = True
                    log.info(f"📊  [{symbol}] Closure already handled by trailing manager. Skipping duplicate.")
                    continue
                if not state["_trade_logged"]:
                    try:
                        outcome, pnl = _determine_trade_outcome(client, symbol)
                        daily_pnl += pnl

                        # ★ v31.2: Track consecutive losses per coin
                        # Break-even / scratch trades (tiny PnL near zero) should NOT count as losses
                        phase = state.get("phase", "INITIAL")
                        is_scratch = (abs(pnl) <= 1.0 and phase in ("BREAKEVEN", "TRAILING", "MANUAL_CLOSE"))
                        
                        if pnl > 0:
                            state["consec_losses"] = 0
                            daily_wins += 1
                            kelly_state["total_wins"] += 1
                            kelly_state["total_win_pnl"] += abs(pnl)
                            _save_kelly_state()
                        elif is_scratch:
                            # ★ v31.2: SCRATCH — Break-even exit, don't pollute Kelly stats
                            state["consec_losses"] = 0  # Reset streak (it wasn't a real loss)
                            log.info(f"🛡️  [{symbol}] SCRATCH EXIT (PnL ${pnl:.2f}, Phase: {phase}) — Not counted as win or loss.")
                        else:
                            state["consec_losses"] += 1
                            daily_losses += 1
                            kelly_state["total_losses"] += 1
                            kelly_state["total_loss_pnl"] += abs(pnl)
                            _save_kelly_state()
                            if state["consec_losses"] >= MAX_CONSEC_LOSSES:
                                state["loss_cooldown_until"] = time.time() + LOSS_COOLDOWN_S
                                log.warning(f"🧊  [{symbol}] {MAX_CONSEC_LOSSES} CONSECUTIVE LOSSES → Extra {LOSS_COOLDOWN_S}s cooldown activated.")
                        
                        # ── TELEGRAM ALERT: Trade Exit (with REASON) ──
                        streak_info = f"\nLoss Streak: {state['consec_losses']}/{MAX_CONSEC_LOSSES}" if state["consec_losses"] > 0 else ""
                        
                        phase = state.get("phase", "INITIAL")
                        
                        # ★ PROP: Deduct virtual fees for scratch trades before saving
                        if VIRTUAL_ACCOUNT and pnl <= 0.0 and pnl >= -1.0 and phase in ("BREAKEVEN", "TRAILING", "MANUAL_CLOSE"):
                            pnl = -0.05  # Flat 5 cents virtual fee for scratches
                            log.info(f"🛡️  [PROP] Applied -$0.05 virtual fee for SCRATCH exit.")
                            
                        # Update prop state directly
                        if VIRTUAL_ACCOUNT:
                            prop_state["current_balance"] += pnl
                            _save_prop_state()
                        
                        # ★ If PnL is extremely small negative (fees), but phase is BreakEven/Trailing, classify as Scratch (Neutral)
                        if pnl <= 0.0 and pnl >= -1.0 and phase in ("BREAKEVEN", "TRAILING", "MANUAL_CLOSE"):
                            res_emoji = "🛡 SCRATCH"
                        else:
                            res_emoji = "✅ WIN" if pnl > 0 else "❌ LOSS"
                        
                        # ★ v12: Determine close reason from state
                        trail_sl = state.get("current_trail_sl", 0.0)
                        last_side = state.get("last_side", "UNKNOWN")
                        override_reason = state.get("close_reason_override")
                        
                        if override_reason:
                            close_reason = override_reason
                        elif pnl > 0:
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
                        
                        if VIRTUAL_ACCOUNT:
                            progress_pct = ((prop_state['current_balance'] - INITIAL_BALANCE) / PROFIT_TARGET) * 100
                            daily_rem = DAILY_LOSS_LIMIT + (prop_state['current_balance'] - prop_state['today_start_balance'])
                            status_str = "✅ HEALTHY" if prop_state['current_balance'] > INITIAL_BALANCE else "⚠️ DRAWDOWN"
                            
                            prop_msg = (
                                f"\n\n📊 <b>Prop Challenge Update:</b>\n"
                                f"Current Balance: ${prop_state['current_balance']:.2f}\n"
                                f"Target Progress: ${(prop_state['current_balance'] - INITIAL_BALANCE):.2f} / ${PROFIT_TARGET:.2f} ({progress_pct:.1f}%)\n"
                                f"Today's PnL: ${prop_state['current_balance'] - prop_state['today_start_balance']:+.2f}\n"
                                f"Daily Limit Remaining: ${daily_rem:.2f}\n"
                                f"Status: {status_str}"
                            )
                        else:
                            prop_msg = f"\nToday's Net PnL: ${daily_pnl:.2f}{streak_info}"

                        send_telegram_alert(
                            f"{res_emoji} <b>Trade Closed</b>\n"
                            f"Coin: {symbol}\n"
                            f"Side: {last_side}\n"
                            f"Close Reason: {close_reason}\n"
                            f"Phase: {phase}\n"
                            f"Realized PnL: ${pnl:.2f}"
                            f"{prop_msg}"
                        )

                        # ── KILL SWITCH EVALUATION ──
                        # ★ v22: Skip kill switch in DRY RUN mode (balance is $0.02, any PnL triggers it)
                        if not DRY_RUN and daily_pnl < 0 and abs(daily_pnl) >= daily_start_balance * (MAX_DAILY_LOSS_PCT / 100.0):
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

                # ★ v34-fix3: Skip TradFi coins on weekends (markets closed Sat/Sun)
                utc_now = datetime.now(timezone.utc)
                if symbol in TRADFI_SYMBOLS and utc_now.weekday() >= 5:  # 5=Sat, 6=Sun
                    if scan_count % 30 == 0:  # Log rarely
                        log.info(f"📅  [{symbol}] TradFi WEEKEND SKIP — Market closed (day={utc_now.strftime('%A')})")
                    continue

                # ★ GLOBAL TRADE CAP: Max 5 positions + pending orders
                total_active = global_open_trades_count + global_pending_orders
                if total_active >= 5:  # ★ v42: Max 5 open positions (was 2)
                    if scan_count % 6 == 0:
                        log.info(f"⚓  [{symbol}] Global Cap Reached ({global_open_trades_count} pos + {global_pending_orders} orders = {total_active}/5). Skipping.")
                    visualizer.set_bot_status("CAP PAUSE")
                    visualizer.update()
                    time.sleep(1)  # small delay before moving to next symbol
                    continue

                # ★ v34-fix3: PER-SYMBOL SPAM GUARD — prevent re-entry on same coin within 10 min
                last_entry_time = state.get("last_entry_attempt_time", 0)
                spam_cooldown = 600  # 10 minutes
                if (time.time() - last_entry_time) < spam_cooldown:
                    remaining = int(spam_cooldown - (time.time() - last_entry_time))
                    if scan_count % 6 == 0:
                        log.info(f"🚫  [{symbol}] SPAM GUARD — {remaining}s until re-entry allowed.")
                    continue

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
                fr_val = signal_data.get("funding_rate", 0.0)

                # ★ v24: CHECK PENDING LIMIT ENTRY ORDERS (Auto-Cancel Manager)
                pending_result = _check_pending_entry_order(client, symbol, state, current_price)
                if pending_result == "PENDING":
                    # Still waiting for pullback fill — skip all other logic for this coin
                    continue
                elif pending_result == "FILLED":
                    # Order just filled! Set up trade management state
                    pending_data = state.get("pending_entry_data") or {}
                    state["last_signal_data"] = pending_data.get("signal_data")
                    state["last_side"] = pending_data.get("side", "UNKNOWN")
                    state["_trade_logged"] = False
                    state["original_qty"] = pending_data.get("quantity", 0)
                    state["phase"] = "INITIAL"
                    state["best_price"] = 0.0
                    state["current_trail_sl"] = 0.0
                    state["trade_open_time"] = time.time()
                    log.info(f"🟢  [{symbol}] PENDING → ACTIVE — Trade management engaged.")
                    continue
                # elif "CANCELLED" or "NONE" — proceed normally

                # ── ★ FUNDING RATE ENTRY GATE ──
                # If BUY and funding is strongly positive (> 0.03%), skip (costly to hold long)
                if signal == "BUY" and fr_val > 0.0003:
                    log.info(f"🚫  [{symbol}] FUNDING GATE — FR {fr_val*100:.4f}% > 0.03% │ Too expensive to hold LONG. Skipping BUY.")
                    signal = "WAIT"
                elif signal == "SELL" and fr_val < -0.0001:
                    log.info(f"🚫  [{symbol}] FUNDING GATE — FR {fr_val*100:.4f}% < -0.01% │ Too expensive to hold SHORT. Skipping SELL.")
                    signal = "WAIT"

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

                wq_just_passed = False
                # ── ★ v20: SMC WAIT QUEUE PROCESSOR (re-check OB/FVG zone retest) ──
                if state["wait_queue_signal"] != "NONE":
                    signal = "WAIT"  # Prevent new indicators from overwriting our wait setup
                    
                if state["wait_queue_signal"] != "NONE" and state["armed_signal"] == "NONE" and not in_loss_cooldown:
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
                            wq_just_passed = True
                            log.info(f"🔄  [{symbol}] WAIT QUEUE → SMC PASS after {wq_candles} candles! Executing {wq_dir}...")
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
                if signal in ("BUY", "SELL") and state["armed_signal"] == "NONE" and not in_loss_cooldown:
                    # ★ v28.1: Check veto cooldown before arming
                    if state["veto_cooldown_until"] > 0 and now < state["veto_cooldown_until"] and state["veto_cooldown_dir"] == signal:
                        remaining_cd = int(state["veto_cooldown_until"] - now)
                        if scan_count % 5 == 0:  # Log every 5th scan to reduce spam
                            log.info(f"⏳  [{symbol}] VETO COOLDOWN: {signal} blocked for {remaining_cd}s (4H trend opposes)")
                    else:
                        # Clear expired cooldown
                        if now >= state["veto_cooldown_until"]:
                            state["veto_cooldown_until"] = 0
                            state["veto_cooldown_dir"] = "NONE"
                        log.info(f"🔫  [{symbol}] SIGNAL ARMED ({signal}) │ Waiting max 4 mins for Vol Burst...")
                        state["armed_signal"] = signal
                        state["armed_time"] = now
                        state["armed_signal_data"] = signal_data

                # ── Process Armed State (Wait for Micro-Momentum) ────────────
                if state["armed_signal"] != "NONE":
                    armed_age = now - state["armed_time"]
                    
                    if armed_age > 420:  # 7 minutes timeout (★ v36: Increased from 4 mins to catch slightly delayed vol bursts)
                        log.warning(f"⌛  [{symbol}] ARMED CANCELLED │ No volume burst within 7 mins. Setup stale.")
                        state["armed_signal"] = "NONE"
                        state["armed_time"] = 0
                        state["armed_signal_data"] = None
                    else:
                        armed_dir = state["armed_signal"]
                        if is_high_volume_session:
                            burst_detected = True if wq_just_passed else _check_volume_burst(client, symbol, armed_dir)
                        else:
                            burst_detected = True  # ★ v29.6: Bypassed volume wait during low-liquidity Asian/Sydney sessions
                        
                        if burst_detected:
                            log.info(f"💥  [{symbol}] VOLUME BURST TRIGGERED! Running Pre-Flight Check...")
                            
                            # ── ★ ADVANCED PRE-FLIGHT CHECK ──
                            armed_data = state.get("armed_signal_data")
                            armed_score = int(armed_data.get("score", 0)) if armed_data else 0
                            passed, reason, smc_zone = AdvancedExecutionValidator.validate(client, symbol, armed_dir, armed_score)
                            if smc_zone:
                                signal_data["smc_zone"] = smc_zone
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
                            
                            # ★★★ SMC MICRO-REVERSAL (SWEEP) CONFIRMATION
                            # SMC entry requires micro-structure break to confirm rejection:
                            # BUY: Latest closed body must close ABOVE the previous closed body.
                            # SELL: Latest closed body must close BELOW the previous closed body.
                            try:
                                m5_momentum = client.futures_klines(symbol=symbol, interval='5m', limit=4)  # ★ v34-fix: 5m momentum (original)
                                if m5_momentum and len(m5_momentum) >= 3:
                                    closed_klines = m5_momentum[:-1]  # Exclude unfinished live candle
                                    curr_open = float(closed_klines[-1][1])
                                    curr_close = float(closed_klines[-1][4])
                                    prev_close = float(closed_klines[-2][4])
                                    
                                    if armed_dir == "BUY":
                                        # Relaxed: Must be a green candle OR closed higher than prev close
                                        has_momentum = (curr_close > curr_open) or (curr_close > prev_close)
                                        if not has_momentum:
                                            log.warning(f"🚫  [{symbol}] MOMENTUM REJECT: BUY but 5m candle is red and failed to break previous close.")
                                            visualizer.record_rejection(f"MOMENTUM: Failed micro-reversal for BUY")
                                            state["armed_signal"] = "NONE"
                                            state["armed_time"] = 0
                                            state["armed_signal_data"] = None
                                            continue
                                    else:  # SELL
                                        # Relaxed: Must be a red candle OR closed lower than prev close
                                        has_momentum = (curr_close < curr_open) or (curr_close < prev_close)
                                        if not has_momentum:
                                            log.warning(f"🚫  [{symbol}] MOMENTUM REJECT: SELL but 5m candle is green and failed to break previous close.")
                                            visualizer.record_rejection(f"MOMENTUM: Failed micro-reversal for SELL")
                                            state["armed_signal"] = "NONE"
                                            state["armed_time"] = 0
                                            state["armed_signal_data"] = None
                                            continue
                                    log.info(f"✅  [{symbol}] SMC MICRO-REVERSAL CONFIRMED ✔")
                            except Exception as mom_err:
                                log.warning(f"⚠  [{symbol}] Momentum check failed: {mom_err} — Proceeding anyway.")
                            
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

                            # ── ★ v25: MULTI-TIMEFRAME DIRECTIONAL CONFLUENCE (MTDC) GATE ──
                            # 4H = Hard Veto │ 1H + 15m + OI + FR = Confidence Score
                            if MTFA_ENABLED:
                                confidence, conf_breakdown, veto, veto_reason = _calculate_directional_confidence(
                                    client, symbol, armed_dir, state.get("armed_signal_data")
                                )
                                if veto:
                                    log.warning(f"🚫  [{symbol}] {veto_reason}")
                                    visualizer.record_rejection(veto_reason)
                                    # ★ v28.1: Set 15-minute cooldown for this direction on this coin
                                    state["veto_cooldown_until"] = time.time() + 900  # 15 min
                                    state["veto_cooldown_dir"] = armed_dir
                                    send_telegram_alert(
                                        f"🚫 <b>4H HARD VETO</b>\nCoin: {symbol}\nSignal: {armed_dir}\n"
                                        f"<i>{veto_reason}</i>\n"
                                        f"⏳ Cooldown: 15 min (won't retry this direction)"
                                    )
                                    state["armed_signal"] = "NONE"
                                    state["armed_time"] = 0
                                    state["armed_signal_data"] = None
                                    continue
                                elif confidence < MTDC_MIN_CONFIDENCE:
                                    mtdc_reason = f"MTDC LOW: {confidence*100:.0f}% < {MTDC_MIN_CONFIDENCE*100:.0f}% │ {' │ '.join(conf_breakdown)}"
                                    log.warning(f"🧭  [{symbol}] {mtdc_reason}")
                                    visualizer.record_rejection(mtdc_reason)
                                    state["armed_signal"] = "NONE"
                                    state["armed_time"] = 0
                                    state["armed_signal_data"] = None
                                    continue
                                else:
                                    log.info(f"🧭  [{symbol}] MTDC PASS: {confidence*100:.0f}% │ {' │ '.join(conf_breakdown)}")

                            # ── ★ v20: SMC ENTRY VALIDATION GATE ──
                            smc_zone = None  # ★ Fix UnboundLocalError when validation bypass is used
                            if ENTRY_VALIDATION_ENABLED:
                                armed_data = state.get("armed_signal_data")
                                armed_score = int(armed_data.get("score", 0)) if armed_data else 0
                                smc_result, smc_reason, smc_zone = _smc_entry_validate(client, symbol, armed_dir, armed_score)
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
                                    # ★ v36: Only override WAIT if trade is WITH the H1 trend
                                    # Counter-trend WAIT overrides led to dead cat bounce losses (e.g. ORDI BUY in H1 BEAR)
                                    h1_tr = state.get("armed_signal_data", {}).get("h1_trend", "UNKNOWN")
                                    armed_dir_check = state.get("armed_signal", "NONE")
                                    is_counter_trend = (h1_tr == "BEARISH" and armed_dir_check == "BUY") or \
                                                       (h1_tr == "BULLISH" and armed_dir_check == "SELL")
                                    if is_counter_trend:
                                        log.warning(f"🚫  [{symbol}] SMC WAIT — Counter-trend {armed_dir_check} (H1: {h1_tr}). NOT overriding. Queued.")
                                        state["armed_signal"] = "NONE"
                                        state["armed_time"] = 0
                                        state["armed_signal_data"] = None
                                        continue
                                    else:
                                        # ★ v39: SMART WAIT OVERRIDE — If price is very close to zone, proceed anyway
                                        # This prevents the "double-check WAIT" from blocking near-zone trades
                                        zone_h = float(smc_zone.get("zone_high", 0)) if isinstance(smc_zone, dict) else 0
                                        zone_l = float(smc_zone.get("zone_low", 0)) if isinstance(smc_zone, dict) else 0
                                        
                                        if zone_h > 0 and zone_l > 0:
                                            # Check if price is within 1.5 ATR of zone edge
                                            exc_atr_check = state.get("armed_signal_data", {}).get("atr", 0)
                                            if exc_atr_check > 0:
                                                if armed_dir == "BUY":
                                                    dist_to_zone = current_price - zone_h
                                                else:
                                                    dist_to_zone = zone_l - current_price
                                                    
                                                if dist_to_zone <= 1.5 * exc_atr_check:
                                                    log.info(f"🎯  [{symbol}] SMART OVERRIDE: Price ${current_price:.4f} is only {dist_to_zone:.4f} from zone (< 1.5 ATR). Proceeding!")
                                                    # Don't block, let it flow to execution below
                                                else:
                                                    log.warning(f"⏳  [{symbol}] SMC WAIT — Price ${current_price:.4f} too far from zone ({dist_to_zone:.4f} > 1.5 ATR). Queued.")
                                                    state["armed_signal"] = "NONE"
                                                    state["armed_time"] = 0
                                                    state["armed_signal_data"] = None
                                                    continue
                                            else:
                                                log.warning(f"⏳  [{symbol}] SMC WAIT — No ATR data. Queued.")
                                                state["armed_signal"] = "NONE"
                                                state["armed_time"] = 0
                                                state["armed_signal_data"] = None
                                                continue
                                        else:
                                            log.warning(f"⏳  [{symbol}] SMC WAIT — No zone data. Queued.")
                                            state["armed_signal"] = "NONE"
                                            state["armed_time"] = 0
                                            state["armed_signal_data"] = None
                                            continue
                                # else: "PASS" — continue to execution

                            # Restore data for execution
                            # ★ v22: Use SMC-flipped direction if available
                            exc_signal = smc_zone.get("smc_direction", armed_dir) if isinstance(smc_zone, dict) else armed_dir
                            
                            # Protect against "NONE" string being truthy
                            if isinstance(smc_zone, str) and smc_zone == "NONE":
                                smc_zone = None

                            exc_data = state["armed_signal_data"].copy()
                            if smc_zone: 
                                exc_data["smc_zone"] = smc_zone  # ★ Inject SMC mathematical SL geometry into execution data
                                # ★ v25: Liquidity Sweep Premium (+5 points)
                                if isinstance(smc_zone, dict) and smc_zone.get("has_sweep"):
                                    exc_data["score"] += 5
                                    exc_data["score_breakdown"].append("💧 LIQ SWEEP Premium: +5")
                            exc_price = current_price  # Use latest price, not the one from 2 mins ago
                            exc_atr = exc_data["atr"]
                            
                            # ★ CORRELATION FILTER
                            btc_corr = exc_data.get("btc_correlation", 0.0)
                            size_multiplier = 1.0

                            if abs(btc_corr) > CORRELATION_THRESHOLD and symbol != "BTCUSDT":
                                # ★ FIX: Direct BTC position check (replaces missing _check_btc_same_direction)
                                btc_pos = _has_open_position(client, "BTCUSDT")
                                btc_same_dir = False
                                if btc_pos:
                                    btc_same_dir = (btc_pos["side"] == exc_signal)  # Same direction = correlated risk
                                if btc_same_dir:
                                    if CORR_ACTION == "SKIP":
                                        log.info(f"🚫  [{symbol}] CORR FILTER SKIP — BTC Corr: {btc_corr:+.4f} > {CORRELATION_THRESHOLD} & BTC {exc_signal} running")
                                        state["armed_signal"] = "NONE"
                                        time.sleep(2)
                                        continue
                                    else:  # REDUCE
                                        size_multiplier = CORR_REDUCE_SIZE_PCT / 100.0
                                        log.info(f"⚠  [{symbol}] CORR FILTER REDUCE — Size: {CORR_REDUCE_SIZE_PCT}% │ BTC Corr: {btc_corr:+.4f}")

                            # ★ v34-fix3: Set spam guard BEFORE execute (prevents retry spam)
                            state["last_entry_attempt_time"] = time.time()
                            
                            success, entry_qty = execute_trade(
                                client, symbol, exc_signal, exc_price, exc_atr, exc_data,
                                size_multiplier=size_multiplier, is_micro_scalp=is_micro_scalp
                            )
                            
                            # ★ v24: Handle PENDING return (GTC limit placed but not filled yet)
                            if success == "PENDING" and isinstance(entry_qty, dict):
                                pending_info = entry_qty  # entry_qty is actually the pending_info dict
                                state["pending_entry_order_id"] = pending_info["order_id"]
                                state["pending_entry_time"] = pending_info["placed_time"]
                                state["pending_entry_side"] = pending_info["side"]
                                state["pending_entry_zone"] = pending_info.get("zone")
                                state["pending_entry_data"] = pending_info
                                state["pending_entry_price"] = pending_info["limit_price"]
                                log.info(f"📋  [{symbol}] PENDING ORDER REGISTERED — Monitoring for fill/cancel.")
                            elif success:
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
        # ★ v22.2: DRY RUN — use simulated PnL instead of Binance API
        if DRY_RUN:
            sim_pnl = dry_run_last_pnl.pop(symbol, 0.0)
            outcome = 1 if sim_pnl > 0 else 0
            result_emoji = "✅" if outcome == 1 else "❌"
            log.info(f"🤖  [{symbol}] Trade Result: {result_emoji} Sim PnL: ${sim_pnl:.4f} | Fees: $0.00 (DRY RUN)")
            return outcome, sim_pnl

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
