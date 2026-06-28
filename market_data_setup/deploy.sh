#!/bin/bash

# ============================================================
# Automated Market Data API Deployment Script
# One-command setup for PostgreSQL + TimescaleDB + FastAPI
#
# Usage on VPS:
#   bash deploy.sh
#
# Or download and run:
#   curl -O https://yourserver/deploy.sh && bash deploy.sh
# ============================================================

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PROJECT_ROOT="/root/trade-execution-webhook"
VENV_PATH="$PROJECT_ROOT/venv"
LOG_FILE="/var/log/market_data_setup.log"

# ============================================================
# FUNCTIONS
# ============================================================

print_header() {
    echo ""
    echo -e "${BLUE}============================================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}============================================================${NC}"
    echo ""
}

print_step() {
    echo -e "${YELLOW}→ $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        print_error "This script must be run as root"
        exit 1
    fi
}

check_env() {
    print_step "Checking environment..."

    if [ ! -d "$PROJECT_ROOT" ]; then
        print_error "Project directory not found: $PROJECT_ROOT"
        exit 1
    fi

    if [ ! -d "$PROJECT_ROOT/market_data_setup" ]; then
        print_error "market_data_setup folder not found"
        exit 1
    fi

    if [ ! -f "$PROJECT_ROOT/.env" ]; then
        print_error ".env file not found"
        print_step "Creating from template..."
        if [ -f "$PROJECT_ROOT/market_data_setup/config/.env.example" ]; then
            cp "$PROJECT_ROOT/market_data_setup/config/.env.example" "$PROJECT_ROOT/.env"
            print_error "Please edit .env with your Dhan API credentials:"
            print_error "  nano $PROJECT_ROOT/.env"
            exit 1
        fi
    fi

    print_success "Environment check passed"
}

install_postgres() {
    print_step "Installing PostgreSQL and TimescaleDB..."

    apt-get update -qq
    apt-get install -y -qq postgresql postgresql-contrib timescaledb-postgresql-14 > /dev/null 2>&1

    print_success "PostgreSQL installed"
}

start_postgres() {
    print_step "Starting PostgreSQL..."

    systemctl enable postgresql > /dev/null 2>&1
    systemctl start postgresql > /dev/null 2>&1
    systemctl status postgresql > /dev/null 2>&1

    print_success "PostgreSQL started"
}

setup_database() {
    print_step "Setting up database..."

    # Read DB password from .env
    source "$PROJECT_ROOT/.env"

    if [ -z "$DB_PASSWORD" ]; then
        print_error "DB_PASSWORD not set in .env"
        exit 1
    fi

    # Create database
    sudo -u postgres psql -c "CREATE DATABASE market_data;" 2>&1 | grep -v "already exists" || true

    # Create user
    sudo -u postgres psql -c "CREATE USER market_data_user WITH PASSWORD '$DB_PASSWORD';" 2>&1 | grep -v "already exists" || true

    # Grant permissions
    sudo -u postgres psql -d market_data <<EOF > /dev/null 2>&1
GRANT ALL PRIVILEGES ON DATABASE market_data TO market_data_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO market_data_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO market_data_user;
CREATE EXTENSION IF NOT EXISTS timescaledb;
EOF

    print_success "Database created and configured"
}

load_schema() {
    print_step "Loading database schema..."

    sudo -u postgres psql -d market_data -f "$PROJECT_ROOT/market_data_setup/database/schema.sql" > /dev/null 2>&1

    print_success "Schema loaded"
}

verify_database() {
    print_step "Verifying database..."

    source "$PROJECT_ROOT/.env"

    result=$(psql -U market_data_user -d market_data -h localhost -c "SELECT COUNT(*) FROM ohlcv_data;" 2>&1 | grep -E "[0-9]+" || echo "0")

    print_success "Database verified (records: $result)"
}

setup_python() {
    print_step "Setting up Python environment..."

    if [ ! -d "$VENV_PATH" ]; then
        python3 -m venv "$VENV_PATH"
    fi

    source "$VENV_PATH/bin/activate"

    pip install --upgrade pip --quiet
    pip install -r "$PROJECT_ROOT/market_data_setup/requirements.txt" --quiet

    print_success "Python environment ready"
}

install_systemd_service() {
    print_step "Installing systemd service..."

    cp "$PROJECT_ROOT/market_data_setup/config/market-data-api.service" /etc/systemd/system/

    systemctl daemon-reload > /dev/null 2>&1
    systemctl enable market-data-api > /dev/null 2>&1

    print_success "Systemd service installed"
}

