# CLAUDE.md — Project Reference for `trade-execution-webhook`

This file is the single source of truth for how this project is laid out, where everything
lives, and how to safely make and deploy changes. Read this before making any change.
It was compiled by directly inspecting the Mac repo, the GitHub remote, and the live VPS
(SSH) on 2026-08-01. Treat the ~20 other `*.md` files scattered in the repo root as
historical/point-in-time notes — this file supersedes them for "where is X / how do I
deploy Y" questions.

---

## 1. What this project is

A personal, end-to-end swing/positional trading system for NSE equities, built around:
- A quant screener (`screen_gpt.py`) that scans ~2,300 NSE EQ-series stocks nightly, applies
  a technical/liquidity/base-quality funnel, and produces ranked trade candidates.
- An optional AI (Gemini) "pure visual" chart-analysis pass that re-ranks the same candidates.
- Telegram-based trade confirmation (buy alerts with inline confirm buttons).
- A web dashboard (React) for recommendations, portfolio, and stop-loss/profit-booking
  management, with an R-multiple-based SL ladder methodology.
- Dhan (broker) API integration for holdings, positions, orders, and forever (GTT-style) orders.
- A separate "custom-screener" sub-app (own FastAPI backend + own React SPA) used for
  deeper/experimental AI chart-analysis work, served at `/custom-screener/` on the same domain.

Live URL: **https://ohmstockvault.duckdns.org**

---

## 2. Where things live

### 2.1 Local Mac (source of truth for development)

Repo root: `/Users/kamal/IdeaProjects/trade-execution-webhook`

```
trade-execution-webhook/
├── screen_gpt.py              # Main nightly quant screener (funnel, sizing, alerts)
├── ai_rank_candidates.py      # Post-scan Gemini v3 ranking pass over ALL candidates
├── entry_engine.py            # Entry-signal logic shared by screener/webhook
├── sl_engine.py                # SL/trailing logic used by the Telegram webhook flow
├── tick_utils.py               # Tick-size rounding helpers
├── google_sheets_db.py         # Google Sheet read/write (legacy trade log / structural SL)
│
├── Webhook-app/                 # Telegram bot + buy-confirm webhook (Flask, gunicorn)
│   ├── app.py                     # Main Flask app — Telegram callback handling, buy execution
│   ├── entry_engine.py, sl_engine.py, google_sheets_db.py, tick_utils.py  (own copies)
│   ├── intraday_protection_cron.py
│   └── migrate_sheet.py
│
├── web-platform/                # React dashboard + FastAPI backend (source of truth)
│   ├── App.jsx / AppMobile.jsx      # Desktop / mobile shells
│   ├── index.jsx, index.html, index.css
│   ├── pages/
│   │   ├── Dashboard.jsx / DashboardMobile.jsx        # Recommendations (Quant + AI sections)
│   │   ├── StopLossTracker.jsx / StopLossTrackerMobile.jsx   # "Tonight's actions" SL screen
│   │   ├── Portfolio.jsx / PortfolioMobile.jsx
│   │   ├── ProfitLossTracker.jsx / ProfitLossTrackerMobile.jsx
│   │   └── Settings.jsx                                # Trading Protection (PIN → API key)
│   ├── components/ChartModal.jsx, SLOrderModal.jsx
│   ├── hooks/useTheme.jsx (light/dark, MUST be .jsx not .js), useDevice.js
│   ├── backend/                     # FastAPI backend — SEE §5.3, this is mirrored to `web_api/` on the VPS
│   │   ├── main.py                     # App entry, APIKeyMiddleware (PIN-gated trading actions)
│   │   ├── dhan_client.py              # All Dhan API calls incl. auto token-refresh
│   │   ├── database/db.py              # SQLAlchemy models — see §6.2, NOT currently used by sl_engine
│   │   └── routers/
│   │       ├── recommendations.py, orders.py, sl_engine.py, charts.py,
│   │       │   portfolio.py, settings.py, health.py
│   │       └── push.py   ⚠️ EXISTS ON VPS ONLY — see §8.1, pull it back into this repo
│   ├── tailwind.config.js, vite.config.js, package.json, deploy.sh
│   └── public/, utils/   ⚠️ EXIST ON VPS ONLY — see §8.1
│
├── custom-screener/              # Separate sub-app: deep AI chart-analysis workbench
│   ├── backend/ (FastAPI, port 8005)
│   │   ├── app/main.py, app/filtering.py, app/models.py, app/db.py
│   │   ├── ai_analysis/ (config.py, pipeline.py, outcomes.py — Gemini prompt v2/v3 logic)
│   │   └── compute/ (indicators.py, ifp.py, snapshot.py)
│   ├── frontend/                    # Own React SPA, built separately, served at /custom-screener/
│   └── deploy/ (deploy_vps.sh, *.service, *.timer files)
│
└── market_data_setup/             # OHLCV ingestion + charting API (separate FastAPI, port 8001)
    ├── api/main.py, scripts/ingest_ohlcv.py, scripts/ingest_missing.py,
    │   scripts/update_symbols_meta.py, scripts/update_index_membership.py
    ├── database/schema.sql
    └── mcp/ (Model Context Protocol server wrapping the charting API)
```

