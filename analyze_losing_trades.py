import os
import re
from datetime import datetime

def get_session(utc_hour):
    if 0 <= utc_hour < 8:
        return "Asian"
    elif 8 <= utc_hour < 16:
        return "London"
    else:
        return "NY"

# Use os.popen to tail the last 50000 lines
lines = os.popen('tail -n 50000 /root/.pm2/logs/trading-bot-error.log').readlines()

executions = []
current_block = []
in_block = False

for line in lines:
    if "SMC:" in line and "Swing Highs" in line:
        current_block = [line]
        in_block = True
    elif in_block:
        current_block.append(line)
        if "EXECUTING" in line:
            executions.append(current_block)
            in_block = False

print(f"Found {len(executions)} execution blocks in the last 50000 lines.")

for block in executions[-15:]:
    trade_time = ""
    coin = ""
    score = ""
    confluence = ""
    sweep_candles = ""
    
    for line in block:
        if "EXECUTING" in line:
            m = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[(.*?)\] EXECUTING.*Score: (\d+)', line)
            if m:
                trade_time = m.group(1)
                coin = m.group(2)
                score = m.group(3)
        if "Confluence:" in line:
            if "ALIGNED" in line:
                confluence = "Aligned"
            elif "NEUTRAL" in line:
                confluence = "Neutral"
            elif "CONTRADICT" in line:
                confluence = "Contradict"
            else:
                confluence = line.strip()
            
    sweep_bar = 0
    choch_bar = 0
    for line in block:
        if "swept low" in line or "swept high" in line:
            m = re.search(r'Candle (\d+) swept', line)
            if m:
                sweep_bar = int(m.group(1))
        if "broke above" in line or "broke below" in line:
            m = re.search(r'bar (\d+)', line)
            if m:
                choch_bar = int(m.group(1))
                
    candles_after = 200 - sweep_bar if sweep_bar > 0 else "Unknown"
    
    if trade_time:
        dt = datetime.strptime(trade_time, "%Y-%m-%d %H:%M:%S")
        session = get_session(dt.hour)
        print(f"[{trade_time}] {coin} | Score: {score} | 4H/15m Conf: {confluence} | Entry {candles_after} candles after sweep | Session: {session}")
