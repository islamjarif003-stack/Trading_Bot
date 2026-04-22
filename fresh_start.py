#!/usr/bin/env python3
"""
================================================================================
  fresh_start.py -- Complete Fresh Start Script
  
  1. Reads dry_run_positions.json
  2. WIN => kelly wins++   |  LOSS => kelly losses++
  3. Resets prop balance to $5,000
  4. SSHs into VPS -> restarts bot -> sleep 180 -> pm2 logs
================================================================================
"""

import os
import sys
import json
import time
import subprocess
from datetime import datetime, timezone

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# ─── PATHS ───────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DRY_RUN_FILE = os.path.join(BASE_DIR, "dry_run_positions.json")
KELLY_FILE   = os.path.join(BASE_DIR, "kelly_state.json")
PROP_FILE    = os.path.join(BASE_DIR, "prop_state.json")

# ─── CONFIG ──────────────────────────────────────────────────────────────────
VPS_HOST     = "root@103.174.50.149"
PM2_APP      = "trading-bot"
VPS_BOT_DIR  = "/opt/trading-bot"
FRESH_BAL    = 5000.00
TODAY        = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ─── LOAD .env ───────────────────────────────────────────────────────────────
def load_env():
    env = {}
    try:
        with open(os.path.join(BASE_DIR, ".env")) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except Exception:
        pass
    return env

_env       = load_env()
API_KEY    = _env.get("BINANCE_API_KEY",    os.environ.get("BINANCE_API_KEY", ""))
API_SECRET = _env.get("BINANCE_API_SECRET", os.environ.get("BINANCE_API_SECRET", ""))

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def sep(title=""):
    print("\n" + "=" * 62)
    if title:
        print("  " + title)
        print("=" * 62)

def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception as e:
        print("  [WARN] %s: %s" % (os.path.basename(path), e))
    return default

def save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print("  [SAVED] %s" % os.path.basename(path))
    except Exception as e:
        print("  [ERROR] %s: %s" % (os.path.basename(path), e))

# ─── BINANCE CLIENT ──────────────────────────────────────────────────────────
BINANCE_OK = False
client = None
try:
    from binance.client import Client
    client = Client(API_KEY, API_SECRET)
    st = client.futures_time()
    client.timestamp_offset = st["serverTime"] - int(time.time() * 1000)
    BINANCE_OK = True
    print("[OK] Binance connected")
except Exception as e:
    print("[WARN] Binance unavailable: %s" % e)

def get_price(symbol):
    if not BINANCE_OK or not client:
        return 0.0
    try:
        return float(client.futures_symbol_ticker(symbol=symbol)["price"])
    except Exception:
        return 0.0

# =============================================================================
sep("STEP 1: Reading open positions")
# =============================================================================
positions = load_json(DRY_RUN_FILE, {})
kelly     = load_json(KELLY_FILE, {"total_wins":0,"total_losses":0,"total_win_pnl":0.0,"total_loss_pnl":0.0})
kelly.setdefault("total_wins",    0)
kelly.setdefault("total_losses",  0)
kelly.setdefault("total_win_pnl", 0.0)
kelly.setdefault("total_loss_pnl",0.0)

net_pnl = wins = losses = 0

if not positions:
    print("  No open positions found.")
else:
    print("  Found %d position(s):\n" % len(positions))
    for sym, d in positions.items():
        entry = float(d.get("entry_price", 0))
        qty   = float(d.get("qty", 0))
        side  = d.get("side", "BUY")
        mark  = get_price(sym) or entry
        pnl   = (mark - entry) * qty if side == "BUY" else (entry - mark) * qty
        tag   = "WIN " if pnl >= 0 else "LOSS"
        print("  %s | %-14s | %s | entry=%.4f mark=%.4f pnl=$%.2f" % (tag, sym, side, entry, mark, pnl))
        if pnl >= 0:
            kelly["total_wins"]    += 1;  kelly["total_win_pnl"]  += abs(pnl);  wins   += 1
        else:
            kelly["total_losses"]  += 1;  kelly["total_loss_pnl"] += abs(pnl);  losses += 1
        net_pnl += pnl
    print("\n  %d WIN(s) | %d LOSS(es) | Net: $%.2f" % (wins, losses, net_pnl))

# =============================================================================
sep("STEP 2: Saving Kelly state")
# =============================================================================
save_json(KELLY_FILE, kelly)
total = kelly["total_wins"] + kelly["total_losses"]
wr    = (kelly["total_wins"] / total * 100) if total else 0
print("  Trades=%d  WinRate=%.1f%%  WinPnL=$%.2f  LossPnL=$%.2f" % (
      total, wr, kelly["total_win_pnl"], kelly["total_loss_pnl"]))

# =============================================================================
sep("STEP 3: Clearing positions + resetting balance to $5,000")
# =============================================================================
save_json(DRY_RUN_FILE, {})
prop = {"current_balance": FRESH_BAL, "today_start_balance": FRESH_BAL, "date_str": TODAY}
save_json(PROP_FILE, prop)
print("  Balance = $%.2f  Date = %s" % (FRESH_BAL, TODAY))

# =============================================================================
sep("STEP 4: Restarting bot on VPS (%s)" % VPS_HOST)
# =============================================================================
kelly_json = json.dumps(kelly, indent=2)
prop_json  = json.dumps(prop,  indent=2)

REMOTE = """
pm2 stop {app} 2>/dev/null || true
pm2 delete {app} 2>/dev/null || true
echo '{{}}' > {dir}/dry_run_positions.json
cat > {dir}/kelly_state.json << 'KEOF'
{kelly}
KEOF
cat > {dir}/prop_state.json << 'PEOF'
{prop}
PEOF
INTERP="{dir}/venv/bin/python"
[ -f "$INTERP" ] || INTERP="python3"
cd {dir} && pm2 start main.py --name {app} --interpreter "$INTERP" --restart-delay=3000
pm2 save --force
pm2 status
echo "=== Warmup 180s ==="
sleep 180
pm2 logs {app} --lines 150 --nostream
""".format(app=PM2_APP, dir=VPS_BOT_DIR, kelly=kelly_json, prop=prop_json)

print("  Connecting... (warmup 180s then logs)")
subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no", VPS_HOST, REMOTE])

# =============================================================================
sep("DONE")
# =============================================================================
print("  Positions closed : %d (%d wins, %d losses)" % (wins+losses, wins, losses))
print("  Balance reset    : $%.2f" % FRESH_BAL)
print("  VPS bot          : restarted @ %s" % VPS_HOST)
print("=" * 62)
