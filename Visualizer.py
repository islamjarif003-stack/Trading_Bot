import multiprocessing
import time
import pandas as pd
import numpy as np
import mplfinance as mpf
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from binance.client import Client

# ─── CONFIGURATION (must match SignalEngine) ─────────────────────────────────
SYMBOL = "BTCUSDT"
KLINE_INTERVAL = Client.KLINE_INTERVAL_5MINUTE
KLINE_LIMIT = 100

def _empty_df():
    """Return a tiny valid OHLCV DataFrame so plt doesn't crash on init."""
    idx = pd.date_range('2025-01-01', periods=2, freq='5min')
    return pd.DataFrame({
        'Open': [1, 1], 'High': [1, 1], 'Low': [1, 1],
        'Close': [1, 1], 'Volume': [0, 0],
    }, index=idx)

def _run_chart_window(shared_state):
    """
    This function runs in a completely separate processing kernel.
    It reads raw primitive lists from shared_state and reconstructs the data for mplfinance.
    Native interactive tools (Zoom, Pan) work flawlessly because plt.show(block=True) 
    captures the native event loop gracefully without tying up the parent trading loop.
    """
    mc = mpf.make_marketcolors(
        up='#26a69a', down='#ef5350',
        edge={'up': '#26a69a', 'down': '#ef5350'},
        wick={'up': '#26a69a', 'down': '#ef5350'},
        volume='in',
    )
    style = mpf.make_mpf_style(
        marketcolors=mc,
        base_mpf_style='nightclouds',
        gridstyle='-', gridcolor='#2a2e39',
        facecolor='#131722', figcolor='#131722',
        rc={'axes.labelcolor': '#9598a1', 'xtick.color': '#9598a1',
            'ytick.color': '#9598a1'},
    )
    
    fig, axes = mpf.plot(
        _empty_df(), type='candle', style=style,
        volume=True, returnfig=True, figsize=(13, 8),
        title='\nBTCUSDT — Live Analysis (Interactive)'
    )
    fig.canvas.manager.set_window_title('Binance Quant Bot — LIVE CHART')

    plt.ion()
    plt.show(block=False)

    while True:
        chart_data = shared_state.get('chart_data')
        overlays = shared_state.get('overlays')
        
        if not chart_data or not overlays:
            plt.pause(1.5)
            if not plt.fignum_exists(fig.number):
                break
            continue

        try:
            # Reconstruct exactly the dataframe format mplfinance wants
            df = pd.DataFrame(chart_data)
            df.index = pd.to_datetime(df['index'])
            df = df[['open', 'high', 'low', 'close', 'volume']]
            df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
            
            poc = overlays.get('poc')
            mag1 = overlays.get('mag1')
            mag2 = overlays.get('mag2')
            gp_low = overlays.get('gp_low')
            gp_high = overlays.get('gp_high')
            rh = overlays.get('rh')
            rl = overlays.get('rl')

            # Capture interactive limits before clearing lines 
            # so panning/zooming user isn't brutally reset.
            curr_xlim = axes[0].get_xlim()
            curr_ylim = axes[0].get_ylim()
            n = len(df)
            
            # User is "zoomed in" if the X window covers fewer candles than available (-2 threshold for padding)
            # However, if the X window is currently trapped to the dummy data (-0.5 to 1.5), we shouldn't trap them.
            user_zoomed_in = False
            if curr_xlim[1] - curr_xlim[0] < n - 2:
                if curr_xlim[1] > 2: # Meaning they aren't trapped on the first 2 dummy candles
                    user_zoomed_in = True

            for ax in axes:
                for c in list(ax.collections): c.remove()
                for l in list(ax.lines): l.remove()
                for p in list(ax.patches): p.remove()

            add_plots = []
            # POC
            if poc is not None and poc > 0:
                add_plots.append(mpf.make_addplot([poc] * n, ax=axes[0], color='#ff4444', linestyle='--', width=2))
            
            # Liquidity
            if mag2 != mag1 and mag1 is not None and mag2 is not None:
                add_plots.append(mpf.make_addplot([mag1] * n, ax=axes[0], color='#bb86fc', linestyle=':', width=1.5))
                add_plots.append(mpf.make_addplot([mag2] * n, ax=axes[0], color='#bb86fc', linestyle=':', width=1.5))

            # Volume Colors
            if len(axes) > 2 and 'Volume' in df:
                df_colors = ['#26a69a' if c >= o else '#ef5350' for c, o in zip(df['Close'], df['Open'])]
                add_plots.append(mpf.make_addplot(df['Volume'], type='bar', ax=axes[2], color=df_colors))

            # Draw Base 
            if add_plots:
                mpf.plot(df, type='candle', ax=axes[0], addplot=add_plots, style=style)
            else:
                mpf.plot(df, type='candle', ax=axes[0], style=style)

            # Fibonacci Golden Pocket
            if gp_low is not None and gp_high is not None and gp_low > 0 and gp_high > 0:
                axes[0].axhspan(gp_low, gp_high, color='#ffd700', alpha=0.15, label='Golden Pocket')

            # Liquidity Range Sweep lines
            if rh is not None and rh > 0:
                axes[0].axhline(rh, color='#42a5f5', ls='-.', lw=1, alpha=0.6, label='Range High')
            if rl is not None and rl > 0:
                axes[0].axhline(rl, color='#42a5f5', ls='-.', lw=1, alpha=0.6, label='Range Low')

            # Limit Scaling Logic
            if not user_zoomed_in:
                # Autoscaling with margins explicitly ignoring 0 values
                price_min = df['Low'].min()
                price_max = df['High'].max()
                price_margin = (price_max - price_min) * 0.05 # 5% margin
                if price_margin == 0:
                    price_margin = price_max * 0.005

                # Compare only with valid strictly positive overlay values
                levels = [price_min, price_max]
                if gp_low is not None and gp_low > 0: levels.append(gp_low)
                if gp_high is not None and gp_high > 0: levels.append(gp_high)
                if rl is not None and rl > 0: levels.append(rl)
                if rh is not None and rh > 0: levels.append(rh)

                y_lower = min(levels)
                y_upper = max(levels)
                
                axes[0].set_ylim(y_lower - price_margin, y_upper + price_margin)
                
                # Map default X limits neatly
                axes[0].set_xlim(-0.5, n + 0.5)
            else:
                # Re-apply tracked boundaries because clearing patches auto-adjusts limits
                axes[0].set_xlim(curr_xlim)
                axes[0].set_ylim(curr_ylim)

            # Independent strictly scaling Volume so it doesn't squash the page
            if len(axes) > 2 and 'Volume' in df:
                vol_max = df['Volume'].max()
                if vol_max > 0:
                    axes[2].set_ylim(0, vol_max * 1.05)

            axes[0].legend(loc='upper left', fontsize=8, facecolor='#1e222d',
                           labelcolor='#d1d4dc', edgecolor='#363a45')
            axes[0].set_title('BTCUSDT M5 — POC · Fib GP · Liq Zones', color='#d1d4dc')

        except Exception as e:
            pass

        # Let the UI backend process native events without stealing focus
        try:
            fig.canvas.draw_idle()
            # Run events gracefully over 1.5 seconds so we don't un-minimize
            for _ in range(15):
                fig.canvas.flush_events()
                time.sleep(0.1)
        except Exception:
            pass
        
        # Monitor window destruction
        if not plt.fignum_exists(fig.number):
            break


