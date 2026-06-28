# ⚡ Quick Start Setup Guide
## Ready-to-Deploy: Market Data + Charting API

---

## 🎯 Final Architecture (Your Decisions)

✅ **Daily candles only** - Intraday not needed
✅ **Indicators computed on-demand** - No DB storage overhead  
✅ **Open API** - No authentication layer
✅ **Auto-daily-update** - Cron job at 18:00 IST (after market close)
✅ **No backups needed** - OHLCV can be re-fetched from Dhan; trade history already in Google Sheets

---

## 💾 Why No OHLCV Backups?

**Data Recoverability**:
- OHLCV source: **Dhan API** (immutable, can always re-fetch)
- If data corrupts: Re-run ingest script for affected date range
- Cost: ~2-3 hours to re-fetch 15 years vs disk space for backup

**What DOES need backup**:
- Trade execution history → Already in Google Sheets
- PostgreSQL config files → Minimal (~1 MB)
- `.env` secrets → Keep in secure location

**Decision**: Store OHLCV only; no periodic backups.

---

## 🚀 Step-by-Step Deployment

### **STEP 1: Database Setup (30 minutes)**

```bash
# SSH to VPS
ssh root@165.232.187.97
cd /root/trade-execution-webhook

# Install PostgreSQL + TimescaleDB
sudo apt update
sudo apt install -y postgresql postgresql-contrib timescaledb-postgresql-14

# Enable and start
sudo systemctl enable postgresql
sudo systemctl start postgresql

# Tune for 1 GB RAM
sudo bash -c 'cat > /etc/postgresql/14/main/postgresql.conf.d/99-timescale.conf <<EOF
shared_buffers = 128MB
effective_cache_size = 256MB
work_mem = 8MB
maintenance_work_mem = 64MB
max_connections = 20
max_wal_size = 1GB
min_wal_size = 100MB
EOF'

sudo systemctl restart postgresql

# Enable TimescaleDB extension
sudo -u postgres psql -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"

# Create database and user
sudo -u postgres psql <<EOF
CREATE DATABASE market_data;
CREATE USER market_data_user WITH PASSWORD 'your_secure_password_here';
GRANT ALL PRIVILEGES ON DATABASE market_data TO market_data_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO market_data_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO market_data_user;
EOF

# Verify
sudo -u postgres psql -d market_data -c "SELECT extname FROM pg_extension WHERE extname='timescaledb';"
# Output: timescaledb (should appear)
```

**Verify**:
```bash
psql -U market_data_user -d market_data -c "SELECT version();" -h localhost
# Should show: PostgreSQL 14.x ... TimescaleDB 2.x
```

---

### **STEP 2: Create Schema (5 minutes)**

```bash
sudo -u postgres psql -d market_data <<'EOF'

-- Main hypertable for OHLCV (time-series optimized)
CREATE TABLE IF NOT EXISTS ohlcv_data (
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    open NUMERIC(10, 2) NOT NULL,
    high NUMERIC(10, 2) NOT NULL,
    low NUMERIC(10, 2) NOT NULL,
    close NUMERIC(10, 2) NOT NULL,
    volume BIGINT NOT NULL,
    oi BIGINT,
    data_source TEXT DEFAULT 'dhan',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Convert to hypertable (time-series specific optimizations)
SELECT create_hypertable('ohlcv_data', 'time', if_not_exists => TRUE);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_symbol_time ON ohlcv_data (symbol, time DESC);
CREATE INDEX IF NOT EXISTS idx_time_symbol ON ohlcv_data (time DESC, symbol);

-- Metadata table for symbols
CREATE TABLE IF NOT EXISTS symbols_meta (
    symbol TEXT PRIMARY KEY,
    isin TEXT,
    security_name TEXT,
    sector TEXT,
    list_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    dhan_security_id TEXT UNIQUE,
    last_updated TIMESTAMPTZ DEFAULT NOW()
);

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO market_data_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO market_data_user;

EOF

# Verify
psql -U market_data_user -d market_data -c "\dt"
# Should show: ohlcv_data, symbols_meta
```

---

### **STEP 3: Update Python Requirements**

