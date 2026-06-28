# 🚀 Deploy to VPS in 5 Minutes

**Automated deployment script that sets up everything with one command.**

---

## ⚡ One-Command Deployment

### Step 1: SSH to VPS
```bash
ssh root@165.232.187.97
```

### Step 2: Navigate to Project
```bash
cd /root/trade-execution-webhook
```

### Step 3: Update .env with Dhan Credentials
```bash
nano .env
```

**Fill in these values**:
```
DHAN_CLIENT_ID=your_dhan_client_id
DHAN_PIN=your_dhan_pin
DHAN_TOTP_SECRET=your_dhan_totp_secret
DB_PASSWORD=choose_secure_password
```

Save and exit (Ctrl+X, Y, Enter)

### Step 4: Run Deployment Script
```bash
chmod +x market_data_setup/deploy.sh
sudo bash market_data_setup/deploy.sh
```

**That's it!** ✅

---

## 📊 What Gets Installed

The script automatically:

✅ **PostgreSQL + TimescaleDB** - Database setup  
✅ **Database Schema** - Tables and indexes  
✅ **Python Environment** - Dependencies installed  
✅ **FastAPI Server** - Started and running  
✅ **Systemd Service** - Auto-restart on reboot  
✅ **Nginx Integration** - Reverse proxy configured  
✅ **Cron Job** - Daily updates at 18:00 IST  
✅ **Health Check** - API tested and verified  

---

## ⏱️ Deployment Timeline

```
Total time: ~5 minutes
├─ PostgreSQL install:    1 min
├─ Database setup:        1 min
├─ Python deps:           1 min
├─ API start:             30 sec
├─ Nginx config:          30 sec
└─ Testing:               1 min
```

---

## 🎯 After Deployment

### Check API is Running
```bash
curl http://localhost:8000/api/v1/health

# Should return:
# {"status":"ok","timestamp":"...","database":"connected"}
```

### Load Historical Data (Background)
```bash
# Start in background (20-30 hours)
nohup python market_data_setup/scripts/ingest_ohlcv.py > ingest.log 2>&1 &

# Monitor progress
tail -f ingest.log
```

### Test API Endpoints
```bash
# Query OHLCV data
curl "http://localhost:8000/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31"

# Generate chart
curl "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31" > chart.svg

# Get symbol list
curl "http://localhost:8000/api/v1/symbols"
```

### Access API Documentation
```
http://your-server-ip/api/v1/docs
```

---

## 🔧 Common Commands After Deployment

### Check API Status
```bash
sudo systemctl status market-data-api
```

### View API Logs
```bash
sudo journalctl -u market-data-api -f
```

### Restart API
```bash
sudo systemctl restart market-data-api
```

### Check Database
```bash
psql -U market_data_user -d market_data -h localhost
SELECT COUNT(*) FROM ohlcv_data;
```

### Monitor Resource Usage
```bash
free -h          # Memory
df -h /root      # Disk
ps aux | grep market
```

### Stop/Start Services
```bash
sudo systemctl stop market-data-api
sudo systemctl start market-data-api
```

---

## 📋 Script Details

**Location**: `market_data_setup/deploy.sh`

**What it does**:
1. Checks root access
2. Verifies .env file exists
3. Installs PostgreSQL + TimescaleDB
4. Creates database and user
5. Loads schema
6. Verifies database connection
7. Installs Python dependencies
8. Configures systemd service
9. Starts API service
10. Configures Nginx
11. Tests API health
12. Sets up daily cron job
13. Displays summary

**Error handling**: Exits on any error with helpful message

---

## ✅ Verification Checklist

After running the script:

- [ ] Script completes without errors
- [ ] PostgreSQL service running: `sudo systemctl status postgresql`
- [ ] API service running: `sudo systemctl status market-data-api`
- [ ] Health check passes: `curl http://localhost:8000/api/v1/health`
- [ ] Database connected: `psql -U market_data_user -d market_data -c "SELECT 1;"`
- [ ] Swagger UI accessible: `http://localhost:8000/docs`

---

## 🆘 Troubleshooting

### Script Fails with "root required"
```bash
# Must use sudo
sudo bash market_data_setup/deploy.sh
```

### PostgreSQL port already in use
```bash
# Check what's using port 5432
sudo lsof -i :5432

# Kill the process
sudo kill -9 <PID>

# Rerun script
sudo bash market_data_setup/deploy.sh
```

### API won't start
```bash
# Check service status
sudo systemctl status market-data-api

# View logs
sudo journalctl -u market-data-api -n 50

# Check if port 8000 is available
sudo lsof -i :8000
```

### Database connection fails
```bash
# Verify PostgreSQL is running
sudo systemctl status postgresql

# Test connection manually
psql -U market_data_user -d market_data -h localhost -c "SELECT 1;"
```

---

## 📊 What Gets Running

After successful deployment:

```
Service Status:
✅ PostgreSQL (port 5432)
✅ FastAPI (port 8000, internal)
✅ Nginx (port 80/443)
✅ Systemd (auto-restart enabled)

Cron Jobs:
✅ Daily update at 18:00 IST (12:30 UTC)
   └─ Syncs last 5 days from Dhan API

API Endpoints:
✅ /api/v1/health
✅ /api/v1/ohlcv
✅ /api/v1/ohlcv/multi
✅ /api/v1/symbols
✅ /api/v1/charts/daily
✅ /api/v1/charts/weekly
✅ /api/v1/indicators
```

---

## 🎯 Next: Load Historical Data

Once deployment is complete, load 15 years of OHLCV:

```bash
# SSH to VPS
ssh root@165.232.187.97

# Activate environment
cd /root/trade-execution-webhook
source venv/bin/activate

# Start ingestion (runs in background)
nohup python market_data_setup/scripts/ingest_ohlcv.py > ingest.log 2>&1 &

# Monitor progress
tail -f ingest.log

# Check record count
watch -n 10 "psql -U market_data_user -d market_data -c 'SELECT COUNT(*) FROM ohlcv_data;'"
```

**Timeline**: 20-30 hours for 2000 symbols × 15 years

---

## 📚 Documentation

After deployment, see:
- `QUICK_REFERENCE.md` - Common commands
- `CHARTING_API_SPEC.md` - API details
- `README.md` - Overview
- `/docs` - Swagger UI with interactive testing

---

**Status**: Ready to Deploy ✅  
**Complexity**: Fully Automated ✅  
**Time Required**: ~5 minutes ✅
