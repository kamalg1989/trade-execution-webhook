# 📈 Technical Analysis & Charting API
## Daily + Weekly Charts with EMA, ATR, RSI, MACD & More

---

## 🎯 VPS Constraints Impact

### Your VPS Resources
```
CPU: 1 vCPU
RAM: 1 GB (shared between PostgreSQL + API + system)
Disk: 25 GB (market data ~200 MB + OS/backups ~5 GB = 19.8 GB free)
```

### Optimization Strategy for 1 GB RAM
1. **PostgreSQL**: Modest settings (shared_buffers=128MB, effective_cache_size=256MB)
2. **API**: Lightweight FastAPI, connection pool 5-10 (not 20)
3. **Caching**: Cache-aside pattern with small in-memory cache (100-200 MB)
4. **Chart Generation**: On-demand (not pre-generated) to save disk
5. **Async Processing**: Use task queue for heavy computations

---

## 📊 Useful Metrics Beyond Volume

### Momentum Indicators (Backtesting Critical)
| Indicator | Formula | Use Case | Calculation Time |
|-----------|---------|----------|-----------------|
| **ATR (14)** | Avg True Range over 14 bars | Stop-loss placement, volatility measure | O(n) |
| **RSI (14)** | 100 - (100 / (1 + RS)) | Overbought/oversold detection | O(n) |
| **MACD** | 12-EMA - 26-EMA, signal=9-EMA | Trend direction & momentum | O(n) |
| **Stochastic** | (Close - Lowest Low) / (Highest High - Lowest Low) × 100 | Trend reversal | O(n) |

### Volatility Indicators
| Indicator | Formula | Use Case | Calculation Time |
|-----------|---------|----------|-----------------|
| **Bollinger Bands** | SMA ± (2 × StdDev) | Support/resistance, volatility | O(n) |
| **Volume Profile** | Aggregate volume at price levels | Key support/resistance | O(n) memory |

### Trend Indicators
| Indicator | Formula | Use Case | Calculation Time |
|-----------|---------|----------|-----------------|
| **EMA (10, 21, 50, 200)** | Weighted moving average | Trend direction, fast/slow crosses | O(n) |
| **Pivot Points** | (H + L + C) / 3 | Daily support/resistance | O(1) |

### Volume Analysis
| Indicator | Formula | Use Case | Calculation Time |
|-----------|---------|----------|-----------------|
| **OBV (On Balance Volume)** | Cumulative volume if close > prev | Volume-based momentum | O(n) |
| **Volume SMA** | Average volume over 20 days | Liquidity assessment | O(n) |

### Recommended for Your Backtester
```
Essential (calculate daily):
  ✅ EMA 10, 21, 50, 200
  ✅ ATR 14 (for stop placement)
  ✅ RSI 14 (for entry/exit signals)
  ✅ MACD (trend confirmation)
  ✅ Volume SMA 20 (liquidity check)

Nice-to-have (calculate on-demand):
  • Bollinger Bands (volatility context)
  • Pivot Points (resistance identification)
  • Stochastic (mean reversion trades)
  • OBV (volume-trend divergence)
```

---

## 🏗️ Charting API Architecture

### Endpoint Design

```python
# GET /api/v1/charts/daily
# Returns: SVG or PNG chart with all indicators
GET /api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31&indicators=ema,atr,rsi
Response: SVG image (embeddable) or PNG

# GET /api/v1/charts/weekly
GET /api/v1/charts/weekly?symbol=INFY&from=2020-01-01&to=2024-12-31&indicators=ema,macd

# GET /api/v1/indicators
# Returns: Raw indicator values (JSON) for programmatic use
GET /api/v1/indicators?symbol=INFY&from=2024-01-01&to=2024-12-31&indicators=ema,rsi,atr
Response:
{
  "meta": {
    "symbol": "INFY",
    "from": "2024-01-01",
    "to": "2024-12-31",
    "period": "daily"
  },
  "data": [
    {
      "date": "2024-01-01",
      "open": 1450.25,
      "high": 1465.50,
      "low": 1445.00,
      "close": 1455.75,
      "volume": 8500000,
      "ema_10": 1452.10,
      "ema_21": 1448.50,
      "ema_50": 1440.20,
      "ema_200": 1425.80,
      "atr_14": 12.35,
      "rsi_14": 65.5,
      "macd": 3.45,
      "macd_signal": 2.80,
      "macd_hist": 0.65
    },
    ...
  ]
}
```

---

