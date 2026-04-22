"""
================================================================================
  StateExporter.py — Headless JSON State Publisher v2.0
  Replaces the desktop matplotlib Visualizer with a lightweight JSON exporter.
  Writes live_state.json every update cycle for the Streamlit dashboard to read.
  VPS-safe: No GUI, no display, no matplotlib import.
  
  v2.0: Added position PnL, trailing SL, bot status, and total PnL tracking.
================================================================================
"""

import os
import json
import time
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone

from binance.client import Client

log = logging.getLogger("InstitutionalBot")

# ─── CONFIGURATION ──────────────────────────────────────────────────────────
KLINE_INTERVAL = Client.KLINE_INTERVAL_5MINUTE
KLINE_LIMIT = 100


class StateExporter:
    """
    Headless replacement for LiveVisualizer.
    
    Instead of rendering a matplotlib chart, this class:
    1. Fetches fresh M5 candles from Binance
    2. Calculates overlay levels (POC, Fib GP, Liquidity Zones)
    3. Tracks live position PnL, trailing SL, and bot status
    4. Writes everything to live_state.json atomically
    
    The Streamlit dashboard.py reads this file to render the chart.
    API-compatible with LiveVisualizer (drop-in replacement).
    """

    def __init__(self, client: Client, symbol: str):
        self.client = client
        self.symbol = symbol
        self._last_signal_data = {}
        self._position_data = {}       # Live position PnL/SL info
        self._bot_status = "SCANNING"  # SCANNING, IN_TRADE, LOCKOUT, TRAILING
        self._scan_count = 0
        self._total_pnl = 0.0
        self._trade_count = 0
        self._win_count = 0
        self._reject_count = 0        # ★ v20: Track rejected trades
        self._saved_amount = 0.0      # ★ v20: Track estimated avoided losses
        self.state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"live_state_{self.symbol}.json")
        self.pnl_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"pnl_history_{self.symbol}.json")
        self._load_pnl_history()
        log.info(f"📡  StateExporter v2.0 initialized │ Symbol: {self.symbol} │ Output: {os.path.basename(self.state_file)}")

    # ─── PNL HISTORY PERSISTENCE ────────────────────────────────────────
    def _load_pnl_history(self):
        """Load cumulative PnL from disk."""
        try:
            if os.path.exists(self.pnl_file):
                with open(self.pnl_file, "r") as f:
                    data = json.load(f)
                    self._total_pnl = data.get("total_pnl", 0.0)
                    self._trade_count = data.get("trade_count", 0)
                    self._win_count = data.get("win_count", 0)
                    self._reject_count = data.get("reject_count", 0)
                    self._saved_amount = data.get("saved_amount", 0.0)
                    self._recent_trades = data.get("recent_trades", [])
                    log.info(f"📡  PnL History [{self.symbol}] loaded: ${self._total_pnl:.2f} over {self._trade_count} trades")
        except Exception:
            pass
        # ★ AUDIT FIX: Ensure _recent_trades always exists (even if load fails or key is missing)
        if not hasattr(self, '_recent_trades') or self._recent_trades is None:
            self._recent_trades = []

    def _save_pnl_history(self):
        """Persist cumulative PnL to disk."""
        try:
            with open(self.pnl_file, "w") as f:
                json.dump({
                    "total_pnl": round(self._total_pnl, 4),
                    "trade_count": self._trade_count,
                    "win_count": self._win_count,
                    "reject_count": self._reject_count,
                    "saved_amount": round(self._saved_amount, 2),
                    "recent_trades": getattr(self, '_recent_trades', []),
                }, f, indent=2)
        except Exception:
            pass

    def record_trade_result(self, pnl_usdt: float, side: str = "UNKNOWN"):
        """Called after each trade closes to update cumulative PnL."""
        self._total_pnl += pnl_usdt
        self._trade_count += 1
        if pnl_usdt > 0:
            self._win_count += 1
            
        trade_record = {
            "trade_no": self._trade_count,
            "side": side,
            "pnl": round(pnl_usdt, 4),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        if not hasattr(self, '_recent_trades'):
            self._recent_trades = []
        self._recent_trades.insert(0, trade_record)
        self._recent_trades = self._recent_trades[:20]  # Keep last 20 trades
        
        self._save_pnl_history()
        log.info(f"📡  PnL updated: ${pnl_usdt:+.2f} ({side}) │ Total: ${self._total_pnl:.2f} │ {self._trade_count} trades")

    def record_rejection(self, reason: str, est_sl_loss: float = 2.0):
        """★ v20: Record a rejected signal (e.g. from MTFA or 3-Layer) to track avoided losses."""
        self._reject_count += 1
        self._saved_amount += est_sl_loss
        self._save_pnl_history()
        # Internal tracking, no need to log here since main.py already logs the rejection

    # ─── STATUS SETTERS (called from main.py) ───────────────────────────
    def set_signal_data(self, signal_data: dict):
        """Store latest signal data for export."""
        self._last_signal_data = signal_data

    def set_bot_status(self, status: str):
        """Update bot status: SCANNING, IN_TRADE, LOCKOUT, TRAILING."""
        self._bot_status = status

    def set_scan_count(self, count: int):
        """Update current scan count."""
        self._scan_count = count

    def set_position_data(self, pos_data: dict):
        """
        Update live position data for dashboard display.
        Called from TTP manager every check cycle.
        
        pos_data keys: side, entry_price, mark_price, pnl_pct, pnl_usdt,
                       trailing_active, best_price, trail_sl, trail_distance, current_atr
        """
        self._position_data = pos_data

    def clear_position_data(self):
        """Clear position data when trade closes."""
        self._position_data = {}

    # ─── CANDLE & OVERLAY CALCULATIONS ──────────────────────────────────
    def _fetch_candles(self) -> pd.DataFrame:
        """Fetch fresh M5 candles from Binance."""
        raw = self.client.futures_klines(
            symbol=self.symbol, interval=KLINE_INTERVAL, limit=KLINE_LIMIT,
        )
        df = pd.DataFrame(raw, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore',
        ])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms')
        return df

    @staticmethod
    def _calc_fib_levels(df: pd.DataFrame) -> tuple:
        macro_high = df['high'].max()
        macro_low = df['low'].min()
        diff = macro_high - macro_low
        if diff == 0:
            return None, None
        lvl_05 = macro_low + 0.5 * diff
        lvl_0618 = macro_low + 0.382 * diff
        return float(min(lvl_05, lvl_0618)), float(max(lvl_05, lvl_0618))

    @staticmethod
    def _calc_poc(df: pd.DataFrame, num_bins: int = 50) -> tuple:
        tp = (df['high'] + df['low'] + df['close']) / 3.0
        bin_edges = np.linspace(df['low'].min(), df['high'].max(), num_bins + 1)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
        indices = np.clip(np.digitize(tp.values, bin_edges) - 1, 0, num_bins - 1)
        vol_profile = np.zeros(num_bins)
        for i, v in zip(indices, df['volume'].values):
            vol_profile[i] += v
        sorted_idx = np.argsort(vol_profile)[::-1]
        poc = float(bin_centers[sorted_idx[0]])
        mag1 = poc
        mag2 = float(bin_centers[sorted_idx[1]]) if len(sorted_idx) > 1 else poc
        return poc, mag1, mag2

    @staticmethod
    def _calc_sweep(df: pd.DataFrame, lookback: int = 24) -> tuple:
        if len(df) < lookback + 1:
            return 0.0, 0.0
        r = df.iloc[-(lookback + 1):-1]
        return float(r['high'].max()), float(r['low'].min())

    # ─── MAIN UPDATE (writes live_state.json) ───────────────────────────
    def update(self):
        """
        Fetch fresh data, calculate overlays, and write live_state.json.
        Drop-in replacement for LiveVisualizer.update().
        """
        try:
            df = self._last_signal_data.get("klines_df")
            if df is not None and not df.empty:
                if 'timestamp' not in df.columns:
                    df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms')
            else:
                df = self._fetch_candles()
                
            if df is None or df.empty:
                return

            poc, mag1, mag2 = self._calc_poc(df)
            gp_low, gp_high = self._calc_fib_levels(df)
            rh, rl = self._calc_sweep(df)

            # Build the state payload
            state = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "symbol": self.symbol,

                # ── Bot status ─────────────────────────────────────────────
                "bot": {
                    "status": self._bot_status,
                    "scan_count": self._scan_count,
                    "total_pnl": round(self._total_pnl, 2),
                    "trade_count": self._trade_count,
                    "win_count": self._win_count,
                    "win_rate": round(self._win_count / max(self._trade_count, 1) * 100, 1),
                    "reject_count": self._reject_count,
                    "saved_amount": round(self._saved_amount, 2),
                    "recent_trades": getattr(self, '_recent_trades', []),
                },

                # ── Candle data (last 100 M5 candles) ─────────────────────
                "candles": {
                    "timestamps": df['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S').tolist(),
                    "open": df['open'].tolist(),
                    "high": df['high'].tolist(),
                    "low": df['low'].tolist(),
                    "close": df['close'].tolist(),
                    "volume": df['volume'].tolist(),
                },

                # ── Overlay levels ─────────────────────────────────────────
                "overlays": {
                    "poc": poc,
                    "magnet_1": mag1,
                    "magnet_2": mag2,
                    "gp_low": gp_low,
                    "gp_high": gp_high,
                    "range_high": rh,
                    "range_low": rl,
                },

                # ── Signal engine metrics ──────────────────────────────────
                "signal": {
                    "direction": self._last_signal_data.get("signal", "NONE"),
                    "score": self._last_signal_data.get("score", 0),
                    "score_threshold": self._last_signal_data.get("score_threshold", 9),
                    "max_score": self._last_signal_data.get("max_score", 0),
                    "score_breakdown": self._last_signal_data.get("score_breakdown", []),
                    "waiting_for": self._last_signal_data.get("waiting_for", []),
                    "regime": self._last_signal_data.get("regime", "UNKNOWN"),
                    "h1_trend": self._last_signal_data.get("h1_trend", "UNKNOWN"),
                    "adx": self._last_signal_data.get("adx", 0.0),
                    "rsi": self._last_signal_data.get("rsi", 50.0),
                    "atr": self._last_signal_data.get("atr", 0.0),
                    "current_price": self._last_signal_data.get("current_price", 0.0),
                    "fibo_gp_active": self._last_signal_data.get("fibo_gp_active", False),
                },

                # ── Live position data (PnL + trailing SL) ────────────────
                "position": {
                    "active": bool(self._position_data),
                    "side": self._position_data.get("side", ""),
                    "entry_price": self._position_data.get("entry_price", 0.0),
                    "mark_price": self._position_data.get("mark_price", 0.0),
                    "pnl_pct": self._position_data.get("pnl_pct", 0.0),
                    "pnl_usdt": self._position_data.get("pnl_usdt", 0.0),
                    "phase": self._position_data.get("phase", "INITIAL"),
                    "tp1_hit": self._position_data.get("tp1_hit", False),
                    "tp2_hit": self._position_data.get("tp2_hit", False),
                    "remaining_pct": self._position_data.get("remaining_pct", 100),
                    "best_price": self._position_data.get("best_price", 0.0),
                    "trail_sl": self._position_data.get("trail_sl", 0.0),
                    "trail_distance": self._position_data.get("trail_distance", 0.0),
                    "current_atr": self._position_data.get("current_atr", 0.0),
                },

                # ── Derivative data ───────────────────────────────────────
                "derivatives": {
                    "open_interest": self._last_signal_data.get("open_interest", 0.0),
                    "funding_rate": self._last_signal_data.get("funding_rate", 0.0),
                    "oi_trend": self._last_signal_data.get("oi_trend", "FLAT"),
                    "price_trend": self._last_signal_data.get("price_trend", "FLAT"),
                    "btc_correlation": self._last_signal_data.get("btc_correlation", 0.0),
                },

                # ── ML filter metrics ─────────────────────────────────────
                "ml": {
                    "win_prob": self._last_signal_data.get("ml_win_prob", 1.0),
                    "vetoed": self._last_signal_data.get("ml_vetoed", False),
                    "stats": self._last_signal_data.get("ml_stats", {}),
                },
            }

            try:
                # Write to temp file then rename for atomicity to grab clean reads in dashboard
                tmp_file = f"{self.state_file}.tmp"
                with open(tmp_file, "w") as f:
                    json.dump(state, f, indent=2)
                os.replace(tmp_file, self.state_file)
            except IOError as e:
                log.warning(f"⚠  State dump failed: {e}")

        except Exception as e:
            log.warning(f"⚠  StateExporter write failed: {e}")

    def pause(self, interval: float):
        """Drop-in replacement for LiveVisualizer.pause()."""
        time.sleep(interval)
