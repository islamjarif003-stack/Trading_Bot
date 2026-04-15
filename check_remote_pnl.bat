@echo off
title 📈 Bot PnL - LIVE MONITOR
mode con: cols=80 lines=50

:loop
cls
echo.
echo    ╔══════════════════════════════════════════════════════════╗
echo    ║   📈 LIVE PnL MONITOR  (Auto-refresh every 5 sec)      ║
echo    ║   Press Ctrl+C to stop                                  ║
echo    ╚══════════════════════════════════════════════════════════╝
echo.
ssh root@103.174.50.149 "python3 /opt/trading-bot/check_pnl.py && echo '' && cat /opt/trading-bot/kelly_state.json"
echo.
echo    ⏳ Refreshing in 7 seconds...
timeout /t 7 /nobreak >nul
goto loop