## 🔧 Implementation: Indicator Calculations

### Using `ta-lib` vs `pandas_ta` vs Manual

```python
# Option 1: Manual NumPy (FAST, LOW MEMORY, VPS-FRIENDLY) ✅
# Option 2: pandas_ta (EASY, RELIABLE, MODERATE MEMORY)
# Option 3: ta-lib (FASTEST, C-compiled, requires build)

# RECOMMENDATION: pandas_ta (best balance for VPS)
pip install pandas-ta
```

### Indicator Calculation Module

```python
# indicators.py - Calculate all technical indicators
import pandas as pd
import pandas_ta as ta
import numpy as np

class TechnicalIndicators:
    """Calculate technical indicators efficiently"""
    
    def __init__(self, ohlcv_df: pd.DataFrame):
        """
        Args:
            ohlcv_df: DataFrame with columns [open, high, low, close, volume]
        """
        self.df = ohlcv_df.copy()
        self.indicators = {}
    
    def calculate_ema(self, periods=[10, 21, 50, 200]) -> pd.DataFrame:
        """Calculate Exponential Moving Averages"""
        for period in periods:
            self.df[f'ema_{period}'] = self.df['close'].ewm(span=period, adjust=False).mean()
        return self.df[[f'ema_{period}' for period in periods]]
    
    def calculate_atr(self, period=14) -> pd.Series:
        """Calculate Average True Range (volatility)"""
        high_low = self.df['high'] - self.df['low']
        high_close = np.abs(self.df['high'] - self.df['close'].shift())
        low_close = np.abs(self.df['low'] - self.df['close'].shift())
        
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        self.df['atr'] = true_range.rolling(period).mean()
        return self.df['atr']
    
    def calculate_rsi(self, period=14) -> pd.Series:
        """Calculate Relative Strength Index (momentum)"""
        delta = self.df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        self.df['rsi'] = 100 - (100 / (1 + rs))
        return self.df['rsi']
    
    def calculate_macd(self, fast=12, slow=26, signal=9) -> tuple:
        """Calculate MACD (trend following)"""
        ema_fast = self.df['close'].ewm(span=fast, adjust=False).mean()
        ema_slow = self.df['close'].ewm(span=slow, adjust=False).mean()
        
        self.df['macd'] = ema_fast - ema_slow
        self.df['macd_signal'] = self.df['macd'].ewm(span=signal, adjust=False).mean()
        self.df['macd_hist'] = self.df['macd'] - self.df['macd_signal']
        
        return self.df[['macd', 'macd_signal', 'macd_hist']]
    
    def calculate_bollinger_bands(self, period=20, std_dev=2) -> tuple:
        """Calculate Bollinger Bands (volatility)"""
        sma = self.df['close'].rolling(period).mean()
        std = self.df['close'].rolling(period).std()
        
        self.df['bb_upper'] = sma + (std * std_dev)
        self.df['bb_middle'] = sma
        self.df['bb_lower'] = sma - (std * std_dev)
        
        return self.df[['bb_upper', 'bb_middle', 'bb_lower']]
    
    def calculate_obv(self) -> pd.Series:
        """Calculate On Balance Volume (volume momentum)"""
        obv = np.where(self.df['close'] > self.df['close'].shift(1), 
                      self.df['volume'], 
                      np.where(self.df['close'] < self.df['close'].shift(1), 
                              -self.df['volume'], 0))
        self.df['obv'] = pd.Series(obv).cumsum()
        return self.df['obv']
    
    def calculate_volume_sma(self, period=20) -> pd.Series:
        """Calculate Volume SMA (liquidity)"""
        self.df['volume_sma'] = self.df['volume'].rolling(period).mean()
        return self.df['volume_sma']
    
    def calculate_all(self) -> pd.DataFrame:
        """Calculate all standard indicators"""
        self.calculate_ema([10, 21, 50, 200])
        self.calculate_atr(14)
        self.calculate_rsi(14)
        self.calculate_macd(12, 26, 9)
        self.calculate_bollinger_bands(20, 2)
        self.calculate_volume_sma(20)
        self.calculate_obv()
        
        return self.df

# Usage example:
# df = pd.read_sql("SELECT * FROM ohlcv WHERE symbol='INFY'", conn)
# indicators = TechnicalIndicators(df)
# result = indicators.calculate_all()
```

---

## 🎨 Charting Implementation

### Option 1: mplfinance (Lightweight, Good for VPS)
**Pros**: Lightweight, matplotlib-based, good for traditional charts
**Cons**: Limited interactivity

