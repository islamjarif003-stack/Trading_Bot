#!/usr/bin/env python3
"""
================================================================================
  performance_tracker.py — Live Performance & Profitability Analyzer v1.0
  ★ Fetches closed trades from Binance Futures API
  ★ Calculates professional-grade KPIs
  ★ Renders a premium terminal dashboard
  ★ Optional: Sends daily summary to Telegram
================================================================================
"""

import os
import sys
import math
import argparse
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from dotenv import load_dotenv

# ── Load environment variables ───────────────────────────────────────────────
# Support both local dev (.env in script dir) and VPS deployment
_script_dir = os.path.dirname(os.path.abspath(__file__))
_env_candidates = [
    os.path.join(_script_dir, ".env"),
    "/opt/trading-bot/.env",
]
for _p in _env_candidates:
    if os.path.exists(_p):
        load_dotenv(_p)
        break
else:
    load_dotenv()  # fallback: current working dir

from binance.client import Client
import requests

# ═════════════════════════════════════════════════════════════════════════════
# ██  CONFIGURATION                                                         ██
# ═════════════════════════════════════════════════════════════════════════════

API_KEY    = os.environ.get("BINANCE_API_KEY", "")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

# ── Terminal Color Codes ─────────────────────────────────────────────────────
class C:
    """ANSI color codes for terminal styling."""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    UNDER   = "\033[4m"
    # Colors
    GREEN   = "\033[38;5;114m"
    RED     = "\033[38;5;210m"
    YELLOW  = "\033[38;5;222m"
    CYAN    = "\033[38;5;117m"
    MAGENTA = "\033[38;5;177m"
    WHITE   = "\033[38;5;255m"
    GRAY    = "\033[38;5;245m"
    ORANGE  = "\033[38;5;215m"
    BLUE    = "\033[38;5;75m"
    # Backgrounds
    BG_DARK   = "\033[48;5;235m"
    BG_HEADER = "\033[48;5;236m"
    BG_ROW    = "\033[48;5;234m"
    BG_GREEN  = "\033[48;5;22m"
    BG_RED    = "\033[48;5;52m"


# ═════════════════════════════════════════════════════════════════════════════
# ██  TELEGRAM HELPER                                                       ██
# ═════════════════════════════════════════════════════════════════════════════

