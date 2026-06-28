# 🚀 Complete Implementation Roadmap
## Market Data Storage + Charting API for 1 GB VPS

---

## 📋 Context Summary

**Your Setup**:
- VPS: 1 vCPU, 1 GB RAM, 25 GB disk
- Goal: Store 15 years of OHLCV for 2000 NSE stocks + expose efficient APIs
- Use Cases: Backtesting, charting, technical analysis
- Data: Daily candles only (7.5M total)

**Two Major Components**:
1. **Market Data API** - Query historical OHLCV (document: `MARKET_DATA_STORAGE_ANALYSIS.md`)
2. **Charting API** - Generate charts + calculate indicators (document: `CHARTING_API_ANALYSIS.md`)

---

## 📊 System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          VPS (165.232.187.97)                    │
│                     1 vCPU | 1 GB RAM | 25 GB Disk              │
└─────────────────────────────────────────────────────────────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
            ┌───────▼────────┐     │    ┌────────▼────────┐
            │  PostgreSQL +  │     │    │  Nginx Proxy    │
            │  TimescaleDB   │     │    │  (Port 80/443)  │
            │  (Market Data) │     │    └────────┬────────┘
            │  ~200 MB       │     │             │
            └────────────────┘     │    ┌────────┴────────┐
                    │              │    │                 │
                    │          ┌───▼────▼───┐        ┌───▼──────┐
                    │          │ Telegram    │        │ FastAPI  │
                    │          │ Webhook App │        │ Market   │
                    │          │ (Existing)  │        │ Data API │
                    │          │ Port 5000   │        │ Port 8000│
                    │          └─────────────┘        └────┬─────┘
                    │                                      │
                    │                                 ┌────▼──────┐
                    │                                 │ FastAPI   │
                    │                                 │ Charting  │
                    │                                 │ API       │
                    │                                 │ Port 8001 │
                    │                                 └───────────┘
                    └─────────────────────────────────────────────┘
                                    │
                            Dhan API │ (pull historical)
                                    ▼
```

---

## 🔧 Tech Stack Decision (Optimized for 1 GB RAM)

| Component | Choice | Rationale |
|-----------|--------|-----------|
| **Database** | PostgreSQL 14 + TimescaleDB | 3-5x compression, time-series optimized |
| **Data API** | FastAPI (async) + asyncpg | Connection pooling, memory efficient |
| **Charting** | SVG (primary) + mplfinance | Minimal memory, CPU-friendly |
| **Indicators** | pandas_ta | Vectorized, numpy-based (fast) |
| **Cache** | In-memory LRU (100 charts) | 50-100 MB max, sufficient for 1 GB |
| **Reverse Proxy** | Nginx | Already running, add new locations |
| **Task Queue** | Optional: Celery (if needed for long jobs) | Start without; add if needed |

---

## 🚀 Implementation Timeline

### **WEEK 1: Database Infrastructure**

#### Day 1-2: PostgreSQL + TimescaleDB Setup
```bash
# On VPS
sudo apt update && sudo apt install postgresql postgresql-contrib
sudo apt install timescaledb-postgresql-14
sudo timescaledb-tune --quiet --yes
sudo systemctl restart postgresql

# Create database
sudo -u postgres createdb market_data
sudo -u postgres createuser market_data_user -P

# Enable TimescaleDB
sudo -u postgres psql -d market_data -c "CREATE EXTENSION timescaledb;"

# Run schema (from MARKET_DATA_STORAGE_ANALYSIS.md)
sudo -u postgres psql -d market_data -f /root/schema.sql
```

**Verify**:
```bash
sudo -u postgres psql -d market_data -c "SELECT * FROM timescaledb_information.hypertable;"
# Should show: ohlcv_data hypertable
```

#### Day 3: Test Connection + Tune PostgreSQL
```python
# test_db.py
import asyncpg
import asyncio

async def test_connection():
    conn = await asyncpg.connect(
        host="127.0.0.1",
        port=5432,
        user="market_data_user",
        password="secure_password",
        database="market_data"
    )
    result = await conn.fetch("SELECT COUNT(*) FROM ohlcv_data;")
    print(f"✅ Connected. Records: {result}")
    await conn.close()

asyncio.run(test_connection())
```

**Memory Tuning** (PostgreSQL for 1 GB RAM):
```bash
# /etc/postgresql/14/main/postgresql.conf
shared_buffers = 128MB          # 1/8 of RAM
effective_cache_size = 256MB    # 1/4 of RAM
work_mem = 8MB
maintenance_work_mem = 64MB
max_connections = 20

