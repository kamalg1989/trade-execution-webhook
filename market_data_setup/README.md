# 📊 Market Data Setup - Complete Implementation Package

Production-ready code for storing and querying 15 years of NSE OHLCV data using PostgreSQL + TimescaleDB.

**Status**: ✅ Ready to Deploy  
**Target VPS**: 165.232.187.97 (1 vCPU, 1 GB RAM, 25 GB Disk)  
**Components**: Database, FastAPI, Scripts, Config Files

---

## 📁 Directory Structure

```
market_data_setup/
├── database/
│   └── schema.sql              # PostgreSQL + TimescaleDB schema (hypertable setup)
│
├── api/
│   ├── __init__.py            # Package marker
│   ├── main.py                # FastAPI application (400+ lines)
│   └── indicators.py          # Technical indicators (EMA, RSI, ATR, MACD, etc.)
│
├── scripts/
│   ├── ingest_ohlcv.py        # Fetch 15 years from Dhan API (run once)
│   └── update_daily_ohlcv.py  # Daily update script (cron job)
│
├── config/
│   ├── market-data-api.service     # Systemd service file
│   ├── nginx.conf                  # Nginx reverse proxy config
│   └── .env.example                # Environment variables template
│
├── requirements.txt            # Python dependencies
├── README.md                   # This file
├── DEPLOYMENT_CHECKLIST.md     # Step-by-step deployment guide
└── QUICK_REFERENCE.md          # Common commands cheat sheet
```

---

## 🚀 Quick Start (5 steps)

### Step 1: Prepare Environment (5 min)

```bash
# On your VPS
ssh root@165.232.187.97
cd /root/trade-execution-webhook

# Create .env from template
cp market_data_setup/config/.env.example .env
nano .env  # Fill in Dhan API credentials and DB password
```

### Step 2: Setup PostgreSQL (15 min)

```bash
# Install PostgreSQL + TimescaleDB
sudo apt update
sudo apt install -y postgresql postgresql-contrib timescaledb-postgresql-14
sudo systemctl start postgresql

# Create database and user
sudo -u postgres createdb market_data
sudo -u postgres createuser market_data_user -P
sudo -u postgres psql -d market_data -c "CREATE EXTENSION timescaledb;"

# Load schema
sudo -u postgres psql -d market_data -f market_data_setup/database/schema.sql
```

### Step 3: Install Python Dependencies (5 min)

```bash
source venv/bin/activate
pip install -r market_data_setup/requirements.txt
```

### Step 4: Ingest Historical Data (20-30 hours background)

```bash
# Run in background
nohup python market_data_setup/scripts/ingest_ohlcv.py > ingest.log 2>&1 &

# Monitor progress
tail -f ingest.log
```

### Step 5: Deploy API (10 min)

```bash
# Install systemd service
sudo cp market_data_setup/config/market-data-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable market-data-api
sudo systemctl start market-data-api

# Update Nginx config (add market data endpoints)
sudo nano /etc/nginx/sites-available/default  # See config/nginx.conf
sudo nginx -t
sudo systemctl reload nginx

# Test API
curl http://localhost:8000/api/v1/health
```

---

## 📊 Components Overview

### 1. Database Layer (`database/schema.sql`)

**PostgreSQL + TimescaleDB Hypertable**
- 7.5M OHLCV records (2000 symbols × 15 years)
- Storage: ~200 MB compressed
- Indexes: symbol+time, time+symbol (optimized for backtesting)
- Retention: Indefinite (or configurable)

**Tables**:
- `ohlcv_data` - Main hypertable with daily candles
- `symbols_meta` - Symbol metadata (sector, ISIN, etc.)
- `ingestion_log` - Track import progress

### 2. API Layer (`api/main.py`)

**FastAPI Application** - 7 endpoints:

| Endpoint | Purpose | Cache |
|----------|---------|-------|
| `/api/v1/health` | Health check | None |
| `/api/v1/ohlcv` | Single symbol OHLCV | 2h |
| `/api/v1/ohlcv/multi` | Multiple symbols | 2h |
| `/api/v1/symbols` | Symbol list | 24h |
| `/api/v1/charts/daily` | Candlestick chart | 1h |
| `/api/v1/charts/weekly` | Weekly chart | 24h |
| `/api/v1/indicators` | Raw indicator data | 30m |

**Features**:
- Connection pooling (asyncpg)
- Nginx caching (500 MB cache)
- In-memory LRU for charts
- SVG chart generation (lightweight)
- Error handling + logging

### 3. Indicators Module (`api/indicators.py`)

**Calculated On-Demand**:
- EMA (10, 21, 50, 200)
- ATR (14)
- RSI (14)
- MACD (12, 26, 9)
- Bollinger Bands
- OBV (On Balance Volume)
- Stochastic Oscillator

**Not Stored in DB** - saves 500 MB+ disk space

### 4. Data Ingestion (`scripts/ingest_ohlcv.py`)

**One-time Historical Load**:
```bash
python market_data_setup/scripts/ingest_ohlcv.py
```

- Fetches 15 years from Dhan API (2010-2024)
- Handles 2000 NSE symbols
- ~20-30 hours runtime
- Safe to interrupt (resumes from where it stopped)
- Upsert logic (handles duplicates)

### 5. Daily Updates (`scripts/update_daily_ohlcv.py`)

**Cron Job** (runs at 18:00 IST daily):
```bash
# Add to crontab:
30 12 * * 1-5 cd /root/trade-execution-webhook && source venv/bin/activate && python market_data_setup/scripts/update_daily_ohlcv.py
```