def send_telegram_report(message: str):
    """Sends a performance report to the configured Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"\n{C.YELLOW}⚠  Telegram not configured. Skipping send.{C.RESET}")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message[:4096],
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"\n{C.GREEN}✅  Report sent to Telegram successfully!{C.RESET}")
            return True
        else:
            print(f"\n{C.RED}❌  Telegram API error: {resp.status_code} — {resp.text[:200]}{C.RESET}")
            return False
    except Exception as e:
        print(f"\n{C.RED}❌  Telegram send failed: {e}{C.RESET}")
        return False


# ═════════════════════════════════════════════════════════════════════════════
# ██  DATA FETCHING                                                         ██
# ═════════════════════════════════════════════════════════════════════════════

def fetch_realized_pnl(client: Client, lookback_hours: int = 168, limit: int = 1000) -> list:
    """
    Fetch realized PnL entries from Binance Futures income history.
    
    Args:
        client: Binance client instance
        lookback_hours: How many hours back to look (default 168 = 7 days)
        limit: Max entries per API call (Binance max = 1000)
    
    Returns:
        List of trade dicts with keys: symbol, pnl, time, asset
    """
    start_time = int((datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).timestamp() * 1000)
    
    all_income = []
    try:
        income = client.futures_income_history(
            incomeType="REALIZED_PNL",
            startTime=start_time,
            limit=limit,
        )
        for entry in income:
            pnl = float(entry.get("income", 0.0))
            # Skip zero-PnL entries (funding, adjustments, dust)
            if abs(pnl) < 0.0001:
                continue
            all_income.append({
                "symbol": entry.get("symbol", "UNKNOWN"),
                "pnl": pnl,
                "time": int(entry.get("time", 0)),
                "asset": entry.get("asset", "USDT"),
            })
    except Exception as e:
        print(f"{C.RED}❌  Error fetching income history: {e}{C.RESET}")
    
    return all_income


def fetch_commission_total(client: Client, lookback_hours: int = 168) -> float:
    """
    Fetch total commission (fees) paid from income history.
    
    Returns:
        Total commission paid as a positive number (USD).
    """
    start_time = int((datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).timestamp() * 1000)
    total_fees = 0.0
    try:
        income = client.futures_income_history(
            incomeType="COMMISSION",
            startTime=start_time,
            limit=1000,
        )
        for entry in income:
            total_fees += abs(float(entry.get("income", 0.0)))
    except Exception as e:
        print(f"{C.YELLOW}⚠  Could not fetch commission data: {e}{C.RESET}")
    return total_fees


def fetch_funding_total(client: Client, lookback_hours: int = 168) -> float:
    """Fetch total funding fee income/cost."""
    start_time = int((datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).timestamp() * 1000)
    total = 0.0
    try:
        income = client.futures_income_history(
            incomeType="FUNDING_FEE",
            startTime=start_time,
            limit=1000,
        )
        for entry in income:
            total += float(entry.get("income", 0.0))
    except Exception as e:
        pass
    return total


def fetch_account_balance(client: Client) -> dict:
    """Fetch current USDT balance info."""
    try:
        account = client.futures_account()
        return {
            "total_balance": float(account.get("totalWalletBalance", 0)),
            "available": float(account.get("availableBalance", 0)),
            "unrealized_pnl": float(account.get("totalUnrealizedProfit", 0)),
        }
    except Exception as e:
        print(f"{C.RED}❌  Could not fetch account balance: {e}{C.RESET}")
        return {"total_balance": 0, "available": 0, "unrealized_pnl": 0}


# ═════════════════════════════════════════════════════════════════════════════
# ██  KPI CALCULATIONS                                                      ██
# ═════════════════════════════════════════════════════════════════════════════

def calculate_kpis(trades: list) -> dict:
    """
    Calculate comprehensive Key Performance Indicators from a list of trades.
    
    Each trade: {"symbol": str, "pnl": float, "time": int, "asset": str}
    """
    if not trades:
        return {"total_trades": 0}
    
    pnl_values = [t["pnl"] for t in trades]
    wins = [p for p in pnl_values if p > 0]
    losses = [p for p in pnl_values if p < 0]
    
    total_trades = len(pnl_values)
    num_wins = len(wins)
    num_losses = len(losses)
    
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    net_pnl = sum(pnl_values)
    
    win_rate = (num_wins / total_trades * 100) if total_trades > 0 else 0.0
    avg_win = (gross_profit / num_wins) if num_wins > 0 else 0.0
    avg_loss = (gross_loss / num_losses) if num_losses > 0 else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    
    # Expectancy: Average expected $ per trade
    expectancy = net_pnl / total_trades if total_trades > 0 else 0.0
    
    # Risk-Reward Ratio (average)
    avg_rr = (avg_win / avg_loss) if avg_loss > 0 else float("inf")
    
    # Best / Worst single trade
    best_trade = max(pnl_values)
    worst_trade = min(pnl_values)
    
    # Win/Loss Streaks
    max_win_streak = 0
    max_loss_streak = 0
    current_streak = 0
    streak_type = None
    for p in pnl_values:
        if p > 0:
            if streak_type == "win":
                current_streak += 1
            else:
                current_streak = 1
                streak_type = "win"
            max_win_streak = max(max_win_streak, current_streak)
        elif p < 0:
            if streak_type == "loss":
                current_streak += 1
            else:
                current_streak = 1
                streak_type = "loss"
            max_loss_streak = max(max_loss_streak, current_streak)
    
    # Max Drawdown (peak-to-trough in cumulative PnL)
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for p in pnl_values:
        cumulative += p
        peak = max(peak, cumulative)
        drawdown = peak - cumulative
        max_drawdown = max(max_drawdown, drawdown)
    
    # Sharpe-like ratio (simplified: mean return / std dev of returns)
    if len(pnl_values) > 1:
        mean_ret = sum(pnl_values) / len(pnl_values)
        variance = sum((p - mean_ret) ** 2 for p in pnl_values) / (len(pnl_values) - 1)
        std_dev = math.sqrt(variance) if variance > 0 else 0.001
        sharpe = mean_ret / std_dev
    else:
        sharpe = 0.0
        std_dev = 0.0
    
    # Per-symbol breakdown
    by_symbol = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        s = t["symbol"]
        by_symbol[s]["trades"] += 1
        by_symbol[s]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            by_symbol[s]["wins"] += 1
    
    # Daily PnL breakdown
    by_day = defaultdict(float)
    for t in trades:
        day_str = datetime.fromtimestamp(t["time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        by_day[day_str] += t["pnl"]
    
    return {
        "total_trades": total_trades,
        "num_wins": num_wins,
        "num_losses": num_losses,
        "win_rate": win_rate,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net_pnl": net_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "avg_rr": avg_rr,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "std_dev": std_dev,
        "by_symbol": dict(by_symbol),
        "by_day": dict(by_day),
    }


# ═════════════════════════════════════════════════════════════════════════════
# ██  TERMINAL RENDERING                                                    ██
# ═════════════════════════════════════════════════════════════════════════════

def _color_pnl(value: float, fmt: str = "+.2f") -> str:
    """Color a PnL value green/red."""
    color = C.GREEN if value >= 0 else C.RED
    return f"{color}${value:{fmt}}{C.RESET}"


def _color_pct(value: float, fmt: str = ".1f") -> str:
    """Color a percentage green/red."""
    color = C.GREEN if value >= 0 else C.RED
    return f"{color}{value:{fmt}}%{C.RESET}"


def _bar(value: float, max_val: float, width: int = 20, fill_char: str = "█", empty_char: str = "░") -> str:
    """Create a progress bar."""
    if max_val <= 0:
        filled = 0
    else:
        filled = min(int((value / max_val) * width), width)
    return f"{C.CYAN}{fill_char * filled}{C.GRAY}{empty_char * (width - filled)}{C.RESET}"


def _grade(win_rate: float, profit_factor: float) -> str:
    """Assign a letter grade based on performance."""
    if profit_factor >= 3.0 and win_rate >= 60:
        return f"{C.GREEN}{C.BOLD}A+{C.RESET}"
    elif profit_factor >= 2.0 and win_rate >= 55:
        return f"{C.GREEN}A{C.RESET}"
    elif profit_factor >= 1.5 and win_rate >= 50:
        return f"{C.CYAN}B+{C.RESET}"
    elif profit_factor >= 1.2 and win_rate >= 45:
        return f"{C.YELLOW}B{C.RESET}"
    elif profit_factor >= 1.0:
        return f"{C.ORANGE}C{C.RESET}"
    else:
        return f"{C.RED}D{C.RESET}"


def render_terminal_report(kpis: dict, trades: list, balance: dict,
                            commission: float, funding: float,
                            lookback_hours: int):
    """Render a beautiful, professional performance report in the terminal."""
    
    now = datetime.now(timezone.utc)
    lookback_label = f"{lookback_hours}h" if lookback_hours < 48 else f"{lookback_hours // 24}d"
    
    W = 76  # total width
    
    def hr(char="═"):
        return f"{C.GRAY}{char * W}{C.RESET}"
    
    def header(title):
        pad = W - len(title) - 4
        left = pad // 2
        right = pad - left
        return f"{C.BOLD}{C.CYAN}{'═' * left}  {title}  {'═' * right}{C.RESET}"
    
    def section(title):
        return f"\n{C.BOLD}{C.MAGENTA}  ┌─ {title}{C.RESET}"
    
    def row(label: str, value: str, indent: int = 4):
        label_w = 24
        return f"{' ' * indent}{C.WHITE}{label:<{label_w}}{C.RESET} {value}"
    
    # ═══════════════════════════════════════════════════════════════════════
    print()
    print(hr())
    print(header("⚡ PERFORMANCE TRACKER v1.0 ⚡"))
    print(hr())
    print(row("Report Generated", f"{C.WHITE}{now.strftime('%Y-%m-%d %H:%M:%S UTC')}{C.RESET}"))
    print(row("Lookback Period", f"{C.CYAN}{lookback_label}{C.RESET} ({lookback_hours} hours)"))
    print(row("Pairs Tracked", f"{C.CYAN}{', '.join(SYMBOLS)}{C.RESET}"))
    print(hr("─"))
    
    if kpis["total_trades"] == 0:
        print(f"\n{C.YELLOW}  ⚠  No closed trades found in the selected period.{C.RESET}")
        print(f"{C.GRAY}     Try increasing --hours or check if the bot has been trading.{C.RESET}\n")
        print(hr())
        return
    
    # ── CORE KPIs ────────────────────────────────────────────────────────
    pf = kpis["profit_factor"]
    pf_str = f"{pf:.2f}" if pf < 999 else "∞"
    rr = kpis["avg_rr"]
    rr_str = f"1:{rr:.1f}" if rr < 999 else "1:∞"
    grade = _grade(kpis["win_rate"], pf)
    
    print(section("CORE METRICS"))
    print(f"  │")
    _total = kpis['total_trades']
    _wins = kpis['num_wins']
    _losses = kpis['num_losses']
    print(f"  │  {row('Total Trades', f'{C.BOLD}{C.WHITE}{_total}{C.RESET}')}")
    print(f"  │  {row('Wins / Losses', f'{C.GREEN}{_wins}{C.RESET} / {C.RED}{_losses}{C.RESET}')}")
    
    wr = kpis["win_rate"]
    wr_color = C.GREEN if wr >= 50 else C.RED
    wr_bar = _bar(wr, 100, width=20)
    print(f"  │  {row('Win Rate', f'{wr_color}{wr:.1f}%{C.RESET}  {wr_bar}')}")
    print(f"  │  {row('Profit Factor', f'{C.CYAN}{pf_str}{C.RESET}')}")
    print(f"  │  {row('Avg Risk:Reward', f'{C.CYAN}{rr_str}{C.RESET}')}")
    print(f"  │  {row('Performance Grade', grade)}")
    print(f"  │")
    print(f"{C.MAGENTA}  └─{C.RESET}")
    
    # ── PnL BREAKDOWN ────────────────────────────────────────────────────
    print(section("PnL BREAKDOWN"))
    print(f"  │")
    print(f"  │  {row('Gross Profit', _color_pnl(kpis['gross_profit']))}")
    print(f"  │  {row('Gross Loss', _color_pnl(-kpis['gross_loss']))}")
    print(f"  │  {row('Trading Fees', f'{C.RED}-${commission:.2f}{C.RESET}')}")
    print(f"  │  {row('Funding Fees', _color_pnl(funding))}")
    
    net_after_fees = kpis["net_pnl"]  # realized PnL already includes fee impact
    print(f"  │  {'─' * 40}")
    print(f"  │  {row('★ NET PnL', f'{C.BOLD}{_color_pnl(net_after_fees)}{C.BOLD}{C.RESET}')}")
    print(f"  │")
    print(f"{C.MAGENTA}  └─{C.RESET}")
    
    # ── TRADE QUALITY ────────────────────────────────────────────────────
    print(section("TRADE QUALITY"))
    print(f"  │")
    print(f"  │  {row('Average Win', _color_pnl(kpis['avg_win']))}")
    print(f"  │  {row('Average Loss', _color_pnl(-kpis['avg_loss']))}")
    print(f"  │  {row('Expectancy/Trade', _color_pnl(kpis['expectancy']))}")
    print(f"  │  {row('Best Single Trade', _color_pnl(kpis['best_trade']))}")
    print(f"  │  {row('Worst Single Trade', _color_pnl(kpis['worst_trade']))}")
    print(f"  │")
    print(f"{C.MAGENTA}  └─{C.RESET}")
    
    # ── RISK METRICS ─────────────────────────────────────────────────────
    print(section("RISK METRICS"))
    print(f"  │")
    _dd = kpis['max_drawdown']
    _ws = kpis['max_win_streak']
    _ls = kpis['max_loss_streak']
    _sh = kpis['sharpe']
    _sd = kpis['std_dev']
    print(f"  │  {row('Max Drawdown', f'{C.RED}${_dd:.2f}{C.RESET}')}")
    print(f"  │  {row('Max Win Streak', f'{C.GREEN}{_ws}{C.RESET}')}")
    print(f"  │  {row('Max Loss Streak', f'{C.RED}{_ls}{C.RESET}')}")
    print(f"  │  {row('Sharpe Ratio', f'{C.CYAN}{_sh:.2f}{C.RESET}')}")
    print(f"  │  {row('Std Dev (per trade)', f'{C.GRAY}${_sd:.2f}{C.RESET}')}")
    print(f"  │")
    print(f"{C.MAGENTA}  └─{C.RESET}")
    
    # ── PER-SYMBOL BREAKDOWN ─────────────────────────────────────────────
    print(section("PER-SYMBOL BREAKDOWN"))
    print(f"  │")
    print(f"  │  {C.DIM}{'Symbol':<12s} {'Trades':>7s} {'Wins':>6s} {'WR%':>7s} {'Net PnL':>12s}{C.RESET}")
    print(f"  │  {C.GRAY}{'─' * 48}{C.RESET}")
    
    for sym in sorted(kpis["by_symbol"].keys()):
        data = kpis["by_symbol"][sym]
        sym_wr = (data["wins"] / data["trades"] * 100) if data["trades"] > 0 else 0
        pnl_colored = _color_pnl(data["pnl"])
        wr_c = C.GREEN if sym_wr >= 50 else C.RED
        print(f"  │  {C.WHITE}{sym:<12s}{C.RESET} {data['trades']:>7d} {data['wins']:>6d} {wr_c}{sym_wr:>6.1f}%{C.RESET} {pnl_colored:>12s}")
    
    print(f"  │")
    print(f"{C.MAGENTA}  └─{C.RESET}")
    
    # ── DAILY PnL ────────────────────────────────────────────────────────
    if kpis["by_day"]:
        print(section("DAILY PnL"))
        print(f"  │")
        
        max_daily = max(abs(v) for v in kpis["by_day"].values()) if kpis["by_day"] else 1
        for day in sorted(kpis["by_day"].keys()):
            pnl = kpis["by_day"][day]
            bar_w = int(abs(pnl) / max(max_daily, 0.01) * 15)
            bar_char = "█" * bar_w
            if pnl >= 0:
                bar = f"{C.GREEN}{bar_char}{C.RESET}"
            else:
                bar = f"{C.RED}{bar_char}{C.RESET}"
            print(f"  │  {C.WHITE}{day}{C.RESET}  {_color_pnl(pnl):>18s}  {bar}")
        
        print(f"  │")
        print(f"{C.MAGENTA}  └─{C.RESET}")
    
    # ── RECENT TRADES TABLE ──────────────────────────────────────────────
    print(section(f"RECENT TRADES (last {min(15, len(trades))})"))
    print(f"  │")
    print(f"  │  {C.DIM}{'#':>3s}  {'Time (UTC)':<18s} {'Symbol':<10s} {'PnL':>12s} {'Result':>8s}{C.RESET}")
    print(f"  │  {C.GRAY}{'─' * 56}{C.RESET}")
    
    recent = trades[-15:]  # last 15 trades
    for i, t in enumerate(recent, 1):
        ts = datetime.fromtimestamp(t["time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        pnl = t["pnl"]
        result = f"{C.GREEN}  WIN ✓{C.RESET}" if pnl > 0 else f"{C.RED} LOSS ✗{C.RESET}"
        print(f"  │  {C.GRAY}{i:>3d}{C.RESET}  {C.WHITE}{ts:<18s}{C.RESET} {t['symbol']:<10s} {_color_pnl(pnl):>12s} {result}")
    
    print(f"  │")
    print(f"{C.MAGENTA}  └─{C.RESET}")
    
    # ── ACCOUNT SNAPSHOT ─────────────────────────────────────────────────
    print(section("ACCOUNT SNAPSHOT"))
    print(f"  │")
    _bal = balance['total_balance']
    _avail = balance['available']
    print(f"  │  {row('Total Balance', f'{C.BOLD}{C.WHITE}${_bal:.2f}{C.RESET}')}")
    print(f"  │  {row('Available Margin', f'{C.WHITE}${_avail:.2f}{C.RESET}')}")
    print(f"  │  {row('Unrealized PnL', _color_pnl(balance['unrealized_pnl']))}")
    print(f"  │")
    print(f"{C.MAGENTA}  └─{C.RESET}")
    
    # ── VERDICT ──────────────────────────────────────────────────────────
    print()
    if kpis["net_pnl"] > 0 and kpis["win_rate"] >= 50 and pf >= 1.2:
        print(f"  {C.BG_GREEN}{C.BOLD}{C.WHITE}  ✅  VERDICT: Strategy is PROFITABLE — $7 margin + R:R logic WORKING  {C.RESET}")
    elif kpis["net_pnl"] > 0:
        print(f"  {C.BG_GREEN}{C.BOLD}{C.WHITE}  ⚠️   VERDICT: Net positive but metrics need improvement              {C.RESET}")
    else:
        print(f"  {C.BG_RED}{C.BOLD}{C.WHITE}  ❌  VERDICT: Strategy is UNPROFITABLE — Review entry/exit logic       {C.RESET}")
    print()
    print(hr())
    print()


# ═════════════════════════════════════════════════════════════════════════════
# ██  TELEGRAM REPORT BUILDER                                               ██
# ═════════════════════════════════════════════════════════════════════════════

def build_telegram_report(kpis: dict, balance: dict, commission: float,
                          funding: float, lookback_hours: int) -> str:
    """Build a clean HTML-formatted Telegram message."""
    
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lookback_label = f"{lookback_hours}h" if lookback_hours < 48 else f"{lookback_hours // 24}d"
    
    if kpis["total_trades"] == 0:
        return f"📊 <b>Performance Report ({lookback_label})</b>\n⚠ No closed trades in this period."
    
    pf = kpis["profit_factor"]
    pf_str = f"{pf:.2f}" if pf < 999 else "∞"
    net = kpis["net_pnl"]
    net_emoji = "🟢" if net >= 0 else "🔴"
    
    # Per-symbol summary
    sym_lines = []
    for sym in sorted(kpis["by_symbol"].keys()):
        d = kpis["by_symbol"][sym]
        wr = (d["wins"] / d["trades"] * 100) if d["trades"] > 0 else 0
        emoji = "🟢" if d["pnl"] >= 0 else "🔴"
        sym_lines.append(f"  {emoji} {sym}: {d['trades']}T | WR {wr:.0f}% | ${d['pnl']:+.2f}")
    
    msg = (
        f"📊 <b>Performance Report ({lookback_label})</b>\n"
        f"🕐 {now}\n"
        f"{'─' * 30}\n"
        f"\n"
        f"<b>Core Metrics:</b>\n"
        f"  📈 Trades: {kpis['total_trades']} ({kpis['num_wins']}W / {kpis['num_losses']}L)\n"
        f"  🎯 Win Rate: {kpis['win_rate']:.1f}%\n"
        f"  💰 Profit Factor: {pf_str}\n"
        f"\n"
        f"<b>PnL:</b>\n"
        f"  {net_emoji} <b>Net PnL: ${net:+.2f}</b>\n"
        f"  📗 Gross Profit: ${kpis['gross_profit']:+.2f}\n"
        f"  📕 Gross Loss: ${-kpis['gross_loss']:+.2f}\n"
        f"  💸 Fees Paid: ${commission:.2f}\n"
        f"\n"
        f"<b>Quality:</b>\n"
        f"  ✅ Avg Win: ${kpis['avg_win']:.2f}\n"
        f"  ❌ Avg Loss: ${kpis['avg_loss']:.2f}\n"
        f"  📐 Expectancy: ${kpis['expectancy']:+.2f}/trade\n"
        f"  🏆 Best: ${kpis['best_trade']:+.2f}\n"
        f"  💀 Worst: ${kpis['worst_trade']:+.2f}\n"
        f"\n"
        f"<b>Risk:</b>\n"
        f"  📉 Max Drawdown: ${kpis['max_drawdown']:.2f}\n"
        f"  🔥 Max Win Streak: {kpis['max_win_streak']}\n"
        f"  ❄️ Max Loss Streak: {kpis['max_loss_streak']}\n"
        f"\n"
        f"<b>Per Coin:</b>\n"
        f"{chr(10).join(sym_lines)}\n"
        f"\n"
        f"<b>Account:</b>\n"
        f"  💼 Balance: ${balance['total_balance']:.2f}\n"
        f"  📊 Unrealized: ${balance['unrealized_pnl']:+.2f}\n"
        f"{'─' * 30}\n"
    )
    
    # Verdict
    if net > 0 and kpis["win_rate"] >= 50 and pf >= 1.2:
        msg += "✅ <b>VERDICT: Strategy PROFITABLE</b>"
    elif net > 0:
        msg += "⚠️ <b>VERDICT: Net positive, needs tuning</b>"
    else:
        msg += "❌ <b>VERDICT: UNPROFITABLE — Review needed</b>"
    
    return msg


# ═════════════════════════════════════════════════════════════════════════════
# ██  MAIN ENTRY POINT                                                      ██
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="⚡ Binance Futures Performance Tracker — Analyze your bot's live profitability",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python performance_tracker.py                  # Last 7 days
  python performance_tracker.py --hours 24       # Last 24 hours
  python performance_tracker.py --hours 720      # Last 30 days
  python performance_tracker.py --telegram       # Send report to Telegram
  python performance_tracker.py --hours 24 --telegram
        """,
    )
    parser.add_argument(
        "--hours", type=int, default=168,
        help="Lookback period in hours (default: 168 = 7 days)"
    )
    parser.add_argument(
        "--telegram", action="store_true",
        help="Send the summary report to Telegram"
    )
    
    args = parser.parse_args()
    
    # ── Initialize Binance Client ────────────────────────────────────────
    if not API_KEY or not API_SECRET:
        print(f"{C.RED}❌  Missing BINANCE_API_KEY / BINANCE_API_SECRET in .env{C.RESET}")
        sys.exit(1)
    
    print(f"\n{C.CYAN}⏳  Connecting to Binance Futures...{C.RESET}")
    use_testnet = os.environ.get("USE_TESTNET", "true").lower() in ("true", "1", "yes")
    client = Client(API_KEY, API_SECRET, testnet=use_testnet, requests_params={"timeout": 20})
    if use_testnet:
        print(f"{C.YELLOW}    Mode: TESTNET{C.RESET}")
    else:
        print(f"{C.GREEN}    Mode: MAINNET{C.RESET}")
    
    # ── Fetch Data ───────────────────────────────────────────────────────
    print(f"{C.CYAN}📡  Fetching realized PnL ({args.hours}h lookback)...{C.RESET}")
    trades = fetch_realized_pnl(client, lookback_hours=args.hours)
    
    print(f"{C.CYAN}💸  Fetching commissions & funding fees...{C.RESET}")
    commission = fetch_commission_total(client, lookback_hours=args.hours)
    funding = fetch_funding_total(client, lookback_hours=args.hours)
    
    print(f"{C.CYAN}💼  Fetching account balance...{C.RESET}")
    balance = fetch_account_balance(client)
    
    # ── Calculate KPIs ───────────────────────────────────────────────────
    print(f"{C.CYAN}🧮  Computing KPIs from {len(trades)} trades...{C.RESET}")
    kpis = calculate_kpis(trades)
    
    # ── Render Terminal Report ───────────────────────────────────────────
    render_terminal_report(kpis, trades, balance, commission, funding, args.hours)
    
    # ── Send to Telegram (optional) ──────────────────────────────────────
    if args.telegram:
        print(f"{C.CYAN}📲  Sending report to Telegram...{C.RESET}")
        tg_msg = build_telegram_report(kpis, balance, commission, funding, args.hours)
        send_telegram_report(tg_msg)


if __name__ == "__main__":
    main()