# Restart
sudo systemctl restart postgresql
```

---

### **WEEK 2-3: Data Ingestion**

#### Phase 1: Historical Data Load (Days 1-5)

```python
# ingest_historical.py
import asyncio
import asyncpg
from dhan_client import DhanClient
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def ingest_15_years():
    """
    Fetch 15 years of daily OHLCV from Dhan API
    Estimated time: 20-30 hours (2000 stocks × 15 years / Dhan rate limits)
    Run in background: nohup python ingest_historical.py &
    """
    
    pool = await asyncpg.create_pool(
        host="127.0.0.1",
        port=5432,
        user="market_data_user",
        password="secure_password",
        database="market_data",
        min_size=1,
        max_size=5
    )
    
    dhan = DhanClient()
    
    # Get all NSE symbols
    symbols = await dhan.get_nse_symbols()  # ~2000 symbols
    logger.info(f"📊 Fetching data for {len(symbols)} symbols")
    
    for idx, symbol in enumerate(symbols):
        logger.info(f"[{idx+1}/{len(symbols)}] {symbol}...")
        
        # Fetch 15 years (in yearly chunks to avoid API limits)
        all_candles = []
        for year in range(2010, 2025):
            try:
                candles = await dhan.get_historical(
                    symbol=symbol,
                    from_date=f"{year}-01-01",
                    to_date=f"{year}-12-31",
                    resolution="daily"
                )
                all_candles.extend(candles)
                await asyncio.sleep(0.1)  # Rate limit respect
            except Exception as e:
                logger.warning(f"  ⚠️ Year {year}: {e}")
                continue
        
        # Bulk insert
        if all_candles:
            async with pool.acquire() as conn:
                await conn.executemany(
                    """INSERT INTO ohlcv_data 
                       (symbol, trading_date, open, high, low, close, volume, oi)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                       ON CONFLICT (symbol, trading_date) DO UPDATE SET
                       close=EXCLUDED.close, volume=EXCLUDED.volume
                    """,
                    all_candles
                )
                logger.info(f"  ✅ Inserted {len(all_candles)} candles")
    
    await pool.close()
    logger.info("🎉 Ingestion complete!")

# Run
if __name__ == "__main__":
    asyncio.run(ingest_15_years())
```

**Running**:
```bash
cd /root/trade-execution-webhook
source venv/bin/activate
nohup python Webhook-app/ingest_historical.py > ingest.log 2>&1 &

# Monitor progress
tail -f ingest.log
```

**Expected**: 
- ~7.5M records inserted
- ~1-2 GB temporary disk space
- 20-30 hours runtime

#### Phase 2: Data Validation (Day 6)

```python
# validate_data.py
import asyncpg
import asyncio

async def validate():
    conn = await asyncpg.connect(
        host="127.0.0.1", port=5432,
        user="market_data_user", password="secure_password",
        database="market_data"
    )
    
    # Check row counts
    total = await conn.fetchval("SELECT COUNT(*) FROM ohlcv_data;")
    symbols = await conn.fetchval("SELECT COUNT(DISTINCT symbol) FROM ohlcv_data;")
    
    print(f"✅ Total records: {total:,} (expected ~7.5M)")
    print(f"✅ Unique symbols: {symbols} (expected ~2000)")
    
    # Check date range
    date_range = await conn.fetch(
        "SELECT MIN(trading_date), MAX(trading_date) FROM ohlcv_data;"
    )
    print(f"✅ Date range: {date_range[0]}")
    
    # Sample query performance
    import time
    start = time.time()
    rows = await conn.fetch(
        "SELECT * FROM ohlcv_data WHERE symbol='INFY' LIMIT 250;"
    )
    elapsed = (time.time() - start) * 1000
    print(f"✅ Query time (INFY, 250 rows): {elapsed:.1f}ms")
    
    await conn.close()

asyncio.run(validate())
```

---

### **WEEK 3: API Layer (Market Data)**

#### Day 1-2: Create FastAPI App

```python
# Webhook-app/market_data_api/main.py
from fastapi import FastAPI, Query
from datetime import date
import asyncpg
import os
from typing import Optional

app = FastAPI(title="Market Data API", version="1.0")

# Database pool
pool: asyncpg.pool.Pool = None

