#!/usr/bin/env bash
# ============================================================
# One-shot VPS deploy for the standalone Custom Screener.
# Run ON THE VPS (root) after `git pull origin main`:
#     bash /root/trade-execution-webhook/custom-screener/deploy/deploy_vps.sh
#
# Idempotent & safe: backs up nginx, validates before reload, reverts on error.
# Does NOT touch the existing app/services beyond adding two nginx locations.
# ============================================================
set -uo pipefail

REPO=/root/trade-execution-webhook
CS="$REPO/custom-screener"
ok(){ echo -e "  \033[32m✔\033[0m $*"; }
warn(){ echo -e "  \033[33m!\033[0m $*"; }
die(){ echo -e "  \033[31mX\033[0m $*"; exit 1; }

# load DB creds from the existing platform .env
[ -f "$REPO/.env" ] && set -a && . "$REPO/.env" && set +a
DB_HOST="${DB_HOST:-localhost}"; DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-market_data_user}"; DB_NAME="${DB_NAME:-market_data}"

# choose python: prefer repo venv, else system
PY="$REPO/venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3)"
echo "Using python: $PY"

echo "==> [1/6] Backend deps"
"$PY" -m pip install -q -r "$CS/backend/requirements.txt" && ok "deps installed" \
  || die "pip install failed"

echo "==> [2/6] Database schema (idempotent)"
export PGPASSWORD="${DB_PASSWORD:-}"
for f in 001_stock_indicators.sql 002_market_snapshot.sql; do
  psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -q -f "$CS/backend/sql/$f" \
    && ok "$f applied" || warn "$f had warnings (ok if already created)"
done

echo "==> [3/6] systemd service on :8005"
cat >/etc/systemd/system/custom-screener-api.service <<EOF
[Unit]
Description=Custom Screener API (:8005)
After=network.target postgresql.service
[Service]
Type=simple
User=root
WorkingDirectory=$CS/backend
EnvironmentFile=$REPO/.env
ExecStart=$PY -m uvicorn app.main:app --host 0.0.0.0 --port 8005
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now custom-screener-api >/dev/null 2>&1
sleep 2
H=$(curl -s localhost:8005/api/health || true)
echo "     health: $H"
echo "$H" | grep -q '"status":"ok"' && ok "backend up" || warn "backend not healthy yet — check: journalctl -u custom-screener-api -n 40"

echo "==> [4/6] nginx routing"
CONF=$(grep -rl "server_name ohmstockvault" /etc/nginx/sites-enabled /etc/nginx/conf.d 2>/dev/null | head -1)
[ -n "$CONF" ] || die "could not find nginx server block for ohmstockvault"
echo "     config: $CONF"
# Backups must live OUTSIDE sites-enabled — nginx includes every file there,
# so a .bak sitting alongside would load as a duplicate server block.
BAKDIR=/root/nginx-backups; mkdir -p "$BAKDIR"
CONFDIR=$(dirname "$CONF")
mv "$CONFDIR"/*.bak.* "$BAKDIR"/ 2>/dev/null || true   # sweep any strays from earlier runs
BAK="$BAKDIR/$(basename "$CONF").bak.$(date +%s)"
cp "$CONF" "$BAK"
if grep -q "custom-screener" "$CONF"; then
  ok "location blocks already present"
else
  awk '!ins && /^[[:space:]]*location \/[[:space:]]*\{/ {
    print "    location ^~ /custom-screener/api/ {";
    print "        proxy_pass http://127.0.0.1:8005/api/;";
    print "        proxy_set_header Host $host;";
    print "        proxy_set_header X-Real-IP $remote_addr;";
    print "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;";
    print "        proxy_set_header X-Forwarded-Proto $scheme;";
    print "    }";
    print "    location ^~ /custom-screener/ {";
    print "        try_files $uri $uri/ /custom-screener/index.html;";
    print "    }";
    ins=1
  } { print }' "$CONF" > /tmp/cs_conf && mv /tmp/cs_conf "$CONF"
  if nginx -t 2>/dev/null; then ok "blocks inserted"; else
    warn "nginx -t failed — reverting"; cp "$BAK" "$CONF"; nginx -t; die "reverted nginx change"
  fi
fi

echo "==> [5/6] Frontend build -> web root/custom-screener"
# detect the docroot that holds the live index.html
WEBROOT=""
for c in $(nginx -T 2>/dev/null | grep -oP '(?<=root )\S+' | tr -d ';' | sort -u); do
  [ -f "$c/index.html" ] && WEBROOT="$c" && break
done
[ -n "$WEBROOT" ] || WEBROOT=/root/web-app/dist
echo "     web root: $WEBROOT"
( cd "$CS/frontend" && npm ci --silent && npm run build ) && ok "frontend built" || die "frontend build failed"
mkdir -p "$WEBROOT/custom-screener"
cp -r "$CS/frontend/dist/"* "$WEBROOT/custom-screener/"
ok "static deployed to $WEBROOT/custom-screener/"

nginx -t && systemctl reload nginx && ok "nginx reloaded" || warn "nginx reload issue"

echo "==> [6/6] Done. Verify:"
echo "     curl -s https://ohmstockvault.duckdns.org/custom-screener/api/health"
echo "     open  https://ohmstockvault.duckdns.org/custom-screener/"
echo
echo "One-time data backfill (long-running; run in a screen/tmux when ready):"
echo "     cd $CS/backend && $PY -m compute.compute_stock_indicators --backfill-years 15"
echo "Then enable the nightly compute timer:"
echo "     cp $CS/deploy/custom-screener-compute.{service,timer} /etc/systemd/system/"
echo "     systemctl daemon-reload && systemctl enable --now custom-screener-compute.timer"