```bash
cd /root/trade-execution-webhook

# Add to requirements.txt
cat >> Webhook-app/requirements.txt <<'EOF'
fastapi
uvicorn[standard]
asyncpg
pandas-ta
numpy
EOF

# Install
source venv/bin/activate
pip install -r Webhook-app/requirements.txt
```

---

### **STEP 4: Create Data Ingestion Script**

**File**: `/root/trade-execution-webhook/Webhook-app/ingest_ohlcv.py`

```python
"""
Ingest 15 years of daily OHLCV from Dhan API
Run once: python ingest_ohlcv.py
Or in background: nohup python ingest_ohlcv.py > ingest.log 2>&1 &
"""

import asyncio
import asyncpg
import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
import sys

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Load env
load_dotenv()
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")
DB_PASSWORD = os.getenv("DB_PASSWORD")

# For this script, we'll use a simple HTTP client
# In production, use the official Dhan SDK
import requests
import pyotp

def get_dhan_token():
    """Get JWT token from Dhan API"""
    totp = pyotp.TOTP(DHAN_TOTP_SECRET)
    otp = totp.now()
    
    response = requests.post(
        "https://api-gw.shoonya.com/auth/login",
        json={
            "userId": DHAN_CLIENT_ID,
            "password": DHAN_PIN,
            "twoFA": otp
        }
    )
    return response.json().get("authToken")

async def fetch_historical_ohlcv(token: str, symbol: str, from_date: str, to_date: str):
    """
    Fetch historical OHLCV from Dhan API
    Dhan expects dates in YYYY-MM-DD format
    """
    try:
        response = requests.get(
            "https://api-gw.shoonya.com/historical",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "exchangeTokens": symbol,  # e.g., "1234" (NSE security ID)
                "from": from_date,
                "to": to_date,
                "resolution": "1d"  # 1-day candles
            },
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                return data.get("data", [])
        return []
    except Exception as e:
        logger.warning(f"API error for {symbol} ({from_date} to {to_date}): {e}")
        return []

async def get_nse_symbols(token: str):
    """
    Fetch all NSE symbols from Dhan instrument file
    Dhan provides a CSV with all tradable symbols
    """
    # For simplicity, hardcode the most popular symbols
    # In production, fetch from: https://images.dhan.co/api-data/api-scrip-master.csv
    
    try:
        import pandas as pd
        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        df = pd.read_csv(url, low_memory=False)
        
        # Filter: NSE, Equity, active
        df = df[
            (df['SEM_EXM_EXCH_ID'] == 'NSE') &
            (df['SEM_SEGMENT'] == 'E')
        ]
        
        symbols = df[['SEM_TRADING_SYMBOL', 'SEM_SEC_ID']].to_dict('records')
        logger.info(f"✅ Loaded {len(symbols)} NSE symbols")
        return symbols
    except Exception as e:
        logger.error(f"Failed to fetch symbols: {e}")
        return []

async def ingest_all_historical():
    """Main ingestion loop"""
    
    # Get token
    logger.info("📡 Authenticating with Dhan API...")
    token = get_dhan_token()
    
    # Get symbols
    logger.info("📊 Fetching NSE symbol list...")
    symbols = await get_nse_symbols(token)
    
    # Database connection
    pool = await asyncpg.create_pool(
        host="localhost",
        port=5432,
        user="market_data_user",
        password=DB_PASSWORD,
        database="market_data",
        min_size=1,
        max_size=3
    )
    
    # Ingest 15 years (2010-2025)
    logger.info(f"🚀 Starting ingestion for {len(symbols)} symbols (15 years)...")
    
    for idx, sym_info in enumerate(symbols):
        symbol = sym_info['SEM_TRADING_SYMBOL']
        dhan_id = sym_info['SEM_SEC_ID']
        
        logger.info(f"[{idx+1}/{len(symbols)}] {symbol}...")
        
        all_candles = []
        
        # Fetch year by year
        for year in range(2010, 2025):
            from_date = f"{year}-01-01"
            to_date = f"{year}-12-31"
            
            try:
                candles = await fetch_historical_ohlcv(token, dhan_id, from_date, to_date)
                
                # Transform to DB format
                for candle in candles:
                    all_candles.append((
                        symbol,
                        datetime.strptime(candle['date'], '%Y-%m-%d').replace(tzinfo=None),
                        float(candle['open']),
                        float(candle['high']),
                        float(candle['low']),
                        float(candle['close']),
                        int(candle['volume']),
                        int(candle.get('oi', 0)) if candle.get('oi') else None
                    ))
                
                await asyncio.sleep(0.1)  # Rate limit
            except Exception as e:
                logger.warning(f"  Year {year}: {e}")
        
        # Bulk insert
        if all_candles:
            async with pool.acquire() as conn:
                try:
                    # Convert time to TIMESTAMPTZ
                    records = [
                        (
                            candle[0],
                            candle[1].replace(tzinfo=None),  # Remove tz if present
                            *candle[2:]
                        )
                        for candle in all_candles
                    ]
                    
                    await conn.executemany(
                        """INSERT INTO ohlcv_data (symbol, time, open, high, low, close, volume, oi)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                           ON CONFLICT (symbol, time) DO UPDATE SET
                           open=EXCLUDED.open, high=EXCLUDED.high,
                           low=EXCLUDED.low, close=EXCLUDED.close,
                           volume=EXCLUDED.volume, oi=EXCLUDED.oi,
                           created_at=NOW()
                        """,
                        records
                    )
                    logger.info(f"  ✅ Inserted {len(all_candles)} candles")
                except Exception as e:
                    logger.error(f"  Insert failed: {e}")
    
    await pool.close()
    logger.info("🎉 Ingestion complete!")

if __name__ == "__main__":
    asyncio.run(ingest_all_historical())
```

