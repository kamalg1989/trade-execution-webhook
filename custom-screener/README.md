# Custom Screener (standalone)

Self-serve NSE stock discovery with flexible filtering, market breadth, and
historical backtesting. **Fully standalone** — own backend service, own frontend
build, own URL. Shares only the existing database (read-only) and the `/api/v1/`
charts API (HTTP). Nothing in `web-platform/` is modified.

- **URL:** `https://ohmstockvault.duckdns.org/custom-screener/`
- **Backend:** FastAPI on `:8005` (`backend/app`)
- **Compute:** nightly at 18:30 IST (`backend/compute`)
- **Frontend:** Vite + React (`frontend/`), base `/custom-screener/`
- **Design:** see `../CUSTOM_SCREENER_DESIGN.md`

## Layout
```
custom-screener/
├── backend/
│   ├── app/           FastAPI app, config, db (read-only pool), models, filtering, routers
│   ├── compute/       indicators.py, snapshot.py, compute_stock_indicators.py
│   ├── sql/           001_stock_indicators.sql, 002_market_snapshot.sql
│   ├── tests/         pytest (indicators, filtering, snapshot, api, e2e)
│   └── requirements.txt, .env.example
├── frontend/          standalone SPA (own package.json / vite / tailwind)
└── deploy/            systemd units, nginx blocks, deploy.md
```

## Data model
- `stock_indicators` — TimescaleDB hypertable, one row per symbol per day
  (close, EMA10/21, SMA50/200 + distances, 52W high/low + distances,
  %chg 1d/5d/1m/3m/6m/1y, ATR14, turnover, bars_available).
- `market_snapshot` — one row per day (breadth counts, regime, trend/breadth
  scores, `is_complete` gate).

Filtering runs in Python over a single day's ≤2,710-row slice (the DB query is a
single `WHERE indicator_date = $1`), so filter logic is fully unit-tested without
a database.

## Develop / test
```bash
# backend
cd backend
pip install -r requirements.txt
pytest -q                       # 29 tests, no DB required
DB_PASSWORD=... python -m uvicorn app.main:app --port 8005

# frontend
cd frontend
npm install
npm run build                   # or: npm run dev  (proxies api->:8005, charts->:8001)
```

## Deploy
See `deploy/deploy.md` (DB DDL → backend service → 15y backfill + nightly timer →
frontend build → nginx location blocks).
