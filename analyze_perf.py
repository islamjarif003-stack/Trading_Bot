import re

results = []
with open('/root/.pm2/logs/trading-bot-error.log') as f:
    for line in f:
        if 'Trade Result' in line:
            m = re.search(r'PnL: .?([-+]?[\d.]+)', line)
            if m:
                pnl = float(m.group(1))
                if '\u274c' in line:
                    pnl = -abs(pnl)
                results.append(pnl)

wins = [r for r in results if r > 0]
losses = [r for r in results if r <= 0]
total = sum(results)
print(f'Total Trades: {len(results)}')
print(f'Wins: {len(wins)} | Losses: {len(losses)}')
if results:
    print(f'Win Rate: {len(wins)/len(results)*100:.1f}%')
print(f'Total PnL: ${total:.2f}')
if wins:
    print(f'Avg Win: ${sum(wins)/len(wins):.2f}')
if losses:
    print(f'Avg Loss: ${sum(losses)/len(losses):.2f}')
if wins and losses:
    print(f'Profit Factor: {sum(wins)/abs(sum(losses)):.2f}')
