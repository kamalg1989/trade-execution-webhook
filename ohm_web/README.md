# OHM web pages

Two read-mostly web pages on the VPS. They sit beside the BAU pipeline and do not modify it.

| Folder | URL | API port | What it is |
|---|---|---|---|
| `ohm_screener_web/` | `/ohm-screener/` | 8008 | CAN SLIM + OHM screener with charts, chart patterns and a Buy button |
| `ohm_web/` | `/ohm/` | 8009 | The full OHM setup as taught in the course: Today, Weekend review, Watchlist, Focus list, Trade plan + Buy, Positions, Risk |

## Where things live on the VPS
- Code: `/root/ohm_screener_web/`, `/root/ohm_web/`
- Pages served from: `/root/web-app/dist/ohm-screener/index.html`, `/root/web-app/dist/ohm/index.html`
- systemd units: the files in each `deploy/` folder go in `/etc/systemd/system/`
- nginx: `ohm_web/deploy/nginx-locations.conf` is the block inside `/etc/nginx/sites-enabled/trade-platform`

## Nightly jobs
- `ohm-screener-web-earnings.timer`: NSE Integrated Filing results go into `ohm_scr_earnings_if`
- `ohm-screener-web-compute.timer`: writes `ohm_screener_web/data/latest.json`
- `ohm-web-fundamentals.timer`, daily at 21:30: screener.in Coffee-can fields go into `ohm_fundamentals`
- `ohm-web-compute.timer`: writes `ohm_web/data/ohm.json`

## Reused from BAU (imported, never edited)
- `web_api.dhan_client`
- `tick_utils`
- `market_data_setup.api.main.create_svg_chart`
- Trade Web API `/api/buy`, `/api/sl-alerts` and `/api/sl/place-safety`

## Rules reference
Project doc `claude/2026-11-23-OHM-course-setup-as-taught.md`.
