# 📊 NSE Market Data Storage Architecture Analysis
## 15 Years OHLCV for 2000 Stocks + Efficient Backtesting API

---

## 📈 Data Volume Calculation

### Raw Data Metrics
- **Stocks**: 2000 NSE equities
- **Trading Days/Year**: ~250 days (excluding weekends/holidays)
- **Time Period**: 15 years
- **Data Points per Stock**: 250 × 15 = **3,750 OHLCV records**
- **Total Records**: 2000 × 3,750 = **7.5 million OHLCV candles**

### Storage Estimate (by DB type)
| Metric | Value |
|--------|-------|
| **Raw Data (uncompressed)** | ~375 MB (assuming 50 bytes/record) |
| **PostgreSQL (with indexes)** | ~800 MB - 1.2 GB |
| **TimescaleDB (compressed)** | ~150-200 MB |
| **InfluxDB (compressed)** | ~100-150 MB |

---

## 🏗️ Schema Design Options

### Option 1: TimescaleDB (RECOMMENDED FOR BACKTESTING)
**Best fit for time-series OHLCV queries**

```sql
-- Main hypertable for OHLCV data
CREATE TABLE ohlcv_data (
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    open NUMERIC(10, 2) NOT NULL,
    high NUMERIC(10, 2) NOT NULL,
    low NUMERIC(10, 2) NOT NULL,
    close NUMERIC(10, 2) NOT NULL,
    volume BIGINT NOT NULL,
    oi BIGINT,  -- Open Interest (if available from Dhan)
    data_source TEXT DEFAULT 'dhan',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Convert to hypertable (time-series optimized)
SELECT create_hypertable('ohlcv_data', 'time', if_not_exists => TRUE);

-- Indexes for common query patterns
CREATE INDEX idx_symbol_time ON ohlcv_data (symbol, time DESC);
CREATE INDEX idx_time_symbol ON ohlcv_data (time DESC, symbol);

-- Metadata table for symbols
CREATE TABLE symbols_meta (
    symbol TEXT PRIMARY KEY,
    isin TEXT,
    security_name TEXT,
    sector TEXT,
    list_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    dhan_security_id TEXT UNIQUE,
    last_updated TIMESTAMPTZ DEFAULT NOW()
);

-- Compression (TimescaleDB enterprise feature, or use native compression)
ALTER TABLE ohlcv_data SET (
    timescaledb.compress = TRUE,
    timescaledb.compress_interval = '1 months'
);

-- Retention policy (optional: auto-delete raw data older than 15+ months, keep compressed)
SELECT add_retention_policy('ohlcv_data', INTERVAL '16 months', if_not_exists => TRUE);
```

**Query Performance Examples**:
```sql
-- Single stock, date range (< 10ms for 250 days)
SELECT * FROM ohlcv_data 
WHERE symbol = 'INFY' 
  AND time >= '2024-01-01'::date 
  AND time < '2024-12-31'::date 
ORDER BY time;

-- Multiple stocks, date range (< 50ms for 10 stocks × 250 days)
SELECT symbol, time, close, volume 
FROM ohlcv_data 
WHERE symbol IN ('INFY', 'TCS', 'RELIANCE', 'BHARTIARTL', 'HDFCBANK')
  AND time >= '2023-01-01' 
  AND time < '2024-01-01'
ORDER BY symbol, time;

-- Volume analysis (efficient due to time-bucketing)
SELECT 
    date_trunc('month', time) as month,
    symbol,
    AVG(volume) as avg_volume,
    MAX(close) as month_high
FROM ohlcv_data 
WHERE time >= now() - INTERVAL '3 years'
GROUP BY 1, 2
ORDER BY 1 DESC, 2;
```

**Pros**:
- ✅ Purpose-built for time-series data
- ✅ Automatic compression (3-5x space savings)
- ✅ Sub-second queries on date ranges
- ✅ Seamless PostgreSQL compatibility
- ✅ Native partitioning by time
- ✅ Retention policies (auto-cleanup)

**Cons**:
- Requires PostgreSQL 12+
- TimescaleDB license ($$$$ for enterprise features)
- Community version limited (but still excellent)

---

### Option 2: PostgreSQL Native (RELIABLE FALLBACK)
**Traditional RDBMS approach, proven reliability**