**Uncommitted work sitting in the repo root right now** (proposed but not wired in —
see §8.3): `STRUCTURAL_SL_DB_FIX.md`, `structural_sl_migration.sql`, `structural_sl_integration.py`.

### 2.2 GitHub

- Repo: `https://github.com/kamalg1989/trade-execution-webhook` (private, presumed)
- Local remote `origin` is authenticated via a **Personal Access Token embedded directly in
  the remote URL** (visible via `git remote -v`). Treat that token as a live secret — it's
  sitting in `.git/config` in plaintext. Consider switching to SSH-key auth or the macOS
  git credential manager at some point so the token isn't sitting in a config file.
- Branches: `main` (active), `stableV4`, `feature/v3-final-screener`, `BasicGhettInt` (old).
- **Important**: the VPS's own local git checkout (`/root/trade-execution-webhook/.git`) is
  **behind** GitHub `main` — deployments have historically gone via direct `scp` of changed
  files, not `git pull`, so `git log` on the VPS does not reflect what's actually running.
  Don't trust `git status`/`git log` on the VPS as a picture of deployed state — check the
  files themselves.

### 2.3 VPS

- **Host**: `165.232.187.97` (DigitalOcean droplet, hostname `ubuntu-s-1vcpu-1gb-blr1-01`)
- **Domain**: `ohmstockvault.duckdns.org` → this IP (DuckDNS dynamic DNS)
- **OS**: Ubuntu 24.04.4 LTS
- **⚠️ Size: 1 vCPU / 961 MB RAM / 24 GB disk.** This is a *very* small box. It comfortably
  runs the Python services + Postgres, but has near-zero headroom for anything heavy
  (see §7 — running `npm run build` here caused an OOM kill of a live Python service).
