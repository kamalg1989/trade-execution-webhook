# 🚀 Quick Reference Guide

Common commands, queries, and troubleshooting for the Market Data API.

---

## 🔧 Service Management

```bash
# Start/stop/restart API
sudo systemctl start market-data-api
sudo systemctl stop market-data-api
sudo systemctl restart market-data-api

# Check status
sudo systemctl status market-data-api

# View logs
sudo journalctl -u market-data-api -f                    # Real-time
sudo journalctl -u market-data-api -n 50               # Last 50 lines
sudo journalctl -u market-data-api --since "1 hour ago" # Last hour

# PostgreSQL
sudo systemctl start postgresql
sudo systemctl stop postgresql
sudo systemctl status postgresql
```

---

## 🗄️ Database Queries

```bash
# Connect to database
psql -U market_data_user -d market_data -h localhost

# Count total records
SELECT COUNT(*) as total FROM ohlcv_data;

# Count by symbol
SELECT symbol, COUNT(*) FROM ohlcv_data GROUP BY symbol ORDER BY COUNT(*) DESC LIMIT 10;

# Check date range
SELECT MIN(time), MAX(time) FROM ohlcv_data;

# Check data for specific symbol
SELECT time, open, high, low, close, volume FROM ohlcv_data WHERE symbol = 'INFY' ORDER BY time DESC LIMIT 5;

# Check compression status
SELECT hypertable_name, num_chunks FROM timescaledb_information.hypertables;

# View index usage
SELECT indexrelname, idx_scan FROM pg_stat_user_indexes WHERE relname = 'ohlcv_data';

# Table sizes
SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) FROM pg_class WHERE relname IN ('ohlcv_data', 'symbols_meta') ORDER BY pg_total_relation_size(relid) DESC;

# Cleanup old data (if needed)
DELETE FROM ohlcv_data WHERE time < '2010-01-01'::timestamp with time zone;
VACUUM ANALYZE ohlcv_data;
```

---

## 📊 API Testing

### Health Check
```bash
curl http://localhost:8000/api/v1/health
curl http://localhost/api/v1/health  # Through Nginx
```

### Query OHLCV (Single Symbol)
```bash
# Basic
curl "http://localhost:8000/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31"

# Pretty print
curl -s "http://localhost:8000/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31" | jq '.'

# Save to file
curl -s "http://localhost:8000/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31" > infy_data.json
```

### Query OHLCV (Multiple Symbols)
```bash
# Bulk query for backtesting
curl "http://localhost:8000/api/v1/ohlcv/multi?symbols=INFY,TCS,RELIANCE,HDFCBANK&from=2024-01-01&to=2024-12-31"

# With jq for pretty print
curl -s "http://localhost:8000/api/v1/ohlcv/multi?symbols=INFY,TCS,RELIANCE&from=2024-01-01&to=2024-12-31" | jq '.'
```

### Get Symbols
```bash
curl "http://localhost:8000/api/v1/symbols"
curl "http://localhost:8000/api/v1/symbols?sector=IT"
curl "http://localhost:8000/api/v1/symbols?is_active=true" | jq '.count'
```

### Generate Chart
```bash
# Daily chart with EMA
curl "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31&indicators=ema" > infy_daily.svg

# Weekly chart with MACD
curl "http://localhost:8000/api/v1/charts/weekly?symbol=INFY&from=2020-01-01&to=2024-12-31&indicators=ema,macd" > infy_weekly.svg

# Open in browser
open infy_daily.svg  # macOS
xdg-open infy_daily.svg  # Linux
```

### Get Indicators
```bash
# EMA only
curl "http://localhost:8000/api/v1/indicators?symbol=INFY&from=2024-01-01&indicators=ema" | jq '.'

# All indicators
curl "http://localhost:8000/api/v1/indicators?symbol=INFY&from=2024-01-01&indicators=ema,rsi,atr,macd" | jq '.data[0]'
```

---

## 📈 Performance Testing

```bash
# Single symbol query performance
time curl -s "http://localhost:8000/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31" > /dev/null
# Target: <100ms

# Bulk query performance
time curl -s "http://localhost:8000/api/v1/ohlcv/multi?symbols=INFY,TCS,RELIANCE,HDFCBANK,ICICIBANK&from=2024-01-01&to=2024-12-31" > /dev/null
# Target: <300ms

# Chart generation
time curl -s "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31" > /dev/null
# Target: <200ms

# Concurrent requests (10 simultaneous)
for i in {1..10}; do
  curl -s "http://localhost:8000/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31" &
done
wait
echo "✅ 10 concurrent requests completed"
```

---

## 🔍 Monitoring

### Resource Usage
```bash
# Memory
free -h
watch -n 1 free -h

# Disk
df -h /root
du -sh market_data_setup/

# CPU/Process
ps aux | grep -E "postgres|uvicorn"
top -b -n 1 | head -20
```

### API Load
```bash
# Check request rate
sudo journalctl -u market-data-api --since "5 min ago" | grep -c "GET\|POST"

# Top requested endpoints
sudo journalctl -u market-data-api | grep -oP 'GET \K[^ ]+' | sort | uniq -c | sort -rn | head -10
```

### Database Load
```bash
# Connected clients
psql -U market_data_user -d market_data -c "SELECT datname, usename, count(*) FROM pg_stat_activity GROUP BY datname, usename;"

# Long-running queries
psql -U market_data_user -d market_data -c "SELECT pid, now() - pg_stat_activity.query_start AS duration, query FROM pg_stat_activity WHERE (now() - pg_stat_activity.query_start) > interval '5 minutes';"

# Index usage
psql -U market_data_user -d market_data -c "SELECT schemaname, tablename, indexname, idx_scan FROM pg_stat_user_indexes WHERE idx_scan = 0 ORDER BY relname;"
```