```python
# charting.py - Generate charts with mplfinance
import mplfinance as mpf
import pandas as pd
from io import BytesIO

def create_daily_chart(symbol: str, df: pd.DataFrame, indicators: list) -> bytes:
    """
    Generate candlestick chart with indicators
    
    Args:
        symbol: Stock symbol
        df: DataFrame with OHLCV + indicators
        indicators: List of indicators to plot ['ema_10', 'rsi_14', etc.]
    
    Returns:
        PNG bytes
    """
    
    # Add additional plots (APD = Additional Plots Dictionary)
    apd = []
    
    if 'ema_10' in indicators:
        apd.append(mpf.make_addplot(df['ema_10'], color='blue', width=1.5, panel=0))
    if 'ema_21' in indicators:
        apd.append(mpf.make_addplot(df['ema_21'], color='green', width=1.5, panel=0))
    if 'ema_50' in indicators:
        apd.append(mpf.make_addplot(df['ema_50'], color='orange', width=1.5, panel=0))
    if 'ema_200' in indicators:
        apd.append(mpf.make_addplot(df['ema_200'], color='red', width=1.5, panel=0))
    
    # RSI in separate panel
    if 'rsi_14' in indicators:
        apd.append(mpf.make_addplot(df['rsi_14'], color='purple', panel=1, secondary_y=False))
        apd.append(mpf.make_addplot([70]*len(df), color='gray', panel=1, type='line', linestyle='--'))
        apd.append(mpf.make_addplot([30]*len(df), color='gray', panel=1, type='line', linestyle='--'))
    
    # MACD in separate panel
    if 'macd' in indicators:
        colors = ['green' if x > 0 else 'red' for x in df['macd_hist']]
        apd.append(mpf.make_addplot(df['macd'], color='blue', panel=2))
        apd.append(mpf.make_addplot(df['macd_signal'], color='red', panel=2))
        apd.append(mpf.make_addplot(df['macd_hist'], type='bar', color=colors, panel=2))
    
    # Style configuration
    style = mpf.make_mpf_style(
        base_mpf_style='charles',
        gridcolor='#444444',
        y_on_right=True
    )
    
    # Generate chart
    output = BytesIO()
    
    mpf.plot(
        df[['open', 'high', 'low', 'close', 'volume']],
        type='candle',
        volume=True,
        addplot=apd,
        style=style,
        returnfig=True,
        figsize=(14, 8),
        title=f"{symbol} - Daily Chart",
        ylabel="Price",
        ylabel_lower="Volume"
    )
    
    # Save to bytes
    mpf.plot(
        df[['open', 'high', 'low', 'close', 'volume']],
        type='candle',
        volume=True,
        addplot=apd,
        style=style,
        savefig=dict(fname=output, dpi=100, pad_inches=0.5),
        title=f"{symbol} - Daily Chart"
    )
    
    output.seek(0)
    return output.getvalue()
```

### Option 2: Plotly (Interactive, More Memory)
**Pros**: Interactive, web-native, good for dashboards
**Cons**: Larger memory footprint (not ideal for 1 GB VPS)

```python
import plotly.graph_objects as go

def create_interactive_chart(symbol: str, df: pd.DataFrame) -> str:
    """Generate interactive Plotly chart (returns HTML)"""
    
    fig = go.Figure()
    
    # Candlestick
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['open'],
        high=df['high'],
        low=df['low'],
        close=df['close'],
        name='OHLC'
    ))
    
    # EMAs
    fig.add_trace(go.Scatter(
        x=df.index, y=df['ema_10'],
        name='EMA 10', line=dict(color='blue', width=2)
    ))
    fig.add_trace(go.Scatter(
        x=df.index, y=df['ema_21'],
        name='EMA 21', line=dict(color='green', width=2)
    ))
    
    fig.update_layout(
        title=f"{symbol} - Daily Chart",
        yaxis_title="Price",
        xaxis_title="Date",
        template="plotly_dark",
        height=600
    )
    
    return fig.to_html()
```

### Option 3: Lightweight SVG (RECOMMENDED FOR VPS)
**Pros**: Minimal memory, fast, scalable, embeddable
**Cons**: Manual chart rendering