```sql
CREATE TABLE ohlcv_data (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    trading_date DATE NOT NULL,
    open DECIMAL(10, 2) NOT NULL,
    high DECIMAL(10, 2) NOT NULL,
    low DECIMAL(10, 2) NOT NULL,
    close DECIMAL(10, 2) NOT NULL,
    volume BIGINT NOT NULL,
    oi BIGINT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, trading_date)
);

-- Critical indexes for backtesting queries
CREATE INDEX idx_symbol_date ON ohlcv_data (symbol, trading_date DESC);
CREATE INDEX idx_date_symbol ON ohlcv_data (trading_date DESC, symbol);
CREATE INDEX idx_symbol_date_range ON ohlcv_data (symbol, trading_date) 
    WHERE is_active = TRUE;

-- Partitioning by year (for very large tables)
CREATE TABLE ohlcv_data_2024 PARTITION OF ohlcv_data
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE ohlcv_data_2023 PARTITION OF ohlcv_data
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
-- ... etc for other years

CREATE TABLE symbols_meta (
    symbol VARCHAR(20) PRIMARY KEY,
    isin VARCHAR(12),
    security_name VARCHAR(255),
    sector VARCHAR(50),
    list_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    dhan_security_id VARCHAR(20) UNIQUE,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Query Performance**:
```sql
-- Single symbol, date range (< 100ms with good indexes)
SELECT * FROM ohlcv_data 
WHERE symbol = 'INFY' 
  AND trading_date BETWEEN '2024-01-01' AND '2024-12-31'
ORDER BY trading_date;

-- Bulk query (< 200ms for multiple symbols)
SELECT symbol, trading_date, close, volume 
FROM ohlcv_data 
WHERE symbol = ANY(ARRAY['INFY', 'TCS', 'RELIANCE'])
  AND trading_date >= '2023-01-01'
ORDER BY symbol, trading_date;
```

**Pros**:
- ✅ No additional license
- ✅ Proven, battle-tested
- ✅ Excellent tooling/documentation
- ✅ Easy to backup/replicate
- ✅ Partition support for scaling

**Cons**:
- ❌ Larger storage footprint (2-3x vs TimescaleDB)
- ❌ Manual partitioning required at scale
- ❌ Slower aggregations on time-series data
- ❌ No automatic compression

---

### Option 3: InfluxDB (LIGHTWEIGHT ALTERNATIVE)
**Purpose-built time-series DB, minimal ops overhead**

```
Measurement: ohlcv
Tags: symbol (indexed)
Fields: open, high, low, close, volume, oi (numeric)
Timestamp: Unix nanoseconds

Example write:
ohlcv,symbol=INFY open=1500.25,high=1510.50,low=1495.00,close=1505.75,volume=5000000i 1704067200000000000
```

**Query Pattern (InfluxQL)**:
```sql
SELECT close, volume FROM ohlcv 
WHERE symbol = 'INFY' 
  AND time >= '2024-01-01' AND time < '2025-01-01'
```

**Pros**:
- ✅ Smallest storage footprint (~100-150 MB)
- ✅ High write throughput
- ✅ Simplest setup for time-series
- ✅ Built-in retention policies

**Cons**:
- ❌ Not relational (harder to join with metadata)
- ❌ Less mature query language
- ❌ Community edition limitations (InfluxDB 3.x closed-source)
- ❌ Weaker for complex analytics

---

## 🎯 Recommendation: **TimescaleDB**

### Why TimescaleDB for Your Use Case?

1. **Backtesting Performance**: Sub-100ms queries for date ranges across thousands of records
2. **Storage Efficiency**: Automatic time-series compression (3-5x savings)
3. **SQL Familiarity**: Full PostgreSQL compatibility—no new query language
4. **Operational Simplicity**: Single database, automatic partitioning, built-in retention
5. **Scalability**: Handles distributed (multi-node) deployments
6. **Cost**: Community edition is free; features sufficient for your needs

### Timeline: 15 Years = ~3,750 records per stock
```
Compression Timeline (TimescaleDB):
- Recent 2 weeks: Raw (hot data)
- 2 weeks → 12 months: Uncompressed
- >12 months: Compressed (3-5x savings)
  └─ Total compressed storage: ~200 MB for 7.5M candles
```

---

## 🔌 API Design (Flask/FastAPI)

### Endpoint Structure

```python
# GET /api/v1/ohlcv/symbol/<symbol>
# Query Params: from_date, to_date, interval (optional: 1d, 1h, 4h, etc.)
# Response: List of OHLCV candles