- **SSH**: `ssh root@165.232.187.97` (key-based auth already set up on Kamal's Mac).
  **Claude's cloud sandbox (the `mcp__workspace__bash` tool) has no route to this IP or to
  the internet in general** — always use the **Desktop Commander** MCP tools
  (`mcp__Desktop_Commander__start_process`, run on Kamal's actual Mac) for anything that
  needs to reach the VPS or GitHub.
- **Repo path on VPS**: `/root/trade-execution-webhook`
- **Python venv**: `/root/trade-execution-webhook/venv` (Python 3.12.3)
- **Web root (nginx)**: `/root/web-app/dist` (built frontend lives here — see §5.5, NOT
  `/root/trade-execution-webhook/web-platform/dist`)
  - `/root/web-app/dist/custom-screener/` is the custom-screener SPA — **never
    `rm -rf` or wholesale-overwrite `/root/web-app/dist` without excluding this
    subdirectory**, it has been accidentally deleted before.

---

## 3. VPS services (systemd)

| Service | Port | Purpose | Entry point |
|---|---|---|---|
| `trade-web-api` | 8004 | Main dashboard FastAPI backend | `web_api.main:app` (uvicorn) — **not** `web-platform/backend/`, see §5.3 |
| `trade-webhook` | 8000 (localhost only) | Telegram bot + buy-confirm webhook | `Webhook-app.app:app` (gunicorn, 1 worker) |
| `custom-screener-api` | 8005 | Custom-screener FastAPI (AI chart analysis) | `app.main:app` in `custom-screener/backend/` |
| `market-data-api` | 8001 | OHLCV queries + charting SVGs | `market_data_setup.api.main:app` |
| `market-data-mcp` / `market-data-mcp-http` | — | MCP server wrapping the charting API | proxied at `/mcp` (port 8003) |

All run `User=root`, `WorkingDirectory=/root/trade-execution-webhook`, restart via
`systemctl restart <name>`. Secrets are loaded from `EnvironmentFile=/root/trade-execution-webhook/.env`
(except `trade-web-api`, which gets `DATABASE_URL` inline in the unit file).

### Scheduled jobs (systemd timers, all IST)

| Timer | Time | What it does |
|---|---|---|
| `sl-danger-monitor.timer` | every 10 min (market hours gated) | Push alert if price crosses structural SL |
| `ohlcv-gapfill.timer` | 17:45 daily | Backfill any OHLCV gaps before the screener runs |
| `custom-screener-compute.timer` | 18:00 daily | Recompute indicators/snapshot for custom-screener |
| `trade-journal-reconcile.timer` | 18:15 daily | Reconcile buy fills + closed positions into DB |
| `daily-screener.timer` | 18:30 daily | Runs `screen_gpt.py` → `latest_recommendations.json` + fires `ai_rank_candidates.py` in background |

### Cron (plain crontab, not systemd)

```
0 10 * * 0   update_symbols_meta.py           # weekly, Sunday
10 10 * * 0  update_index_membership.py       # weekly, Sunday
45 18 * * *  ai_analysis.outcomes              # daily, AI pick outcome scoring
```

---

## 4. Nginx routing (site: `/etc/nginx/sites-enabled/trade-platform`)

Single HTTPS vhost for `ohmstockvault.duckdns.org` (Let's Encrypt via certbot), HTTP→HTTPS
redirect. There's also a leftover generic `trade-web-platform` site on plain port 80
(`server_name _`) — low-priority cleanup candidate, not the active path.

| Path | Routed to |
|---|---|
| `/` | static files from `/root/web-app/dist` (main dashboard SPA) |
| `/assets/` | static, long-cache |
| `/custom-screener/` | static files from `/root/web-app/dist/custom-screener` |
| `/custom-screener/api/` | proxy → `127.0.0.1:8005/api/` (custom-screener-api) |
| `/api/v1/` | proxy → `127.0.0.1:8001` (market-data-api) |
| `/api/` | proxy → `127.0.0.1:8004` (trade-web-api — main dashboard backend) |
| `/mcp` | proxy → `127.0.0.1:8003` (MCP server, chunked/streaming) |

Note `trade-webhook` (port 8000, Telegram bot) is **not** exposed via nginx — Telegram
calls it directly or it's not internet-facing; treat as internal-only.

---

## 5. Databases & data storage

### 5.1 Postgres — `market_data` (owner: `market_data_user`)
OHLCV + derived data, used by `market-data-api` and `custom-screener-api`:
`ohlcv_data`, `stock_indicators`, `symbols_meta`, `index_membership`, `market_snapshot`,
`ingestion_log`, `ai_analysis_results`, `ai_call_budget`.

### 5.2 Postgres — `trading_platform` (owner: `postgres`)
`DATABASE_URL=postgresql://postgres:postgres@localhost:5432/trading_platform`
(used by `trade-web-api` and `trade-journal-reconcile`).
Tables: `sl_positions`, `user_trades`, `sl_audit_log`, `sl_alerts`, `sl_order_log`,
`sl_danger_alerts_sent`, `portfolio_history`, `position_snapshot`, `push_subscriptions`,
`stock_recommendations`, `trades`.

**⚠️ Important nuance**: `web-platform/backend/database/db.py` defines SQLAlchemy models for
`sl_positions` / `user_trades` / etc., and the tables genuinely exist in Postgres — but the
**live `sl_engine.py` router does NOT currently read or write this DB for SL tracking.**
SL state today comes live from the **Dhan API** (holdings + positions + forever orders) plus
the **Google Sheet** (`google_sheets_db.py`) and local **JSON files** on the VPS (§5.4). The
DB tables are effectively legacy/unused for this purpose right now — see §8.3 for the
proposed (not-yet-implemented) plan to make the DB the source of truth.

### 5.3 Google Sheet (`google_sheets_db.py`)
Legacy/primary trade log — structural SL, entry, target, status per trade. Requires
`SPREADSHEET_ID` + `SERVICE_ACCOUNT_KEY_PATH` (service account JSON, root-only perms on VPS).
Used as the **first-priority source** for structural SL in `sl_engine.py`.

### 5.4 Flat JSON files (all in `/root/trade-execution-webhook/`)
| File | Written by | Read by | Purpose |
|---|---|---|---|
| `latest_recommendations.json` | `screen_gpt.py` (nightly) | `recommendations.py`, `sl_engine.py` | Today's top-3 alerted stocks (`stocks`) + full ~33-candidate funnel survivors (`candidates`) |
| `latest_ai_picks.json` | `ai_rank_candidates.py` (fired async after screen_gpt.py) | `recommendations.py` | Gemini v3 chart-analysis ranking over all candidates |
| `structural_sl_history.json` | `screen_gpt.py` (upsert, **added 2026-08-01**) | `sl_engine.py` (`_screener_structural_map`) | Permanent per-symbol structural SL, survives a stock dropping out of a later scan's top-3 — this is the fix for the "SAREGAMA fell back to −8%" bug |
| `manual_structural_sl.json` | Settings/SL-screen UI (`/sl/set-structural`) | `sl_engine.py` | User-entered manual structural SL override (highest priority after the Sheet) |
| `half_booked.json` | `sl_engine.py` (`/sl/sell-half`) | `sl_engine.py` | Tracks which positions already had their +2R half-booking done |
| `api_key.json` | `web_api/main.py` (`load_or_create_api_key`) | `APIKeyMiddleware` | The trading-actions API key (paired with the Settings-screen PIN flow) |
| `screener_settings.json` | screener config UI (if any) | `screen_gpt.py` | Screener tunables |
| `.dhan_token_cache.json` | `dhan_client.py` | `dhan_client.py` | Cached Dhan access token, auto-refreshed on DH-906/401 |

### 5.5 Frontend build output
- Dashboard SPA source: `web-platform/` → built with `npm run build` → `web-platform/dist/`
  → deployed to `/root/web-app/dist/` on the VPS (nginx web root).
- Custom-screener SPA source: `custom-screener/frontend/` → built separately → deployed to
  `/root/web-app/dist/custom-screener/`.

---

## 6. Secrets / environment variables

Stored in `/root/trade-execution-webhook/.env` on the VPS (root-only, `chmod 600`), loaded
via `EnvironmentFile=` in the systemd units. **Never commit actual values.** Variable names
in use today:

```
DHAN_CLIENT_ID, DHAN_PIN, DHAN_TOTP_SECRET          # Dhan broker auth
TELEGRAM_TOKEN, TELEGRAM_CHAT_ID                     # main Telegram bot
SL_TELEGRAM_TOKEN, SL_TELEGRAM_CHAT_ID               # SL danger-alert Telegram bot
GEMINI_API_KEY, AI_MODEL                             # AI chart analysis
ANTHROPIC_API_KEY                                    # (Claude, if used anywhere server-side)
SPREADSHEET_ID, SERVICE_ACCOUNT_KEY_PATH             # Google Sheet trade log
GITHUB_REPO, GITHUB_TOKEN                            # any server-side git automation
DB_PASSWORD (x2 — market_data + trading_platform)
CAPITAL                                              # position-sizing base capital
SETUP_PIN                                            # PIN for the Trading Protection API-key flow
VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY_PATH, VAPID_CLAIM_EMAIL   # web push (see §8.1)
```

Frontend-side: `localStorage` keys `trading_api_key` (the API key from Settings → Trading
Protection) and `theme` (`light`/`dark`).

---

## 7. Deployment — the correct, safe recipe

### 7.0 Access
Claude's own sandboxed shell (`mcp__workspace__bash`) **cannot reach the VPS or the open
internet** (confirmed 2026-08-01 — DNS, ping, curl, ssh all fail with "network unreachable").
For any VPS/GitHub work, use the **Desktop Commander** MCP (`mcp__Desktop_Commander__start_process`
etc.) — it runs a real shell on Kamal's Mac, which has normal internet access. If Desktop
Commander tools aren't loaded yet, `ToolSearch` for them first.

### 7.1 Backend changes (Python/FastAPI)
1. Edit the file(s) under `web-platform/backend/...` in the local repo (source of truth).
2. `scp` the changed file(s) to **both**:
   - `root@165.232.187.97:/root/trade-execution-webhook/web-platform/backend/...` (repo mirror)
   - `root@165.232.187.97:/root/trade-execution-webhook/web_api/...` (**the actual path
     imported by the live `trade-web-api` systemd service** — `ExecStart=... uvicorn
     web_api.main:app ...`. If you only copy to `web-platform/backend/`, the change is
     **not live** until someone also copies it to `web_api/`.)
3. `ssh root@165.232.187.97 "systemctl restart trade-web-api"` (or `trade-webhook` /
   `custom-screener-api` / `market-data-api` depending which file changed).
4. Verify: `curl -sk -H "Host: ohmstockvault.duckdns.org" https://localhost/api/<endpoint>`
   from the VPS, or hit the public URL.

Root-level scripts (`screen_gpt.py`, `ai_rank_candidates.py`, `entry_engine.py`,
`google_sheets_db.py`, `tick_utils.py`) live at the repo root on both sides — just `scp` to
the matching root path, no dual-copy concern there. `Webhook-app/app.py` similarly deploys
straight to `Webhook-app/app.py` on the VPS, then `systemctl restart trade-webhook`.

### 7.2 Frontend changes (React/Vite)
**Never run `npm run build` directly on the VPS.** It's a 1 vCPU / 961 MB box; a Vite/Rollup
build there can consume most of available RAM+swap and has already triggered the Linux OOM
killer to kill a live Python service mid-build (observed 2026-08-01). The safe recipe:

1. `cd /Users/kamal/IdeaProjects/trade-execution-webhook/web-platform && npm run build`
   (on the Mac — finishes in ~5s there vs. minutes/OOM-risk on the VPS).
2. `rsync -az --delete -e ssh dist/ root@165.232.187.97:/root/trade-execution-webhook/web-platform/dist_new/`
3. **⚠️ CONFIRMED DATA-LOSS BUG (2026-08-01)**: `rsync -a --delete --exclude=custom-screener
   dist_new/ /root/web-app/dist/` **does NOT protect `custom-screener` — it deleted it** on a
   real deploy despite matching the documented pattern. `--exclude` does not reliably stop
   `--delete` from removing the excluded path on this rsync version/setup. Do **not** use
   `--delete` for this copy. Safe recipe instead:
   ```bash
   ssh root@165.232.187.97 '
     rsync -a /root/web-app/dist/custom-screener/ /root/web-app/dist.bak/custom-screener/ &&
     rm -rf /root/web-app/dist/assets && rm -f /root/web-app/dist/index.html &&
     rsync -a /root/trade-execution-webhook/web-platform/dist_new/ /root/web-app/dist/ --exclude=custom-screener &&
     rm -rf /root/trade-execution-webhook/web-platform/dist_new
   '
   ```
   This backs up `custom-screener` first, explicitly removes only the main app's old
   `assets/`+`index.html` (never a blanket `--delete`), then copies the new build in.
4. Verify `custom-screener` survived: `ls /root/web-app/dist/custom-screener` should still
   show `assets/`, `index.html`. If it's gone, restore from `/root/web-app/dist.bak/custom-screener/`
   (a known-good copy from 2026-07-15 lives there as of this writing — keep it fresh via step 3's backup).

### 7.3 Custom-screener sub-app
Frontend: build in `custom-screener/frontend/` locally, deploy to
`/root/web-app/dist/custom-screener/` (same OOM caution as §7.2 — build locally, not on VPS).
Backend: `scp` to `custom-screener/backend/...` on the VPS, `systemctl restart custom-screener-api`.

### 7.4 Sanity checklist after any deploy
```bash
ssh root@165.232.187.97 "free -h"                                    # confirm no memory pressure
ssh root@165.232.187.97 "systemctl is-active trade-web-api trade-webhook custom-screener-api market-data-api nginx postgresql"
curl -sk -H "Host: ohmstockvault.duckdns.org" https://localhost/api/sl-alerts -o /dev/null -w '%{http_code}\n'
curl -sk -H "Host: ohmstockvault.duckdns.org" https://localhost/ -o /dev/null -w '%{http_code}\n'
curl -sk -H "Host: ohmstockvault.duckdns.org" https://localhost/custom-screener/ -o /dev/null -w '%{http_code}\n'
```

---

## 8. Known gaps / risks / TODOs (as of 2026-08-01)

### 8.1 VPS-only files never synced back to git — **data-loss risk**
These exist on the VPS but are **not** in the local repo or GitHub. If the VPS disk were
lost, this functionality would be gone:
- `web_api/routers/push.py` / `web-platform/backend/routers/push.py` — web push notification endpoints
- `push_notify.py`, `sl_danger_monitor.py` (root level) — the SL danger-alert push service (backs `sl-danger-monitor.timer`)
- `vapid_private_key.pem` — **a private key; if pulling this back, do NOT commit it to git, add to `.gitignore` and store like the other secrets**
- `web-platform/public/`, `web-platform/utils/`
- `trade_journal.py` (root level) — backs `trade-journal-reconcile.timer`, also modified but uncommitted on the VPS

**Recommendation**: next session, `scp` these back to the Mac, add the `.py`/`.jsx` files to
git (commit + push), and add `*.pem` to `.gitignore` explicitly.

### 8.2 VPS git history is stale
`git log` on the VPS stops at an older merge commit and doesn't include recent local commits
(e.g. the AI dual-picks work, chart-modal fixes). This is expected given deploys go via
`scp`, not `git pull` — just don't use the VPS's git state to reason about what's deployed;
check files directly (§7.4).

### 8.3 Proposed (unbuilt) DB-backed structural SL
Uncommitted in repo root: `STRUCTURAL_SL_DB_FIX.md`, `structural_sl_migration.sql`,
`structural_sl_integration.py`. Proposes adding `structural_sl` / `structural_sl_source`
columns to `sl_positions`/`user_trades` and making the DB the source of truth instead of the
JSON-file/Sheet fallback chain. **Note the doc has the wrong DB name** — it says
`trade_execution_platform`, the real database is `trading_platform` (§5.2). The lighter-weight
fix actually shipped today (§5.4, `structural_sl_history.json`) solves the immediate bug
(structural SL lost when a stock ages out of the daily top-3) without a schema migration;
the DB-backed approach remains a reasonable future hardening step if JSON-file fragility
becomes a recurring problem.

### 8.4 Resource ceiling
The VPS is genuinely tight on RAM (961 MB total, routinely 700-900 MB used across 5+ Python
services + Postgres + nginx). Avoid: running builds there, running ad-hoc heavy Python/pandas
scripts there, or piling on more always-on services without checking `free -h` first. If more
headroom is needed, resizing the droplet is the straightforward fix.

---

## 9. Feature log — what's currently implemented

- **Trading-action security**: PIN-gated API key (Settings → Trading Protection). All
  mutating trade endpoints (`/api/buy`, `/api/close-position`, `/api/sl`) require
  `X-API-Key`, verified by `APIKeyMiddleware`.
- **Stop-loss engine, "Tonight's actions" screen**: R-multiple ladder — set initial SL,
  sell-half + trail at +2R (or trail full instead), breakeven at +1R, hard exit if closed
  below structural SL. Shows structural (−1R) and target (+2R) price/% on every row,
  including "nothing to do" rows. Structural SL sourced Sheet → manual override → screener
  history (in that priority order).
- **Position-stacking guard**: both the Telegram buy-confirm flow and `/api/buy` check
  Dhan holdings *and* positions before allowing a buy, blocking a second buy into an
  already-held symbol (root fix for the ASAHIINDIA over-allocation incident).
- **Ownership badges**: recommendations show held/position/resting-forever-order status.
- **Global light/dark theme** across all screens (not just charts), persisted in `localStorage`.
- **Screener universe**: all NSE EQ-series equities (~2,300), SME/T2T ("lot stock") series
  excluded, 3-tier fallback (NSE archive → NIFTY-500 CSV → local Dhan scrip master) for
  resilience against NSE archive outages.
- **Chart modal**: fits one screen (no scroll) at default zoom including volume pane,
  follows app light/dark theme.
- **Quant vs AI dual-picks dashboard**: top-3 by the quant funnel ("📐 Quant Picks") shown
  alongside a Gemini v3 chart-analysis re-ranking of the *same* ~33 candidates
  ("🤖 AI Chart Picks"). AI only re-ranks/analyzes charts — entry/SL/target/position-sizing
  always comes from the quant engine. Runs automatically on every scan (fire-and-forget
  background process so it never blocks/breaks the core scan). Per-stock AI detail panel
  (IFP ratings, base type, extended flag, recommendation, confidence, verdict) available for
  any analyzed stock in either section.
- **Dhan token auto-refresh**: universal request wrapper retries once with a fresh token on
  401/DH-906 across all Dhan calls (holdings, positions, orders, forever orders).
- **Structural SL persistence fix (2026-08-01)**: structural SL now survives a stock aging
  out of the daily top-3 list (`structural_sl_history.json`, upserted every scan) instead of
  silently falling back to the −8% safety level; the SL screen also now correctly labels a
  safety-fallback level as such instead of mislabeling it "Struct".

---

## 10. Quick command reference

```bash
# SSH in
ssh root@165.232.187.97

# Service status / logs
systemctl status trade-web-api
journalctl -u trade-web-api -f

# Restart after a backend deploy
systemctl restart trade-web-api        # dashboard backend (web_api/)
systemctl restart trade-webhook        # Telegram bot / buy execution
systemctl restart custom-screener-api  # custom-screener backend
systemctl restart market-data-api      # OHLCV/charting API

# Manually trigger the nightly screener (normally runs via daily-screener.timer at 18:30 IST)
systemctl start daily-screener

# Check memory before/after any heavy operation
free -h

# Postgres
sudo -u postgres psql -d trading_platform     # SL/portfolio/trades tables
sudo -u postgres psql -d market_data          # OHLCV/indicators (user: market_data_user)
```