```python
# svg_chart.py - Ultra-lightweight SVG generation
def create_svg_chart(symbol: str, df: pd.DataFrame, width=1200, height=600) -> str:
    """
    Generate chart as SVG (no image library needed)
    Minimal memory footprint, perfect for VPS
    """
    
    # Normalize data to canvas coordinates
    prices = pd.concat([df['open'], df['high'], df['low'], df['close']])
    min_price, max_price = prices.min(), prices.max()
    price_range = max_price - min_price
    
    # Canvas padding
    padding = 50
    chart_width = width - 2 * padding
    chart_height = height - 2 * padding
    
    # Scaling functions
    def x_coord(i):
        return padding + (i / len(df)) * chart_width
    
    def y_coord(price):
        return height - padding - ((price - min_price) / price_range) * chart_height
    
    svg_lines = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
        '<rect width="100%" height="100%" fill="#1a1a1a"/>',
        f'<text x="10" y="25" font-size="16" fill="#fff">{symbol}</text>',
    ]
    
    # Draw candlesticks
    candle_width = chart_width / len(df) * 0.7
    for i, (idx, row) in enumerate(df.iterrows()):
        x = x_coord(i)
        y_open = y_coord(row['open'])
        y_close = y_coord(row['close'])
        y_high = y_coord(row['high'])
        y_low = y_coord(row['low'])
        
        color = 'green' if row['close'] > row['open'] else 'red'
        
        # Wick
        svg_lines.append(
            f'<line x1="{x}" y1="{y_high}" x2="{x}" y2="{y_low}" '
            f'stroke="{color}" stroke-width="1"/>'
        )
        
        # Body
        body_height = abs(y_open - y_close) or 1
        svg_lines.append(
            f'<rect x="{x - candle_width/2}" y="{min(y_open, y_close)}" '
            f'width="{candle_width}" height="{body_height}" '
            f'fill="{color}" stroke="{color}"/>'
        )
    
    # Draw EMA lines
    if 'ema_10' in df.columns:
        ema_path = ' '.join([
            f"{x_coord(i)},{y_coord(val)}"
            for i, val in enumerate(df['ema_10']) if not pd.isna(val)
        ])
        svg_lines.append(
            f'<polyline points="{ema_path}" fill="none" stroke="blue" stroke-width="2"/>'
        )
    
    # Grid lines
    for i in range(10):
        y = padding + (i / 10) * chart_height
        svg_lines.append(
            f'<line x1="{padding}" y1="{y}" x2="{width-padding}" y2="{y}" '
            f'stroke="#333" stroke-width="0.5"/>'
        )
    
    svg_lines.append('</svg>')
    return '\n'.join(svg_lines)
```

---

## ⚡ FastAPI Charting Endpoints

```python
# charting_api.py
from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse, HTMLResponse
from datetime import date
import asyncpg
import pandas as pd

from indicators import TechnicalIndicators
from svg_chart import create_svg_chart

app = FastAPI()

@app.get("/api/v1/charts/daily", responses={200: {"content": {"image/svg+xml": {}}}})
async def get_daily_chart(
    symbol: str = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...),
    indicators: str = Query("ema,rsi,macd"),  # comma-separated
    format: str = Query("svg", regex="^(svg|json)$")
):
    """Generate daily chart with technical indicators"""
    
    # 1. Fetch OHLCV from database
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT trading_date, open, high, low, close, volume
            FROM ohlcv_data
            WHERE symbol = $1 AND trading_date BETWEEN $2 AND $3
            ORDER BY trading_date
        """, symbol, from_date, to_date)
    
    if not rows:
        return {"error": "No data found"}
    
    # 2. Convert to DataFrame
    df = pd.DataFrame(rows)
    df['trading_date'] = pd.to_datetime(df['trading_date'])
    df.set_index('trading_date', inplace=True)
    
    # 3. Calculate indicators
    indicator_list = [x.strip() for x in indicators.split(',')]
    tech = TechnicalIndicators(df)
    
    if 'ema' in indicator_list:
        tech.calculate_ema([10, 21, 50, 200])
    if 'rsi' in indicator_list:
        tech.calculate_rsi(14)
    if 'atr' in indicator_list:
        tech.calculate_atr(14)
    if 'macd' in indicator_list:
        tech.calculate_macd(12, 26, 9)
    
    df = tech.df
    
    # 4. Return format
    if format == "svg":
        svg_content = create_svg_chart(symbol, df)
        return StreamingResponse(
            iter([svg_content]),
            media_type="image/svg+xml"
        )
    else:  # json
        return {
            "symbol": symbol,
            "data": df[['open', 'high', 'low', 'close', 'volume'] + 
                      [c for c in df.columns if c in ['ema_10', 'ema_21', 'ema_50', 'ema_200', 'rsi_14', 'atr', 'macd']]
                     ].to_dict(orient='records')
        }

@app.get("/api/v1/charts/weekly", responses={200: {"content": {"image/svg+xml": {}}}})
async def get_weekly_chart(
    symbol: str = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...),
    indicators: str = Query("ema,rsi")
):
    """Generate weekly chart (aggregated from daily)"""
    
    # 1. Fetch daily data
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT trading_date, open, high, low, close, volume
            FROM ohlcv_data
            WHERE symbol = $1 AND trading_date BETWEEN $2 AND $3
            ORDER BY trading_date
        """, symbol, from_date, to_date)
    
    df = pd.DataFrame(rows)
    df['trading_date'] = pd.to_datetime(df['trading_date'])
    
    # 2. Aggregate to weekly (Friday close)
    df.set_index('trading_date', inplace=True)
    weekly = df.resample('W-FRI').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()
    
    # 3. Calculate indicators on weekly data
    tech = TechnicalIndicators(weekly)
    # ... same as daily
    
    svg_content = create_svg_chart(symbol, weekly)
    return StreamingResponse(iter([svg_content]), media_type="image/svg+xml")

@app.get("/api/v1/indicators")
async def get_indicators_data(
    symbol: str = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...),
    indicators: str = Query("ema,rsi,atr,macd"),
    period: str = Query("daily", regex="^(daily|weekly)$")
):
    """Return raw indicator values as JSON"""
    
    # Fetch and calculate (same as above)
    # Return JSON with all indicator values
    pass
```

