#!/usr/bin/env python3
"""
SL Hit Aftermath: Did price go our direction or against us after SL hit?
Uses Binance Futures API to check price at 5, 15, 30, 60 min after each loss.
"""
import re, sys, json
from datetime import datetime
import urllib.request

def get_klines(symbol, start_ms, interval='5m', limit=12):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&startTime={start_ms}&limit={limit}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"    [API Error for {symbol}: {e}]")
        return []

def main():
    filepath = sys.argv[1]
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    # Build list of all trades (trade result + direction from next PnL updated line)
    trades = []
    i = 0
    while i < len(lines):
        l = lines[i]
        if 'Trade Result' in l and ('2026-04-27' in l or '2026-04-28' in l):
            m_pnl = re.search(r'PnL: \$([-0-9.]+)', l)
            m_sym = re.search(r'\[(\w+)\]', l)
            m_time = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', l)
            is_loss = '\u274c' in l  # ❌
            
            if m_pnl and m_sym and m_time:
                # Look at the NEXT line for direction
                direction = 'UNKNOWN'
                if i + 1 < len(lines) and 'PnL updated' in lines[i+1]:
                    m_dir = re.search(r'\((BUY|SELL|UNKNOWN)\)', lines[i+1])
                    if m_dir:
                        direction = m_dir.group(1)
                
                trades.append({
                    'symbol': m_sym.group(1),
                    'time': m_time.group(1),
                    'pnl': float(m_pnl.group(1)),
                    'direction': direction,
                    'is_loss': is_loss,
                })
        i += 1

    losing_trades = [t for t in trades if t['is_loss'] and t['direction'] != 'UNKNOWN']
    
    print("=" * 80)
    print("  SL HIT ANALYSIS: মার্কেট SL হিটের পর কোন দিকে গেছে?")
    print("  (5, 15, 30, 60 মিনিট পর প্রাইস চেক করা হচ্ছে)")
    print("=" * 80)
    print(f"  Losing trades found: {len(losing_trades)}")
    print()

    went_our_way = 0
    went_against = 0
    total_checked = 0
    results = []

    for trade in losing_trades:
        symbol = trade['symbol']
        direction = trade['direction']
        time_str = trade['time']
        pnl = trade['pnl']

        dt = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
        start_ms = int(dt.timestamp() * 1000)

        klines = get_klines(symbol, start_ms, '5m', 13)
        if not klines or len(klines) < 3:
            print(f"  ⚠ {symbol} {time_str} — কোনো ডেটা পাওয়া যায়নি, স্কিপ")
            continue

        sl_price = float(klines[0][4])  # close of the candle when SL hit

        checkpoints = {}
        for idx, k in enumerate(klines[1:], 1):  # skip first candle
            mins = idx * 5
            hi = float(k[2])
            lo = float(k[3])
            cl = float(k[4])
            
            if mins in [5, 15, 30, 60]:
                if direction == 'BUY':
                    move_pct = (cl - sl_price) / sl_price * 100
                    best = (hi - sl_price) / sl_price * 100
                    went_right = cl > sl_price
                else:
                    move_pct = (sl_price - cl) / sl_price * 100
                    best = (sl_price - lo) / sl_price * 100
                    went_right = cl < sl_price
                    
                checkpoints[mins] = {
                    'close': cl,
                    'move_pct': move_pct,
                    'best_pct': best,
                    'went_our_way': went_right
                }

        check_key = 30 if 30 in checkpoints else (15 if 15 in checkpoints else 5)
        if check_key in checkpoints:
            total_checked += 1
            our_way = checkpoints[check_key]['went_our_way']
            if our_way:
                went_our_way += 1
            else:
                went_against += 1

            emoji = '😤' if our_way else '👍'
            verdict = 'আমাদের দিকে গেছে (SL tight!)' if our_way else 'বিপরীত দিকে (ট্রেড ভুল)'
            
            print(f"  {emoji} [{symbol:12s}] {time_str} | {direction:4s} | ${pnl:+.2f} | {verdict}")
            for mins in sorted(checkpoints.keys()):
                cp = checkpoints[mins]
                arrow = '↑' if cp['move_pct'] > 0 else '↓'
                way = '✅' if cp['went_our_way'] else '❌'
                print(f"       +{mins:2d}min: {arrow}{abs(cp['move_pct']):.3f}% (best: {cp['best_pct']:+.3f}%) {way}")
            print()

    print("=" * 80)
    print("  ফাইনাল রেজাল্ট")
    print("=" * 80)
    print(f"  মোট লস ট্রেড চেক করা হয়েছে: {total_checked}")
    print(f"  😤 SL হিটের পর আমাদের দিকেই গেছে: {went_our_way} ({went_our_way/max(total_checked,1)*100:.0f}%)")
    print(f"  👍 SL হিটের পর বিপরীত দিকে গেছে:  {went_against} ({went_against/max(total_checked,1)*100:.0f}%)")
    print()
    if went_our_way > went_against:
        pct = went_our_way/max(total_checked,1)*100
        print(f"  🚨 উত্তর: {pct:.0f}% বার মার্কেট আমাদের দিকেই গেছে SL হিট করার পর!")
        print(f"     মানে আমাদের ট্রেডের দিক সঠিক ছিল, শুধু SL খুব টাইট ছিল!")
        print(f"     → SL বড় করলে (15m ATR ব্যবহার) এই ট্রেডগুলো WIN হতো!")
    elif went_against > went_our_way:
        pct = went_against/max(total_checked,1)*100
        print(f"  📊 উত্তর: {pct:.0f}% বার মার্কেট বিপরীত দিকে গেছে।")
        print(f"     মানে SL ঠিক ছিল, ট্রেডের দিক ভুল ছিল।")
        print(f"     → সিগন্যাল ইঞ্জিনে আরো ফিল্টার দরকার।")
    else:
        print(f"  📊 উত্তর: ৫০-৫০ মিক্সড রেজাল্ট।")

if __name__ == '__main__':
    main()
