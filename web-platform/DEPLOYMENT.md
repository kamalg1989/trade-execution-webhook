# Web Platform Deployment Guide

Complete deployment instructions for the web trading platform.

---

## Prerequisites

### Local Machine
- Node.js 18+ with npm
- Python 3.8+ with venv
- SSH access to VPS configured
- Git (optional)

### VPS Requirements
- Ubuntu 22.04+
- PostgreSQL 12+ running
- Python 3.8+ installed
- Nginx installed and running
- Let's Encrypt certificate installed (for HTTPS)

---

## Deployment Steps

### Step 1: Prepare Deployment Script

```bash
cd /Users/kamal/IdeaProjects/trade-execution-webhook/web-platform
chmod +x deploy.sh
```

### Step 2: Verify VPS Prerequisites

SSH into VPS and check:

```bash
ssh root@165.232.187.97

# Check PostgreSQL
psql --version
systemctl status postgresql

# Check Python
python3 --version

# Check Nginx
nginx -v
systemctl status nginx

# Check directory structure
ls -la /root/trade-execution-webhook/
```

### Step 3: Create Database

On VPS:

```bash
# Connect to PostgreSQL
sudo -u postgres psql

# Create database
CREATE DATABASE trading_platform;
CREATE USER trading WITH PASSWORD 'secure_password';
ALTER ROLE trading SET client_encoding TO 'utf8';
ALTER ROLE trading SET default_transaction_isolation TO 'read committed';
ALTER ROLE trading SET default_transaction_deferrable TO on;
ALTER ROLE trading SET default_transaction_read_only TO off;
GRANT ALL PRIVILEGES ON DATABASE trading_platform TO trading;

# Exit psql
\q
```

Or use root user (as currently configured):

```bash
psql -U root -d postgres -c "CREATE DATABASE trading_platform;"
```

### Step 4: Run Deployment

From local machine:

```bash
cd /Users/kamal/IdeaProjects/trade-execution-webhook/web-platform

# Make deploy script executable
chmod +x deploy.sh

# Run deployment
./deploy.sh
```

The script will:
1. ✅ Build React frontend (`npm run build`)
2. ✅ Copy backend files to VPS
3. ✅ Copy frontend dist to VPS
4. ✅ Initialize database tables
5. ✅ Install Python dependencies
6. ✅ Create systemd service
7. ✅ Configure Nginx
8. ✅ Test deployment

### Step 5: Manual Database Schema Setup (if needed)

If automatic schema creation fails:

```bash
ssh root@165.232.187.97
cd /root/trade-execution-webhook
psql -U root -d trading_platform -f web_api/database/schema.sql
```

### Step 6: Verify Services

Check all services are running:

```bash
# Check API service
ssh root@165.232.187.97 "systemctl status trade-web-api"

# Check Nginx
ssh root@165.232.187.97 "systemctl status nginx"

# Check PostgreSQL
ssh root@165.232.187.97 "systemctl status postgresql"
```

### Step 7: Test Endpoints

```bash
# Health check
curl https://ohmstockvault.duckdns.org/health

# API root
curl https://ohmstockvault.duckdns.org/api/

# Frontend
curl https://ohmstockvault.duckdns.org/ | head -20
```

---

## Configuration

### Environment Variables

Create `.env` file in `/root/trade-execution-webhook/web_api/`:

```env
# Database
DATABASE_URL=postgresql://root:postgres@localhost:5432/trading_platform

# Environment
ENV=production
HOST=0.0.0.0
PORT=8004

# Dhan API (if needed)
DHAN_API_URL=https://api.dhan.co
DHAN_API_VERSION=v2

# Logging
LOG_LEVEL=INFO
```

### Nginx Configuration

Nginx reverse proxy (auto-configured by deploy script):

```nginx
server {
    listen 80;
    server_name ohmstockvault.duckdns.org;

    # Redirect to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name ohmstockvault.duckdns.org;

    # SSL certificates (Let's Encrypt)
    ssl_certificate /etc/letsencrypt/live/ohmstockvault.duckdns.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ohmstockvault.duckdns.org/privkey.pem;

    # Frontend
    location / {
        root /root/web-app/dist;
        try_files $uri /index.html;
    }

    # API
    location /api/ {
        proxy_pass http://127.0.0.1:8004;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Host $host;
    }
}
```

### Systemd Service

Service file: `/etc/systemd/system/trade-web-api.service`

```ini
[Unit]
Description=Trade Web API
After=network.target postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/trade-execution-webhook
Environment="PYTHONUNBUFFERED=1"
Environment="DATABASE_URL=postgresql://root:postgres@localhost:5432/trading_platform"
ExecStart=/root/trade-execution-webhook/venv/bin/python -m uvicorn web_api.main:app --host 0.0.0.0 --port 8004
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## Monitoring & Logs

### View Logs

```bash
# API logs
ssh root@165.232.187.97 "journalctl -u trade-web-api -f"

# Last 50 lines
ssh root@165.232.187.97 "journalctl -u trade-web-api -n 50"

# Nginx logs
ssh root@165.232.187.97 "tail -f /var/log/nginx/error.log"
ssh root@165.232.187.97 "tail -f /var/log/nginx/access.log"