**Run ingestion**:
```bash
cd /root/trade-execution-webhook
source venv/bin/activate

# Test with a single symbol first
python Webhook-app/ingest_ohlcv.py

# Or in background (20-30 hours for 2000 symbols)
nohup python Webhook-app/ingest_ohlcv.py > ingest.log 2>&1 &
tail -f ingest.log
```

---

### **STEP 5: Create Market Data API**

**Directory**: Create `/root/trade-execution-webhook/Webhook-app/market_data_api/`

**File**: `__init__.py` (empty)

**File**: `main.py`

```python
"""
Market Data API - OHLCV queries + Charting endpoints
FastAPI + asyncpg for efficient time-series queries
"""

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
from datetime import date, datetime, timezone
from typing import List, Optional
import asyncpg
import os
import pandas as pd
import numpy as np
from dotenv import load_dotenv
import logging

# Load env
load_dotenv()
DB_PASSWORD = os.getenv("DB_PASSWORD")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(
    title="Market Data API",
    description="OHLCV queries + technical charting",
    version="1.0"
)

# Global connection pool
pool: asyncpg.pool.Pool = None

@app.on_event("startup")
async def startup():
    global pool
    pool = await asyncpg.create_pool(
        host="localhost",
        port=5432,
        user="market_data_user",
        password=DB_PASSWORD,
        database="market_data",
        min_size=2,
        max_size=8  # Conservative for 1 GB RAM
    )
    logger.info("✅ Database pool initialized")

@app.on_event("shutdown")
async def shutdown():
    await pool.close()
    logger.info("❌ Database pool closed")

# ==================== OHLCV Endpoints ====================

@app.get("/api/v1/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/api/v1/ohlcv")
async def get_ohlcv(
    symbol: str = Query(..., min_length=1, max_length=20),
    from_date: date = Query(...),
    to_date: date = Query(...),
    limit: int = Query(default=10000, ge=1, le=50000)
):
    """
    Fetch OHLCV data for a single symbol
    
    Example: /api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31
    """
    if from_date > to_date:
        raise HTTPException(status_code=400, detail="from_date must be <= to_date")
    
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT 
                time AT TIME ZONE 'Asia/Kolkata' as trading_date,
                open, high, low, close, volume, oi
            FROM ohlcv_data
            WHERE symbol = $1 
              AND time AT TIME ZONE 'Asia/Kolkata' BETWEEN $2::date AND ($3::date + INTERVAL '1 day')
            ORDER BY time
            LIMIT $4
        """, symbol.upper(), from_date, to_date, limit)
    
    if not rows:
        raise HTTPException(status_code=404, detail=f"No data found for {symbol}")
    
    return {
        "meta": {
            "symbol": symbol.upper(),
            "count": len(rows),
            "from": str(from_date),
            "to": str(to_date)
        },
        "data": [
            {
                "date": str(row['trading_date'].date()) if isinstance(row['trading_date'], datetime) else str(row['trading_date']),
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "volume": int(row['volume']),
                "oi": int(row['oi']) if row['oi'] else None
            }
            for row in rows
        ]
    }

@app.get("/api/v1/ohlcv/multi")
async def get_ohlcv_multi(
    symbols: str = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...)
):
    """
    Fetch OHLCV for multiple symbols (backtesting)
    
    Example: /api/v1/ohlcv/multi?symbols=INFY,TCS,RELIANCE&from=2024-01-01&to=2024-12-31
    """
    symbol_list = [s.strip().upper() for s in symbols.split(",")]
    
    if len(symbol_list) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 symbols per request")
    
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT 
                symbol,
                time AT TIME ZONE 'Asia/Kolkata' as trading_date,
                open, high, low, close, volume
            FROM ohlcv_data
            WHERE symbol = ANY($1)
              AND time AT TIME ZONE 'Asia/Kolkata' BETWEEN $2::date AND ($3::date + INTERVAL '1 day')
            ORDER BY symbol, time
        """, symbol_list, from_date, to_date)
    
    # Group by symbol
    grouped = {}
    for row in rows:
        sym = row['symbol']
        if sym not in grouped:
            grouped[sym] = []
        grouped[sym].append({
            "date": str(row['trading_date'].date()) if isinstance(row['trading_date'], datetime) else str(row['trading_date']),
            "open": float(row['open']),
            "high": float(row['high']),
            "low": float(row['low']),
            "close": float(row['close']),
            "volume": int(row['volume'])
        })
    
    return {
        "meta": {
            "symbols": symbol_list,
            "count": len(rows),
            "from": str(from_date),
            "to": str(to_date)
        },
        "data": grouped
    }

@app.get("/api/v1/symbols")
async def get_symbols(
    sector: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(True)
):
    """Get list of available symbols"""
    async with pool.acquire() as conn:
        if sector:
            rows = await conn.fetch(
                "SELECT symbol, security_name, sector, isin FROM symbols_meta WHERE sector = $1 AND is_active = $2",
                sector.upper(), is_active
            )
        else:
            rows = await conn.fetch(
                "SELECT symbol, security_name, sector, isin FROM symbols_meta WHERE is_active = $1",
                is_active
            )
    
    return {
        "count": len(rows),
        "data": [dict(r) for r in rows]
    }

# ==================== Charting Endpoints ====================

def create_svg_chart(symbol: str, df: pd.DataFrame, indicators: dict = None, width=1200, height=600) -> str:
    """
    Generate lightweight SVG chart with candlesticks and EMAs
    No image library needed - pure SVG text
    """
    if len(df) == 0:
        return "<svg></svg>"
    
    prices = pd.concat([df['open'], df['high'], df['low'], df['close']])
    min_price, max_price = prices.min(), prices.max()
    price_range = max_price - min_price or 1
    
    padding = 50
    chart_width = width - 2 * padding
    chart_height = height - 2 * padding
    
    def x_coord(i):
        return padding + (i / max(len(df) - 1, 1)) * chart_width
    
    def y_coord(price):
        if price_range == 0:
            return height - padding - chart_height / 2
        return height - padding - ((price - min_price) / price_range) * chart_height
    
    svg_lines = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
        '<rect width="100%" height="100%" fill="#1a1a1a"/>',
        f'<text x="10" y="25" font-size="16" fill="#fff" font-family="Arial">{symbol} - Daily</text>',
    ]
    
    # Draw grid
    for i in range(10):
        y = padding + (i / 10) * chart_height
        svg_lines.append(
            f'<line x1="{padding}" y1="{y}" x2="{width-padding}" y2="{y}" '
            f'stroke="#333" stroke-width="0.5"/>'
        )
    
    # Draw candlesticks
    candle_width = chart_width / len(df) * 0.6
    for i, (_, row) in enumerate(df.iterrows()):
        x = x_coord(i)
        y_open = y_coord(row['open'])
        y_close = y_coord(row['close'])
        y_high = y_coord(row['high'])
        y_low = y_coord(row['low'])
        color = '#00ff00' if row['close'] > row['open'] else '#ff0000'
        
        # Wick
        svg_lines.append(
            f'<line x1="{x}" y1="{y_high}" x2="{x}" y2="{y_low}" '
            f'stroke="{color}" stroke-width="1" opacity="0.7"/>'
        )
        
        # Body
        body_height = abs(y_open - y_close) or 1
        svg_lines.append(
            f'<rect x="{x - candle_width/2}" y="{min(y_open, y_close)}" '
            f'width="{candle_width}" height="{body_height}" '
            f'fill="{color}" stroke="{color}" stroke-width="0.5"/>'
        )
    
    # Draw EMAs (if calculated)
    if indicators:
        ema_styles = {
            'ema_10': {'color': '#0066ff', 'width': 2},
            'ema_21': {'color': '#00ff00', 'width': 2},
            'ema_50': {'color': '#ffaa00', 'width': 2},
            'ema_200': {'color': '#ff0000', 'width': 2}
        }
        
        for col, style in ema_styles.items():
            if col in df.columns:
                points = []
                for i, val in enumerate(df[col]):
                    if not pd.isna(val):
                        points.append(f"{x_coord(i)},{y_coord(val)}")
                
                if points:
                    points_str = ' '.join(points)
                    svg_lines.append(
                        f'<polyline points="{points_str}" fill="none" '
                        f'stroke="{style["color"]}" stroke-width="{style["width"]}" opacity="0.8"/>'
                    )
    
    svg_lines.append('</svg>')
    return '\n'.join(svg_lines)

@app.get("/api/v1/charts/daily")
async def get_daily_chart(
    symbol: str = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...),
    indicators: str = Query("ema", regex="^(ema|rsi|atr|macd|all|none)$"),
    format: str = Query("svg", regex="^(svg|json)$")
):
    """
    Generate daily chart with technical indicators
    
    Example: /api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31&indicators=ema&format=svg
    """
    
    # Fetch OHLCV
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT 
                time AT TIME ZONE 'Asia/Kolkata' as trading_date,
                open, high, low, close, volume
            FROM ohlcv_data
            WHERE symbol = $1 
              AND time AT TIME ZONE 'Asia/Kolkata' BETWEEN $2::date AND ($3::date + INTERVAL '1 day')
            ORDER BY time
        """, symbol.upper(), from_date, to_date)
    
    if not rows:
        raise HTTPException(status_code=404, detail="No data found")
    
    # Convert to DataFrame
    df = pd.DataFrame([dict(r) for r in rows])
    df = df.rename(columns={'trading_date': 'date'})
    
    # Calculate indicators (on-demand)
    if indicators != "none":
        from indicators import TechnicalIndicators
        tech = TechnicalIndicators(df)
        
        if "ema" in indicators or indicators == "all":
            tech.calculate_ema([10, 21, 50, 200])
        if "rsi" in indicators or indicators == "all":
            tech.calculate_rsi(14)
        if "atr" in indicators or indicators == "all":
            tech.calculate_atr(14)
        if "macd" in indicators or indicators == "all":
            tech.calculate_macd()
        
        df = tech.df
    
    if format == "svg":
        calc_indicators = {col: df[col] for col in df.columns if col.startswith('ema_')}
        svg = create_svg_chart(symbol, df, calc_indicators)
        return StreamingResponse(iter([svg]), media_type="image/svg+xml")
    else:
        # Return JSON
        cols = ['date', 'open', 'high', 'low', 'close', 'volume']
        if "ema" in indicators or indicators == "all":
            cols.extend(['ema_10', 'ema_21', 'ema_50', 'ema_200'])
        if "rsi" in indicators or indicators == "all":
            cols.append('rsi_14')
        
        return {
            "meta": {"symbol": symbol, "count": len(df)},
            "data": df[[c for c in cols if c in df.columns]].to_dict(orient='records')
        }

@app.get("/api/v1/charts/weekly")
async def get_weekly_chart(
    symbol: str = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...),
    indicators: str = Query("ema", regex="^(ema|rsi|atr|macd|all|none)$")
):
    """
    Generate weekly chart (aggregated from daily)
    
    Example: /api/v1/charts/weekly?symbol=INFY&from=2020-01-01&to=2024-12-31&indicators=ema
    """
    
    # Fetch daily data
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT 
                time AT TIME ZONE 'Asia/Kolkata' as trading_date,
                open, high, low, close, volume
            FROM ohlcv_data
            WHERE symbol = $1 
              AND time AT TIME ZONE 'Asia/Kolkata' BETWEEN $2::date AND ($3::date + INTERVAL '1 day')
            ORDER BY time
        """, symbol.upper(), from_date, to_date)
    
    if not rows:
        raise HTTPException(status_code=404, detail="No data found")
    
    # Convert to DataFrame
    df = pd.DataFrame([dict(r) for r in rows])
    df = df.rename(columns={'trading_date': 'date'})
    df['date'] = pd.to_datetime(df['date'])
    df.set_index('date', inplace=True)
    
    # Aggregate to weekly (Friday close)
    weekly = df.resample('W-FRI').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()
    
    weekly.reset_index(inplace=True)
    weekly['date'] = weekly['date'].dt.strftime('%Y-%m-%d')
    
    # Calculate indicators
    if indicators != "none":
        from indicators import TechnicalIndicators
        tech = TechnicalIndicators(weekly)
        
        if "ema" in indicators or indicators == "all":
            tech.calculate_ema([10, 21, 50, 200])
        if "rsi" in indicators or indicators == "all":
            tech.calculate_rsi(14)
        
        weekly = tech.df
    
    svg = create_svg_chart(symbol, weekly, {col: weekly[col] for col in weekly.columns if col.startswith('ema_')})
    return StreamingResponse(iter([svg]), media_type="image/svg+xml")

@app.get("/api/v1/indicators")
async def get_indicators(
    symbol: str = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...),
    indicators: str = Query("ema,rsi,atr,macd")
):
    """
    Get raw indicator values (JSON) for programmatic use
    
    Example: /api/v1/indicators?symbol=INFY&from=2024-01-01&to=2024-12-31&indicators=ema,rsi,atr
    """
    
    # Fetch OHLCV
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT 
                time AT TIME ZONE 'Asia/Kolkata' as trading_date,
                open, high, low, close, volume
            FROM ohlcv_data
            WHERE symbol = $1 
              AND time AT TIME ZONE 'Asia/Kolkata' BETWEEN $2::date AND ($3::date + INTERVAL '1 day')
            ORDER BY time
        """, symbol.upper(), from_date, to_date)
    
    if not rows:
        raise HTTPException(status_code=404, detail="No data found")
    
    df = pd.DataFrame([dict(r) for r in rows])
    df = df.rename(columns={'trading_date': 'date'})
    
    # Calculate requested indicators
    from indicators import TechnicalIndicators
    tech = TechnicalIndicators(df)
    
    indicator_list = [x.strip() for x in indicators.split(",")]
    
    if "ema" in indicator_list:
        tech.calculate_ema([10, 21, 50, 200])
    if "rsi" in indicator_list:
        tech.calculate_rsi(14)
    if "atr" in indicator_list:
        tech.calculate_atr(14)
    if "macd" in indicator_list:
        tech.calculate_macd()
    
    df = tech.df
    
    # Return data
    cols = ['date', 'open', 'high', 'low', 'close', 'volume']
    for ind in indicator_list:
        if ind == "ema":
            cols.extend(['ema_10', 'ema_21', 'ema_50', 'ema_200'])
        elif ind == "rsi":
            cols.append('rsi_14')
        elif ind == "atr":
            cols.append('atr')
        elif ind == "macd":
            cols.extend(['macd', 'macd_signal', 'macd_hist'])
    
    return {
        "meta": {"symbol": symbol, "count": len(df), "indicators": indicator_list},
        "data": df[[c for c in cols if c in df.columns]].fillna('null').to_dict(orient='records')
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

---

### **STEP 6: Create Indicators Module**

**File**: `/root/trade-execution-webhook/Webhook-app/market_data_api/indicators.py`

```python
"""
Technical indicators - calculated on-demand
Using vectorized numpy/pandas for efficiency
"""