- Fetches last 5 days from Dhan API
- Updates database with new/modified candles
- Takes ~5-10 minutes

### 6. Configuration Files

**Systemd Service** (`config/market-data-api.service`)
- Auto-restart on reboot
- 2 Uvicorn workers
- Resource limits (512 MB RAM)
- Logs to journalctl

**Nginx Reverse Proxy** (`config/nginx.conf`)
- Caching for different endpoints
- Timeouts tuned for chart generation
- Security headers
- Compression enabled

**Environment** (`config/.env.example`)
- Dhan API credentials
- Database connection strings
- API configuration

---

## 🔌 API Usage Examples

### Query Single Symbol
```bash
curl "http://api.yourserver/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31"

# Response:
{
  "meta": {"symbol": "INFY", "count": 250, "from": "2024-01-01", "to": "2024-12-31"},
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
```

### Bulk Query (Backtesting)
```bash
curl "http://api.yourserver/api/v1/ohlcv/multi?symbols=INFY,TCS,RELIANCE&from=2024-01-01&to=2024-12-31"

# Returns: All 3 symbols × 250 days in <300ms
```

### Generate Chart
```bash
curl "http://api.yourserver/api/v1/charts/daily?symbol=INFY&from=2024-01-01&indicators=ema,rsi,macd" > chart.svg
```

### Get Indicators
```bash
curl "http://api.yourserver/api/v1/indicators?symbol=INFY&indicators=ema,rsi,atr"
```

---

## 🧪 Testing

### Health Check
```bash
curl http://localhost:8000/api/v1/health
# {"status": "ok", "timestamp": "2024-06-28T...", "database": "connected"}
```

### Query Performance
```bash
# Single symbol (should be <100ms)
time curl -s "http://localhost:8000/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31" > /dev/null

# Bulk query (should be <300ms)
time curl -s "http://localhost:8000/api/v1/ohlcv/multi?symbols=INFY,TCS,RELIANCE,HDFCBANK,ICICIBANK&from=2024-01-01&to=2024-12-31" > /dev/null
```

### Monitor Resources
```bash
# Watch memory and disk
watch -n 1 "free -h && df -h /root && ps aux | grep -E 'postgres|uvicorn' | grep -v grep"

# Monitor API logs
sudo journalctl -u market-data-api -f

# Monitor database
psql -U market_data_user -d market_data -c "SELECT COUNT(*) FROM ohlcv_data;"
```

---

## 📈 Performance Targets

| Metric | Target | Notes |
|--------|--------|-------|
| Single symbol query | <100 ms | 250 candles |
| Bulk query (10 symbols) | <300 ms | 2,500 candles |
| Bulk query (50 symbols) | <500 ms | 12,500 candles |
| Chart generation | <200 ms | SVG rendering |
| Cache hit rate | >70% | Nginx + in-memory |
| Memory usage | <1 GB | Peak under load |
| Disk space | <500 MB | Data + logs |

---

## 🔐 Security Considerations

✅ **What's Secure**:
- Database user with minimal permissions
- Nginx reverse proxy (hides internal ports)
- HTTPS-ready (add SSL cert to nginx)
- Input validation on API endpoints
- Rate limiting via Nginx

⚠️ **What to Add** (optional):
- API authentication (API keys)
- HTTPS/TLS
- Request signing
- IP whitelisting

---

## 🐛 Troubleshooting

### API Won't Start
```bash
# Check systemd service
sudo systemctl status market-data-api
sudo journalctl -u market-data-api -n 50

# Test directly
python -m uvicorn market_data_setup.api.main:app --host 127.0.0.1 --port 8000
```

### Database Connection Failed
```bash
# Test connection
psql -U market_data_user -d market_data -h localhost

# Check PostgreSQL status
sudo systemctl status postgresql
sudo -u postgres psql -c "SELECT version();"
```

### Ingestion Slow
```bash
# Monitor progress
tail -f ingest.log | grep "records"

# Check Dhan API status
# (Dhan may have rate limits during peak hours)
```

### Memory Usage High
```bash
# Reduce FastAPI workers
# Edit: /etc/systemd/system/market-data-api.service
# Change: ExecStart workers from 2 to 1

# Reduce PostgreSQL buffer pool
# Edit: /etc/postgresql/14/main/postgresql.conf
# shared_buffers = 64MB (instead of 128MB)
```

---

## 📞 Documentation Files

- **DEPLOYMENT_CHECKLIST.md** - Step-by-step deployment with verification
- **QUICK_REFERENCE.md** - Common commands and queries cheat sheet
- **../MARKET_DATA_STORAGE_ANALYSIS.md** - Deep dive on database design
- **../CHARTING_API_ANALYSIS.md** - Detailed indicator documentation
- **../IMPLEMENTATION_ROADMAP.md** - Timeline and architecture

---

## 🎯 Next Steps

1. **Review** this README
2. **Follow** DEPLOYMENT_CHECKLIST.md for step-by-step deployment
3. **Test** with curl commands above
4. **Monitor** resources and logs for 1 week
5. **Integrate** with your backtesting system

---

## 📧 Support

For issues or questions:
1. Check logs: `sudo journalctl -u market-data-api -f`
2. Review QUICK_REFERENCE.md
3. Check PostgreSQL: `psql -U market_data_user -d market_data`
4. Test API: `curl http://localhost:8000/api/v1/health`

---

**Created**: June 28, 2026  
**Status**: Production Ready ✅  
**Last Updated**: 2026-06-28
