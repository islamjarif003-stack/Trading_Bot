import yfinance as yf
import pandas as pd
import vectorbt as vbt

def calculate_rsi(data, window=14):
    """Calculates Wilder's RSI."""
    delta = data.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    # Wilder's smoothing
    ema_up = up.ewm(com=window-1, adjust=False).mean()
    ema_down = down.ewm(com=window-1, adjust=False).mean()
    rs = ema_up / ema_down
    rsi = 100 - (100 / (1 + rs))
    return rsi

def main():
    print("Fetching last 1 year of BTC-USD data (1h timeframe)...")
    # Download 1-hour data for the last 1 year
    df = yf.download("BTC-USD", period="1y", interval="1h")
    
    if df.empty:
        print("Failed to download data. Try again later.")
        return
        
    # Handle yfinance multi-index (present in newer yfinance versions)
    if isinstance(df.columns, pd.MultiIndex):
         close = df['Close'].squeeze()
    else:
         close = df['Close']
         
    # Drop any missing values 
    close = close.dropna()
    
    print("Calculating indicators...")
    # Calculate 200 EMA
    ema200 = close.ewm(span=200, adjust=False).mean()
    
    # Calculate RSI 14
    rsi14 = calculate_rsi(close, 14)
    
    print("Generating signals based on strict conditions...")
    # --- Entry Conditions ---
    # Long: Price > 200 EMA AND RSI crosses above 40
    long_trend = close > ema200
    rsi_cross_above_40 = (rsi14.shift(1) < 40) & (rsi14 >= 40)
    entries = long_trend & rsi_cross_above_40
    
    # Short: Price < 200 EMA AND RSI crosses below 60
    short_trend = close < ema200
    rsi_cross_below_60 = (rsi14.shift(1) > 60) & (rsi14 <= 60)
    short_entries = short_trend & rsi_cross_below_60
    
    # --- Exit Conditions ---
    # Long Exit: RSI crosses above 70
    exits = (rsi14.shift(1) < 70) & (rsi14 >= 70)
    
    # Short Exit: RSI crosses below 30
    short_exits = (rsi14.shift(1) > 30) & (rsi14 <= 30)
    
    print("Running VectorBT Portfolio Simulation...")
    # Portfolio Simulation Initialization
    pf = vbt.Portfolio.from_signals(
        close=close,
        entries=entries,
        exits=exits,
        short_entries=short_entries,
        short_exits=short_exits,
        init_cash=10000,     # Initial Capital: $10,000
        fees=0.001,          # Exchange Fees: 0.1%
        slippage=0.0005,     # Slippage: 0.05%
        freq='1h'            # 1-Hour Timeframe
    )
    
    print("\n" + "="*50)
    print("BACKTEST RESULTS (HIGHLIGHTS)")
    print("="*50)
    stats = pf.stats()
    print(stats)
    
    try:
        total_return = stats['Total Return [%]']
        win_rate = stats['Win Rate [%]']
        max_drawdown = stats['Max Drawdown [%]']
        
        print("\n" + "="*50)
        print("🔥 STRATEGY PERFORMANCE HIGHLIGHTS 🔥")
        print("="*50)
        print(f"Total Return: {total_return:.2f}%")
        print(f"Win Rate:     {win_rate:.2f}%")
        print(f"Max Drawdown: {max_drawdown:.2f}%")
        print("="*50)
    except KeyError:
        pass # In case column names vary by vectorbt version
    
    print("\nOpening interactive chart in your browser...")
    # Plot the results and open in browser
    fig = pf.plot()
    fig.show()

if __name__ == "__main__":
    main()