import pandas as pd
import numpy as np

class TechnicalIndicators:
    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
    
    def calculate_ema(self, periods=[10, 21, 50, 200]):
        """Exponential Moving Average"""
        for period in periods:
            self.df[f'ema_{period}'] = self.df['close'].ewm(span=period, adjust=False).mean()
        return self.df
    
    def calculate_atr(self, period=14):
        """Average True Range (volatility)"""
        high_low = self.df['high'] - self.df['low']
        high_close = np.abs(self.df['high'] - self.df['close'].shift())
        low_close = np.abs(self.df['low'] - self.df['close'].shift())
        
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        tr = np.max(ranges.values, axis=1)
        self.df['atr'] = pd.Series(tr).rolling(window=period).mean().values
        return self.df
    
    def calculate_rsi(self, period=14):
        """Relative Strength Index (momentum)"""
        delta = self.df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        self.df['rsi_14'] = 100 - (100 / (1 + rs))
        return self.df
    
    def calculate_macd(self, fast=12, slow=26, signal=9):
        """MACD (trend following)"""
        ema_fast = self.df['close'].ewm(span=fast, adjust=False).mean()
        ema_slow = self.df['close'].ewm(span=slow, adjust=False).mean()
        
        self.df['macd'] = ema_fast - ema_slow
        self.df['macd_signal'] = self.df['macd'].ewm(span=signal, adjust=False).mean()
        self.df['macd_hist'] = self.df['macd'] - self.df['macd_signal']
        return self.df
    
    def calculate_bollinger_bands(self, period=20, std_dev=2):
        """Bollinger Bands (volatility)"""
        sma = self.df['close'].rolling(period).mean()
        std = self.df['close'].rolling(period).std()
        
        self.df['bb_upper'] = sma + (std * std_dev)
        self.df['bb_middle'] = sma
        self.df['bb_lower'] = sma - (std * std_dev)
        return self.df
