# Custom Screener Design Document

**Purpose:** Self-serve stock discovery tool for the NSE universe with flexible filtering, historical backtesting, and market-wide situational awareness.

**Status:** v4 — standalone app. **Implemented** under `custom-screener/` (backend + compute + SQL + frontend + deploy). URL locked to same-domain dedicated path `/custom-screener/`. Backend 29/29 tests pass; frontend builds; server boots and degrades gracefully without DB. Remaining: run DDL + 15y backfill against the live DB and wire nginx/systemd on the VPS (see `custom-screener/deploy/deploy.md`).

**Deployment model:** This is a **fully standalone application** — its own frontend build, its own backend service (own port + systemd unit), its own URL, and its own top-level `custom-screener/` folder tree. It does **not** touch the existing web app, its React router, or its backend. The only thing shared is the **data**: it reads the existing PostgreSQL + TimescaleDB (read-only) and may call the existing Market Data charts API (:8001) over HTTP for chart SVGs. No source files are imported from the existing app.

**DB stack:** Reuses the **existing PostgreSQL + TimescaleDB 2.28.1** instance (port 5432, asyncpg pooling). No new database/engine — just two additional tables alongside the `ohlcv_data` hypertable.

**Changelog vs v3:**
- Reworked into a **fully standalone app**: new top-level `custom-screener/` folder (backend + frontend + compute + sql + deploy), backend on **:8005** with its own systemd unit, its own SPA at a **separate URL** (subdomain or dedicated path). No changes to `web-platform/` or existing routers.
- Endpoint paths simplified to `/api/market-snapshot|filter|historical` on the new origin.
- Calc logic **copied** into `compute/indicators.py` (no cross-project import); ChartModal re-created locally (fetches SVG from :8001 charts API).
- Added Phase 0 (scaffold) and a project structure + deployment section.

**Changelog vs v2:**
- DDL aligned to existing conventions: `symbol TEXT` (matches `ohlcv_data`), `indicator_date DATE` derived from the `time TIMESTAMPTZ` column via cast.
- `stock_indicators` made a **TimescaleDB hypertable** on `indicator_date` (same chunking/compression as `ohlcv_data`); `market_snapshot` stays a plain table.

**Changelog vs v1:**
- Compute rewritten to **vectorized per-symbol** (load full series once, compute all dates in one pass) — fixes the per-symbol-per-day loop.
- Boolean flags replaced with **numeric distance columns** (arbitrary thresholds, no hardcoded flags).
- Postgres-correct DDL (`CREATE INDEX` separate from `CREATE TABLE`).
- **Cross-DB ownership** made explicit (tables live in the market_data DB).
- **15-year backfill** (storage is ~a few GB, not 170 GB).
- Regime upgraded to a **breadth composite** with defined score bands; `trend_score` range corrected to -1..+1.
- `ltp` renamed to `close` (EOD, not live).
- Lookbacks defined as **trading-day offsets**.
- **Pagination removed** (return all matches, sort client-side).
- Insufficient-history and snapshot-completeness handling defined.

---