@app.on_event("startup")
async def startup():
    global pool
    pool = await asyncpg.create_pool(
        host="localhost",
        port=5432,
        user="market_data_user",
        password=os.getenv("DB_PASSWORD"),
        database="market_data",
        min_size=3,
        max_size=10  # Modest pool for 1 GB RAM
    )

@app.on_event("shutdown")
async def shutdown():
    await pool.close()

@app.get("/api/v1/health")
async def health_check():
    return {"status": "ok"}

@app.get("/api/v1/ohlcv")
async def get_ohlcv(
    symbol: str = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...),
    limit: int = Query(default=10000, le=50000)
):
    """Fetch OHLCV for single symbol"""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT trading_date, open, high, low, close, volume
            FROM ohlcv_data
            WHERE symbol = $1 AND trading_date BETWEEN $2 AND $3
            ORDER BY trading_date
            LIMIT $4
        """, symbol.upper(), from_date, to_date, limit)
    
    return {
        "meta": {"symbol": symbol, "count": len(rows), "from": str(from_date), "to": str(to_date)},
        "data": [dict(r) for r in rows]
    }

@app.get("/api/v1/ohlcv/multi")
async def get_ohlcv_multi(
    symbols: str = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...)
):
    """Fetch OHLCV for multiple symbols"""
    symbol_list = [s.strip().upper() for s in symbols.split(",")]
    
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT symbol, trading_date, open, high, low, close, volume
            FROM ohlcv_data
            WHERE symbol = ANY($1) AND trading_date BETWEEN $2 AND $3
            ORDER BY symbol, trading_date
        """, symbol_list, from_date, to_date)
    
    # Group by symbol
    grouped = {}
    for row in rows:
        if row['symbol'] not in grouped:
            grouped[row['symbol']] = []
        grouped[row['symbol']].append({
            "date": str(row['trading_date']),
            "open": float(row['open']),
            "high": float(row['high']),
            "low": float(row['low']),
            "close": float(row['close']),
            "volume": int(row['volume'])
        })
    
    return {
        "meta": {"symbols": symbol_list, "count": len(rows), "from": str(from_date), "to": str(to_date)},
        "data": grouped
    }
```

#### Day 3: Test Endpoints
```bash
cd /root/trade-execution-webhook

# Start API (test mode)
source venv/bin/activate
python -m uvicorn Webhook-app.market_data_api.main:app --host 127.0.0.1 --port 8000

# In another terminal, test
curl "http://localhost:8000/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31"
```

**Expected**: Response with OHLCV data in <100ms

---

### **WEEK 4: Charting API**

#### Day 1: Build Indicators Module

```python
# Webhook-app/market_data_api/indicators.py
import pandas as pd
import numpy as np