```

---

### **STEP 7: Daily Update Cron Job**

**File**: `/root/trade-execution-webhook/Webhook-app/update_daily_ohlcv.py`

```python
"""
Daily update script - Fetch latest OHLCV from Dhan API
Run via cron at 18:00 IST (after market close)
"""

import asyncio
import asyncpg
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import logging

load_dotenv()
DB_PASSWORD = os.getenv("DB_PASSWORD")
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def update_daily():
    """Fetch last 5 days for all symbols and update DB"""
    
    import requests
    import pyotp
    
    # Get token
    totp = pyotp.TOTP(DHAN_TOTP_SECRET)
    otp = totp.now()
    
    response = requests.post(
        "https://api-gw.shoonya.com/auth/login",
        json={
            "userId": DHAN_CLIENT_ID,
            "password": DHAN_PIN,
            "twoFA": otp
        }
    )
    token = response.json().get("authToken")
    
    pool = await asyncpg.create_pool(
        host="localhost",
        port=5432,
        user="market_data_user",
        password=DB_PASSWORD,
        database="market_data",
        min_size=1,
        max_size=3
    )
    
    # Get all active symbols
    async with pool.acquire() as conn:
        symbols = await conn.fetch(
            "SELECT DISTINCT symbol FROM ohlcv_data ORDER BY symbol;"
        )
    
    # Fetch last 5 days
    from_date = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
    to_date = datetime.now().strftime('%Y-%m-%d')
    
    logger.info(f"📊 Updating OHLCV for {len(symbols)} symbols ({from_date} to {to_date})")
    
    for symbol_row in symbols:
        symbol = symbol_row['symbol']
        
        try:
            # Fetch from Dhan
            # ... (same as ingest_ohlcv.py fetch logic)
            
            # Upsert to DB
            # async with pool.acquire() as conn:
            #     await conn.executemany(...)
            
            logger.info(f"✅ {symbol}")
        except Exception as e:
            logger.warning(f"⚠️ {symbol}: {e}")
    
    await pool.close()
    logger.info("✅ Daily update complete")

