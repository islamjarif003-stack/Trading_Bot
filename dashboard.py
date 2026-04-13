"""
================================================================================
  dashboard.py — Institutional Web Dashboard v2.0
  Streamlit + Plotly | Reads live_state.json from the trading bot
  Run: streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0
  Access: http://localhost:8501 (or http://<VPS_IP>:8501)
  
  v2.0: Added live PnL panel, trailing SL on chart, bot status, total PnL.
================================================================================
"""

import os
import json
from datetime import datetime
import time

import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ─── PAGE CONFIGURATION ─────────────────────────────────────────────────────
st.set_page_config(
    page_title="BTCUSDT — Institutional Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── AUTO-REFRESH ─────────────────────────────────────────────────────────────
# We use native st.rerun() at the end of the script instead of third-party plugins.

# ─── CONFIGURATION ──────────────────────────────────────────────────────────
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_state.json")

# ─── CUSTOM CSS ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap');
    
    .stApp { background-color: #0a0e17; }
    
    .metric-card {
        background: linear-gradient(145deg, #12192880 0%, #0a0e1780 100%);
        backdrop-filter: blur(12px);
        border: 1px solid #1e293b;
        border-radius: 14px;
        padding: 18px 22px;
        text-align: center;
        transition: border-color 0.3s ease;
    }
    .metric-card:hover { border-color: #3b82f6; }
    .metric-value {
        font-size: 26px;
        font-weight: 700;
        color: #e2e8f0;
        font-family: 'JetBrains Mono', monospace;
        line-height: 1.2;
    }
    .metric-label {
        font-size: 10px;
        font-weight: 700;
        color: #64748b;
        text-transform: uppercase;
        letter-spacing: 1.5px;
        margin-bottom: 6px;
    }
    .status-badge {
        display: inline-flex; align-items: center; gap: 8px;
        padding: 6px 16px; border-radius: 20px;
        font-size: 13px; font-weight: 700;
        font-family: 'JetBrains Mono', monospace;
        letter-spacing: 0.5px;
    }
    .status-scanning { background: #0f3b2d; color: #34d399; border: 1px solid #065f46; }
    .status-trailing { background: #312e81; color: #a78bfa; border: 1px solid #4c1d95; }
    .status-lockout  { background: #422006; color: #fbbf24; border: 1px solid #78350f; }
    .status-trade    { background: #1e3a5f; color: #60a5fa; border: 1px solid #1e40af; }
    
    .pnl-positive { color: #34d399 !important; }
    .pnl-negative { color: #f87171 !important; }
    
    .live-dot {
        display: inline-block; width: 8px; height: 8px;
        border-radius: 50%; margin-right: 6px;
        animation: livePulse 2s infinite;
    }
    .dot-green { background: #34d399; box-shadow: 0 0 8px #34d39960; }
    .dot-amber { background: #fbbf24; }
    @keyframes livePulse {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.4; transform: scale(0.85); }
    }
    
    div[data-testid="stMetric"] {
        background: linear-gradient(145deg, #121928 0%, #0a0e17 100%);
        border: 1px solid #1e293b;
        border-radius: 14px;
        padding: 14px 18px;
    }
    div[data-testid="stMetric"] label { font-family: 'Inter', sans-serif; }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] {
        font-family: 'JetBrains Mono', monospace;
    }
    
    .breakdown-item {
        font-family: 'JetBrains Mono', monospace;
        font-size: 12px; color: #cbd5e1;
        padding: 5px 0; border-bottom: 1px solid #1e293b;
    }
    section[data-testid="stSidebar"] { background: #0f172a; }
</style>
""", unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════════════════════
# ██  DATA                                                                  ██
# ═════════════════════════════════════════════════════════════════════════════

def load_state(symbol: str) -> dict:
    """Load the latest state from live_state_{symbol}.json."""
    state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"live_state_{symbol}.json")
    try:
        if os.path.exists(state_file):
            with open(state_file, "r") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return None


# ═════════════════════════════════════════════════════════════════════════════
# ██  CHART                                                                 ██
# ═════════════════════════════════════════════════════════════════════════════

def build_chart(state: dict, symbol: str) -> go.Figure:
    """Build an institutional-grade Plotly candlestick chart with overlays."""
    candles = state["candles"]
    overlays = state["overlays"]
    signal = state.get("signal", {})
    position = state.get("position", {})

    ts = candles["timestamps"]
    op = candles["open"]
    hi = candles["high"]
    lo = candles["low"]
    cl = candles["close"]
    vo = candles["volume"]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03,
        row_heights=[0.78, 0.22],
    )

    # ── Candlestick ──────────────────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=ts, open=op, high=hi, low=lo, close=cl,
        increasing=dict(line=dict(color="#22c55e", width=1.2), fillcolor="#22c55e"),
        decreasing=dict(line=dict(color="#ef4444", width=1.2), fillcolor="#ef4444"),
        name=symbol, showlegend=False,
    ), row=1, col=1)

    # ── Volume Bars ──────────────────────────────────────────────────────
    vol_colors = ["rgba(34,197,94,0.5)" if c >= o else "rgba(239,68,68,0.5)" for c, o in zip(cl, op)]
    fig.add_trace(go.Bar(
        x=ts, y=vo, marker_color=vol_colors, name="Volume", showlegend=False,
    ), row=2, col=1)

    # ── POC Line ─────────────────────────────────────────────────────────
    poc = overlays.get("poc")
    if poc and poc > 0:
        fig.add_hline(
            y=poc, line=dict(color="#ef4444", width=2, dash="dash"),
            annotation_text=f"POC ${poc:,.1f}", annotation_position="right",
            annotation_font=dict(color="#ef4444", size=11, family="JetBrains Mono"),
            row=1, col=1,
        )

    # ── Liquidity Magnets ────────────────────────────────────────────────
    mag1 = overlays.get("magnet_1")
    mag2 = overlays.get("magnet_2")
    for mag, label in [(mag1, "Mag1"), (mag2, "Mag2")]:
        if mag and mag > 0 and mag != poc and (label != "Mag2" or mag != mag1):
            fig.add_hline(
                y=mag, line=dict(color="#a78bfa", width=1.5, dash="dot"),
                annotation_text=f"{label} ${mag:,.1f}", annotation_position="left",
                annotation_font=dict(color="#a78bfa", size=10),
                row=1, col=1,
            )

    # ── Fibonacci Golden Pocket (shaded gold zone) ───────────────────────
    gp_low = overlays.get("gp_low")
    gp_high = overlays.get("gp_high")
    if gp_low and gp_high and gp_low > 0 and gp_high > 0:
        fig.add_hrect(
            y0=gp_low, y1=gp_high,
            fillcolor="rgba(250, 204, 21, 0.08)",
            line=dict(color="rgba(250, 204, 21, 0.35)", width=1),
            annotation_text="Golden Pocket (0.5–0.618)",
            annotation_position="top left",
            annotation_font=dict(color="#facc15", size=10),
            row=1, col=1,
        )

    # ── Liquidity Sweep Range ────────────────────────────────────────────
    rh = overlays.get("range_high")
    rl = overlays.get("range_low")
    if rh and rh > 0:
        fig.add_hline(y=rh, line=dict(color="#38bdf8", width=1, dash="dashdot"),
                      annotation_text=f"Range High ${rh:,.1f}", annotation_position="right",
                      annotation_font=dict(color="#38bdf8", size=10), row=1, col=1)
    if rl and rl > 0:
        fig.add_hline(y=rl, line=dict(color="#38bdf8", width=1, dash="dashdot"),
                      annotation_text=f"Range Low ${rl:,.1f}", annotation_position="right",
                      annotation_font=dict(color="#38bdf8", size=10), row=1, col=1)

    # ── ★ Trailing SL Line (if active position) ─────────────────────────
    trail_sl = position.get("trail_sl", 0)
    if position.get("active") and trail_sl > 0:
        sl_color = "#f97316"  # Orange
        fig.add_hline(
            y=trail_sl, line=dict(color=sl_color, width=2, dash="solid"),
            annotation_text=f"🛡 Trail SL ${trail_sl:,.1f}",
            annotation_position="right",
            annotation_font=dict(color=sl_color, size=11, family="JetBrains Mono"),
            row=1, col=1,
        )
        # Entry price line
        entry = position.get("entry_price", 0)
        if entry > 0:
            fig.add_hline(
                y=entry, line=dict(color="#60a5fa", width=1.5, dash="dot"),
                annotation_text=f"Entry ${entry:,.1f}",
                annotation_position="left",
                annotation_font=dict(color="#60a5fa", size=10),
                row=1, col=1,
            )

    # ── Current Price Line ───────────────────────────────────────────────
    current_price = signal.get("current_price", 0)
    if current_price > 0:
        fig.add_hline(
            y=current_price, line=dict(color="#ffffff", width=1),
            annotation_text=f"  ${current_price:,.1f}",
            annotation_position="right",
            annotation_font=dict(color="#ffffff", size=12, family="JetBrains Mono"),
            row=1, col=1,
        )

    # ── Layout ───────────────────────────────────────────────────────────
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0a0e17",
        plot_bgcolor="#0f172a",
        font=dict(family="Inter, sans-serif", color="#94a3b8"),
        title=dict(
            text=f"<b>{symbol} M5</b>  ·  POC  ·  Golden Pocket  ·  Liquidity Zones",
            font=dict(size=15, color="#e2e8f0"), x=0.5,
        ),
        xaxis_rangeslider_visible=False,
        yaxis=dict(title="Price (USDT)", gridcolor="#1e293b", showgrid=True, side="right"),
        yaxis2=dict(title="Volume", gridcolor="#1e293b", showgrid=False),
        margin=dict(l=10, r=90, t=50, b=10),
        height=600,
    )
    fig.update_xaxes(type="category", gridcolor="#1e293b", showgrid=False, nticks=15, row=1, col=1)
    fig.update_xaxes(type="category", gridcolor="#1e293b", showgrid=False, nticks=15, row=2, col=1)

    return fig


# ═════════════════════════════════════════════════════════════════════════════
# ██  DASHBOARD LAYOUT TAB                                                  ██
# ═════════════════════════════════════════════════════════════════════════════

def render_symbol_dashboard(symbol: str, state: dict):
    if state is None:
        st.markdown(f"## ⚠️ Waiting for bot data for {symbol}...")
        st.info("Start the trading bot with `python main.py` or wait for the first scan cycle.")
        return

    # ── Parse state ──────────────────────────────────────────────────────
    updated_at = state.get("updated_at", "")
    try:
        update_time = datetime.fromisoformat(updated_at)
        age_s = (datetime.now(update_time.tzinfo) - update_time).total_seconds()
        is_live = age_s < 30
    except Exception:
        is_live = False
        age_s = 999

    bot = state.get("bot", {})
    sig = state.get("signal", {})
    pos = state.get("position", {})
    deriv = state.get("derivatives", {})
    ml = state.get("ml", {})

    # ═══════════════════════════════════════════════════════════════════
    #  HEADER: Status Badge + Live Indicator
    # ═══════════════════════════════════════════════════════════════════
    h1, h2, h3 = st.columns([3, 2, 2])
    with h1:
        st.markdown(f"# 📈 {symbol} Dashboard")
    with h2:
        bot_status = bot.get("status", "SCANNING")
        if "LOCKOUT" in bot_status:
            badge_cls = "status-lockout"
            badge_icon = "🔒"
        elif bot_status == "TRAILING":
            badge_cls = "status-trailing"
            badge_icon = "📈"
        elif bot_status == "IN_TRADE":
            badge_cls = "status-trade"
            badge_icon = "⚡"
        else:
            badge_cls = "status-scanning"
            badge_icon = "🔍"
        st.markdown(
            f'<div style="margin-top:22px;">'
            f'<span class="status-badge {badge_cls}">{badge_icon} {bot_status}</span>'
            f'</div>', unsafe_allow_html=True,
        )
    with h3:
        dot_cls = "dot-green" if is_live else "dot-amber"
        live_text = "LIVE" if is_live else f"STALE ({int(age_s)}s)"
        st.markdown(
            f'<div style="text-align:right; margin-top:25px;">'
            f'<span class="live-dot {dot_cls}"></span>'
            f'<span style="color:{"#34d399" if is_live else "#fbbf24"};font-weight:700;font-size:14px;">'
            f'{live_text} · Scan #{bot.get("scan_count", 0)}</span></div>',
            unsafe_allow_html=True,
        )

    # ═══════════════════════════════════════════════════════════════════
    #  TOP METRICS BAR
    # ═══════════════════════════════════════════════════════════════════
    st.markdown("---")
    m1, m2, m3, m4, m5, m6, m7, m8 = st.columns(8)

    price = sig.get("current_price", 0)
    score = sig.get("score", 0)
    max_sc = sig.get("max_score", 1)
    signal_dir = sig.get("direction", "NONE")
    total_pnl = bot.get("total_pnl", 0.0)
    ml_prob = ml.get("win_prob", 1.0)

    m1.metric("Price", f"${price:,.1f}")

    # Signal with color
    sig_color = "#22c55e" if signal_dir == "BUY" else ("#ef4444" if signal_dir == "SELL" else "#64748b")
    m2.markdown(f'<div class="metric-card"><div class="metric-label">Signal</div>'
                f'<div class="metric-value" style="color:{sig_color};">{signal_dir}</div></div>',
                unsafe_allow_html=True)

    m3.metric("Score", f"{score}/{max_sc}")
    m4.metric("ADX", f"{sig.get('adx', 0):.1f}")
    m5.metric("RSI", f"{sig.get('rsi', 50):.1f}")
    m6.metric("Regime", sig.get("regime", "—"))

    # Total PnL with color
    pnl_cls = "pnl-positive" if total_pnl >= 0 else "pnl-negative"
    m7.markdown(f'<div class="metric-card"><div class="metric-label">Total PnL</div>'
                f'<div class="metric-value {pnl_cls}">${total_pnl:+,.2f}</div></div>',
                unsafe_allow_html=True)

    # ML Win %
    ml_color = "#22c55e" if ml_prob >= 0.65 else ("#fbbf24" if ml_prob >= 0.5 else "#ef4444")
    m8.markdown(f'<div class="metric-card"><div class="metric-label">ML Win %</div>'
                f'<div class="metric-value" style="color:{ml_color};">{ml_prob*100:.0f}%</div></div>',
                unsafe_allow_html=True)

    # ═══════════════════════════════════════════════════════════════════
    #  LIVE POSITION PANEL (if in trade)
    # ═══════════════════════════════════════════════════════════════════
    if pos.get("active"):
        st.markdown("---")
        pnl_usdt = pos.get("pnl_usdt", 0)
        pnl_pct = pos.get("pnl_pct", 0)
        pos_color = "#22c55e" if pnl_usdt >= 0 else "#ef4444"
        pos_emoji = "🤑" if pnl_usdt >= 0 else "🩸"
        trail_status = "ACTIVE" if pos.get("trailing_active") else "WAITING"

        p1, p2, p3, p4, p5, p6 = st.columns(6)
        p1.markdown(f'<div class="metric-card"><div class="metric-label">Position</div>'
                    f'<div class="metric-value" style="color:#60a5fa;">{pos.get("side","—")}</div></div>',
                    unsafe_allow_html=True)
        p2.markdown(f'<div class="metric-card"><div class="metric-label">{pos_emoji} Unrealized PnL</div>'
                    f'<div class="metric-value" style="color:{pos_color};">${pnl_usdt:+.2f} ({pnl_pct:+.2f}%)</div></div>',
                    unsafe_allow_html=True)
        p3.metric("Entry", f"${pos.get('entry_price', 0):,.1f}")
        p4.metric("Mark", f"${pos.get('mark_price', 0):,.1f}")
        p5.metric("Trail SL", f"${pos.get('trail_sl', 0):,.1f}")
        p6.markdown(f'<div class="metric-card"><div class="metric-label">Trailing</div>'
                    f'<div class="metric-value" style="color:{"#a78bfa" if trail_status=="ACTIVE" else "#fbbf24"};">'
                    f'{trail_status}</div></div>', unsafe_allow_html=True)

    # ═══════════════════════════════════════════════════════════════════
    #  MAIN CHART
    # ═══════════════════════════════════════════════════════════════════
    st.markdown("---")
    fig = build_chart(state, symbol)
    st.plotly_chart(fig, use_container_width=True, config={
        "scrollZoom": True, "displayModeBar": True,
        "modeBarButtonsToRemove": ["lasso2d", "select2d"],
    })

    # ═══════════════════════════════════════════════════════════════════
    #  BOTTOM PANELS
    # ═══════════════════════════════════════════════════════════════════
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.markdown("### 🧠 Confluence Breakdown")
        breakdown = sig.get("score_breakdown", [])
        if breakdown:
            for item in breakdown:
                st.markdown(f'<div class="breakdown-item">✦ {item}</div>', unsafe_allow_html=True)
        else:
            st.caption("No scoring data yet")
        waiting = sig.get("waiting_for", [])
        if waiting:
            st.markdown(f"**⏳ Waiting for:** {', '.join(waiting)}")

    with col_right:
        st.markdown("### 📊 Derivatives & ML")

        # Derivatives row
        d1, d2, d3 = st.columns(3)
        oi = deriv.get("open_interest", 0)
        fr = deriv.get("funding_rate", 0)
        oi_trend = deriv.get("oi_trend", "FLAT")
        oi_arrow = "↑" if oi_trend == "RISING" else ("↓" if oi_trend == "FALLING" else "→")
        d1.metric("Open Interest", f"{oi:,.2f}", delta=f"{oi_arrow} {oi_trend}")
        d2.metric("Funding Rate", f"{fr*100:.4f}%")
        d3.metric("Price Trend", deriv.get("price_trend", "FLAT"))

        # ML Stats row
        ml_stats = ml.get("stats", {})
        ml1, ml2, ml3, ml4 = st.columns(4)
        ml1.metric("ML Buffer", f"{ml_stats.get('total', 0)} trades")
        ml2.metric("Wins", ml_stats.get("wins", 0))
        ml3.metric("Losses", ml_stats.get("losses", 0))
        ml4.metric("Win Rate", f"{ml_stats.get('win_rate', 0):.1f}%")

        if ml.get("vetoed"):
            st.error("🤖 ML Filter **VETOED** the last signal")

        # Bot lifetime stats
        st.markdown("### 📈 Performance")
        b1, b2, b3 = st.columns(3)
        b1.metric("Total Trades", bot.get("trade_count", 0))
        b2.metric("Bot Win Rate", f"{bot.get('win_rate', 0):.1f}%")
        b3.metric("Total PnL", f"${bot.get('total_pnl', 0):+.2f}")

        # ★ v20: Safety Engine stats
        st.markdown("### 🛡️ Safety Filters")
        s1, s2 = st.columns(2)
        r_count = bot.get("reject_count", 0)
        s_amount = bot.get("saved_amount", 0.0)
        s1.metric("Bad Setups Blocked", f"{r_count} 🚫")
        s2.metric("Est. Money Saved", f"${s_amount:,.2f} 💰")

    # ═══════════════════════════════════════════════════════════════════
    #  RECENT TRADE HISTORY
    # ═══════════════════════════════════════════════════════════════════
    recent_trades = bot.get("recent_trades", [])
    st.markdown("---")
    st.markdown("### 📜 Recent Trade History")
    
    if recent_trades:
        import pandas as pd
        trade_data = []
        for t in recent_trades:
            pnl = t.get("pnl", 0.0)
            status = "🟢 Win" if pnl > 0 else "🔴 Loss"
            side = t.get("side", "UNKNOWN")
            trade_data.append({
                "Trade #": t.get("trade_no"),
                "Side": side,
                "Result": status,
                "PnL (USDT)": f"${pnl:+.2f}",
                "Time (UTC)": t.get("timestamp", "").split("T")[0] + " " + t.get("timestamp", "T").split("T")[1][:8] if "T" in t.get("timestamp", "") else "—"
            })
            
        df = pd.DataFrame(trade_data)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No recent trades recorded yet. The table will populate automatically once the next trade is closed.")

    # ═══════════════════════════════════════════════════════════════════
    #  LIVE TERMINAL LOGS (STYLED)
    # ═══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 🖥️ Live Terminal Logs")
    
    log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading_bot.log")
    try:
        if os.path.exists(log_file):
            with open(log_file, "r", encoding="utf-8") as f:
                # Read last 12 lines for a better terminal look
                lines = f.readlines()
                latest_logs = lines[-12:] if len(lines) >= 12 else lines
                
                log_text = "".join(latest_logs).strip()
                if log_text:
                    # Escape HTML characters just in case
                    log_text = log_text.replace("<", "&lt;").replace(">", "&gt;")
                    
                    # Terminal UI styled with CSS
                    terminal_html = f"""
                    <div style="
                        background-color: #050505; 
                        color: #00ff00; 
                        font-family: 'JetBrains Mono', 'Courier New', Courier, monospace; 
                        font-size: 13px; 
                        padding: 16px; 
                        border-radius: 10px; 
                        border: 1px solid #1e293b; 
                        box-shadow: inset 0 0 10px rgba(0,0,0,0.8);
                        overflow-x: auto; 
                        white-space: pre; 
                        line-height: 1.6;
                    ">
{log_text}
                    </div>
                    """
                    st.markdown(terminal_html, unsafe_allow_html=True)
                else:
                    st.info("Log file is empty.")
        else:
            st.info("trading_bot.log not found. Make sure the bot is running and writing to this file.")
    except Exception as e:
        st.error(f"Error reading logs: {e}")

    # ── Footer ───────────────────────────────────────────────────────────
    st.markdown("---")
    st.caption(
        f"Updated: {updated_at} UTC  ·  "
        f"H1 Trend: {sig.get('h1_trend', '—')}  ·  "
        f"ATR: ${sig.get('atr', 0):.2f}  ·  "
        f"Fib GP: {'✅' if sig.get('fibo_gp_active') else '❌'}  ·  "
        f"Trail Dist: ${pos.get('trail_distance', 0):.2f}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# ██  APP ENTRY POINT                                                       ██
# ═════════════════════════════════════════════════════════════════════════════

def main():
    col1, col2 = st.columns([3, 1])
    with col1:
        st.title("Elite Multi-Pair Dashboard")
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            '<a href="http://103.174.50.149:8085" target="_blank" '
            'style="display: inline-block; padding: 10px 20px; background-color: #4f46e5; color: white; '
            'text-decoration: none; border-radius: 8px; font-weight: bold; font-family: Inter, sans-serif; text-align: center; width: 100%;">'
            '📊 Open Live Trade Chart Viewer</a>', 
            unsafe_allow_html=True
        )
    
    SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    
    # Create tabs for each symbol
    tabs = st.tabs(SYMBOLS)
    
    for i, symbol in enumerate(SYMBOLS):
        with tabs[i]:
            state = load_state(symbol)
            if state:
                render_symbol_dashboard(symbol, state)
            else:
                st.info(f"Waiting for {symbol} live data...")

    # Native Streamlit looping to force real-time updates without browser throttling
    time.sleep(5)
    st.rerun()

if __name__ == "__main__":
    main()
