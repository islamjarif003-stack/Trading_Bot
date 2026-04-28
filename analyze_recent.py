#!/usr/bin/env python3
"""Analyze recent trades from the error log on the remote server."""
import re, sys

def main():
    with open(sys.argv[1], 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    trades_27 = []
    trades_28 = []
    for l in lines:
        if 'Trade Result' not in l:
            continue
        m = re.search(r'PnL: \$([-0-9.]+)', l)
        if not m:
            continue
        pnl = float(m.group(1))
        fee_m = re.search(r'Fees: \$([-0-9.]+)', l)
        fee = float(fee_m.group(1)) if fee_m else 0
        sym_m = re.search(r'\[(\w+)\]', l)
        sym = sym_m.group(1) if sym_m else '?'
        win = '✅' in l or 'PnL: $0' not in l and pnl > 0
        time_m = re.search(r'(\d{2}:\d{2}:\d{2})', l)
        time_str = time_m.group(1) if time_m else '??:??:??'
        
        if '2026-04-27' in l:
            trades_27.append((time_str, pnl, fee, sym, win))
        elif '2026-04-28' in l:
            trades_28.append((time_str, pnl, fee, sym, win))

    print('=' * 60)
    print('  APRIL 27 TRADES (v44.7 deployed ~21:09 UTC)')
    print('=' * 60)
    t27_pnl = sum(t[1] for t in trades_27)
    t27_fee = sum(t[2] for t in trades_27)
    t27_win = sum(1 for t in trades_27 if t[4])
    print(f'  Total: {len(trades_27)} trades | PnL: ${t27_pnl:.2f} | Fees: ${t27_fee:.2f}')
    print(f'  Net (after fees): ${t27_pnl:.2f} | Win: {t27_win}/{len(trades_27)}')
    print('-' * 60)
    for time, p, f, s, w in trades_27:
        icon = '✅' if w else '❌'
        print(f'  {time} {icon} {s:12s}: ${p:+.2f}  (fee ${f:.2f})')

    # v44.7 deployed at ~21:09 on Apr 27
    post_v44_27 = [(t, p, f, s, w) for t, p, f, s, w in trades_27 if t >= '21:09']
    if post_v44_27:
        print()
        print('  --- After v44.7 update (21:09+) ---')
        post_pnl = sum(t[1] for t in post_v44_27)
        post_fee = sum(t[2] for t in post_v44_27)
        post_win = sum(1 for t in post_v44_27 if t[4])
        print(f'  Trades: {len(post_v44_27)} | PnL: ${post_pnl:.2f} | Fees: ${post_fee:.2f} | Win: {post_win}/{len(post_v44_27)}')

    print()
    print('=' * 60)
    print('  APRIL 28 TRADES')
    print('=' * 60)
    t28_pnl = sum(t[1] for t in trades_28)
    t28_fee = sum(t[2] for t in trades_28)
    t28_win = sum(1 for t in trades_28 if t[4])
    print(f'  Total: {len(trades_28)} trades | PnL: ${t28_pnl:.2f} | Fees: ${t28_fee:.2f}')
    print(f'  Net (after fees): ${t28_pnl:.2f} | Win: {t28_win}/{len(trades_28)}')
    print('-' * 60)
    for time, p, f, s, w in trades_28:
        icon = '✅' if w else '❌'
        print(f'  {time} {icon} {s:12s}: ${p:+.2f}  (fee ${f:.2f})')

    print()
    print('=' * 60)
    print('  COMBINED SUMMARY (Apr 27 + Apr 28)')
    print('=' * 60)
    total_pnl = t27_pnl + t28_pnl
    total_fee = t27_fee + t28_fee
    total_trades = len(trades_27) + len(trades_28)
    total_wins = t27_win + t28_win
    print(f'  Total Trades: {total_trades}')
    print(f'  Total PnL: ${total_pnl:.2f}')
    print(f'  Total Fees: ${total_fee:.2f}')
    print(f'  NET P&L: ${total_pnl:.2f}')
    print(f'  Win Rate: {total_wins}/{total_trades} ({total_wins/max(total_trades,1)*100:.1f}%)')

    # Post v44.7 only
    all_post = post_v44_27 + trades_28
    if all_post:
        print()
        print('=' * 60)
        print('  POST v44.7 ONLY (Apr 27 21:09+ and Apr 28)')
        print('=' * 60)
        pp = sum(t[1] for t in all_post)
        pf = sum(t[2] for t in all_post)
        pw = sum(1 for t in all_post if t[4])
        print(f'  Total Trades: {len(all_post)}')
        print(f'  Total PnL: ${pp:.2f}')
        print(f'  Total Fees: ${pf:.2f}')
        print(f'  NET P&L: ${pp:.2f}')
        print(f'  Win Rate: {pw}/{len(all_post)} ({pw/max(len(all_post),1)*100:.1f}%)')

if __name__ == '__main__':
    main()