class TechnicalIndicators:
    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
    
    def calculate_ema(self, periods=[10, 21, 50, 200]):
        for period in periods:
            self.df[f'ema_{period}'] = self.df['close'].ewm(span=period).mean()
        return self.df
    
    def calculate_atr(self, period=14):
        high_low = self.df['high'] - self.df['low']
        high_close = np.abs(self.df['high'] - self.df['close'].shift())
        low_close = np.abs(self.df['low'] - self.df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        tr = np.max(ranges, axis=1)
        self.df['atr'] = tr.rolling(period).mean()
        return self.df
    
    def calculate_rsi(self, period=14):
        delta = self.df['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = -delta.where(delta < 0, 0).rolling(period).mean()
        rs = gain / loss
        self.df['rsi'] = 100 - (100 / (1 + rs))
        return self.df
    
    def calculate_macd(self):
        ema12 = self.df['close'].ewm(span=12).mean()
        ema26 = self.df['close'].ewm(span=26).mean()
        self.df['macd'] = ema12 - ema26
        self.df['macd_signal'] = self.df['macd'].ewm(span=9).mean()
        self.df['macd_hist'] = self.df['macd'] - self.df['macd_signal']
        return self.df
```

#### Day 2: Build SVG Charting

```python
# Webhook-app/market_data_api/charting.py
def create_svg_chart(symbol: str, df: pd.DataFrame, width=1200, height=600) -> str:
    """Ultra-lightweight SVG generation"""
    
    prices = pd.concat([df['open'], df['high'], df['low'], df['close']])
    min_price, max_price = prices.min(), prices.max()
    price_range = max_price - min_price
    
    padding = 50
    chart_width = width - 2 * padding
    chart_height = height - 2 * padding
    
    def x_coord(i):
        return padding + (i / len(df)) * chart_width
    
    def y_coord(price):
        return height - padding - ((price - min_price) / price_range) * chart_height
    
    svg = [f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
           '<rect width="100%" height="100%" fill="#1a1a1a"/>',
           f'<text x="10" y="25" font-size="16" fill="#fff">{symbol}</text>']
    
    # Candlesticks
    candle_width = chart_width / len(df) * 0.7
    for i, (_, row) in enumerate(df.iterrows()):
        x = x_coord(i)
        y_open = y_coord(row['open'])
        y_close = y_coord(row['close'])
        y_high = y_coord(row['high'])
        y_low = y_coord(row['low'])
        color = 'green' if row['close'] > row['open'] else 'red'
        
        svg.append(f'<line x1="{x}" y1="{y_high}" x2="{x}" y2="{y_low}" stroke="{color}"/>')
        svg.append(f'<rect x="{x - candle_width/2}" y="{min(y_open, y_close)}" '
                  f'width="{candle_width}" height="{abs(y_open - y_close) or 1}" fill="{color}"/>')
    
    # EMA lines
    for period, color in [(10, 'blue'), (21, 'green'), (50, 'orange'), (200, 'red')]:
        col = f'ema_{period}'
        if col in df.columns:
            points = ' '.join([f"{x_coord(i)},{y_coord(val)}" for i, val in enumerate(df[col]) if not pd.isna(val)])
            svg.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
    
    svg.append('</svg>')
    return '\n'.join(svg)
```

#### Day 3-4: Charting Endpoints

```python
# Add to main.py
from fastapi.responses import StreamingResponse

@app.get("/api/v1/charts/daily")
async def get_daily_chart(
    symbol: str = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...),
    indicators: str = Query("ema,rsi,macd"),
    format: str = Query("svg", regex="^(svg|json)$")
):
    """Generate daily chart with indicators"""
    
    # Fetch OHLCV
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT trading_date, open, high, low, close, volume FROM ohlcv_data "
            "WHERE symbol=$1 AND trading_date BETWEEN $2 AND $3 ORDER BY trading_date",
            symbol, from_date, to_date
        )
    
    if not rows:
        return {"error": "No data"}
    
    # Convert to DataFrame
    df = pd.DataFrame(rows)
    df['trading_date'] = pd.to_datetime(df['trading_date'])
    df.set_index('trading_date', inplace=True)
    
    # Calculate indicators
    tech = TechnicalIndicators(df)
    if 'ema' in indicators:
        tech.calculate_ema([10, 21, 50, 200])
    if 'rsi' in indicators:
        tech.calculate_rsi(14)
    if 'atr' in indicators:
        tech.calculate_atr(14)
    if 'macd' in indicators:
        tech.calculate_macd()
    
    if format == "svg":
        svg = create_svg_chart(symbol, tech.df)
        return StreamingResponse(iter([svg]), media_type="image/svg+xml")
    else:
        return {"data": tech.df.to_dict(orient='records')}

@app.get("/api/v1/charts/weekly")
async def get_weekly_chart(
    symbol: str = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...)
):
    """Generate weekly chart (aggregated from daily)"""
    # ... similar to daily, but aggregate to weekly
    pass
```

---

### **WEEK 4-5: Deployment & Optimization**

#### Day 1: Update requirements.txt
```
# Webhook-app/requirements.txt
flask
requests
pandas
pyotp
yfinance
matplotlib
mplfinance
openai
reportlab
python-dotenv
gunicorn
fastapi
uvicorn
asyncpg
pandas-ta
numpy
```

#### Day 2: Create Systemd Services

```ini
# /etc/systemd/system/market-data-api.service
[Unit]
Description=Market Data API
After=postgresql.service
Wants=postgresql.service

[Service]
Type=notify
User=root
WorkingDirectory=/root/trade-execution-webhook
EnvironmentFile=/root/trade-execution-webhook/.env
ExecStart=/root/trade-execution-webhook/venv/bin/uvicorn \
    Webhook-app.market_data_api.main:app \
    --host 127.0.0.1 --port 8000 --workers 2

Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable market-data-api
sudo systemctl start market-data-api
sudo systemctl status market-data-api
```

#### Day 3: Nginx Configuration

```nginx
# Update /etc/nginx/sites-available/default

