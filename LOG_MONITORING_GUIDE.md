# Log Monitoring Guide - Market Data API

## 🚀 Quick Commands

### **Real-Time Monitoring (Live Logs)**
```bash
# Market Data API - Watch live
ssh root@165.232.187.97 'journalctl -u market-data-api -f'

# MCP Server - Watch live
ssh root@165.232.187.97 'journalctl -u market-data-mcp -f'

# Daily Update Script - Watch live
ssh root@165.232.187.97 'tail -f ~/trade-execution-webhook/market_data_setup/scripts/update.log'

# Nginx Access Log - Watch live
ssh root@165.232.187.97 'tail -f /var/log/nginx/access.log'
```

### **View Recent Logs (Non-Live)**
```bash
# API - Last 50 lines
ssh root@165.232.187.97 'journalctl -u market-data-api -n 50 --no-pager'

# MCP - Last 50 lines
ssh root@165.232.187.97 'journalctl -u market-data-mcp -n 50 --no-pager'

# MCP - Last 20 lines
ssh root@165.232.187.97 'journalctl -u market-data-mcp -n 20 --no-pager'

# Update Log - Last 20 lines
ssh root@165.232.187.97 'tail -20 ~/trade-execution-webhook/market_data_setup/scripts/update.log'
```

### **Search for Errors**
```bash
# API errors in last hour
ssh root@165.232.187.97 "journalctl -u market-data-api --since '1 hour ago' | grep -i error"

# MCP errors in last hour
ssh root@165.232.187.97 "journalctl -u market-data-mcp --since '1 hour ago' | grep -i error"

# Update script errors
ssh root@165.232.187.97 "grep -i error ~/trade-execution-webhook/market_data_setup/scripts/update.log"

# API errors today
ssh root@165.232.187.97 "journalctl -u market-data-api --since today | grep -i error"
```

### **Time-Based Filtering**
```bash
# Logs from last 30 minutes
ssh root@165.232.187.97 "journalctl -u market-data-api --since '30 minutes ago' --no-pager"

# Logs from last 2 hours
ssh root@165.232.187.97 "journalctl -u market-data-api --since '2 hours ago' --no-pager"

# Logs from last 24 hours
ssh root@165.232.187.97 "journalctl -u market-data-api --since '24 hours ago' --no-pager"

# Logs from specific date
ssh root@165.232.187.97 "journalctl -u market-data-api --since '2026-06-28' --no-pager"
```

### **Service Status**
```bash
# Check all services
ssh root@165.232.187.97 'systemctl status market-data-api market-data-mcp postgresql nginx'

# Check specific service
ssh root@165.232.187.97 'systemctl status market-data-api'

# See running services
ssh root@165.232.187.97 'systemctl list-units --type=service --state=running | grep market'

# Count total active services
ssh root@165.232.187.97 'systemctl list-units --type=service --state=running | wc -l'
```

---

## 📊 Log Files & Locations

| Service | Log Command | Location |
|---------|-------------|----------|
| **API** | `journalctl -u market-data-api` | Systemd journal |
| **MCP** | `journalctl -u market-data-mcp` | Systemd journal |
| **Update** | `tail -f ~/trade-execution-webhook/market_data_setup/scripts/update.log` | File-based log |
| **PostgreSQL** | `journalctl -u postgresql` | Systemd journal |
| **Nginx Access** | `/var/log/nginx/access.log` | File-based log |
| **Nginx Error** | `/var/log/nginx/error.log` | File-based log |

---

## 🔍 Advanced Log Commands

### **Get logs in JSON format**
```bash
ssh root@165.232.187.97 'journalctl -u market-data-api --output json | tail -5'
```

### **Count log entries**
```bash
ssh root@165.232.187.97 'journalctl -u market-data-api --since today | wc -l'
```

### **Get only ERROR level logs**
```bash
ssh root@165.232.187.97 'journalctl -u market-data-api --priority err --no-pager'
```

