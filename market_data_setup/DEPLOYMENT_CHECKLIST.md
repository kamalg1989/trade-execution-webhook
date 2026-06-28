# ✅ Deployment Checklist - Market Data API

Complete step-by-step deployment guide with verification at each stage.

**Estimated Time**: 30 min setup + 20-30 hours for data ingestion (background)

---

## Phase 1: Environment Setup (5 min)

- [ ] **SSH to VPS**
```bash
ssh root@165.232.187.97
```

- [ ] **Navigate to project**
```bash
cd /root/trade-execution-webhook
ls -la market_data_setup/  # Verify folder exists
```

- [ ] **Create .env file**
```bash
cp market_data_setup/config/.env.example .env
nano .env  # Fill in these values:
# DHAN_CLIENT_ID=your_id
# DHAN_PIN=your_pin
# DHAN_TOTP_SECRET=your_secret
# DB_PASSWORD=choose_secure_password
```

- [ ] **Verify Dhan credentials work**
```bash
python3 << 'EOF'
import os
from dotenv import load_dotenv
load_dotenv()
print(f"Client ID: {os.getenv('DHAN_CLIENT_ID', 'NOT SET')[:10]}...")
print(f"PIN length: {len(os.getenv('DHAN_PIN', ''))}")
print(f"TOTP length: {len(os.getenv('DHAN_TOTP_SECRET', ''))}")
print(f"DB Password: {'SET' if os.getenv('DB_PASSWORD') else 'NOT SET'}")
EOF
```

---

## Phase 2: PostgreSQL Setup (15 min)

- [ ] **Install PostgreSQL + TimescaleDB**
```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib timescaledb-postgresql-14
sudo systemctl enable postgresql
sudo systemctl start postgresql
```

- [ ] **Verify installation**
```bash
sudo -u postgres psql -c "SELECT version();"
# Should show: PostgreSQL 14.x ... and TimescaleDB installed
```

- [ ] **Create database**
```bash
sudo -u postgres createdb market_data
echo "✅ Database created"
```

- [ ] **Create database user**
```bash
sudo -u postgres createuser market_data_user -P
# When prompted, enter the DB_PASSWORD from your .env
echo "✅ User created"
```

- [ ] **Grant permissions**
```bash
sudo -u postgres psql <<EOF
GRANT ALL PRIVILEGES ON DATABASE market_data TO market_data_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO market_data_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO market_data_user;
EOF
echo "✅ Permissions granted"
```

- [ ] **Enable TimescaleDB extension**
```bash
sudo -u postgres psql -d market_data -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
echo "✅ TimescaleDB extension enabled"
```

- [ ] **Load schema**
```bash
sudo -u postgres psql -d market_data -f market_data_setup/database/schema.sql
echo "✅ Schema loaded"
```

- [ ] **Verify schema**
```bash
sudo -u postgres psql -d market_data <<EOF
SELECT hypertable_name FROM timescaledb_information.hypertables;
SELECT COUNT(*) as symbol_count FROM symbols_meta;
EOF
# Should show: ohlcv_data hypertable, 10 initial symbols
```

---

## Phase 3: Python Dependencies (5 min)

- [ ] **Activate virtual environment**
```bash
source venv/bin/activate
```

- [ ] **Upgrade pip**
```bash
pip install --upgrade pip
```

- [ ] **Install requirements**
```bash
pip install -r market_data_setup/requirements.txt
```

- [ ] **Verify installation**
```bash
python3 -c "import fastapi, asyncpg, pandas; print('✅ All dependencies installed')"
```

---

## Phase 4: Test Database Connection (5 min)

- [ ] **Test connection from Python**
```bash
python3 << 'EOF'
import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

async def test():
    conn = await asyncpg.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=int(os.getenv('DB_PORT', 5432)),
        user=os.getenv('DB_USER', 'market_data_user'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME', 'market_data')
    )
    result = await conn.fetchval('SELECT COUNT(*) FROM ohlcv_data;')
    print(f"✅ Connected! Current records: {result}")
    await conn.close()

asyncio.run(test())
EOF
```

- [ ] **Verify can write to database**
```bash
sudo -u postgres psql -d market_data -c "INSERT INTO symbols_meta (symbol, security_name, sector) VALUES ('TEST', 'Test Company', 'Test') ON CONFLICT DO NOTHING; SELECT COUNT(*) FROM symbols_meta;"
```