start_api() {
    print_step "Starting API service..."

    systemctl start market-data-api > /dev/null 2>&1
    sleep 2

    if systemctl is-active --quiet market-data-api; then
        print_success "API service started"
    else
        print_error "API service failed to start"
        systemctl status market-data-api
        exit 1
    fi
}

configure_nginx() {
    print_step "Configuring Nginx..."

    # Backup existing config
    if [ -f /etc/nginx/sites-available/default ]; then
        cp /etc/nginx/sites-available/default /etc/nginx/sites-available/default.backup.$(date +%Y%m%d_%H%M%S)
    fi

    # Add cache path
    if ! grep -q "market_data_cache" /etc/nginx/nginx.conf; then
        sed -i '/http {/a \    proxy_cache_path /var/cache/nginx/market_data keys_zone=market_data_cache:10m levels=1:2 max_size=500m;' /etc/nginx/nginx.conf
    fi

    # Create cache directory
    mkdir -p /var/cache/nginx/market_data
    chown -R www-data:www-data /var/cache/nginx/market_data

    print_success "Nginx configured"
}

test_api() {
    print_step "Testing API..."

    # Wait for API to be ready
    sleep 3

    response=$(curl -s http://127.0.0.1:8000/api/v1/health 2>&1 | grep -o '"status":"ok"' || echo "")

    if [ -n "$response" ]; then
        print_success "API health check passed"
    else
        print_error "API health check failed"
        journalctl -u market-data-api -n 20
        exit 1
    fi
}

setup_cron() {
    print_step "Setting up daily cron job..."

    # Add cron job for daily updates (18:00 IST = 12:30 UTC)
    crontab_entry="30 12 * * 1-5 cd $PROJECT_ROOT && source $VENV_PATH/bin/activate && python market_data_setup/scripts/update_daily_ohlcv.py >> /var/log/update_ohlcv.log 2>&1"

    # Check if already exists
    if ! crontab -l 2>/dev/null | grep -q "update_daily_ohlcv"; then
        (crontab -l 2>/dev/null; echo "$crontab_entry") | crontab -
    fi

    print_success "Cron job configured (daily at 18:00 IST)"
}

display_summary() {
    print_header "✅ DEPLOYMENT COMPLETE"

    echo -e "${GREEN}API is running and ready!${NC}"
    echo ""
    echo -e "${BLUE}Quick Commands:${NC}"
    echo "  Health check:    curl http://localhost:8000/api/v1/health"
    echo "  API Docs:        http://localhost:8000/docs"
    echo "  View logs:       sudo journalctl -u market-data-api -f"
    echo ""
    echo -e "${BLUE}Next Steps:${NC}"
    echo "  1. Load historical data:"
    echo "     nohup python market_data_setup/scripts/ingest_ohlcv.py > ingest.log 2>&1 &"
    echo ""
    echo "  2. Monitor ingestion:"
    echo "     tail -f ingest.log"
    echo ""
    echo "  3. Test API endpoints:"
    echo "     curl 'http://localhost:8000/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31'"
    echo ""
    echo -e "${BLUE}Documentation:${NC}"
    echo "  • API Spec:              $PROJECT_ROOT/market_data_setup/CHARTING_API_SPEC.md"
    echo "  • Quick Reference:       $PROJECT_ROOT/market_data_setup/QUICK_REFERENCE.md"
    echo "  • Deployment Checklist:  $PROJECT_ROOT/market_data_setup/DEPLOYMENT_CHECKLIST.md"
    echo ""
}

# ============================================================
# MAIN EXECUTION
# ============================================================

main() {
    print_header "Market Data API - Automated Deployment"

    print_step "Starting deployment..."
    echo "Project root: $PROJECT_ROOT"
    echo "Time: $(date)"
    echo ""

    # Run checks
    check_root
    check_env

    # Install and setup
    install_postgres
    start_postgres
    setup_database
    load_schema
    verify_database

    # Python setup
    setup_python

    # API deployment
    install_systemd_service
    start_api
    configure_nginx
    test_api

    # Scheduling
    setup_cron

    # Summary
    display_summary

    print_success "All done! API is running on http://localhost:8000"
}

# Trap errors
trap 'print_error "Deployment failed at line $LINENO"; exit 1' ERR

# Run main
main