---

## 📊 Weekly Chart Generation

### Daily → Weekly Aggregation
```python
# Convert daily OHLCV to weekly (Friday close)
def daily_to_weekly(daily_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate daily candles to weekly
    - Open: First trading day's open
    - High: Highest price in the week
    - Low: Lowest price in the week
    - Close: Last trading day's close (Friday)
    - Volume: Sum of week's volume
    """
    daily_df['date'] = pd.to_datetime(daily_df['date'])
    daily_df.set_index('date', inplace=True)
    
    weekly = daily_df.resample('W-FRI').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()
    
    return weekly
```

---

## 💾 Caching Strategy (1 GB RAM Limit)

```python
# chart_cache.py - Simple in-memory cache with LRU eviction
from functools import lru_cache
import hashlib

class ChartCache:
    def __init__(self, max_size=100):  # ~100 cached charts = ~50-100 MB
        self.cache = {}
        self.max_size = max_size
    
    def _key(self, symbol, from_date, to_date, indicators):
        params = f"{symbol}:{from_date}:{to_date}:{indicators}"
        return hashlib.md5(params.encode()).hexdigest()
    
    def get(self, symbol, from_date, to_date, indicators):
        key = self._key(symbol, from_date, to_date, indicators)
        return self.cache.get(key)
    
    def set(self, symbol, from_date, to_date, indicators, svg_content):
        if len(self.cache) >= self.max_size:
            # Remove oldest (FIFO)
            oldest_key = next(iter(self.cache))
            del self.cache[oldest_key]
        
        key = self._key(symbol, from_date, to_date, indicators)
        self.cache[key] = svg_content

chart_cache = ChartCache(max_size=100)

# In API:
cached = chart_cache.get(symbol, from_date, to_date, indicators)
if cached:
    return StreamingResponse(iter([cached]), media_type="image/svg+xml")

# ... generate chart ...
chart_cache.set(symbol, from_date, to_date, indicators, svg_content)
```

---

## 🚀 VPS Deployment Strategy

### Directory Structure
```
/root/trade-execution-webhook/
├── Webhook-app/
│   ├── app.py                  (Telegram webhook + entries)
│   ├── sl_engine.py            (Stop-loss logic)
│   ├── entry_engine.py         (Entry execution)
│   │
│   ├── market_data_api/        (NEW)
│   │   ├── __init__.py
│   │   ├── main.py             (FastAPI app)
│   │   ├── indicators.py       (Calculation logic)
│   │   ├── charting.py         (SVG/PNG generation)
│   │   └── cache.py            (LRU cache)
│   │
│   └── requirements.txt
│
└── systemd/
    ├── trade-webhook.service   (existing)
    └── market-data-api.service (NEW)
```

### Memory-Optimized Settings