---

## Phase 5: Start Historical Data Ingestion (background)

- [ ] **Review ingestion script**
```bash
head -50 market_data_setup/scripts/ingest_ohlcv.py  # Verify it looks correct
```

- [ ] **Start ingestion in background**
```bash
nohup python market_data_setup/scripts/ingest_ohlcv.py > ingest.log 2>&1 &
echo "✅ Ingestion started (PID: $!)"
```

- [ ] **Monitor initial progress (first 5 minutes)**
```bash
sleep 30
tail -20 ingest.log
# Should show: "Authenticating...", "Fetching symbols...", "[1/X] symbol..."
```

- [ ] **Set up log monitoring** (for later)
```bash
# Check progress periodically
tail -f ingest.log | grep "✅\|❌\|records"
# Ctrl+C to stop monitoring
```

---

## Phase 6: Deploy FastAPI Application (10 min)

- [ ] **Test API locally first** (optional)
```bash
timeout 10 python -m uvicorn market_data_setup.api.main:app --host 127.0.0.1 --port 8000 &
sleep 3
curl http://127.0.0.1:8000/api/v1/health
# Should return: {"status": "ok", ...}
pkill -f uvicorn
```

- [ ] **Install systemd service**
```bash
sudo cp market_data_setup/config/market-data-api.service /etc/systemd/system/
sudo systemctl daemon-reload
echo "✅ Systemd service installed"
```

- [ ] **Enable auto-start**
```bash
sudo systemctl enable market-data-api
echo "✅ API will auto-start on reboot"
```

- [ ] **Start service**
```bash
sudo systemctl start market-data-api
sleep 2
sudo systemctl status market-data-api
# Should show: "active (running)"
```

- [ ] **Test API endpoint**
```bash
curl http://127.0.0.1:8000/api/v1/health
# Should return: {"status": "ok", "database": "connected", ...}
```

- [ ] **Check service logs**
```bash
sudo journalctl -u market-data-api -n 20
# Should show: "Database pool initialized", "✅ API initialized"
```

---

## Phase 7: Configure Nginx Reverse Proxy (10 min)

- [ ] **Backup existing nginx config**
```bash
sudo cp /etc/nginx/sites-available/default /etc/nginx/sites-available/default.backup
echo "✅ Backup created"
```

- [ ] **Add market data API locations to nginx**
```bash
# Open your nginx config
sudo nano /etc/nginx/sites-available/default

# Add this upstream block at the top:
# upstream market_data_api {
#     server 127.0.0.1:8000;
# }

# Add these location blocks:
# location /api/v1/ohlcv {
#     proxy_pass http://market_data_api;
#     proxy_cache_valid 200 2h;
# }
#
# location /api/v1/charts {
#     proxy_pass http://market_data_api;
#     proxy_cache_valid 200 1h;
# }
#
# (See market_data_setup/config/nginx.conf for complete config)

echo "✅ Nginx config updated"
```

- [ ] **Test nginx config**
```bash
sudo nginx -t
# Should return: "test is successful"
```

- [ ] **Reload nginx**
```bash
sudo systemctl reload nginx
echo "✅ Nginx reloaded"
```

- [ ] **Test through nginx**
```bash
curl http://localhost/api/v1/health
# Should return: {"status": "ok", ...}
```

---

## Phase 8: Setup Daily Cron Job (5 min)

- [ ] **Review update script**
```bash
head -30 market_data_setup/scripts/update_daily_ohlcv.py
```

- [ ] **Create cron job**
```bash
# Edit crontab
crontab -e

# Add this line (runs at 18:00 IST = 12:30 UTC, weekdays only):
# 30 12 * * 1-5 cd /root/trade-execution-webhook && source venv/bin/activate && python market_data_setup/scripts/update_daily_ohlcv.py >> /var/log/update_ohlcv.log 2>&1

echo "✅ Cron job created"
```

- [ ] **Verify cron job**
```bash
crontab -l | grep update_daily
# Should show your cron job
```

- [ ] **Test cron script manually** (optional, only after initial data load)
```bash
# Wait until initial ingestion completes first!
# python market_data_setup/scripts/update_daily_ohlcv.py
```

---

## Phase 9: Verification & Testing (10 min)

- [ ] **Check ingestion progress**
```bash
tail -20 ingest.log
# Should show recent records being inserted
```