class LiveVisualizer:
    """
    Main Thread Interface for the Live Chart.
    The visualizer runs natively out-of-process ensuring absolutely no freezing over extended 
    periods or networking slowdowns. It strictly writes primitive data to standard shared memory via Manager.
    """

    def __init__(self, client: Client):
        self.client = client
        
        # 1. Spawn Manager dict to handle seamless shared memory
        self.manager = multiprocessing.Manager()
        self.shared_state = self.manager.dict()
        self.shared_state['chart_data'] = {}
        self.shared_state['overlays'] = {}
        
        # Deploy non-blocking execution process 
        self.chart_process = multiprocessing.Process(
            target=_run_chart_window, 
            args=(self.shared_state,),
            daemon=True
        )
        self.chart_process.start()

    def _fetch_candles(self) -> pd.DataFrame:
        """Fetch fresh M5 candles directly from Binance (in the main pipeline sequence)."""
        raw = self.client.futures_klines(
            symbol=SYMBOL, interval=KLINE_INTERVAL, limit=KLINE_LIMIT,
        )
        df = pd.DataFrame(raw, columns=[
            'open_time', 'Open', 'High', 'Low', 'Close', 'Volume',
            'close_time', 'quote_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore',
        ])
        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            df[col] = df[col].astype(float)
        df['Date'] = pd.to_datetime(df['open_time'], unit='ms')
        df.set_index('Date', inplace=True)
        return df[['Open', 'High', 'Low', 'Close', 'Volume']]

    @staticmethod
    def _calc_fib_levels(df):
        macro_high = df['High'].max()
        macro_low = df['Low'].min()
        diff = macro_high - macro_low
        if diff == 0:
            return None, None
        lvl_05 = macro_low + 0.5 * diff
        lvl_0618 = macro_low + 0.382 * diff
        return min(lvl_05, lvl_0618), max(lvl_05, lvl_0618)

    @staticmethod
    def _calc_poc(df, num_bins=50):
        tp = (df['High'] + df['Low'] + df['Close']) / 3.0
        bin_edges = np.linspace(df['Low'].min(), df['High'].max(), num_bins + 1)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
        indices = np.clip(np.digitize(tp.values, bin_edges) - 1, 0, num_bins - 1)
        vol_profile = np.zeros(num_bins)
        for i, v in zip(indices, df['Volume'].values):
            vol_profile[i] += v
        sorted_idx = np.argsort(vol_profile)[::-1]
        poc = float(bin_centers[sorted_idx[0]])
        mag1 = poc
        mag2 = float(bin_centers[sorted_idx[1]]) if len(sorted_idx) > 1 else poc
        return poc, mag1, mag2

    @staticmethod
    def _calc_sweep(df, lookback=24):
        if len(df) < lookback + 1:
            return 0, 0
        r = df.iloc[-(lookback + 1):-1]
        return float(r['High'].max()), float(r['Low'].min())

    def update(self):
        """
        Updates shared memory asynchronously. Keeps main thread execution to strict IO limits.
        """
        try:
            df = self._fetch_candles()
            if df.empty:
                return

            poc, mag1, mag2 = self._calc_poc(df)
            gp_low, gp_high = self._calc_fib_levels(df)
            rh, rl = self._calc_sweep(df)

            # Dump primitive datatypes into shared manager dict
            self.shared_state['chart_data'] = {
                'index': df.index.astype(np.int64).tolist(),
                'open': df['Open'].tolist(),
                'high': df['High'].tolist(),
                'low': df['Low'].tolist(),
                'close': df['Close'].tolist(),
                'volume': df['Volume'].tolist()
            }
            
            self.shared_state['overlays'] = {
                'poc': poc, 'mag1': mag1, 'mag2': mag2,
                'gp_low': gp_low, 'gp_high': gp_high,
                'rh': rh, 'rl': rl
            }

        except Exception as e:
            pass # Failsafe against chart exceptions crushing logic thread

    def pause(self, interval):
        """
        Replaces legacy plt.pause with a flat delay because processing is decoupled.
        """
        time.sleep(interval)