# PostgreSQL logs
ssh root@165.232.187.97 "sudo tail -f /var/log/postgresql/postgresql-*.log"
```

### Service Management

```bash
# Start/Stop/Restart API
ssh root@165.232.187.97 "systemctl start trade-web-api"
ssh root@165.232.187.97 "systemctl stop trade-web-api"
ssh root@165.232.187.97 "systemctl restart trade-web-api"

# Check service status
ssh root@165.232.187.97 "systemctl status trade-web-api"

# Enable service to start on boot
ssh root@165.232.187.97 "systemctl enable trade-web-api"
```

---

## Troubleshooting

### 1. Database Connection Failed

**Error:** `psycopg2.OperationalError: could not connect to server`

**Solution:**
```bash
# Check PostgreSQL is running
systemctl status postgresql

# Check database exists
psql -U root -l | grep trading_platform

# Check schema is created
psql -U root -d trading_platform -c "\dt"
```

### 2. API Port Already in Use

**Error:** `Address already in use`

**Solution:**
```bash
# Find process using port 8004
lsof -i :8004

# Kill process
kill -9 <PID>

# Or use different port in .env
PORT=8005
```

### 3. Frontend Not Loading

**Error:** 404 on `/`

**Solution:**
```bash
# Check frontend files exist
ls -la /root/web-app/dist/

# Check Nginx config
nginx -t

# Check Nginx can read files
chmod -R 755 /root/web-app/dist
```

### 4. API Responds but Returns Errors

**Error:** 500 Internal Server Error

**Solution:**
```bash
# Check logs
journalctl -u trade-web-api -n 100

# Check Python dependencies
source /root/trade-execution-webhook/venv/bin/activate
pip list | grep -E "fastapi|sqlalchemy"

# Test database
python3 -c "from web_api.database.db import init_db; init_db()"
```

### 5. SSL Certificate Issues

**Error:** `ERR_SSL_PROTOCOL_ERROR`

**Solution:**
```bash
# Check certificate exists
ls -la /etc/letsencrypt/live/ohmstockvault.duckdns.org/

# Renew certificate
certbot renew

# Check certificate expiry
openssl x509 -in /etc/letsencrypt/live/ohmstockvault.duckdns.org/fullchain.pem -noout -dates
```

---

## Updating Deployment

### Update Frontend Only

```bash
cd /Users/kamal/IdeaProjects/trade-execution-webhook/web-platform
npm run build
scp -r dist root@165.232.187.97:/root/web-app/
ssh root@165.232.187.97 "systemctl reload nginx"
```

### Update Backend Only

```bash
cd /Users/kamal/IdeaProjects/trade-execution-webhook/web-platform
scp -r backend/* root@165.232.187.97:/root/trade-execution-webhook/web_api/
ssh root@165.232.187.97 "systemctl restart trade-web-api"
```

### Update Database Schema

```bash
ssh root@165.232.187.97 << 'EOF'
cd /root/trade-execution-webhook
psql -U root -d trading_platform << 'SQL'
-- Add new tables or columns here
SQL
EOF
```

---

## Performance Optimization

### 1. Enable Gzip Compression

In Nginx config:

```nginx
gzip on;
gzip_types text/plain text/css application/json application/javascript;
gzip_min_length 1000;
```

### 2. Add Caching Headers

In Nginx config:

```nginx
location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
    expires 1y;
    add_header Cache-Control "public, immutable";
}
```

### 3. Database Connection Pooling

In `web_api/database/db.py`:

```python
engine = create_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True
)
```

### 4. API Rate Limiting

Use Nginx rate limiting:

```nginx
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;

location /api/ {
    limit_req zone=api burst=20 nodelay;
    proxy_pass http://127.0.0.1:8004;
}
```

---

## Backup & Recovery

### Backup Database

```bash
ssh root@165.232.187.97 "pg_dump -U root trading_platform > /tmp/trading_platform_$(date +%Y%m%d).sql"
scp root@165.232.187.97:/tmp/trading_platform_*.sql ./backups/
```

### Restore Database

```bash
scp ./backups/trading_platform_*.sql root@165.232.187.97:/tmp/
ssh root@165.232.187.97 "psql -U root trading_platform < /tmp/trading_platform_*.sql"
```

### Backup Frontend

```bash
scp -r root@165.232.187.97:/root/web-app/dist ./backups/web-app-dist-$(date +%Y%m%d)/
```

---

## Post-Deployment Checklist

- [ ] Frontend loads at `https://ohmstockvault.duckdns.org`
- [ ] API responds at `https://ohmstockvault.duckdns.org/api/`
- [ ] Health check: `https://ohmstockvault.duckdns.org/health`
- [ ] Database tables created: `psql -c "\dt"`
- [ ] Services running: `systemctl status trade-web-api nginx postgresql`
- [ ] SSL certificate valid: `certbot status`
- [ ] Logs clean: `journalctl -u trade-web-api -n 20`
- [ ] Dhan API connection working
- [ ] Screen_gpt recommendations loading
- [ ] Buy orders placing successfully
- [ ] Stop loss orders creating
- [ ] Portfolio P&L calculating correctly

---

## Support

For issues:
1. Check logs: `journalctl -u trade-web-api -f`
2. Test API: `curl https://ohmstockvault.duckdns.org/api/`
3. Verify database: `psql -U root -d trading_platform -c "SELECT COUNT(*) FROM sl_positions"`
4. Check disk space: `df -h`
5. Check memory: `free -h`