## Table of Contents
1. [Overview](#overview)
2. [Database Schema](#database-schema)
3. [Data Pipeline](#data-pipeline)
4. [Backend API Design](#backend-api-design)
5. [Frontend Design](#frontend-design)
6. [Filter Logic & Dropdown Options](#filter-logic--dropdown-options)
7. [Historical Data & Backtesting](#historical-data--backtesting)
8. [Integration with Existing Setup](#integration-with-existing-setup)
9. [Implementation Phases](#implementation-phases)
10. [Key Design Decisions](#key-design-decisions)
11. [Performance Considerations](#performance-considerations)
12. [Resolved Questions](#resolved-questions)

---

## Overview

### What Problem Does It Solve?
The existing screener (`screen_gpt.py`) is **deterministic and automated**: it runs daily, applies a fixed funnel, and outputs top-N picks. Users have no control.

The **Custom Screener** is **exploratory and manual**: traders define their own filters (liquidity, moving averages, price proximity, % changes) and browse the full NSE universe from pre-computed DB data (no live API calls).

### Key Features
- **Pre-computed indicators** — EMA, SMA, 52W levels, % changes, distances computed nightly at 18:30 IST.
- **Flexible filtering** — dropdowns for liquidity, moving averages, 52-week proximity, price moves; "All" default.
- **Market snapshot** — breadth statistics for situational awareness (how many stocks meet each threshold + a regime label).
- **Historical lookup** — run the same filter against any past date for backtesting (15 years of history).
- **Completely separate** from the deterministic screener; zero impact on portfolio/SL/order logic.

### Architectural Fit — standalone
- **Self-contained project** under a new top-level `custom-screener/` folder (own frontend + backend + compute + SQL). Nothing added to `web-platform/` or the existing routers.
- **Own backend service** (FastAPI) on a **new port (:8005)** with its own systemd unit — not a router bolted onto :8004.
- **Own frontend** (separate Vite/React build) served at its **own URL** (subdomain or dedicated path — see Deployment).
- Uses the **existing OHLCV warehouse** (~2,710 symbols, 15 years) — the same **PostgreSQL + TimescaleDB** instance (port 5432) that `ohlcv_data`, the Market Data API (:8001) and `screen_gpt.py` already use — via a **read-only connection**. Indicator calc logic is **re-implemented locally** in this project (copied, not imported) so there is no code coupling.
- Adds **two new tables in the same database**. No new DB or engine.
- Runs **after market hours only** (18:30 IST), chained after the 18:00 OHLCV update.

---

## Database Schema

> **Ownership:** Both tables live in the **same PostgreSQL + TimescaleDB database** as `ohlcv_data`. The standalone backend (:8005) gets its **own read-only connection** to this database. The nightly compute reads OHLCV locally; EMA/SMA logic is re-implemented inside this project (copied from `screen_gpt.py`, not imported) to keep it decoupled.
>
> **Conventions (match `ohlcv_data`):** `symbol` is `TEXT` (not `VARCHAR`), and `indicator_date` is a `DATE` derived from the source `time TIMESTAMPTZ` column via `time::date` (IST). `stock_indicators` is a TimescaleDB hypertable partitioned on `indicator_date`; `market_snapshot` (one row/day) is a plain table.

### Primary Table: `stock_indicators`

One row per symbol per trading day. Distances stored as **numeric columns** so any threshold can be filtered at query time — no hardcoded boolean flags.

```sql
CREATE TABLE stock_indicators (
  symbol             TEXT        NOT NULL,   -- matches ohlcv_data.symbol
  indicator_date     DATE        NOT NULL,   -- date these values apply to (from time::date, IST)

  -- === Price & Liquidity ===
  close              NUMERIC(12,2),          -- EOD close (renamed from "ltp"; not live)
  turnover_1m_avg_cr NUMERIC(15,2),          -- avg daily turnover ₹ Cr, last 21 trading days
                                             -- AVG(volume*close) over 21 bars / 1e7
  volume_1m_avg      BIGINT,                 -- avg volume (shares), last 21 trading days

  -- === Moving Averages ===
  ema_10             NUMERIC(12,2),
  ema_21             NUMERIC(12,2),
  sma_50             NUMERIC(12,2),
  sma_200            NUMERIC(12,2),

  -- Distance of close from each MA, in % (negative = below). Enables arbitrary
  -- "within X%" / "above/below" filters without per-threshold flags.
  dist_ema_10_pct    NUMERIC(8,2),
  dist_ema_21_pct    NUMERIC(8,2),
  dist_sma_50_pct    NUMERIC(8,2),
  dist_sma_200_pct   NUMERIC(8,2),

  -- === 52-Week Levels ===
  price_52w_high     NUMERIC(12,2),          -- max(high) over last 252 bars (inclusive)
  price_52w_low      NUMERIC(12,2),          -- min(low)  over last 252 bars (inclusive)
  dist_52w_high_pct  NUMERIC(8,2),           -- (close - high)/high*100  (<=0; 0 = at high)
  dist_52w_low_pct   NUMERIC(8,2),           -- (close - low)/low*100    (>=0; 0 = at low)

  -- === Percentage Changes (trading-day offsets, snapped to nearest prior bar) ===
  pct_chg_1d         NUMERIC(8,2),           -- vs 1 bar ago
  pct_chg_5d         NUMERIC(8,2),           -- vs 5 bars ago
  pct_chg_1m         NUMERIC(8,2),           -- vs 21 bars ago
  pct_chg_3m         NUMERIC(8,2),           -- vs 63 bars ago
  pct_chg_6m         NUMERIC(8,2),           -- vs 126 bars ago
  pct_chg_1y         NUMERIC(8,2),           -- vs 252 bars ago

  -- === Volatility ===
  atr_14             NUMERIC(12,2),          -- ATR(14); stored for future use

  -- === Data-quality ===
  bars_available     INT,                    -- # of bars behind this date (for NULL-aware filters)
  is_new_52w_high    BOOLEAN,                 -- per-day fact: this bar set a fresh 252-day high
  is_new_52w_low     BOOLEAN,                 -- per-day fact: this bar set a fresh 252-day low

  created_at         TIMESTAMPTZ DEFAULT NOW(),
  updated_at         TIMESTAMPTZ DEFAULT NOW()
);

-- Make it a hypertable partitioned on indicator_date (same pattern as ohlcv_data).
-- Note: TimescaleDB requires the partitioning column in any UNIQUE constraint, and
-- disallows a separate serial PK — so (symbol, indicator_date) IS the natural key.
SELECT create_hypertable('stock_indicators', 'indicator_date',
                         if_not_exists => TRUE,
                         chunk_time_interval => INTERVAL '1 month');

ALTER TABLE stock_indicators
  ADD CONSTRAINT uq_stock_indicators UNIQUE (symbol, indicator_date);

-- A day's slice is <=2,710 rows, so one date index carries almost every query.
CREATE INDEX idx_si_symbol_date ON stock_indicators (symbol, indicator_date DESC);
-- (indicator_date is already chunk-indexed by the hypertable.)
-- Optional, only if profiling shows benefit:
-- CREATE INDEX idx_si_date_turnover ON stock_indicators (indicator_date, turnover_1m_avg_cr DESC);
```

**Direction filters use the distance columns, not flags:**
- "Above SMA 200" → `dist_sma_200_pct > 0`
- "Within 15% of 52W high" → `dist_52w_high_pct > -15`
- "Within 10% of 52W low" → `dist_52w_low_pct < 10`

Sub-200-bar symbols get `sma_200 = NULL` and `dist_sma_200_pct = NULL`; `bars_available` lets the API/UI decide whether to include or hide them.

### Secondary Table: `market_snapshot`

One row per trading day. Breadth statistics + regime.

```sql
CREATE TABLE market_snapshot (
  id                        BIGSERIAL PRIMARY KEY,
  snapshot_date             DATE NOT NULL UNIQUE,

  total_stocks              INT,   -- symbols with data this date
  eligible_stocks           INT,   -- with >=200 bars (denominator for breadth)

  -- Directional breadth
  count_above_50sma         INT,
  count_above_200sma        INT,
  count_below_50sma         INT,
  count_below_200sma        INT,

  -- 52-week proximity
  count_within_15pct_52w_high INT,
  count_within_10pct_52w_high INT,
  count_within_15pct_52w_low  INT,
  count_within_10pct_52w_low  INT,

  -- New-high / new-low breadth (at or making a fresh 252-bar extreme)
  count_new_52w_high        INT,
  count_new_52w_low         INT,

  -- Big movers
  count_moved_gt_4_5pct_1d  INT,
  count_moved_gt_20pct_1m   INT,
  count_moved_gt_60pct_3m   INT,
  count_moved_gt_100pct_6m  INT,

  -- Regime (see scoring below)
  regime                    VARCHAR(30),   -- Strong Uptrend | Moderate Uptrend |
                                           -- Consolidation | Correction | Strong Correction
  trend_score               NUMERIC(4,2),  -- -1.00 .. +1.00
  breadth_score             NUMERIC(4,2),  -- 0.00 .. 1.00 (composite, see below)

  is_complete               BOOLEAN DEFAULT FALSE,  -- snapshot only trusted when TRUE
  created_at                TIMESTAMP DEFAULT NOW(),
  updated_at                TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_ms_date ON market_snapshot (snapshot_date);
```

### Regime scoring (defined)

```
pct200 = count_above_200sma / eligible_stocks       # 0..1
pct50  = count_above_50sma  / eligible_stocks       # 0..1
nh_nl  = (count_new_52w_high - count_new_52w_low) / eligible_stocks   # -1..1

trend_score   = 2 * (pct200 - 0.5)                  # -1..+1  (fixes v1 range bug)
breadth_score = 0.5*pct200 + 0.3*pct50 + 0.2*((nh_nl + 1)/2)   # 0..1 composite

regime =
  pct200 >= 0.70  -> "Strong Uptrend"
  pct200 >= 0.55  -> "Moderate Uptrend"
  pct200 >= 0.45  -> "Consolidation"
  pct200 >= 0.30  -> "Correction"
  else            -> "Strong Correction"
```

`breadth_score` blends long-trend participation (200), short-trend participation (50), and net new highs — a more robust signal than the single 200-SMA ratio, and leaves room to add a volatility/VIX term later without schema changes.

---

## Data Pipeline

### Schedule
- **When:** 18:30 IST (after the 18:00 OHLCV update completes).
- **Trigger:** cron; also a manual "Recompute" button in the UI.

### Script: `custom-screener/backend/compute/compute_stock_indicators.py`

**Algorithm — vectorized per symbol (not per symbol-per-day):**

```
for each symbol in universe:
    df = load full daily OHLCV series once            # single query per symbol
    df["ema_10"]  = ema(df.close, 10)                 # rolling, whole series at once
    df["ema_21"]  = ema(df.close, 21)
    df["sma_50"]  = df.close.rolling(50).mean()
    df["sma_200"] = df.close.rolling(200).mean()
    df["price_52w_high"] = df.high.rolling(252).max()
    df["price_52w_low"]  = df.low.rolling(252).min()
    df["pct_chg_1d/5d/1m/3m/6m/1y"] = pct_change over 1/5/21/63/126/252 bars
    df["atr_14"]  = atr(df, 14)
    df["turnover_1m_avg_cr"] = (df.volume*df.close).rolling(21).mean() / 1e7
    df["dist_*_pct"] = derived distances
    df["bars_available"] = row index
    bulk-upsert the requested date range (all dates in one pass)

after all symbols for a date are done:
    aggregate counts -> market_snapshot, set is_complete = (processed >= threshold)
```

**Why this matters:** computing rolling windows once over the full series is O(bars) per symbol, ~sub-second each — so a **full 15-year backfill for all symbols is minutes-to-low-hours, not the days a per-date loop would take**. Daily incremental runs recompute only the latest date(s).

**Calc parity (no import):** the EMA/SMA/liquidity formulas are **copied** from `screen_gpt.py` into this project's own `compute/indicators.py` so "above 200 SMA" matches the existing screener, while keeping the standalone app fully decoupled (no cross-project imports).

### Backfill
- **Target: full 15 years** for all symbols (storage is trivial — see Performance).
- Run once as a batch (can shard by symbol across processes); thereafter daily incremental.

### Completeness & error handling
- `market_snapshot.is_complete` is set only when the run processed ≥ threshold (e.g. 2,600) symbols. The API refuses to serve an incomplete snapshot as "current."
- Failed symbols are logged; the next run re-upserts missing (symbol, date) pairs.
- Alert if fewer than 2,600 symbols processed.

---

## Backend API Design

- **Standalone service:** own FastAPI app at `custom-screener/backend/app/main.py`, **port :8005**, own systemd unit (`custom-screener-api`). Not part of the :8004 Trade Web API.
- **DB:** its own read-only asyncpg pool to the shared PostgreSQL + TimescaleDB.
- **Base path:** all routes under `/api/` on its own origin (subdomain or dedicated path — see Deployment). Paths below are shown relative to that origin.
- **Auth:** single-user, behind nginx — no new auth layer added. *(Confirm current posture before exposing.)*

### Endpoint 1 — Market Snapshot
```
GET /api/market-snapshot?date=YYYY-MM-DD   (date optional; default latest complete)
```
```json
{
  "snapshotDate": "2026-07-08",
  "totalStocks": 2710,
  "eligibleStocks": 2480,
  "counts": {
    "above50sma": 1834, "above200sma": 1567, "below50sma": 646, "below200sma": 913,
    "within15pct52wHigh": 342, "within10pct52wHigh": 189,
    "within15pct52wLow": 156, "within10pct52wLow": 98,
    "newHigh": 61, "newLow": 12,
    "movedGt4_5pct1d": 423, "movedGt20pct1m": 756,
    "movedGt60pct3m": 210, "movedGt100pct6m": 74
  },
  "regime": "Strong Uptrend",
  "trendScore": 0.26,
  "breadthScore": 0.71,
  "message": "63% of eligible NSE equities above 200-day SMA"
}
```
404 if no complete snapshot for that date (returns latest available in `detail`).

### Endpoint 2 — Filter & List (no pagination)
```
POST /api/filter
```
Returns **all** matching rows (≤2,710); the client sorts/scrolls.
```json
{
  "indicatorDate": "2026-07-08",           // optional; default latest complete
  "includeInsufficientHistory": false,      // if false, drop symbols with <200 bars
  "filters": {
    "minTurnoverCr": 5,
    "sma200": "above",                      // "above" | "below" | "any"
    "sma50":  "any",
    "ema10Above": null, "ema10Below": null, // absolute ₹ bounds (null = ignore)
    "within52wHighPct": 15,                 // close within X% of 52W high (null = ignore)
    "within52wLowPct":  null,
    "pctChg1d": {"min": null, "max": null},
    "pctChg1m": {"min": 5,    "max": 20},
    "pctChg3m": {"min": null, "max": null},
    "pctChg6m": {"min": null, "max": null},
    "pctChg1y": {"min": null, "max": null}
  }
}
```
```json
{
  "indicatorDate": "2026-07-08",
  "matchCount": 234,
  "results": [
    {
      "symbol": "TRIDENT", "close": 26.73,
      "ema10": 25.80, "ema21": 26.01, "sma50": 26.50, "sma200": 24.32,
      "distSma200Pct": 9.9, "atr14": 0.52,
      "price52wHigh": 28.50, "price52wLow": 15.20,
      "dist52wHighPct": -6.2, "dist52wLowPct": 75.9,
      "pctChg1d": 0.84, "pctChg1m": 12.5, "pctChg3m": 45.2, "pctChg6m": 87.5, "pctChg1y": 120.5,
      "turnover1mAvgCr": 12.5, "volume1mAvg": 5600000, "barsAvailable": 3800
    }
  ]
}
```
400 on invalid ranges (`min > max`); 404 if date has no complete data.

### Endpoint 3 — Historical Indicators (backtesting)
```
GET /api/historical?symbol=TRIDENT&fromDate=…&toDate=…&limit=1000
```
Time series of the same fields for one symbol over a date range. 404 unknown symbol, 400 bad range.

---

## Frontend Design

### Standalone SPA (own build, own URL)

A **separate Vite + React app** under `custom-screener/frontend/`, built independently and served at its own origin (subdomain `screener.ohmstockvault.duckdns.org`, or a dedicated `/custom-screener/` path — see Deployment). It has its own `index.html`, `package.json`, router, and components. **No files are shared with `web-platform/`** — the chart viewer is re-created here (and simply fetches SVGs from the existing Market Data charts API over HTTP).

```
┌───────────────────────────────────────────────────────────────┐
│ Custom Screener                          [Date: 2026-07-08 ▼]  │
├───────────────────────────────────────────────────────────────┤
│ ┌ Market Snapshot (2026-07-08) ─────────────────────────────┐ │
│ │ Regime: Strong Uptrend 📈  Trend 0.26  Breadth 0.71        │ │
│ │ 1,567 >200SMA · 1,834 >50SMA · 61 new highs / 12 new lows │ │
│ │ 342 within 15% of 52W high · 156 within 15% of 52W low    │ │
│ └───────────────────────────────────────────────────────────┘ │
│ ┌ Filters ──────────────────────────────────────────────────┐ │
│ │ Min Turnover [All▼]  SMA200 Dir [All▼]  SMA50 Dir [All▼]  │ │
│ │ EMA10 [All▼] EMA21 [All▼]                                 │ │
│ │ 52W High Prox [All▼]  52W Low Prox [All▼]                 │ │
│ │ %Chg 1D[All▼] 1M[All▼] 3M[All▼] 6M[All▼] 1Y[All▼]         │ │
│ │ ☐ include <200-bar symbols        [Apply] [Reset]         │ │
│ └───────────────────────────────────────────────────────────┘ │
│ ┌ Results — 234 stocks  Sort:[%Chg1M ▼]  [Export CSV] ──────┐ │
│ │ Symbol │Close│52WHi%│SMA200│%1M │%3M │Turnover│           │ │
│ │ TRIDENT│26.73│ -6.2 │ +9.9 │+12.5│+45 │ 12.5Cr │ →chart   │ │
│ │ ...    │     │      │      │     │    │        │           │ │
│ └───────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────┘
```

### Components
```
CustomScreener.jsx  (this project's own page — not the existing app's)
├── DatePicker           (historical date; default latest complete)
├── MarketSnapshot       (regime + breadth counts)
├── FilterPanel          (dropdowns, "All" default)
├── ResultsTable         (client-side sort + scroll, no pagination)
│   └── StockRow → opens ChartModal (own component; fetches SVG from :8001 charts API)
└── ExportCsvButton
```

### Interactions
- **Date picker:** change → refetch snapshot + filter for that date; "No data" if no complete snapshot.
- **Filters:** "All" default; **Apply** → POST /filter; **Reset** → clear.
- **Results:** all rows returned once; sort by clicking headers (client-side). Row click → this app's own `ChartModal` (fetches SVG from the :8001 charts API; can pull /historical).
- **Export CSV:** `custom_screener_YYYY-MM-DD.csv` — symbol, close, EMA10/21, SMA50/200, dist_sma200, 52W high/low + distances, %chg 1d/1m/3m/6m/1y, turnover, bars_available.

---

## Filter Logic & Dropdown Options

**Liquidity (min turnover):** All / >₹1Cr / >₹5Cr / >₹10Cr / >₹50Cr / >₹100Cr → `turnover_1m_avg_cr >= v`

**EMA10 / EMA21 (absolute ₹):** All / >₹50 / >₹100 / >₹200 / >₹500 / >₹1,000 / custom range → `ema_10 > lo AND (hi IS NULL OR ema_10 < hi)`

**SMA50 / SMA200 direction:** All / Above / Below → `dist_sma_200_pct > 0` (above) or `< 0` (below)

**52W high proximity:** All / within 5/10/15/20% → `dist_52w_high_pct > -X`
**52W low proximity:** All / within 5/10/15% → `dist_52w_low_pct < X`

**% change (1D/1M/3M/6M/1Y):** All / >+4.5% / >+10% / >+20% / >+50% / <-5% / <-10% / custom range → range predicate on the matching `pct_chg_*` column.

All predicates run against a single day's ≤2,710-row slice, so any combination is fast without per-threshold indexes.

---

## Historical Data & Backtesting

- Compute upserts **every date**, 15 years back — so any historical date can be replayed.
- Workflow: pick a past date → apply filters → see that day's matches → click a symbol → ChartModal / `/historical` shows its forward path. Manual tracking for Phase 1 (no automated entry→forward-return engine — that's a separate project).

---

## Standalone Project Structure & Deployment

Everything for this feature lives under one new top-level folder. **Nothing is added to `web-platform/` or the existing routers/pages.**

```
custom-screener/
├── README.md
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app (own service, port :8005)
│   │   ├── config.py           # env: DB DSN, port, charts-API base URL
│   │   ├── db.py               # own read-only asyncpg pool
│   │   ├── models.py           # Pydantic request/response schemas
│   │   └── routers/
│   │       └── screener.py     # /api/market-snapshot, /api/filter, /api/historical
│   ├── compute/
│   │   ├── compute_stock_indicators.py   # nightly + backfill entrypoint
│   │   └── indicators.py       # EMA/SMA/ATR/%chg (copied from screen_gpt.py)
│   ├── sql/
│   │   ├── 001_stock_indicators.sql
│   │   └── 002_market_snapshot.sql
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js
│   ├── tailwind.config.js
│   └── src/
│       ├── main.jsx
│       ├── App.jsx
│       ├── api/client.js       # points at the :8005 backend origin
│       ├── pages/CustomScreener.jsx
│       └── components/
│           ├── DatePicker.jsx
│           ├── MarketSnapshot.jsx
│           ├── FilterPanel.jsx
│           ├── ResultsTable.jsx
│           ├── ChartModal.jsx  # own copy; fetches SVG from :8001 charts API
│           └── ExportCsvButton.jsx
└── deploy/
    ├── custom-screener-api.service     # systemd unit for :8005
    ├── custom-screener-compute.timer   # (or cron) 18:30 IST
    └── nginx-custom-screener.conf      # own server block / location
```

**Separate URL — LOCKED: same domain, dedicated path.**
- **`https://ohmstockvault.duckdns.org/custom-screener/`** → nginx serves this frontend's static build from that path; **`/custom-screener/api/`** → proxied to `127.0.0.1:8005` (trailing-slash rewrite strips the prefix so the backend sees `/api/...`). Reuses the existing Let's Encrypt cert; no DNS change.
- Frontend is built with Vite `base: '/custom-screener/'`; the API client base is `/custom-screener/api`.
- (Subdomain remains a future option if stronger isolation is ever wanted, but is not used now.)

**Services (systemd):** `custom-screener-api` (:8005) + a compute timer/cron. Independent of `trade-web-api`, `market-data-api`, `market-data-mcp`.

**Shared, but read-only / over-HTTP only:** the PostgreSQL + TimescaleDB (read-only pool) and the :8001 charts API (HTTP). No shared Python modules, no shared frontend files.

**Unchanged:** `screen_gpt.py`, the 18:00 OHLCV update, the existing web app, portfolio/SL/order logic.

**Dependency chain:**
```
18:00 IST  OHLCV update completes
18:30 IST  custom-screener compute  → stock_indicators → market_snapshot (is_complete)
~18:45     custom screener data live
```
If compute fails, the last complete snapshot remains the default (graceful degradation).

---

## Implementation Phases

**Phase 0 — Scaffold (0.5 wk):** create the `custom-screener/` tree, backend skeleton (:8005 FastAPI), frontend skeleton (Vite/React), and `indicators.py` (copied calc). Stand up an empty service + build.

**Phase 1 — Data pipeline (1 wk):** create tables (TimescaleDB hypertable DDL), write vectorized compute in `compute/`, validate one date against manual calc, **backfill 15 years**, wire 18:30 timer/cron + completeness gate.

**Phase 2 — Backend (1 wk):** three endpoints on :8005 with the standalone read-only pool; validation + error responses; confirm a full-universe filter returns well under target.

**Phase 3 — Frontend (1 wk):** standalone CustomScreener SPA — filter panel, client-sorted results table (no pagination), date picker, own ChartModal, CSV export, mobile + desktop.

**Phase 4 — Deploy & polish (1 wk):** nginx server block + cert for the new URL, systemd units, profile/add indexes as justified, regime/breadth tuning, user guide, monitoring/alerts.

---

## Key Design Decisions

1. **Pre-compute nightly** — fast, consistent, enables backtesting; cost is 1-day-stale data + a monitored job.
2. **Numeric distance columns, not boolean flags** — arbitrary thresholds, no schema churn when a new cutoff is wanted, less write cost.
3. **Separate `market_snapshot`** with an `is_complete` gate — one-query breadth, never served half-computed.
4. **Full 15-year retention** — storage is a few GB; makes backtesting genuinely useful.
5. **Dropdowns with presets** — simple UX; distance columns still allow custom ranges where offered.
6. **No pagination** — ≤2,710 rows, sorted client-side.
7. **Fully standalone app** — own `custom-screener/` tree, own backend service (:8005), own frontend build, own URL and systemd units. Shares only the database (read-only) and the :8001 charts API (HTTP). Calc logic is copied, not imported, so there is zero source coupling to the existing app.
8. **AI is future scope** — add optional `ai_*` columns later; AI runs on the filtered subset, not the full universe.

---

## Performance Considerations

**Storage (15 years):** 2,710 × 252 × 15 ≈ **10.2M rows**. At ~400 B/row ≈ **~4 GB** incl. indexes. Trivial. (v1's "170 GB" was a ~1000× error.)

**Indexes:** one on `(indicator_date)` carries virtually every query, since a date slice is ≤2,710 rows and remaining filters/sort run in memory. Add composite indexes only if profiling shows a need; consider BRIN on `indicator_date` (append-only by date).

**Latency targets:** snapshot <50 ms (1 row); filter <150 ms (one day slice); historical <100 ms (≤1,000 rows).

**Compute:** vectorized rolling per symbol → sub-second each; full backfill minutes-to-low-hours; daily incremental trivial.

---

## Resolved Questions

1. **Backfill:** **15 years.**
2. **Thresholds:** preset liquidity/%-move cutoffs as listed; distance columns allow custom ranges later.
3. **Flags vs computed:** **numeric distance columns.**
4. **Regime:** **breadth composite** (`trend_score` -1..+1, `breadth_score` 0..1) with defined bands; VIX-style term can be added later without schema change.
5. **Pagination:** **none** — return all, sort client-side.
6. **CSV export columns:** symbol, close, EMA10/21, SMA50/200, dist_sma200, 52W high/low + distances, %chg 1d/1m/3m/6m/1y, turnover, bars_available.
7. **Backtest UI:** date-picker + manual tracking for Phase 1; automated forward-return engine deferred.
8. **AI:** deferred; optional `ai_*` columns added later, run post-filter.

---

## Appendix: Postgres DDL

```sql
-- === stock_indicators (TimescaleDB hypertable, same DB as ohlcv_data) ===
CREATE TABLE IF NOT EXISTS stock_indicators (
  symbol             TEXT        NOT NULL,   -- matches ohlcv_data.symbol
  indicator_date     DATE        NOT NULL,   -- from ohlcv_data.time::date (IST)
  close              NUMERIC(12,2),
  turnover_1m_avg_cr NUMERIC(15,2),
  volume_1m_avg      BIGINT,
  ema_10             NUMERIC(12,2),
  ema_21             NUMERIC(12,2),
  sma_50             NUMERIC(12,2),
  sma_200            NUMERIC(12,2),
  dist_ema_10_pct    NUMERIC(8,2),
  dist_ema_21_pct    NUMERIC(8,2),
  dist_sma_50_pct    NUMERIC(8,2),
  dist_sma_200_pct   NUMERIC(8,2),
  price_52w_high     NUMERIC(12,2),
  price_52w_low      NUMERIC(12,2),
  dist_52w_high_pct  NUMERIC(8,2),
  dist_52w_low_pct   NUMERIC(8,2),
  pct_chg_1d         NUMERIC(8,2),
  pct_chg_5d         NUMERIC(8,2),
  pct_chg_1m         NUMERIC(8,2),
  pct_chg_3m         NUMERIC(8,2),
  pct_chg_6m         NUMERIC(8,2),
  pct_chg_1y         NUMERIC(8,2),
  atr_14             NUMERIC(12,2),
  bars_available     INT,
  is_new_52w_high    BOOLEAN,      -- per-day fact: this bar set a fresh 252-day high
  is_new_52w_low     BOOLEAN,      -- per-day fact: this bar set a fresh 252-day low
  created_at         TIMESTAMPTZ DEFAULT NOW(),
  updated_at         TIMESTAMPTZ DEFAULT NOW()
);
SELECT create_hypertable('stock_indicators', 'indicator_date',
                         if_not_exists => TRUE,
                         chunk_time_interval => INTERVAL '1 month');
ALTER TABLE stock_indicators
  ADD CONSTRAINT uq_stock_indicators UNIQUE (symbol, indicator_date);
CREATE INDEX IF NOT EXISTS idx_si_symbol_date ON stock_indicators (symbol, indicator_date DESC);

-- === market_snapshot (plain table, one row/day) ===
CREATE TABLE IF NOT EXISTS market_snapshot (
  id                          BIGSERIAL PRIMARY KEY,
  snapshot_date               DATE NOT NULL UNIQUE,
  total_stocks                INT,
  eligible_stocks             INT,
  count_above_50sma           INT,
  count_above_200sma          INT,
  count_below_50sma           INT,
  count_below_200sma          INT,
  count_within_15pct_52w_high INT,
  count_within_10pct_52w_high INT,
  count_within_15pct_52w_low  INT,
  count_within_10pct_52w_low  INT,
  count_new_52w_high          INT,
  count_new_52w_low           INT,
  count_moved_gt_4_5pct_1d    INT,
  count_moved_gt_20pct_1m     INT,
  count_moved_gt_60pct_3m     INT,
  count_moved_gt_100pct_6m    INT,
  regime                      VARCHAR(30),
  trend_score                 NUMERIC(4,2),
  breadth_score               NUMERIC(4,2),
  is_complete                 BOOLEAN DEFAULT FALSE,
  created_at                  TIMESTAMPTZ DEFAULT NOW(),
  updated_at                  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ms_date ON market_snapshot (snapshot_date);
```

---

**Version:** 4.0 (Draft) · **Date:** July 8, 2026 · standalone app (`custom-screener/`, :8005, own URL), shares only the DB (read-only) and :8001 charts API. Ready for implementation pending final review.