- [ ] **Query database directly**
```bash
psql -U market_data_user -d market_data -h localhost <<EOF
SELECT COUNT(*) as total_records FROM ohlcv_data;
SELECT COUNT(DISTINCT symbol) as unique_symbols FROM ohlcv_data;
SELECT MIN(time), MAX(time) FROM ohlcv_data;
EOF
```

- [ ] **Test API endpoints**
```bash
# Health check
curl http://localhost:8000/api/v1/health | jq '.'

# Get symbols
curl "http://localhost:8000/api/v1/symbols?is_active=true" | jq '.count'

# Query OHLCV (if data exists)
curl "http://localhost:8000/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31" | jq '.meta'
```

- [ ] **Check API logs**
```bash
sudo journalctl -u market-data-api -f --since "1 hour ago"
# Should show: requests coming in, no errors
```

- [ ] **Monitor resource usage**
```bash
free -h
df -h /root
ps aux | grep -E "postgres|uvicorn" | grep -v grep
```

---

## Phase 10: Final Checks (5 min)

- [ ] **Reboot to verify auto-start**
```bash
# OPTIONAL: Only do this if everything works
# sudo reboot
# Wait 2 minutes
# ssh back in
# sudo systemctl status market-data-api  # Should be active
```

- [ ] **Document database credentials**
```bash
echo "=== DEPLOYMENT SUMMARY ==="
echo "API: http://localhost:8000/api/v1/health"
echo "API Docs: http://localhost:8000/api/v1/docs"
echo "Database: market_data"
echo "User: market_data_user"
echo "Ingestion: running (check ingest.log)"
echo "Cron: Daily at 18:00 IST"
echo "======================================"
```

- [ ] **Create backup of .env** (secure location)
```bash
cp .env ~/.env.backup
chmod 600 ~/.env.backup
echo "✅ Backup created at ~/.env.backup"
```

- [ ] **All checks passed!**
```bash
echo "✅ DEPLOYMENT COMPLETE"
echo "Next steps:"
echo "  1. Monitor ingestion progress: tail -f ingest.log"
echo "  2. Query data once ingestion completes"
echo "  3. Test backtesting API queries"
echo "  4. Integrate with your trading system"
```

---

## 🎯 Deployment Timeline

| Phase | Task | Duration | Status |
|-------|------|----------|--------|
| 1 | Environment setup | 5 min | ⏳ |
| 2 | PostgreSQL | 15 min | ⏳ |
| 3 | Python deps | 5 min | ⏳ |
| 4 | DB connection test | 5 min | ⏳ |
| 5 | Start ingestion | - | 🟢 (background) |
| 6 | FastAPI | 10 min | ⏳ |
| 7 | Nginx | 10 min | ⏳ |
| 8 | Cron job | 5 min | ⏳ |
| 9 | Verification | 10 min | ⏳ |
| 10 | Final checks | 5 min | ⏳ |
| **TOTAL** | **Setup** | **~65 min** | **+ 20-30h data** |

---

## 🔧 Troubleshooting During Deployment

### PostgreSQL won't start
```bash
sudo systemctl status postgresql
sudo journalctl -u postgresql -n 30
# Common: Port 5432 already in use
```

### API won't start
```bash
sudo systemctl status market-data-api
sudo journalctl -u market-data-api -n 50
# Common: DB_PASSWORD incorrect, database doesn't exist
```

### Ingestion failing
```bash
tail -50 ingest.log
# Common: Dhan API auth issues, rate limiting, network timeout
# Solution: Retry manually after fixing
```

### Nginx errors
```bash
sudo nginx -t
sudo systemctl status nginx
sudo tail -20 /var/log/nginx/error.log
```

---

## ✨ After Deployment

Once everything is running:

1. **Monitor ingestion** (20-30 hours)
   ```bash
   watch -n 60 'tail -5 ingest.log'
   ```

2. **Test queries** after data loads
   ```bash
   curl "http://localhost/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31"
   ```

3. **Integrate with backtest**
   - Update your backtesting code to use `/api/v1/ohlcv/multi`
   - Verify query performance

4. **Monitor daily updates**
   - Check cron job runs at 18:00 IST
   - Verify new candles are added

---

**Status**: Ready to Deploy ✅  
**Last Updated**: 2026-06-28