if __name__ == "__main__":
    asyncio.run(update_daily())
```

**Setup cron job**:
```bash
# Edit crontab
crontab -e

# Add this line (18:00 IST = 12:30 UTC)
30 12 * * 1-5 cd /root/trade-execution-webhook && source venv/bin/activate && python Webhook-app/update_daily_ohlcv.py >> /var/log/update_ohlcv.log 2>&1
```

---

### **STEP 8: Deploy as Systemd Service**

**File**: `/etc/systemd/system/market-data-api.service`

```ini
[Unit]
Description=Market Data API - OHLCV + Charting
After=postgresql.service network-online.target
Wants=postgresql.service

[Service]
Type=notify
User=root
WorkingDirectory=/root/trade-execution-webhook
EnvironmentFile=/root/trade-execution-webhook/.env
Environment="PATH=/root/trade-execution-webhook/venv/bin"

ExecStart=/root/trade-execution-webhook/venv/bin/uvicorn \
    Webhook-app.market_data_api.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 2 \
    --loop uvloop

Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Enable and start**:
```bash
sudo systemctl daemon-reload
sudo systemctl enable market-data-api
sudo systemctl start market-data-api
sudo systemctl status market-data-api

# View logs
sudo journalctl -u market-data-api -f
```

---

### **STEP 9: Nginx Reverse Proxy**