GET /api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31
Response:
{
  "meta": {
    "symbol": "INFY",
    "count": 250,
    "from": "2024-01-01",
    "to": "2024-12-31"
  },
  "data": [
    {
      "date": "2024-01-01",
      "open": 1450.25,
      "high": 1465.50,
      "low": 1445.00,
      "close": 1455.75,
      "volume": 8500000,
      "oi": null
    },
    ...
  ]
}

# GET /api/v1/ohlcv/multi
# Query symbols as array, date range
GET /api/v1/ohlcv/multi?symbols=INFY,TCS,RELIANCE&from=2024-01-01&to=2024-12-31
Response:
{
  "meta": {
    "symbols": ["INFY", "TCS", "RELIANCE"],
    "count": 750,
    "from": "2024-01-01",
    "to": "2024-12-31"
  },
  "data": {
    "INFY": [ ... ],
    "TCS": [ ... ],
    "RELIANCE": [ ... ]
  }
}

# GET /api/v1/symbols
# Get all available symbols
GET /api/v1/symbols?sector=IT&is_active=true
Response:
{
  "data": [
    {
      "symbol": "INFY",
      "security_name": "Infosys Limited",
      "sector": "IT",
      "isin": "INE009A01021",
      "list_date": "1994-11-07"
    },
    ...
  ],
  "count": 45
}

# POST /api/v1/backtest/query
# Bulk historical data for backtesting
POST /api/v1/backtest/query
{
  "symbols": ["INFY", "TCS", "RELIANCE"],
  "from": "2020-01-01",
  "to": "2024-12-31",
  "fields": ["open", "high", "low", "close", "volume"]
}
Response: CSV or JSON (configurable)
```

### Implementation (FastAPI Example)

```python
from fastapi import FastAPI, Query, HTTPException
from datetime import datetime, date
from typing import List, Optional
import asyncpg
import json

app = FastAPI()

# Connection pool
pool: asyncpg.pool.Pool = None

@app.on_event("startup")
async def connect_db():
    global pool
    pool = await asyncpg.create_pool(
        host="localhost",
        port=5432,
        user="market_data_user",
        password="secure_password",
        database="market_data",
        min_size=5,
        max_size=20
    )