upstream market_data_api {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    
    # Existing endpoints
    location /webhook/ {
        proxy_pass http://127.0.0.1:5000;
    }
    
    # Market Data API
    location /api/v1/ohlcv {
        proxy_pass http://market_data_api;
        proxy_cache market_data_cache;
        proxy_cache_valid 200 2h;
        add_header X-Cache-Status $upstream_cache_status;
    }
    
    location /api/v1/charts {
        proxy_pass http://market_data_api;
        proxy_cache market_data_cache;
        proxy_cache_valid 200 1h;
        add_header X-Cache-Status $upstream_cache_status;
    }
}

# Cache configuration
proxy_cache_path /var/cache/nginx/market_data keys_zone=market_data_cache:10m levels=1:2 max_size=500m;
```

```bash
sudo systemctl reload nginx
```

#### Day 4-5: Testing & Performance Tuning

```bash
# Load test
ab -n 100 -c 10 "http://localhost/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31"

# Monitor resources
watch -n 1 'ps aux | grep -E "(postgres|uvicorn)" | head -10'
free -h
df -h
```

**Target Performance**:
- Single query: <100ms
- Bulk query (100 symbols): <500ms
- Cache hit rate: >70%

---

## 📅 Timeline Summary

| Week | Days | Task | Effort |
|------|------|------|--------|
| 1 | 1-3 | DB setup + tuning | 8 hrs |
| 1 | 4-5 | Connection test | 2 hrs |
| 2-3 | 1-6 | Data ingestion | 30 hrs |
| 2-3 | 7 | Data validation | 4 hrs |
| 3 | 1-3 | Market Data API | 12 hrs |
| 4 | 1-2 | Indicators module | 8 hrs |
| 4 | 3-5 | Charting + endpoints | 12 hrs |
| 4-5 | 1-5 | Deployment + tuning | 10 hrs |
| | | **TOTAL** | **~86 hours** |

**Realistic Timeline**: 3-4 weeks (part-time) or 2 weeks (full-time)

---

## 🎯 Success Criteria

By end of implementation, you should have:

- ✅ PostgreSQL + TimescaleDB running with 7.5M OHLCV candles
- ✅ `/api/v1/ohlcv` endpoint returning data in <100ms
- ✅ `/api/v1/ohlcv/multi` for bulk backtest queries (<500ms)
- ✅ `/api/v1/charts/daily` generating SVG charts in <200ms
- ✅ `/api/v1/charts/weekly` with technical indicators
- ✅ Nginx reverse proxy caching chart results
- ✅ Systemd services for auto-restart on reboot
- ✅ Memory usage stable at <1 GB
- ✅ Disk usage: ~500 MB (data + backups)

---

## 🔄 Day-to-Day Commands on VPS

```bash
# SSH in
ssh root@165.232.187.97

# Navigate
cd /root/trade-execution-webhook
source venv/bin/activate

# Restart services
sudo systemctl restart market-data-api
sudo systemctl restart postgresql
sudo systemctl restart nginx

# Monitor logs
tail -f /var/log/syslog | grep market-data
journalctl -u market-data-api -f

# Database queries
psql -U market_data_user -d market_data
SELECT COUNT(*) FROM ohlcv_data;

# Test API
curl "http://localhost/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31"
curl "http://localhost/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31&format=svg" > chart.svg
```

---

## ⚠️ Potential Issues & Solutions

| Issue | Cause | Solution |
|-------|-------|----------|
| OOM (Out of Memory) | Connection pool too large | Reduce max_connections to 10 |
| Slow ingestion | Dhan API rate limits | Add asyncio.sleep(0.5) between requests |
| Disk space full | Backups not rotated | Set up logrotate, compress old dumps |
| Chart generation slow | Large date ranges | Limit to max 5 years per request |
| Cache misses high | Chart params vary | Implement smarter cache key normalization |
| PostgreSQL slow | Indexes not used | Run ANALYZE; check EXPLAIN ANALYZE |

---

## 📞 Questions Before Starting?

1. **Intraday data**: Do you need 1H/4H candles in future, or daily is final answer?
2. **Indicator persistence**: Should calculated indicators be stored in DB or computed on-demand?
3. **Authentication**: Should market data API require API keys, or open?
4. **Backups**: How often? (daily, weekly? compress?) - affects disk usage
5. **Real-time updates**: Should API auto-update daily candles at market close, or manual trigger?

---

**Ready to start Week 1? Let me know!**