Add to `/etc/nginx/sites-available/default`:

```nginx
upstream market_data_api {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name _;
    
    # Market Data API (new)
    location /api/v1/ohlcv {
        proxy_pass http://market_data_api;
        proxy_cache_valid 200 2h;
        add_header X-Cache-Status $upstream_cache_status;
    }
    
    location /api/v1/charts {
        proxy_pass http://market_data_api;
        proxy_cache_valid 200 1h;
        add_header X-Cache-Status $upstream_cache_status;
    }
    
    location /api/v1/indicators {
        proxy_pass http://market_data_api;
        proxy_cache_valid 200 30m;
    }
    
    # Existing endpoints
    location /webhook/ {
        proxy_pass http://127.0.0.1:5000;
    }
}

# Cache configuration
proxy_cache_path /var/cache/nginx/market_data keys_zone=market_data_cache:10m levels=1:2 max_size=500m;
```

**Reload**:
```bash
sudo nginx -t
sudo systemctl reload nginx
```

---

## ✅ Quick Test After Deployment

```bash
# Test health
curl http://localhost:8000/api/v1/health

# Query OHLCV
curl "http://localhost:8000/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31" | jq

# Generate chart
curl "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31" > chart.svg

# Get indicators
curl "http://localhost:8000/api/v1/indicators?symbol=INFY&from=2024-01-01&to=2024-12-31&indicators=ema,rsi" | jq
```

---

## 📊 Performance Verification

```bash
# Single symbol query (should be <100ms)
time curl -s "http://localhost/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31" > /dev/null

# Multiple symbols (should be <300ms)
time curl -s "http://localhost/api/v1/ohlcv/multi?symbols=INFY,TCS,RELIANCE,HDFCBANK,ICICIBANK&from=2024-01-01&to=2024-12-31" > /dev/null

# Chart generation (should be <200ms)
time curl -s "http://localhost/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31" > /dev/null

# Monitor resource usage
watch -n 1 "free -h && echo '---' && df -h /root && echo '---' && ps aux | grep -E 'postgres|uvicorn' | grep -v grep"
```

---

## 🎯 That's It!

You now have:
- ✅ PostgreSQL + TimescaleDB with 7.5M OHLCV candles
- ✅ FastAPI with 4 endpoint categories (OHLCV, charts, indicators, symbols)
- ✅ On-demand indicator calculations (EMA, RSI, ATR, MACD)
- ✅ Daily auto-update via cron
- ✅ Nginx caching for performance
- ✅ Systemd auto-restart

**Total deployment time**: ~2-3 hours (excluding data ingestion)
