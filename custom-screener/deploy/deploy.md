# Deploy — Custom Screener (standalone, dedicated path)

Public URL: `https://ohmstockvault.duckdns.org/custom-screener/`
Nothing here touches the existing web app. Shares only the DB (read-only) and the
`/api/v1/` charts API (HTTP).

## Quick path (recommended) — one script

On the VPS, after `git pull origin main`:
```bash
bash /root/trade-execution-webhook/custom-screener/deploy/deploy_vps.sh
```
It installs deps, applies the DB schema, creates+starts the `:8005` systemd
service, inserts the two nginx location blocks (with backup + validate + auto-revert),
builds the frontend into the live web root under `/custom-screener/`, and reloads
nginx. Then run the one-time 15-year backfill it prints at the end.

The manual steps below are the same thing broken out, for reference / debugging.

---

## 1. Database (one-time)
Run the DDL against the existing `market_data` database:
```bash
psql -h localhost -U market_data_user -d market_data \
  -f custom-screener/backend/sql/001_stock_indicators.sql
psql -h localhost -U market_data_user -d market_data \
  -f custom-screener/backend/sql/002_market_snapshot.sql
```

## 2. Backend
```bash
cd custom-screener/backend
python3 -m pip install -r requirements.txt
cp .env.example .env        # fill DB_PASSWORD (reuse market_data creds)
# smoke test
python3 -m uvicorn app.main:app --port 8005 &
curl localhost:8005/api/health
```
Install the service:
```bash
cp deploy/custom-screener-api.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now custom-screener-api
```

## 3. Backfill + nightly compute
```bash
cd custom-screener/backend
# full 15-year backfill (minutes-to-hours; safe to re-run, upserts)
python3 -m compute.compute_stock_indicators --backfill-years 15
# (is_new_52w_high/low are populated by this run. If the table was populated by an
#  older build without those columns, just re-run the backfill to fill them in.)
# schedule the nightly incremental at 18:30 IST (13:00 UTC)
cp deploy/custom-screener-compute.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now custom-screener-compute.timer
```

## 4. Frontend
```bash
cd custom-screener/frontend
npm ci && npm run build
mkdir -p /root/web-app-custom-screener
cp -r dist/* /root/web-app-custom-screener/
```

## 5. Nginx
Paste the two `location` blocks from `deploy/nginx-custom-screener.conf` into the
existing HTTPS `server { }` for `ohmstockvault.duckdns.org`, then:
```bash
nginx -t && systemctl reload nginx
```

## Verify
```bash
curl https://ohmstockvault.duckdns.org/custom-screener/api/health
# open https://ohmstockvault.duckdns.org/custom-screener/ in a browser
```
