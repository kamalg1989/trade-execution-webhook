# OhmStockVault — AI Trading Platform for NSE

A self-hosted stock trading platform for the Indian market (NSE). It screens the
NIFTY-500 every day for high-quality breakout setups, shows them in a responsive
web app with charts, and lets you place real orders and manage stop losses on your
own Dhan account — all backed by a 15-year local market-data warehouse that also
doubles as an MCP tool for Claude.

**Live:** https://ohmstockvault.duckdns.org/
**API docs:** https://ohmstockvault.duckdns.org/api/v1/docs
**MCP endpoint (Claude):** https://ohmstockvault.duckdns.org/mcp

---

## Table of contents

- [What it does](#what-it-does)
- [The web app](#the-web-app)
- [The screener](#the-screener-screen_gptpy)
- [Stop-loss engine (forever orders)](#stop-loss-engine-forever-orders)
- [Market Data API + charts](#market-data-api--charts)
- [Connecting to Claude (MCP)](#connecting-to-claude-mcp)
- [Architecture](#architecture)
- [Deployment & operations](#deployment--operations)
- [Configuration](#configuration)
- [Repository layout](#repository-layout)

---

## What it does

- **Daily AI screening** of the NIFTY-500 (breakout-from-base setups, regime-aware).
- **Web dashboard** (mobile + desktop) with recommendations, interactive charts, and a
  one-tap Buy button that places a real Dhan order with an automatic stop loss.
- **Portfolio & P&L** pulled live from your Dhan holdings.
- **Stop-loss manager** built on Dhan **forever orders**, with suggested levels,
  trailing, structural exits, and one-tap placement — all button-driven, nothing
  auto-fires without you.
- **Settings tab** to tune every screener parameter and enable/disable each screening
  gate, applied on the next scan.
- **Market Data API** serving OHLCV + candlestick charts for ~2,710 NSE stocks, also
  usable as an **MCP tool inside Claude**.

---

## The web app

Responsive React SPA (auto-switches between a desktop layout and a mobile bottom-tab
layout). Five sections:

### 1. Dashboard (Home)
- Daily recommendations from the screener: symbol, LTP, target, stop loss, confidence,
  upside %, and the reason (setup type, base stage, R:R, market regime).
- **Interactive charts** — tap **View Chart** to open a fullscreen viewer with:
  - **Daily / Weekly** toggle
  - **Timeframe** selector: 3M / 6M / 1Y / 2Y / 5Y
  - **Dark / Light** theme toggle
  - **Zoom** (1×–4×) and pan
- **Buy button** places a real Dhan CNC order (market or limit) and auto-places a
  stop-loss forever order.

### 2. P&L Tracker
- Live totals from your Dhan holdings: invested, current value, unrealized P&L, %.
- Top gainers / losers with per-position breakdown.

### 3. Stop Loss (SL)
- **Unprotected positions** — holdings without an active SL, each with a dropdown of
  suggested levels (−8% safety from buy, −8% trail from current, structural from the
  screener sheet, −5/−10/−12% from current, breakeven), showing the price and % vs buy.
- **Protected positions** — buy price, current SL and its % vs buy, distance to SL, risk
  zone (Safe / Warning / Critical), plus:
  - **Trail-to-level** dropdown (only levels above the current SL) → *Move SL*
  - **Exit Now** (structural exit-forever)
  - **Cancel**
- Everything places **real Dhan forever orders** and is confirmation-gated.

### 4. Portfolio
- Full holdings table + closed trades, with CSV export.

### 5. Settings
- Toggle each screener feature: liquidity gate, technical gate, base-quality gate,
  fundamental gate, IFP gate, GPT confirmation, Telegram alerts, hard-stop-on-decline,
  pullback / breakout-retest triggers.
- Edit parameters: capital, max picks, min turnover, target R multiple, base range %,
  prior upmove %, giveback %, max base stage, IFP score, max P/E, min ROE, target
  strategy, trend-alignment mode.
- **Run Scan Now** and **Reset to Defaults** buttons. Settings apply on the next scan.

> Every tab refreshes its data each time you open it.

---

## The screener (`screen_gpt.py`)

Deterministic, regime-aware breakout screener over the NIFTY-500.

**Pipeline (funnel):** universe → liquidity gate → technical gate (trend alignment +
base range + volume) → base-quality (prior upmove, giveback, volume dry-up, distance
from high) → base stage → fundamental gate → institutional footprint (IFP) → entry
trigger → deterministic ranking → optional GPT chart confirmation → top-N alerts.

**Key traits:**
- **Reads OHLCV from the local database first** (no per-scan API calls); falls back to
  the Dhan API only when the DB is stale.
- Writes the final picks to `latest_recommendations.json`, which the web API serves.
- Sends the same alerts to Telegram (can be disabled in Settings).
- All thresholds and gate on/off switches are driven by `screener_settings.json`
  (managed from the Settings tab).

Trigger a scan from the UI (**Settings → Run Scan Now**) or via
`POST /api/recommendations/refresh`.

---

## Stop-loss engine (forever orders)

Stop losses use Dhan **forever orders** (GTT-style) so they persist across sessions.
Existing SLs are detected by reading your **forever SELL orders** (not the day order
book). The web layer reuses the production `sl_engine.py` functions directly:

- **Safety SL (−8%)** — resting forever SELL at `entry × 0.92`.
- **Structural SL** — level from the screener sheet; when daily close breaks it, an
  **exit-forever** is placed to sell at the next open.
- **Trail** — ratchets the −8% order up from the latest close (place-first, cancel-old,
  never bare).
- **Cancel** — removes a forever order.

All are exposed as buttons; nothing runs automatically on the web VPS.

---

## Market Data API + charts

FastAPI service (port 8001) over a PostgreSQL/TimescaleDB warehouse.

- `GET /api/v1/health` — status
- `GET /api/v1/symbols?sector=IT` — symbol list
- `GET /api/v1/ohlcv?symbol=TCS&from_date=…&to_date=…` — one stock
- `GET /api/v1/ohlcv/multi?symbols=TCS,INFY&…` — batch
- `GET /api/v1/charts/daily|weekly|combined?symbol=…&theme=dark` — SVG charts

Charts include candles, volume, EMA 10/21/50/200, a **stats header** (LTP, 1-year
change, 52-week high/low) and **52-week reference lines**, in a clean dark/light style.

**Data:** ~2,710 NSE symbols, 15 years of daily candles (~5.8M rows), refreshed daily
at 18:00 IST from Dhan. New NIFTY-500 entrants are auto-ingested.

Full interactive reference: **https://ohmstockvault.duckdns.org/api/v1/docs**

---

## Connecting to Claude (MCP)

The Market Data API is also a **Model Context Protocol** server (Streamable HTTP) at:

```
https://ohmstockvault.duckdns.org/mcp
```

Tools exposed: `get_health`, `get_symbols`, `get_ohlcv`, `get_multi_ohlcv`,
`get_daily_chart`, `get_weekly_chart`, `get_combined_chart`.

### Claude Desktop
1. **Settings → Developer → Edit Config** (opens `claude_desktop_config.json`).
2. Add and save:
   ```json
   {
     "mcpServers": {
       "nse-market-data": {
         "command": "npx",
         "args": ["-y", "mcp-remote", "https://ohmstockvault.duckdns.org/mcp"]
       }
     }
   }
   ```
3. Fully quit and reopen Claude Desktop — the tools appear under the 🔌 menu.
   (`mcp-remote` bridges Desktop to the remote server; needs Node.js, which ships with npx.)

### Claude.ai (Web)
1. **Settings → Connectors → Add custom connector**.
2. Name `NSE Market Data`, URL `https://ohmstockvault.duckdns.org/mcp`, Save.
3. Enable it in a chat from the 🔌 menu.

> The endpoint is served over **HTTPS** with a valid Let's Encrypt certificate
> (auto-renewing), so both Claude.ai (web) and Claude Desktop connect directly. HTTP
> requests are redirected to HTTPS.

### Example prompts
- "Show me the daily and weekly charts for TCS over the last 6 months and analyse the trend."
- "Get OHLCV for TCS, INFY and WIPRO for 2026 so far — which performed best?"
- "List FINANCE-sector stocks and show 6-month dark-theme charts for the top 5."

---

## Architecture

```
                         ┌──────────────────────────────┐
     Browser / Mobile ──▶│  Nginx (port 80)             │
                         │   /            → web app (SPA)│
                         │   /api/        → :8004 Web API│
                         │   /api/v1/     → :8001 MktData│
                         │   /mcp         → :8003 MCP    │
                         └──────────────────────────────┘
                              │            │           │
                 ┌────────────┘   ┌────────┘     ┌─────┘
                 ▼                ▼              ▼
        Trade Web API      Market Data API   MCP server
        (FastAPI :8004)    (FastAPI :8001)   (:8003, Streamable HTTP)
          │      │              │
          │      │              ▼
          │      │        PostgreSQL / TimescaleDB  (OHLCV, 15y)
          │      │              ▲
          │      │        daily ingest cron (18:00 IST)
          │      ▼
          │   Dhan API v2  (holdings, orders, forever SLs)
          ▼
        screen_gpt.py  →  latest_recommendations.json  +  Telegram
```

- **Frontend**: React 18 + Vite + Tailwind (`web-platform/`), built to static files
  served by Nginx.
- **Trade Web API** (`web-platform/backend/`, port 8004): recommendations, orders,
  portfolio, stop loss, settings, chart proxy.
- **Market Data API** (`market_data_setup/api/`, port 8001): OHLCV + SVG charts + swagger.
- **MCP server** (`market_data_setup/mcp/`, port 8003): Streamable HTTP for Claude.
- **Screener** (`screen_gpt.py`) and **SL engine** (`sl_engine.py`) reused by the backend.

---

## Deployment & operations

Hosted on a VPS (Bangalore) behind Nginx. Key services (systemd):

| Service | Port | Purpose |
|---------|------|---------|
| `trade-web-api` | 8004 | Web platform backend |
| `market-data-api` | 8001 | OHLCV + charts + swagger |
| `market-data-mcp` | 8003 | MCP server (Streamable HTTP) |
| `nginx` | 80 | Reverse proxy + static frontend |

**Daily data update:** cron at 18:00 IST runs `market_data_setup/scripts/update_ohlcv.py`
(gap-fills all existing symbols). `ingest_missing.py` backfills any new NIFTY-500 symbols.

**Deploy frontend:**
```bash
cd web-platform && npm run build
scp -r dist root@<vps>:/root/web-app/
```

**Deploy a backend/API change:** copy the file to the VPS and
`systemctl restart <service>`.

---

## Configuration

Secrets live in `.env` (never committed). Required keys:

```
DHAN_CLIENT_ID=…
DHAN_PIN=…
DHAN_TOTP_SECRET=…
DB_PASSWORD=…
OPENAI_API_KEY=…            # optional, for GPT confirmation
TELEGRAM_TOKEN=…            # screener alerts
TELEGRAM_CHAT_ID=…
SL_TELEGRAM_TOKEN=…         # SL engine alerts
SL_TELEGRAM_CHAT_ID=…
SPREADSHEET_ID=…            # trade log (Google Sheet)
SERVICE_ACCOUNT_KEY_PATH=…
```

A single Dhan access token is shared via `.dhan_token_cache.json` across the screener,
SL engine, web API and ingester (Dhan rate-limits token generation to once per 2 minutes).

Screener behaviour is controlled by `screener_settings.json` (edited from the Settings tab).

---

## Repository layout

```
screen_gpt.py                     Daily NIFTY-500 screener
sl_engine.py                      Stop-loss engine (forever orders)
entry_engine.py                   Order sizing / entry helpers
google_sheets_db.py, tick_utils.py  Shared helpers

web-platform/                     React SPA + FastAPI backend
  ├─ pages/ components/           UI (desktop + mobile)
  └─ backend/                     Trade Web API (routers, dhan_client)

market_data_setup/
  ├─ api/main.py                  Market Data API + swagger + chart generator
  ├─ mcp/                         MCP servers
  └─ scripts/                     ingest / daily update scripts
```

---

*Built for personal use. Trading involves risk; the Buy and SL actions place real orders
on your live brokerage account — use them deliberately.*