---

## 🔄 Data Ingestion

```bash
# Start ingestion (background)
nohup python market_data_setup/scripts/ingest_ohlcv.py > ingest.log 2>&1 &

# Monitor progress
tail -f ingest.log

# Count records while ingesting
watch -n 10 "psql -U market_data_user -d market_data -c 'SELECT COUNT(*) as records, COUNT(DISTINCT symbol) as symbols FROM ohlcv_data;'"

# Kill ingestion (if needed)
pkill -f ingest_ohlcv.py

# Check ingestion status
ps aux | grep ingest_ohlcv.py | grep -v grep
```

---

## 📅 Daily Updates

```bash
# Run update manually
python market_data_setup/scripts/update_daily_ohlcv.py

# View update logs
tail -f /var/log/update_ohlcv.log

# Check cron job
crontab -l | grep update_daily

# Edit cron schedule
crontab -e
```

---

## 🐛 Troubleshooting

### API Down
```bash
# Check service
sudo systemctl status market-data-api

# Restart service
sudo systemctl restart market-data-api

# Check for port conflict
sudo lsof -i :8000

# Check logs
sudo journalctl -u market-data-api -n 100
```

### Database Issues
```bash
# Check PostgreSQL status
sudo systemctl status postgresql

# Restart PostgreSQL
sudo systemctl restart postgresql

# Test connection
psql -U market_data_user -d market_data -h localhost -c "SELECT 1;"

# Check database disk space
psql -U market_data_user -d market_data -c "SELECT pg_size_pretty(pg_database_size('market_data'));"
```

### High Memory Usage
```bash
# Check FastAPI processes
ps aux | grep uvicorn | grep -v grep

# Check PostgreSQL buffers
psql -U market_data_user -d market_data -c "SHOW shared_buffers;"

# Reduce workers (edit systemd service)
sudo nano /etc/systemd/system/market-data-api.service
# Change: --workers 2 to --workers 1
sudo systemctl daemon-reload
sudo systemctl restart market-data-api
```

### Slow Queries
```bash
# Enable slow query log
psql -U market_data_user -d market_data -c "ALTER SYSTEM SET log_min_duration_statement = 1000;"
sudo systemctl restart postgresql

# Check slow queries
sudo tail -f /var/log/postgresql/postgresql-*.log | grep "duration:"

# Explain slow query
psql -U market_data_user -d market_data -c "EXPLAIN ANALYZE SELECT * FROM ohlcv_data WHERE symbol = 'INFY' AND time BETWEEN '2024-01-01' AND '2024-12-31';"
```

---

## 🔐 Backups (Optional)

```bash
# Backup database
pg_dump -U market_data_user -d market_data > market_data_backup.sql

# Restore from backup
psql -U market_data_user -d market_data < market_data_backup.sql

# Backup .env (secure!)
cp .env .env.backup
chmod 600 .env.backup

# Create compressed backup
tar -czf market_data_backup_$(date +%Y%m%d).tar.gz \
  market_data_backup.sql \
  .env.backup
```

---

## 📝 Log Locations

| Log | Path | View With |
|-----|------|-----------|
| API | `journalctl -u market-data-api` | `journalctl -u market-data-api -f` |
| Ingestion | `ingest.log` | `tail -f ingest.log` |
| Daily Update | `/var/log/update_ohlcv.log` | `tail -f /var/log/update_ohlcv.log` |
| PostgreSQL | `/var/log/postgresql/` | `sudo tail -f /var/log/postgresql/*.log` |
| Nginx | `/var/log/nginx/access.log` | `tail -f /var/log/nginx/access.log` |

---

## ⚡ Quick Diagnostics

```bash
# Everything healthy?
echo "=== HEALTH CHECK ===" && \
curl -s http://localhost:8000/api/v1/health | jq '.status' && \
echo "=== DATABASE ===" && \
psql -U market_data_user -d market_data -c "SELECT COUNT(*) FROM ohlcv_data;" && \
echo "=== MEMORY ===" && \
free -h | grep Mem: && \
echo "=== DISK ===" && \
df -h /root | tail -1 && \
echo "=== API STATUS ===" && \
sudo systemctl is-active market-data-api && \
echo "✅ All systems operational"
```

---

## 🎯 Common Tasks

### Update single symbol manually
```bash
# Edit ingest script, set specific date range and symbol, run it
```

### Clear cache
```bash
sudo systemctl reload nginx
# Or for in-memory cache: restart API
sudo systemctl restart market-data-api
```

### View cache hit rate
```bash
sudo grep "Cache-Status" /var/log/nginx/access.log | grep -o 'X-Cache-Status: [A-Z]*' | sort | uniq -c
```

### Export data to CSV
```bash
psql -U market_data_user -d market_data -c "COPY (SELECT * FROM ohlcv_data WHERE symbol = 'INFY' ORDER BY time) TO STDOUT WITH CSV HEADER;" > infy_export.csv
```

### Import CSV into database
```bash
psql -U market_data_user -d market_data -c "COPY ohlcv_data(symbol, time, open, high, low, close, volume) FROM STDIN WITH CSV;" < data.csv
```

---

## 📞 Get Help

1. **Check logs first**: `sudo journalctl -u market-data-api -f`
2. **Review this guide**: Ctrl+F for your issue
3. **Check database**: `psql -U market_data_user -d market_data`
4. **Test API**: `curl http://localhost:8000/api/v1/health`

---

**Last Updated**: 2026-06-28  
**Status**: Production Ready ✅