@app.get("/api/v1/ohlcv")
async def get_ohlcv(
    symbol: str = Query(..., min_length=1, max_length=20),
    from_date: date = Query(...),
    to_date: date = Query(...),
    limit: int = Query(default=10000, le=100000)
):
    """Fetch OHLCV data for a single symbol"""
    if from_date > to_date:
        raise HTTPException(status_code=400, detail="from_date must be <= to_date")
    
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT 
                trading_date,
                open, high, low, close, volume, oi
            FROM ohlcv_data
            WHERE symbol = $1 
              AND trading_date BETWEEN $2 AND $3
            ORDER BY trading_date
            LIMIT $4
        """, symbol.upper(), from_date, to_date, limit)
    
    return {
        "meta": {
            "symbol": symbol.upper(),
            "count": len(rows),
            "from": str(from_date),
            "to": str(to_date)
        },
        "data": [
            {
                "date": str(row['trading_date']),
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
    symbols: str = Query(...),  # comma-separated
    from_date: date = Query(...),
    to_date: date = Query(...)
):
    """Fetch OHLCV for multiple symbols (backtesting)"""
    symbol_list = [s.strip().upper() for s in symbols.split(",")]
    
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT 
                symbol, trading_date,
                open, high, low, close, volume, oi
            FROM ohlcv_data
            WHERE symbol = ANY($1)
              AND trading_date BETWEEN $2 AND $3
            ORDER BY symbol, trading_date
        """, symbol_list, from_date, to_date)
    
    # Group by symbol
    grouped = {}
    for row in rows:
        sym = row['symbol']
        if sym not in grouped:
            grouped[sym] = []
        grouped[sym].append({
            "date": str(row['trading_date']),
            "open": float(row['open']),
            "high": float(row['high']),
            "low": float(row['low']),
            "close": float(row['close']),
            "volume": int(row['volume']),
            "oi": int(row['oi']) if row['oi'] else None
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

@app.post("/api/v1/backtest/query")
async def backtest_query(request_body: dict):
    """Bulk export for backtesting"""
    symbols = request_body.get("symbols", [])
    from_date = request_body.get("from")
    to_date = request_body.get("to")
    
    # Fetch data using same pattern as above
    # Return as CSV or JSON-ND (one JSON object per line)
    pass
```

---

## 🔄 Data Ingestion Pipeline

### Dhan API → Database

```python
# Pseudocode for ingestion worker
import asyncpg
from dhan_api import DhanClient
from datetime import datetime, timedelta

async def ingest_historical_data():
    """Fetch 15 years of OHLCV from Dhan and store"""
    
    # Step 1: Get all NSE symbols from Dhan
    symbols = await get_nse_symbols()  # ~2000 symbols
    
    # Step 2: For each symbol, fetch history in chunks
    async with pool.acquire() as conn:
        for symbol in symbols:
            # Dhan may have API rate limits; fetch in batches
            for year in range(2010, 2025):
                data = await dhan_client.get_historical(
                    symbol=symbol,
                    from_date=f"{year}-01-01",
                    to_date=f"{year}-12-31",
                    resolution="daily"
                )
                
                # Bulk insert (batch by 1000)
                if data:
                    await conn.executemany("""
                        INSERT INTO ohlcv_data (
                            symbol, trading_date, open, high, low, close, volume, oi
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        ON CONFLICT (symbol, trading_date) DO UPDATE SET
                            open=EXCLUDED.open,
                            high=EXCLUDED.high,
                            low=EXCLUDED.low,
                            close=EXCLUDED.close,
                            volume=EXCLUDED.volume,
                            oi=EXCLUDED.oi,
                            updated_at=NOW()
                    """, data)

# Run weekly to update latest candles
async def update_recent_data():
    """Fetch last 5 days for all symbols"""
    symbols = await get_active_symbols()
    
    async with pool.acquire() as conn:
        for symbol in symbols:
            data = await dhan_client.get_historical(
                symbol=symbol,
                from_date=(datetime.now() - timedelta(days=5)).date(),
                to_date=datetime.now().date(),
                resolution="daily"
            )
            # Upsert logic...
```

---

## 📊 Performance Benchmarks

### TimescaleDB Typical Query Times (SSD VPS)
| Query Type | Symbols | Duration | Time |
|------------|---------|----------|------|
| Single symbol, 1 year | 1 | 250 candles | **5-10ms** |
| Single symbol, 15 years | 1 | 3,750 candles | **15-25ms** |
| 10 symbols, 5 years | 10 | 12,500 candles | **40-60ms** |
| 100 symbols, 1 year | 100 | 25,000 candles | **100-150ms** |
| All 2000 symbols, 1 month | 2000 | ~167,000 candles | **300-500ms** |

**Optimizations for Backtesting**:
1. **Connection pooling**: 10-20 concurrent connections
2. **Prepared statements**: Eliminate parsing overhead
3. **Batch fetches**: Fetch 10K+ rows at once
4. **Caching layer** (Redis): Cache popular symbol ranges

---

## 🛠️ Implementation Roadmap

### Phase 1: Infrastructure (Week 1)
- [ ] Set up PostgreSQL 14+ with TimescaleDB extension
- [ ] Create schema (ohlcv_data, symbols_meta tables)
- [ ] Create indexes
- [ ] Test connection from Flask app

### Phase 2: Data Ingestion (Week 2-3)
- [ ] Write Dhan API historical fetch script
- [ ] Implement upsert logic (handle duplicates)
- [ ] Load 15 years of data for 2000 stocks (~20-30 hours parallel)
- [ ] Validate data integrity (row counts, date ranges)

### Phase 3: API Layer (Week 3-4)
- [ ] Implement FastAPI endpoints
- [ ] Add query parameter validation
- [ ] Add pagination/caching
- [ ] Add authentication (API keys)

### Phase 4: Optimization (Week 4+)
- [ ] Add Redis caching for popular queries
- [ ] Implement batch/export endpoints for backtesting
- [ ] Add compression policies (TimescaleDB)
- [ ] Monitor query performance with EXPLAIN ANALYZE

---

## 🔐 VPS Deployment Considerations

### Your VPS (165.232.187.97)
- **Current**: Python Flask, Google Sheets (for trades)
- **Proposed**: Add PostgreSQL + TimescaleDB + new Flask API

### Disk Space
```
Current estimate:
- OS + app: ~2 GB
- Market data DB: ~1.2 GB (with TimescaleDB)
- Backups: ~500 MB (compressed weekly)
Total: ~4 GB (well within typical VPS)
```

### Resource Usage
```
For 7.5M candles with ~50 concurrent backtest queries:
- RAM: 512 MB - 1 GB (pool 20 connections)
- CPU: <20% on modern 2-core VPS
- I/O: Minimal (SSD reads are fast)
```

### Installation Steps on VPS
```bash
# 1. Install PostgreSQL + TimescaleDB
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo apt install timescaledb-postgresql-14

# 2. Initialize TimescaleDB
sudo timescaledb-tune --quiet --yes
sudo systemctl restart postgresql

# 3. Create database and user
sudo -u postgres createdb market_data
sudo -u postgres createuser market_data_user -P

# 4. Enable TimescaleDB extension
sudo -u postgres psql -d market_data -c "CREATE EXTENSION timescaledb;"

# 5. Run schema SQL from above
sudo -u postgres psql -d market_data -f /path/to/schema.sql

# 6. Update Flask requirements.txt
pip install asyncpg fastapi uvicorn

# 7. Deploy API (systemd service or supervisor)
```

---

## 🎓 Query Examples for Backtesting

### Moving Average Calculation
```sql
-- 20-day SMA for a symbol
SELECT 
    trading_date,
    close,
    AVG(close) OVER (
        ORDER BY trading_date 
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ) as sma_20
FROM ohlcv_data
WHERE symbol = 'INFY'
  AND trading_date >= '2024-01-01'
ORDER BY trading_date;
```

### Volatility (ATR-like)
```sql
SELECT 
    trading_date,
    high - low as range,
    AVG(high - low) OVER (
        ORDER BY trading_date 
        ROWS BETWEEN 13 PRECEDING AND CURRENT ROW
    ) as atr_14
FROM ohlcv_data
WHERE symbol = 'INFY'
ORDER BY trading_date;
```

### Multi-symbol comparison
```sql
SELECT 
    trading_date,
    symbol,
    close,
    LAG(close) OVER (PARTITION BY symbol ORDER BY trading_date) as prev_close,
    ROUND(100 * (close - LAG(close) OVER (PARTITION BY symbol ORDER BY trading_date)) 
          / LAG(close) OVER (PARTITION BY symbol ORDER BY trading_date), 2) as daily_return_pct
FROM ohlcv_data
WHERE symbol IN ('INFY', 'TCS', 'RELIANCE')
  AND trading_date >= '2024-01-01'
ORDER BY symbol, trading_date;
```

---

## 📋 Summary Table: Database Comparison

| Aspect | TimescaleDB | PostgreSQL | InfluxDB |
|--------|-------------|------------|----------|
| **Storage** | ~200 MB (compressed) | ~1.2 GB | ~150 MB |
| **Query Speed** | ⭐⭐⭐⭐⭐ (5-25ms) | ⭐⭐⭐⭐ (10-100ms) | ⭐⭐⭐⭐⭐ (1-10ms) |
| **Setup Complexity** | Medium | Low | Low |
| **SQL Support** | Full PostgreSQL | Full | Limited (InfluxQL) |
| **Joins/Metadata** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐ (not ideal) |
| **Backtesting Friendly** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| **Cost** | Free (community) | Free | Limited free tier |
| **Operational Burden** | Low | Very Low | Medium |
| **Scalability** | ⭐⭐⭐⭐⭐ (distributed) | ⭐⭐⭐⭐ (with sharding) | ⭐⭐⭐⭐⭐ (native) |

---

## ✅ Next Steps

1. **Validate**: Review this analysis and confirm TimescaleDB choice
2. **Setup**: Provision PostgreSQL + TimescaleDB on VPS
3. **Schema**: Create tables, indexes, partitions
4. **Ingest**: Write Dhan API historical fetch script
5. **API**: Build FastAPI endpoints for backtest queries
6. **Test**: Load real data, benchmark queries, optimize as needed
7. **Deploy**: Add to systemd, integrate with existing Flask app

---

**Questions to address before implementation**:
- Are intraday candles (1h, 4h) needed, or daily only?
- Do you need volume profile or other advanced metrics?
- Is 15-year full history needed immediately, or can it be loaded incrementally?
- What's your VPS storage/RAM limit?
