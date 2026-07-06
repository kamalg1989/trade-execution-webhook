#!/bin/bash

# Web Platform Deployment Script
# Deploys frontend and backend to VPS

set -e

echo "🚀 Starting Web Platform Deployment..."

# Configuration
VPS_HOST="165.232.187.97"
VPS_USER="root"
VPS_PATH="/root/trade-execution-webhook"
WEB_APP_PATH="/root/web-app"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Function to print status
print_status() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️ $1${NC}"
}

# Step 1: Build Frontend
print_status "Step 1: Building React frontend..."
cd "$(dirname "$0")"
npm install
npm run build

if [ ! -d "dist" ]; then
    print_error "Frontend build failed - dist directory not found"
    exit 1
fi

print_status "Frontend built successfully"

# Step 2: Deploy Backend
print_status "Step 2: Deploying backend files..."

# Copy backend files
scp -r backend/* $VPS_USER@$VPS_HOST:$VPS_PATH/web_api/

# Copy database schema
scp backend/database/schema.sql $VPS_USER@$VPS_HOST:$VPS_PATH/

print_status "Backend files deployed"

# Step 3: Deploy Frontend
print_status "Step 3: Deploying frontend..."
ssh $VPS_USER@$VPS_HOST "rm -rf $WEB_APP_PATH/dist"
scp -r dist $VPS_USER@$VPS_HOST:$WEB_APP_PATH/

print_status "Frontend deployed"

# Step 4: Initialize Database
print_status "Step 4: Initializing database..."
ssh $VPS_USER@$VPS_HOST << 'DBCMD'
cd /root/trade-execution-webhook
psql -U root -d trading_platform -f web_api/database/schema.sql
DBCMD

print_status "Database initialized"

# Step 5: Install Backend Dependencies
print_status "Step 5: Installing backend dependencies..."
ssh $VPS_USER@$VPS_HOST << 'PIPCMD'
cd /root/trade-execution-webhook
source venv/bin/activate
pip install fastapi uvicorn sqlalchemy psycopg2-binary httpx psutil -q
PIPCMD

print_status "Dependencies installed"

# Step 6: Create Systemd Service
print_status "Step 6: Setting up systemd service..."
ssh $VPS_USER@$VPS_HOST << 'SERVICECMD'
cat > /etc/systemd/system/trade-web-api.service << 'EOF'
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
EOF

systemctl daemon-reload
systemctl enable trade-web-api
systemctl restart trade-web-api
SERVICECMD

print_status "Systemd service created and started"

# Step 7: Update Nginx
print_status "Step 7: Configuring Nginx..."
ssh $VPS_USER@$VPS_HOST << 'NGINXCMD'
cat >> /etc/nginx/nginx.conf << 'EOF'

server {
    listen 80;
    server_name ohmstockvault.duckdns.org;

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
EOF

nginx -t && systemctl restart nginx
NGINXCMD

print_status "Nginx configured"

# Step 8: Verify Deployment
print_status "Step 8: Verifying deployment..."

echo "Testing API..."
sleep 2
API_RESPONSE=$(curl -s http://165.232.187.97/api/ | grep -q "Trade Web API" && echo "OK" || echo "FAIL")

if [ "$API_RESPONSE" = "OK" ]; then
    print_status "API is responding correctly"
else
    print_warning "API health check returned unexpected result"
fi

# Summary
echo ""
echo "==================================="
echo "✅ DEPLOYMENT COMPLETE!"
echo "==================================="
echo ""
echo "Frontend: https://ohmstockvault.duckdns.org"
echo "API: https://ohmstockvault.duckdns.org/api/"
echo "Health Check: https://ohmstockvault.duckdns.org/health"
echo ""
echo "Services:"
echo "  - trade-web-api: Running on port 8004"
echo "  - nginx: Reverse proxy on port 80/443"
echo ""
echo "Logs:"
echo "  - API: journalctl -u trade-web-api -f"
echo "  - Nginx: tail -f /var/log/nginx/error.log"
echo ""