### **Get logs between two times**
```bash
ssh root@165.232.187.97 'journalctl -u market-data-api --since "2026-06-29 00:00:00" --until "2026-06-29 01:00:00" --no-pager'
```

### **Follow logs with timestamps**
```bash
ssh root@165.232.187.97 'journalctl -u market-data-api -f --output short-monotonic'
```

### **Get last update time**
```bash
ssh root@165.232.187.97 'stat ~/trade-execution-webhook/market_data_setup/scripts/update.log | grep Modify'
```

---

## 📈 Common Log Patterns

### **API Starting Up**
```
INFO:     Started server process [PID]
INFO:     Waiting for application startup.
✅ Database pool initialized (localhost:5432)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8001
```

### **MCP Server Running**
```
INFO:     Started server process [PID]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8002 (Press CTRL+C to quit)
INFO:     127.0.0.1:XXXXX - "GET /tools HTTP/1.1" 200 OK
```

### **Successful API Request**
```
INFO:     127.0.0.1:XXXXX - "GET /api/v1/ohlcv?symbol=TCS&from_date=2024-01-01&to_date=2024-12-31 HTTP/1.1" 200 OK
```

### **Database Connection Error**
```
ERROR: Failed to initialize database pool: ...
```

---

## 🎯 Monitoring Strategies

### **Strategy 1: Real-Time Monitoring**
Best for: Troubleshooting, watching requests come in
```bash
ssh root@165.232.187.97 'journalctl -u market-data-api -f'
```
Stop with: `Ctrl+C`

### **Strategy 2: Check After Problem**
Best for: Finding what went wrong
```bash
ssh root@165.232.187.97 "journalctl -u market-data-api --since '1 hour ago' | grep -i error"
```

### **Strategy 3: Daily Summary**
Best for: Morning health check
```bash
ssh root@165.232.187.97 "journalctl -u market-data-api --since today --no-pager | tail -20"
```

### **Strategy 4: Error Tracking**
Best for: Catching all errors
```bash
ssh root@165.232.187.97 "journalctl -u market-data-api --priority err --no-pager"
```

---

## ⚠️ Troubleshooting

### **If update log is empty**
```bash
# Check if cron job is configured
ssh root@165.232.187.97 'crontab -l | grep update_ohlcv'

# Check if script is executable
ssh root@165.232.187.97 'ls -la ~/trade-execution-webhook/market_data_setup/scripts/update_ohlcv.py'
```

### **If service won't start**
```bash
# Check error reason
ssh root@165.232.187.97 'systemctl status market-data-api'

# View detailed error
ssh root@165.232.187.97 'journalctl -u market-data-api -n 50 --no-pager'
```

### **If logs are too verbose**
```bash
# Get only today's logs
ssh root@165.232.187.97 "journalctl -u market-data-api --since today | tail -50"

# Get last restart logs
ssh root@165.232.187.97 'journalctl -u market-data-api -n 100 --no-pager'
```

---

## 📍 Log Locations on VPS

SSH into VPS and use these paths directly:

```bash
ssh root@165.232.187.97
cd /root/trade-execution-webhook/market_data_setup/scripts/
tail -f update.log

# Or for journal logs
journalctl -u market-data-api -f
```

---

## 🚨 Critical Log Messages to Watch For

| Message | Meaning | Action |
|---------|---------|--------|
| `Database pool initialized` | DB connected | ✅ Normal |
| `Application startup complete` | Service ready | ✅ Normal |
| `200 OK` | Request successful | ✅ Normal |
| `Failed to initialize database pool` | DB connection failed | ❌ Check DB |
| `Connection refused` | Service not running | ❌ Restart service |
| `Permission denied` | File access issue | ❌ Check permissions |
| `timeout` | Slow response/hang | ⚠️ Investigate |

---

**Last Updated**: June 29, 2026
