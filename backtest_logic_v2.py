"""
=============================================================================
  BACKTEST V2 — Optimized Strategy with SL/TP (1:2 R:R) + Maker Fees
=============================================================================
  Changes from V1:
    FIX 1: Added ATR-based Stop-Loss & Take-Profit (1:2 Risk:Reward ratio)
    FIX 2: Reduced fees from 0.1% (Taker) → 0.02% (Maker/Limit Orders)
  
  Runs BOTH V1 and V2 side-by-side for comparison.
=============================================================================
"""

import yfinance as yf
import pandas as pd
import numpy as np
import vectorbt as vbt

# ─────────────────────────────────────────────────────────────
# Indicator Calculations
# ─────────────────────────────────────────────────────────────

def calculate_rsi(data, window=14):
    """Calculates Wilder's RSI."""
    delta = data.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=window - 1, adjust=False).mean()
    ema_down = down.ewm(com=window - 1, adjust=False).mean()
    rs = ema_up / ema_down
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_atr(high, low, close, window=14):
    """Calculates Average True Range (ATR)."""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(span=window, adjust=False).mean()
    return atr


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  BACKTEST V2: Optimized Strategy — SL/TP + Maker Fees")
    print("=" * 60)

    # ── Step 1: Data Download ───────────────────────────────
    print("\n📥 Fetching 1 year of BTC-USD data (1h timeframe)...")
    df = yf.download("BTC-USD", period="1y", interval="1h")

    if df.empty:
        print("❌ Failed to download data. Try again later.")
        return

    # Handle yfinance multi-index columns
    if isinstance(df.columns, pd.MultiIndex):
        close = df['Close'].squeeze()
        high = df['High'].squeeze()
        low = df['Low'].squeeze()
    else:
        close = df['Close']
        high = df['High']
        low = df['Low']

    close = close.dropna()
    high = high.reindex(close.index)
    low = low.reindex(close.index)

    # ── Step 2: Indicator Calculation ───────────────────────
    print("📊 Calculating indicators (EMA-200, RSI-14, ATR-14)...")
    ema200 = close.ewm(span=200, adjust=False).mean()
    rsi14 = calculate_rsi(close, 14)
    atr14 = calculate_atr(high, low, close, 14)

    # ── Step 3: Signal Generation (same as V1) ──────────────
    print("🎯 Generating entry/exit signals...")

    # Long Entry: Price > 200 EMA AND RSI crosses above 40
    long_trend = close > ema200
    rsi_cross_above_40 = (rsi14.shift(1) < 40) & (rsi14 >= 40)
    entries = long_trend & rsi_cross_above_40

    # Short Entry: Price < 200 EMA AND RSI crosses below 60
    short_trend = close < ema200
    rsi_cross_below_60 = (rsi14.shift(1) > 60) & (rsi14 <= 60)
    short_entries = short_trend & rsi_cross_below_60

    # RSI-based exits (kept as backup / trailing logic)
    exits = (rsi14.shift(1) < 70) & (rsi14 >= 70)
    short_exits = (rsi14.shift(1) > 30) & (rsi14 <= 30)

    # ── Step 4: ATR-based dynamic SL/TP ─────────────────────
    # Risk:Reward = 1:2
    # SL = 1.5 × ATR (as % of price)
    # TP = 3.0 × ATR (as % of price) → 2× the SL
    sl_pct = (1.5 * atr14) / close   # Dynamic SL percentage per bar
    tp_pct = (3.0 * atr14) / close   # Dynamic TP percentage per bar (2× SL)

    # ── Step 5: Run BOTH portfolios ─────────────────────────
    print("\n⚡ Running V1 (Original) simulation...")
    pf_v1 = vbt.Portfolio.from_signals(
        close=close,
        entries=entries,
        exits=exits,
        short_entries=short_entries,
        short_exits=short_exits,
        init_cash=10000,
        fees=0.001,          # 0.1% Taker fee
        slippage=0.0005,     # 0.05%
        freq='1h'
    )

    print("⚡ Running V2 (Optimized) simulation...")
    pf_v2 = vbt.Portfolio.from_signals(
        close=close,
        entries=entries,
        exits=exits,
        short_entries=short_entries,
        short_exits=short_exits,
        init_cash=10000,
        fees=0.0002,         # FIX 2: 0.02% Maker fee (Limit orders)
        slippage=0.0001,     # Reduced slippage with limit orders
        sl_stop=sl_pct,      # FIX 1: Dynamic ATR-based Stop-Loss
        tp_stop=tp_pct,      # FIX 1: Dynamic ATR-based Take-Profit (2× SL)
        freq='1h'
    )

    # ── Step 6: Extract Stats ───────────────────────────────
    stats_v1 = pf_v1.stats()
    stats_v2 = pf_v2.stats()

    # Helper to safely extract stat
    def get_stat(stats, key, fmt=".2f"):
        try:
            val = stats[key]
            return f"{val:{fmt}}"
        except KeyError:
            return "N/A"

    # ── Step 7: Print Side-by-Side Comparison ───────────────
    print("\n")
    print("╔" + "═" * 62 + "╗")
    print("║" + "  V1 vs V2 — HEAD-TO-HEAD COMPARISON".center(62) + "║")
    print("╠" + "═" * 62 + "╣")

    comparison_keys = [
        ("Total Return [%]",           "Total Return",          "%"),
        ("Benchmark Return [%]",       "Benchmark Return",      "%"),
        ("Total Fees Paid",            "Total Fees Paid",       "$"),
        ("Max Drawdown [%]",           "Max Drawdown",          "%"),
        ("Max Drawdown Duration",      "Max DD Duration",       ""),
        ("Total Trades",               "Total Trades",          ""),
        ("Total Closed Trades",        "Closed Trades",         ""),
        ("Win Rate [%]",               "Win Rate",              "%"),
        ("Best Trade [%]",             "Best Trade",            "%"),
        ("Worst Trade [%]",            "Worst Trade",           "%"),
        ("Avg Winning Trade [%]",      "Avg Win Trade",         "%"),
        ("Avg Losing Trade [%]",       "Avg Loss Trade",        "%"),
        ("Avg Winning Trade Duration", "Avg Win Duration",      ""),
        ("Avg Losing Trade Duration",  "Avg Loss Duration",     ""),
        ("Profit Factor",              "Profit Factor",         "x"),
        ("Expectancy",                 "Expectancy",            "$"),
        ("Sharpe Ratio",               "Sharpe Ratio",          ""),
        ("Calmar Ratio",               "Calmar Ratio",          ""),
        ("Sortino Ratio",              "Sortino Ratio",         ""),
    ]

    header = f"║ {'Metric':<22} {'V1 (Old)':>16}  {'V2 (New)':>16}  ║"
    print(header)
    print("╠" + "═" * 62 + "╣")

    for key, label, unit in comparison_keys:
        try:
            v1_val = stats_v1[key]
            v2_val = stats_v2[key]

            # Format based on type
            if isinstance(v1_val, (int, np.integer)):
                v1_str = f"{v1_val}"
                v2_str = f"{v2_val}"
            elif isinstance(v1_val, float):
                v1_str = f"{v1_val:.2f}{unit}"
                v2_str = f"{v2_val:.2f}{unit}"
            else:
                v1_str = str(v1_val)[:16]
                v2_str = str(v2_val)[:16]

            # Highlight improvements with arrows
            improved = ""
            if isinstance(v1_val, (int, float, np.integer, np.floating)):
                if "Drawdown" in key or "Fees" in key or "Losing" in key:
                    # Lower is better
                    if float(v2_val) < float(v1_val):
                        improved = " ✅"
                    elif float(v2_val) > float(v1_val):
                        improved = " ⚠️"
                elif "Return" in key or "Win" in key or "Sharpe" in key or "Profit" in key or "Sortino" in key or "Calmar" in key or "Best" in key or "Expectancy" in key:
                    # Higher is better
                    if float(v2_val) > float(v1_val):
                        improved = " ✅"
                    elif float(v2_val) < float(v1_val):
                        improved = " ⚠️"

            line = f"║ {label:<22} {v1_str:>16}  {v2_str:>16}{improved:>3} ║"
            print(line)

        except KeyError:
            pass

    print("╚" + "═" * 62 + "╝")

    # ── Step 8: Print Key Highlights ────────────────────────
    print("\n")
    print("=" * 60)
    print("🔥 KEY IMPROVEMENTS SUMMARY 🔥")
    print("=" * 60)

    try:
        ret_v1 = stats_v1['Total Return [%]']
        ret_v2 = stats_v2['Total Return [%]']
        fee_v1 = stats_v1['Total Fees Paid']
        fee_v2 = stats_v2['Total Fees Paid']
        dd_v1 = stats_v1['Max Drawdown [%]']
        dd_v2 = stats_v2['Max Drawdown [%]']
        wr_v1 = stats_v1['Win Rate [%]']
        wr_v2 = stats_v2['Win Rate [%]']

        print(f"  Return:    V1 = {ret_v1:+.2f}%  →  V2 = {ret_v2:+.2f}%")
        print(f"  Fees:      V1 = ${fee_v1:.2f}  →  V2 = ${fee_v2:.2f}  "
              f"(Saved ${fee_v1 - fee_v2:.2f})")
        print(f"  Drawdown:  V1 = {dd_v1:.2f}%  →  V2 = {dd_v2:.2f}%")
        print(f"  Win Rate:  V1 = {wr_v1:.2f}%  →  V2 = {wr_v2:.2f}%")
    except KeyError:
        pass

    try:
        avg_win_v2 = stats_v2['Avg Winning Trade [%]']
        avg_loss_v2 = stats_v2['Avg Losing Trade [%]']
        rr_ratio = abs(avg_win_v2 / avg_loss_v2) if avg_loss_v2 != 0 else 0
        print(f"\n  Avg Win:   {avg_win_v2:.2f}%")
        print(f"  Avg Loss:  {avg_loss_v2:.2f}%")
        print(f"  R:R Ratio: 1:{rr_ratio:.2f}")
    except KeyError:
        pass

    print("=" * 60)

    # ── Step 9: Full V2 Stats Dump ──────────────────────────
    print("\n\n📋 FULL V2 PORTFOLIO STATS:")
    print("-" * 50)
    print(stats_v2)

    # ── Step 10: Interactive Chart ──────────────────────────
    print("\n📈 Opening V2 interactive chart in browser...")
    fig = pf_v2.plot()
    fig.update_layout(
        title_text="Backtest V2 — Optimized (ATR SL/TP + Maker Fees)",
        title_font_size=20,
        template="plotly_dark"
    )
    fig.show()


if __name__ == "__main__":
    main()