```bash
# PostgreSQL: Modest settings for 1 GB RAM
sudo nano /etc/postgresql/14/main/postgresql.conf

shared_buffers = 128MB           # 1/4 of system RAM
effective_cache_size = 256MB     # ~1/4 of system RAM
work_mem = 8MB                   # Per operation
maintenance_work_mem = 64MB      # For VACUUM/CREATE INDEX

# Connection pooling (API)
max_connections = 20             # Total
reserved_connections = 3         # For maintenance
```

### Systemd Service for Charting API

```ini
# /etc/systemd/system/market-data-api.service

[Unit]
Description=Market Data + Charting API
After=postgresql.service trade-webhook.service

[Service]
Type=notify
User=root
WorkingDirectory=/root/trade-execution-webhook
Environment="PATH=/root/trade-execution-webhook/venv/bin"
ExecStart=/root/trade-execution-webhook/venv/bin/uvicorn \
    Webhook-app.market_data_api.main:app \
    --host 0.0.0.0 --port 8001 --workers 2 --loop uvloop

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Nginx Reverse Proxy (Existing Setup)

```nginx
# Add to existing Nginx config
upstream market_data_api {
    server 127.0.0.1:8001;
}

server {
    listen 80;
    
    # Existing endpoints
    location /webhook/ {
        proxy_pass http://127.0.0.1:5000;
    }
    
    # NEW: Market data + charts
    location /api/v1/charts {
        proxy_pass http://market_data_api;
        proxy_cache my_cache;
        proxy_cache_valid 200 1h;
        proxy_cache_key "$scheme$request_method$host$request_uri";
        add_header X-Cache-Status $upstream_cache_status;
    }
    
    location /api/v1/ohlcv {
        proxy_pass http://market_data_api;
        proxy_cache my_cache;
        proxy_cache_valid 200 2h;
    }
    
    location /api/v1/indicators {
        proxy_pass http://market_data_api;
    }
}

# Cache configuration
proxy_cache_path /var/cache/nginx/market_data levels=1:2 keys_zone=my_cache:10m;
```

---

## 🎯 Implementation Phases

### Phase 1: Indicators Module (3-4 days)
- [ ] Create `indicators.py` with EMA, ATR, RSI, MACD
- [ ] Test on sample data
- [ ] Benchmark performance (should process 1000 candles in <100ms)

### Phase 2: Charting Backend (2-3 days)
- [ ] Implement SVG chart generator (lightweight)
- [ ] Create mplfinance fallback for PNG
- [ ] Test chart rendering

### Phase 3: FastAPI Endpoints (2-3 days)
- [ ] `/api/v1/charts/daily`
- [ ] `/api/v1/charts/weekly`
- [ ] `/api/v1/indicators`
- [ ] Add caching layer

### Phase 4: Integration & Optimization (2-3 days)
- [ ] Connect to market_data DB
- [ ] Deploy to VPS
- [ ] Benchmark under load (concurrent requests)
- [ ] Tune cache settings

---

## 📈 Performance Targets

| Operation | Target | Notes |
|-----------|--------|-------|
| Calculate all indicators (250 candles) | <100ms | Parallel vectorized |
| Generate SVG chart (250 candles) | <200ms | No image library |
| Generate PNG chart (250 candles) | <500ms | mplfinance |
| Cache hit rate | >70% | For daily/weekly charts |
| API response time (cached) | <10ms | From cache |
| API response time (uncached) | <500ms | Query + calc + chart |

---

## 📋 Technical Stack Summary

```
Language: Python 3.10+
Web Framework: FastAPI (async)
Charting: SVG (primary) + mplfinance (fallback)
Indicators: pandas_ta
Database: PostgreSQL + TimescaleDB
Caching: In-memory LRU (100 charts)
Server: Uvicorn (2 workers on 1 vCPU)
Reverse Proxy: Nginx
```

---

## ✅ Recommended Indicators for Backtesting

```
Core (Calculate Daily):
✅ EMA 10, 21, 50, 200 - Trend direction
✅ ATR 14 - Stop-loss placement
✅ RSI 14 - Overbought/oversold
✅ MACD - Momentum + trend

Advanced (On-demand):
○ Bollinger Bands - Volatility context
○ Volume SMA - Liquidity check
○ Stochastic - Mean reversion
○ Pivot Points - Support/resistance
○ OBV - Volume divergence
```

---

## Next Steps

1. **Confirm indicator selection** (which ones to calculate daily vs on-demand?)
2. **Choose charting format** (SVG only or need PNG too?)
3. **Database connection** (will charting API query same PostgreSQL as OHLCV API?)
4. **Start Phase 1**: Build indicators module and test
