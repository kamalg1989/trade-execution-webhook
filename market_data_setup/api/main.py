"""
Market Data API - FastAPI Application
OHLCV queries + Technical charting for NSE stocks
Production-ready with connection pooling and caching
"""

import os
import logging
from datetime import date, datetime, timezone, timedelta
from typing import Optional, List
from pathlib import Path

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import asyncpg
import pandas as pd
import numpy as np
from dotenv import load_dotenv

# Import indicators module from same directory
from .indicators import TechnicalIndicators

# ============================================================
# LOGGING SETUP
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(name)s - %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================
# ENVIRONMENT CONFIGURATION
# ============================================================
# Load .env from multiple locations
env_paths = [
    Path(__file__).parent.parent.parent.parent / '.env',  # Root
    Path('/root/trade-execution-webhook/.env'),  # VPS default
    Path.home() / '.env',  # Home
]

for env_file in env_paths:
    if env_file.exists():
        load_dotenv(env_file)
        logger.info(f"✅ Loaded environment from: {env_file}")
        break

DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = int(os.getenv('DB_PORT', 5432))
DB_USER = os.getenv('DB_USER', 'market_data_user')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME', 'market_data')

if not DB_PASSWORD:
    logger.warning("⚠️ DB_PASSWORD not set in environment")

# ============================================================
# FASTAPI APP INITIALIZATION
# ============================================================
app = FastAPI(
    title="Market Data API",
    description="""
    # Market Data API - NSE Stock Data & Technical Charting

    ## Overview
    OHLCV data + polished candlestick charts for ~2,710 NSE equities with 15 years of
    daily history (2011–present), served as JSON or SVG. Use it directly over HTTP, or
    plug it into Claude as an MCP tool.

    ## What you get
    - **~2,710 NSE stocks**, full NIFTY-500 universe, updated daily at 18:00 IST
    - **Clean SVG charts** (dark/light) with candles, volume, EMA overlays, a stats header
      (LTP, 1-year change, 52-week high/low) and 52-week reference lines
    - **Daily, weekly and combined** chart endpoints
    - **Indicators on demand**: EMA 10/21/50/200, RSI 14, ATR 14, MACD 12/26/9
    - **Batch OHLCV** for multiple symbols in one call
    - **MCP-ready** for Claude Desktop and Claude.ai

    ## Quick Start

    | What | URL |
    |------|-----|
    | **API base** | `https://ohmstockvault.duckdns.org/api/v1/` |
    | **Interactive docs (this page)** | `https://ohmstockvault.duckdns.org/api/v1/docs` |

    Try it now — a daily chart in your browser:
    `https://ohmstockvault.duckdns.org/api/v1/charts/daily?symbol=TCS&from_date=2026-01-01&to_date=2026-07-03&theme=dark`

    ---

    ## 🛠️ API ENDPOINTS (7 Tools)

    All endpoints are served at `https://ohmstockvault.duckdns.org/api/v1/`. Use them
    directly over HTTP in your app, script, or browser.

    ### `get_health` — API status
    Check the API + database are up. No parameters.
    ```bash
    curl "https://ohmstockvault.duckdns.org/api/v1/health"
    ```

    ### `get_symbols` — list NSE stocks
    Optional `sector` filter (IT, FINANCE, PHARMA, AUTO, ENERGY, METALS, BANKS, …).
    ```bash
    curl "https://ohmstockvault.duckdns.org/api/v1/symbols?sector=IT"
    ```

    ### `get_ohlcv` — one stock's candles
    Required: `symbol`, `from_date`, `to_date` (YYYY-MM-DD).
    ```bash
    curl "https://ohmstockvault.duckdns.org/api/v1/ohlcv?symbol=TCS&from_date=2026-01-01&to_date=2026-07-03"
    ```

    ### `get_multi_ohlcv` — several stocks at once
    Required: `symbols` (comma-separated), `from_date`, `to_date`.
    ```bash
    curl "https://ohmstockvault.duckdns.org/api/v1/ohlcv/multi?symbols=TCS,INFY,RELIANCE&from_date=2026-01-01&to_date=2026-07-03"
    ```

    ### `get_daily_chart` — daily candlestick SVG
    Required: `symbol`, `from_date`, `to_date`. Optional: `indicators`
    (`ema` default / `rsi` / `atr` / `macd` / `all` / `none`), `theme` (`dark` / `light`).
    Includes candles, EMA overlays, volume, a stats header (LTP, 1Y %, 52W H/L) and 52-week lines.
    ```bash
    curl "https://ohmstockvault.duckdns.org/api/v1/charts/daily?symbol=TCS&from_date=2026-01-01&to_date=2026-07-03&theme=dark" > tcs_daily.svg
    ```

    ### `get_weekly_chart` — weekly candlestick SVG
    Same parameters as the daily chart.
    ```bash
    curl "https://ohmstockvault.duckdns.org/api/v1/charts/weekly?symbol=INFY&from_date=2024-01-01&to_date=2026-07-03&theme=dark"
    ```

    ### `get_combined_chart` — daily + weekly in one image
    Same parameters as above.
    ```bash
    curl "https://ohmstockvault.duckdns.org/api/v1/charts/combined?symbol=RELIANCE&from_date=2026-01-01&to_date=2026-07-03&theme=dark"
    ```

    ---

    ## 📈 Data specifications

    - **History**: 2011 → present (15+ years of daily candles)
    - **Coverage**: ~2,710 NSE equity symbols (full NIFTY-500 universe included)
    - **Records**: ~5.8 million daily OHLCV rows
    - **Updates**: automatically every day at 18:00 IST (Dhan API v2)
    - **Indicators**: EMA 10/21/50/200, RSI 14, ATR 14, MACD 12/26/9 (computed on demand)

    ---

    ## 📊 TECHNICAL INDICATORS DETAILS

    | Indicator | Default | Purpose | Use Case |
    |-----------|---------|---------|----------|
    | **EMA** | 9/21 | Exponential Moving Average | Trend identification |
    | **RSI** | 14 | Relative Strength Index | Overbought/oversold signals |
    | **ATR** | 14 | Average True Range | Volatility and stop-loss sizing |
    | **MACD** | 12/26/9 | Moving Avg Convergence/Divergence | Momentum and crossover signals |

    ---

    ## 🔌 MCP INTEGRATION (Model Context Protocol)

    **MCP endpoint:** `https://ohmstockvault.duckdns.org/mcp`

    The same 7 API endpoints are also available as **MCP tools** for use in Claude Desktop and Claude.ai.
    Tools include: `get_health`, `get_symbols`, `get_ohlcv`, `get_multi_ohlcv`, `get_daily_chart`,
    `get_weekly_chart`, `get_combined_chart`.

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
    3. Fully quit and reopen Claude Desktop → the 7 tools appear under the tools menu.
    (`mcp-remote` bridges Desktop to the remote server; it needs Node.js, which ships with npx.)

    ### Claude.ai (Web)
    1. **Settings → Connectors → Add custom connector**.
    2. Name: `NSE Market Data`, URL: `https://ohmstockvault.duckdns.org/mcp`, then Save.
    3. Enable it in a chat from the 🔌 menu.
    > The endpoint is served over **HTTPS** with a valid Let's Encrypt certificate.

    ### Example Claude prompts
    - "Show me the daily and weekly candlestick charts for TCS from June 2024 to December 2024 with EMA indicators. Analyze the trend."
    - "Get OHLCV data for IT stocks (TCS, INFY, WIPRO) from Jan-Dec 2024. Which had the best 6-month performance?"
    - "Generate a combined daily+weekly chart for RELIANCE with all technical indicators (EMA, RSI, MACD, ATR). What's the outlook?"
    - "List all FINANCE sector stocks. Get 6-month charts for the top 5 in light theme. Which are in uptrends?"
    - "Backtest data: Get 5 years OHLCV for INFY (2019-2024) for moving average crossover strategy testing."

    ---

    ## Links
    - **Web app**: https://ohmstockvault.duckdns.org/
    - **API docs**: https://ohmstockvault.duckdns.org/api/v1/docs
    - **MCP endpoint**: https://ohmstockvault.duckdns.org/mcp
    - **GitHub**: https://github.com/kamalg1989/trade-execution-webhook
    """,
    version="1.0.0",
    docs_url="/api/v1/docs",
    openapi_url="/api/v1/openapi.json"
)

# Global database connection pool
pool: asyncpg.pool.Pool = None

# ============================================================
# STARTUP / SHUTDOWN EVENTS
# ============================================================

@app.on_event("startup")
async def startup():
    """Initialize database connection pool on app startup"""
    global pool

    try:
        pool = await asyncpg.create_pool(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            min_size=2,
            max_size=8,  # Conservative for 1 GB RAM
            timeout=30
        )
        logger.info(f"✅ Database pool initialized ({DB_HOST}:{DB_PORT})")
    except Exception as e:
        logger.error(f"❌ Failed to initialize database pool: {e}")
        raise

@app.on_event("shutdown")
async def shutdown():
    """Close database connection pool on app shutdown"""
    global pool
    if pool:
        await pool.close()
        logger.info("❌ Database pool closed")

# ============================================================
# HEALTH CHECK ENDPOINT
# ============================================================

@app.get("/api/v1/health")
async def health_check():
    """
    Health check endpoint
    Returns: {'status': 'ok', 'timestamp': ISO8601}
    """
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")

        return {
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "database": "connected"
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail="Database unavailable")

# ============================================================
# OHLCV ENDPOINTS
# ============================================================

@app.get("/api/v1/ohlcv")
async def get_ohlcv(
    symbol: str = Query(..., min_length=1, max_length=20, description="NSE symbol (e.g., INFY)"),
    from_date: date = Query(..., description="Start date (YYYY-MM-DD)"),
    to_date: date = Query(..., description="End date (YYYY-MM-DD)"),
    limit: int = Query(default=10000, ge=1, le=50000, description="Max records to return")
):
    """
    Fetch OHLCV data for a single symbol

    Example: /api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31

    Returns: List of OHLCV candles with metadata
    """
    if from_date > to_date:
        raise HTTPException(
            status_code=400,
            detail="from_date must be <= to_date"
        )

    symbol = symbol.upper()

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    (time AT TIME ZONE 'Asia/Kolkata')::date as trading_date,
                    open, high, low, close, volume, oi
                FROM ohlcv_data
                WHERE symbol = $1
                  AND time AT TIME ZONE 'Asia/Kolkata' BETWEEN $2::date AND ($3::date + INTERVAL '1 day')
                ORDER BY time
                LIMIT $4
            """, symbol, from_date, to_date, limit)

        if not rows:
            raise HTTPException(
                status_code=404,
                detail=f"No data found for symbol {symbol} in date range"
            )

        return {
            "meta": {
                "symbol": symbol,
                "count": len(rows),
                "from": str(from_date),
                "to": str(to_date)
            },
            "data": [
                {
                    "date": str(row['trading_date']),
                    "open": float(row['open']),
                    "high": float(row['high']),
                    "low": float(row['low']),
                    "close": float(row['close']),
                    "volume": int(row['volume']),
                    "oi": int(row['oi']) if row['oi'] else None
                }
                for row in rows
            ]
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Query failed for {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Database query failed")

@app.get("/api/v1/ohlcv/multi")
async def get_ohlcv_multi(
    symbols: str = Query(..., description="Comma-separated symbols (e.g., INFY,TCS,RELIANCE)"),
    from_date: date = Query(..., description="Start date (YYYY-MM-DD)"),
    to_date: date = Query(..., description="End date (YYYY-MM-DD)")
):
    """
    Fetch OHLCV for multiple symbols (optimized for backtesting)

    Example: /api/v1/ohlcv/multi?symbols=INFY,TCS,RELIANCE&from=2024-01-01&to=2024-12-31

    Returns: Dictionary with each symbol's OHLCV candles
    """
    symbol_list = [s.strip().upper() for s in symbols.split(",")]

    if len(symbol_list) > 50:
        raise HTTPException(
            status_code=400,
            detail="Maximum 50 symbols per request"
        )

    if from_date > to_date:
        raise HTTPException(
            status_code=400,
            detail="from_date must be <= to_date"
        )

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    symbol,
                    (time AT TIME ZONE 'Asia/Kolkata')::date as trading_date,
                    open, high, low, close, volume
                FROM ohlcv_data
                WHERE symbol = ANY($1)
                  AND time AT TIME ZONE 'Asia/Kolkata' BETWEEN $2::date AND ($3::date + INTERVAL '1 day')
                ORDER BY symbol, time
            """, symbol_list, from_date, to_date)

        if not rows:
            raise HTTPException(status_code=404, detail="No data found")

        # Group by symbol
        grouped = {}
        for row in rows:
            sym = row['symbol']
            if sym not in grouped:
                grouped[sym] = []
            grouped[sym].append({
                "date": str(row['trading_date']),
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "volume": int(row['volume'])
            })

        return {
            "meta": {
                "symbols": symbol_list,
                "count": len(rows),
                "from": str(from_date),
                "to": str(to_date)
            },
            "data": grouped
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Multi-query failed: {e}")
        raise HTTPException(status_code=500, detail="Database query failed")

@app.get("/api/v1/symbols")
async def get_symbols(
    sector: Optional[str] = Query(None, description="Filter by sector"),
    is_active: bool = Query(True, description="Only active symbols")
):
    """
    Get list of available symbols with metadata

    Example: /api/v1/symbols?sector=IT&is_active=true

    Returns: List of symbols with details
    """
    try:
        async with pool.acquire() as conn:
            if sector:
                rows = await conn.fetch(
                    "SELECT symbol, security_name, sector, isin FROM symbols_meta "
                    "WHERE UPPER(sector) = $1 AND is_active = $2 ORDER BY symbol",
                    sector.upper(), is_active
                )
            else:
                rows = await conn.fetch(
                    "SELECT symbol, security_name, sector, isin FROM symbols_meta "
                    "WHERE is_active = $1 ORDER BY symbol",
                    is_active
                )

        return {
            "count": len(rows),
            "data": [
                {
                    "symbol": row['symbol'],
                    "name": row['security_name'],
                    "sector": row['sector'],
                    "isin": row['isin']
                }
                for row in rows
            ]
        }

    except Exception as e:
        logger.error(f"Symbol query failed: {e}")
        raise HTTPException(status_code=500, detail="Database query failed")

# ============================================================
# CHARTING ENDPOINTS
# ============================================================

def create_svg_chart(
    symbol: str,
    df: pd.DataFrame,
    indicators: dict = None,
    width: int = 1400,
    height: int = 780,
    title_suffix: str = "Daily",
    theme: str = "light",
    stock_name: str = None,
    stats: dict = None,
) -> str:
    """
    Clean SVG candlestick chart: stats header, price/date axes, volume panel,
    EMA overlays + legend, and 52-week high/low reference lines.

    stats (optional): {ltp, chg1y, wk52_high, wk52_low} — rendered as a header row.
    """
    # ---- Theme (TradingView-ish) ----
    if theme == "light":
        bg_color = "#ffffff"; text_color = "#131722"; sub_color = "#787b86"
        grid_color = "#eceff1"; axis_color = "#cfd8dc"
    else:
        bg_color = "#131722"; text_color = "#e6e6e6"; sub_color = "#9aa0a6"
        grid_color = "#242832"; axis_color = "#363a45"
    up_color = "#26a69a"; down_color = "#ef5350"

    if len(df) == 0:
        return "<svg></svg>"

    # ---- Scale factor ----
    # All margins/fonts/strokes below are tuned for the 1400x780 desktop
    # default. When a caller (e.g. the mobile chart popup) requests a much
    # smaller canvas to match its actual on-screen pixel size, everything
    # needs to shrink proportionally too - otherwise a 1400x780-tuned layout
    # crammed into a 380x550 viewBox produces overlapping, illegible text.
    # Scaling by height (not width) keeps text/candle proportions sane even
    # though mobile requests a much taller-relative-to-width aspect ratio
    # than the desktop default.
    s = max(0.45, min(1.4, height / 780))
    # The stats header (LTP/1Y Chg/52W High/52W Low) is laid out inline,
    # top-right, on the same row as the symbol title - that only fits when
    # there's enough absolute width for both. On a narrow mobile-sized
    # request even a height-based font scale doesn't help (fonts would be
    # small enough to fit, but the row is positioned by width math that
    # assumes a wide desktop canvas), so below this threshold the stats
    # move to their own row under the title instead of overlapping/
    # overflowing off-screen.
    is_narrow = width < 700

    def px(v):
        return round(v * s, 1)

    prices = pd.concat([df['open'], df['high'], df['low'], df['close']])
    min_price = float(prices.min()); max_price = float(prices.max())
    _pad = (max_price - min_price) * 0.06 or 1
    min_price -= _pad; max_price += _pad
    price_range = max_price - min_price or 1

    # ---- Layout ----
    # Narrow (mobile) requests get smaller margins than the desktop
    # defaults - the desktop values leave a lot of dead space (wide left
    # gutter for price labels, tall bottom gutter for date labels) that's
    # disproportionately large once everything else has already been
    # scaled down, and that space is better spent on the candles.
    left_margin = px(80) if is_narrow else px(115)
    right_margin = px(20) if is_narrow else px(30)
    if stats:
        top_margin = px(175) if is_narrow else px(100)
    else:
        top_margin = px(64)
    bottom_margin = px(42) if is_narrow else px(62)
    legend_height = px(34)
    chart_width = width - left_margin - right_margin
    chart_height = height - top_margin - bottom_margin - legend_height
    plot_bottom = height - bottom_margin - legend_height

    def x_coord(i):
        return left_margin + (i / max(len(df) - 1, 1)) * chart_width

    def y_coord(price):
        return plot_bottom - ((price - min_price) / price_range) * chart_height

    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    svg = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg" font-family="Arial, sans-serif">',
        f'<rect width="100%" height="100%" fill="{bg_color}"/>',
        f'<text x="{px(20)}" y="{px(34)}" font-size="{px(24)}" font-weight="700" fill="{text_color}">{esc(symbol)}</text>',
        f'<text x="{px(20)}" y="{px(54)}" font-size="{px(13)}" fill="{sub_color}">{esc((stock_name + " · ") if stock_name else "")}{esc(symbol)}.NS · NSE · {esc(title_suffix)}</text>',
    ]

    # ---- Stats header row ----
    if stats:
        items = [
            ("LTP", f"₹{stats['ltp']:.2f}", text_color),
            ("1Y Chg", f"{stats['chg1y']:+.2f}%", up_color if stats['chg1y'] >= 0 else down_color),
            ("52W High", f"₹{stats['wk52_high']:.2f}", text_color),
            ("52W Low", f"₹{stats['wk52_low']:.2f}", text_color),
        ]
        if is_narrow:
            # Own row under the title, evenly spread across the full width -
            # there's no room to sit beside the title on a mobile canvas.
            stats_left = px(20)
            item_w = (width - stats_left - right_margin) / len(items)
            sx = stats_left
            label_y, val_y = px(88), px(108)
            label_size, val_size = px(11), px(15)
        else:
            item_w = px(155)
            sx = width - right_margin - len(items) * item_w
            label_y, val_y = px(30), px(50)
            label_size, val_size = px(12), px(17)
        for label, val, col in items:
            svg.append(f'<text x="{sx}" y="{label_y}" font-size="{label_size}" fill="{sub_color}">{label}</text>')
            svg.append(f'<text x="{sx}" y="{val_y}" font-size="{val_size}" font-weight="700" fill="{col}">{val}</text>')
            sx += item_w

    # ---- Horizontal grid + price labels ----
    for i in range(6):
        price = min_price + (i / 5) * price_range
        y = y_coord(price)
        svg.append(f'<line x1="{left_margin}" y1="{y:.1f}" x2="{width-right_margin}" y2="{y:.1f}" stroke="{grid_color}" stroke-width="{max(1, px(1))}"/>')
        svg.append(f'<text x="{left_margin-px(12)}" y="{y+px(4):.1f}" font-size="{px(13)}" fill="{sub_color}" text-anchor="end">{price:.2f}</text>')

    # ---- X-axis date labels ----
    step = max(1, len(df) // 8)
    try:
        for i in range(0, len(df), step):
            x = x_coord(i)
            date_str = df.index[i].strftime("%d %b %y")
            svg.append(f'<text x="{x:.1f}" y="{plot_bottom+px(22):.1f}" font-size="{px(12)}" fill="{sub_color}" text-anchor="middle">{date_str}</text>')
    except (AttributeError, TypeError):
        pass

    # ---- 52-week high/low reference lines (only if within visible range) ----
    if stats:
        for lvl, lbl, col in [(stats['wk52_high'], "52W High", up_color), (stats['wk52_low'], "52W Low", down_color)]:
            if min_price <= lvl <= max_price:
                y = y_coord(lvl)
                svg.append(f'<line x1="{left_margin}" y1="{y:.1f}" x2="{width-right_margin}" y2="{y:.1f}" stroke="{col}" stroke-width="{max(1, px(1))}" stroke-dasharray="6 4" opacity="0.55"/>')
                svg.append(f'<text x="{width-right_margin-px(4)}" y="{y-px(4):.1f}" font-size="{px(11)}" fill="{col}" text-anchor="end" opacity="0.9">{lbl} {lvl:.2f}</text>')

    # ---- Volume panel (bottom 16%, cleaner) ----
    slot = chart_width / max(len(df), 1)
    body_w = max(1.5, min(slot * 0.7, px(14)))
    if 'volume' in df.columns:
        max_vol = float(df['volume'].max()) or 1
        vol_h = chart_height * 0.16
        vol_base = plot_bottom
        for i, (_, row) in enumerate(df.iterrows()):
            x = x_coord(i)
            bh = (float(row['volume']) / max_vol) * vol_h
            vc = up_color if row['close'] >= row['open'] else down_color
            svg.append(f'<rect x="{x-body_w/2:.1f}" y="{vol_base-bh:.1f}" width="{body_w:.1f}" height="{bh:.1f}" fill="{vc}" opacity="0.32"/>')

    # ---- Candlesticks (bold enough to read on a small mobile canvas) ----
    wick_w = max(1.0, px(1.4))
    for i, (_, row) in enumerate(df.iterrows()):
        x = x_coord(i)
        yo = y_coord(row['open']); yc = y_coord(row['close'])
        yh = y_coord(row['high']); yl = y_coord(row['low'])
        up = row['close'] >= row['open']
        col = up_color if up else down_color
        svg.append(f'<line x1="{x:.1f}" y1="{yh:.1f}" x2="{x:.1f}" y2="{yl:.1f}" stroke="{col}" stroke-width="{wick_w}"/>')
        bh = abs(yo - yc) or 1
        svg.append(f'<rect x="{x-body_w/2:.1f}" y="{min(yo,yc):.1f}" width="{body_w:.1f}" height="{bh:.1f}" fill="{col}" rx="0.5"/>')

    # ---- EMA overlays + legend (top-right, horizontal) ----
    ema_styles = {
        'ema_10': {'color': '#2962ff', 'label': 'EMA 10'},
        'ema_21': {'color': '#ff9800', 'label': 'EMA 21'},
        'ema_50': {'color': '#ab47bc', 'label': 'EMA 50'},
        'ema_200': {'color': '#787b86', 'label': 'EMA 200'},
    }
    if indicators:
        lx = left_margin + px(6)
        ly = top_margin + px(16)
        ema_w = max(1.2, px(1.8))
        for col, style in ema_styles.items():
            if col in df.columns:
                pts = [f"{x_coord(i):.1f},{y_coord(v):.1f}" for i, v in enumerate(df[col]) if not pd.isna(v)]
                if pts:
                    svg.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{style["color"]}" stroke-width="{ema_w}" opacity="0.9"/>')
                    svg.append(f'<line x1="{lx}" y1="{ly}" x2="{lx+px(18)}" y2="{ly}" stroke="{style["color"]}" stroke-width="{max(1.5, px(2.5))}"/>')
                    svg.append(f'<text x="{lx+px(24)}" y="{ly+px(4)}" font-size="{px(13)}" fill="{sub_color}">{style["label"]}</text>')
                    lx += px(92)

    svg.append('</svg>')
    return '\n'.join(svg)


async def compute_symbol_stats(conn, symbol: str):
    """LTP, 1-year % change, 52-week high/low from the last ~52 weeks of data."""
    try:
        rows = await conn.fetch("""
            SELECT high, low, close
            FROM ohlcv_data
            WHERE symbol = $1 AND time > NOW() - INTERVAL '400 days'
            ORDER BY time
        """, symbol)
        if not rows:
            return None
        closes = [float(r['close']) for r in rows]
        highs = [float(r['high']) for r in rows]
        lows = [float(r['low']) for r in rows]
        first = closes[0]
        return {
            "ltp": closes[-1],
            "chg1y": ((closes[-1] - first) / first * 100) if first else 0.0,
            "wk52_high": max(highs),
            "wk52_low": min(lows),
        }
    except Exception:
        return None

@app.get("/api/v1/charts/daily")
async def get_daily_chart(
    symbol: str = Query(..., description="NSE symbol (e.g., INFY, TCS)"),
    from_date: date = Query(..., description="Start date (YYYY-MM-DD)"),
    to_date: date = Query(..., description="End date (YYYY-MM-DD)"),
    indicators: str = Query("ema", regex="^(ema|rsi|atr|macd|all|none)$", description="Indicators: ema, rsi, atr, macd, all, none"),
    format: str = Query("svg", regex="^(svg|json)$", description="Output format: svg or json"),
    theme: str = Query("light", regex="^(light|dark)$", description="Chart theme: light (default) or dark"),
    width: int = Query(1400, ge=300, le=2400, description="SVG viewBox width in px (match caller's display width for crisp text)"),
    height: int = Query(780, ge=300, le=2000, description="SVG viewBox height in px (match caller's display height)")
):
    """
    Generate daily candlestick chart with technical indicators

    Example: /api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31&indicators=ema&format=svg

    Returns: SVG image or JSON with indicator values
    """
    symbol = symbol.upper()

    try:
        async with pool.acquire() as conn:
            # Fetch stock name
            stock_info = await conn.fetchrow(
                "SELECT security_name FROM symbols_meta WHERE symbol = $1",
                symbol
            )
            stock_name = stock_info['security_name'] if stock_info else None

            rows = await conn.fetch("""
                SELECT
                    (time AT TIME ZONE 'Asia/Kolkata')::date as trading_date,
                    open, high, low, close, volume
                FROM ohlcv_data
                WHERE symbol = $1
                  AND time AT TIME ZONE 'Asia/Kolkata' BETWEEN $2::date AND ($3::date + INTERVAL '1 day')
                ORDER BY time
            """, symbol, from_date, to_date)

            stats = await compute_symbol_stats(conn, symbol)

        if not rows:
            raise HTTPException(status_code=404, detail="No data found")

        # Convert to DataFrame
        df = pd.DataFrame([dict(r) for r in rows])

        # Convert Decimal columns to float (from database NUMERIC type)
        for col in ['open', 'high', 'low', 'close']:
            if col in df.columns:
                df[col] = df[col].astype(float)

        # Set index to trading_date for proper date labels in chart
        df['trading_date'] = pd.to_datetime(df['trading_date'])
        df.set_index('trading_date', inplace=True)

        # Calculate indicators (on-demand)
        trading_dates = df.index  # Save dates before TechnicalIndicators resets index
        if indicators != "none":
            tech = TechnicalIndicators(df)

            if "ema" in indicators or indicators == "all":
                tech.calculate_ema([10, 21, 50, 200])
            if "rsi" in indicators or indicators == "all":
                tech.calculate_rsi(14)
            if "atr" in indicators or indicators == "all":
                tech.calculate_atr(14)
            if "macd" in indicators or indicators == "all":
                tech.calculate_macd()

            df = tech.df
            # Restore dates as index (TechnicalIndicators resets it)
            df.index = trading_dates

        # Return format
        if format == "svg":
            calc_indicators = {col: df[col] for col in df.columns if col.startswith('ema_')}
            svg = create_svg_chart(symbol, df, calc_indicators, width=width, height=height, title_suffix="Daily", theme=theme, stock_name=stock_name, stats=stats)
            return StreamingResponse(
                iter([svg]),
                media_type="image/svg+xml",
                headers={"Cache-Control": "public, max-age=3600"}
            )
        else:  # JSON
            cols = ['trading_date', 'open', 'high', 'low', 'close', 'volume']
            if "ema" in indicators or indicators == "all":
                cols.extend(['ema_10', 'ema_21', 'ema_50', 'ema_200'])
            if "rsi" in indicators or indicators == "all":
                cols.append('rsi_14')

            return {
                "meta": {"symbol": symbol, "count": len(df)},
                "data": df[[c for c in cols if c in df.columns]].to_dict(orient='records')
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chart generation failed for {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Chart generation failed")

@app.get("/api/v1/charts/weekly")
async def get_weekly_chart(
    symbol: str = Query(..., description="NSE symbol (e.g., INFY, TCS)"),
    from_date: date = Query(..., description="Start date (YYYY-MM-DD)"),
    to_date: date = Query(..., description="End date (YYYY-MM-DD)"),
    indicators: str = Query("ema", regex="^(ema|rsi|atr|macd|all|none)$", description="Indicators: ema, rsi, atr, macd, all, none"),
    theme: str = Query("light", regex="^(light|dark)$", description="Chart theme: light (default) or dark"),
    width: int = Query(1400, ge=300, le=2400, description="SVG viewBox width in px (match caller's display width for crisp text)"),
    height: int = Query(780, ge=300, le=2000, description="SVG viewBox height in px (match caller's display height)")
):
    """
    Generate weekly candlestick chart (aggregated from daily)

    Example: /api/v1/charts/weekly?symbol=INFY&from=2020-01-01&to=2024-12-31&indicators=ema

    Returns: SVG image with weekly candles and indicators
    """
    symbol = symbol.upper()

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    time AT TIME ZONE 'Asia/Kolkata' as trading_date,
                    open, high, low, close, volume
                FROM ohlcv_data
                WHERE symbol = $1
                  AND time AT TIME ZONE 'Asia/Kolkata' BETWEEN $2::date AND ($3::date + INTERVAL '1 day')
                ORDER BY time
            """, symbol, from_date, to_date)

            stats = await compute_symbol_stats(conn, symbol)
            stock_info = await conn.fetchrow("SELECT security_name FROM symbols_meta WHERE symbol = $1", symbol)
            stock_name = stock_info['security_name'] if stock_info else None

        if not rows:
            raise HTTPException(status_code=404, detail="No data found")

        # Convert to DataFrame
        df = pd.DataFrame([dict(r) for r in rows])

        # Convert Decimal columns to float (from database NUMERIC type)
        for col in ['open', 'high', 'low', 'close']:
            if col in df.columns:
                df[col] = df[col].astype(float)

        df['trading_date'] = pd.to_datetime(df['trading_date'])
        df.set_index('trading_date', inplace=True)

        # Aggregate to weekly (Friday close)
        weekly = df.resample('W-FRI').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()

        weekly.reset_index(inplace=True)
        weekly = weekly.rename(columns={'trading_date': 'date'})

        # Calculate indicators
        if indicators != "none":
            tech = TechnicalIndicators(weekly)

            if "ema" in indicators or indicators == "all":
                tech.calculate_ema([10, 21, 50, 200])
            if "rsi" in indicators or indicators == "all":
                tech.calculate_rsi(14)
            if "atr" in indicators or indicators == "all":
                tech.calculate_atr(14)
            if "macd" in indicators or indicators == "all":
                tech.calculate_macd()

            weekly = tech.df

        # Generate SVG
        # Ensure a datetime index so the chart renders weekly date labels
        if 'date' in weekly.columns:
            weekly = weekly.set_index(pd.to_datetime(weekly['date']))
        calc_indicators = {col: weekly[col] for col in weekly.columns if col.startswith('ema_')}
        svg = create_svg_chart(symbol, weekly, calc_indicators, width=width, height=height, title_suffix="Weekly", theme=theme, stock_name=stock_name, stats=stats)

        return StreamingResponse(
            iter([svg]),
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"}
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Weekly chart failed for {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Chart generation failed")

@app.get("/api/v1/charts/combined")
async def get_combined_charts(
    symbol: str = Query(..., description="NSE symbol (e.g., INFY)"),
    from_date: date = Query(..., description="Start date (YYYY-MM-DD)"),
    to_date: date = Query(..., description="End date (YYYY-MM-DD)"),
    indicators: str = Query("ema", regex="^(ema|rsi|atr|macd|all|none)$", description="Indicators: ema, rsi, atr, macd, all, none"),
    theme: str = Query("light", regex="^(light|dark)$", description="Chart theme: light (default) or dark")
):
    """
    Generate both daily and weekly charts in one request (side-by-side SVG)

    Returns: Combined SVG with daily chart on left, weekly on right
    """
    symbol = symbol.upper()

    try:
        async with pool.acquire() as conn:
            # Fetch stock name
            stock_info = await conn.fetchrow(
                "SELECT security_name FROM symbols_meta WHERE symbol = $1",
                symbol
            )
            stock_name = stock_info['security_name'] if stock_info else None

            rows = await conn.fetch("""
                SELECT
                    (time AT TIME ZONE 'Asia/Kolkata')::date as trading_date,
                    open, high, low, close, volume
                FROM ohlcv_data
                WHERE symbol = $1
                  AND time AT TIME ZONE 'Asia/Kolkata' BETWEEN $2::date AND ($3::date + INTERVAL '1 day')
                ORDER BY time
            """, symbol, from_date, to_date)

            stats = await compute_symbol_stats(conn, symbol)

        if not rows:
            raise HTTPException(status_code=404, detail="No data found")

        # Daily DataFrame
        df_daily = pd.DataFrame([dict(r) for r in rows])
        for col in ['open', 'high', 'low', 'close']:
            if col in df_daily.columns:
                df_daily[col] = df_daily[col].astype(float)
        # Convert and set index to trading_date for proper date labels in chart
        df_daily['trading_date'] = pd.to_datetime(df_daily['trading_date'])
        df_daily.set_index('trading_date', inplace=True)

        # Weekly aggregation (reset index to have trading_date as column)
        df_weekly = df_daily.reset_index()
        df_weekly['trading_date'] = pd.to_datetime(df_weekly['trading_date'])
        df_weekly.set_index('trading_date', inplace=True)

        weekly = df_weekly.resample('W-FRI').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()

        # Calculate indicators
        calc_indicators_daily = {}
        calc_indicators_weekly = {}

        # Save date indices before TechnicalIndicators resets them
        daily_dates = df_daily.index
        weekly_dates = weekly.index

        if indicators != "none":
            # Daily
            tech_daily = TechnicalIndicators(df_daily)
            if "ema" in indicators or indicators == "all":
                tech_daily.calculate_ema([10, 21, 50, 200])
            df_daily = tech_daily.df
            df_daily.index = daily_dates  # Restore dates
            calc_indicators_daily = {col: df_daily[col] for col in df_daily.columns if col.startswith('ema_')}

            # Weekly
            tech_weekly = TechnicalIndicators(weekly)
            if "ema" in indicators or indicators == "all":
                tech_weekly.calculate_ema([10, 21, 50, 200])
            weekly = tech_weekly.df
            weekly.index = weekly_dates  # Restore dates
            calc_indicators_weekly = {col: weekly[col] for col in weekly.columns if col.startswith('ema_')}

        # Theme colors for labels
        if theme == "light":
            bg_color = "#ffffff"
            text_color = "#000000"
            grid_color = "#e0e0e0"
        else:
            bg_color = "#1a1a1a"
            text_color = "#ffffff"
            grid_color = "#333333"

        # Generate SVGs (full width for vertical stacking)
        svg_daily = create_svg_chart(symbol, df_daily, calc_indicators_daily, width=1400, height=550, title_suffix="Daily", theme=theme, stock_name=stock_name)
        svg_weekly = create_svg_chart(symbol, weekly, calc_indicators_weekly, width=1400, height=550, title_suffix="Weekly", theme=theme, stock_name=stock_name)

        # Combine into single SVG (daily on top, weekly on bottom)
        def extract_svg_content(svg_str):
            """Extract content between SVG opening and closing tags"""
            start = svg_str.find('>')
            end = svg_str.rfind('</svg>')
            return svg_str[start+1:end] if start != -1 and end != -1 else ""

        daily_content = extract_svg_content(svg_daily)
        weekly_content = extract_svg_content(svg_weekly)

        # Generate combined SVG with properly sized containers
        combined = f'''<svg width="1400" height="1250" xmlns="http://www.w3.org/2000/svg">
    <rect width="100%" height="100%" fill="{bg_color}"/>
    <text x="20" y="25" font-size="20" font-weight="bold" fill="{text_color}" font-family="Arial">Daily Chart</text>
    <svg x="0" y="30" width="1400" height="580" viewBox="0 0 1400 550">
        {daily_content}
    </svg>
    <line x1="0" y1="610" x2="1400" y2="610" stroke="{grid_color}" stroke-width="2"/>
    <text x="20" y="635" font-size="20" font-weight="bold" fill="{text_color}" font-family="Arial">Weekly Chart</text>
    <svg x="0" y="640" width="1400" height="580" viewBox="0 0 1400 550">
        {weekly_content}
    </svg>
</svg>'''

        return StreamingResponse(
            iter([combined]),
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=3600"}
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Combined chart failed for {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Chart generation failed")

# ============================================================
# INDICATORS ENDPOINT
# ============================================================

@app.get("/api/v1/indicators")
async def get_indicators(
    symbol: str = Query(..., description="NSE symbol"),
    from_date: date = Query(..., description="Start date"),
    to_date: date = Query(..., description="End date"),
    indicators: str = Query("ema,rsi,atr,macd", description="Comma-separated indicators")
):
    """
    Get raw indicator values for programmatic use (backtesting, analysis)

    Example: /api/v1/indicators?symbol=INFY&from=2024-01-01&indicators=ema,rsi,atr

    Returns: JSON with all requested indicator values
    """
    symbol = symbol.upper()

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    (time AT TIME ZONE 'Asia/Kolkata')::date as trading_date,
                    open, high, low, close, volume
                FROM ohlcv_data
                WHERE symbol = $1
                  AND time AT TIME ZONE 'Asia/Kolkata' BETWEEN $2::date AND ($3::date + INTERVAL '1 day')
                ORDER BY time
            """, symbol, from_date, to_date)

        if not rows:
            raise HTTPException(status_code=404, detail="No data found")

        df = pd.DataFrame([dict(r) for r in rows])

        # Calculate requested indicators
        tech = TechnicalIndicators(df)
        indicator_list = [x.strip() for x in indicators.split(",")]

        if "ema" in indicator_list:
            tech.calculate_ema([10, 21, 50, 200])
        if "rsi" in indicator_list:
            tech.calculate_rsi(14)
        if "atr" in indicator_list:
            tech.calculate_atr(14)
        if "macd" in indicator_list:
            tech.calculate_macd()
        if "bb" in indicator_list:
            tech.calculate_bollinger_bands(20, 2)
        if "obv" in indicator_list:
            tech.calculate_obv()

        df = tech.df

        # Prepare columns
        cols = ['trading_date', 'open', 'high', 'low', 'close', 'volume']
        for ind in indicator_list:
            if ind == "ema":
                cols.extend(['ema_10', 'ema_21', 'ema_50', 'ema_200'])
            elif ind == "rsi":
                cols.append('rsi_14')
            elif ind == "atr":
                cols.append('atr')
            elif ind == "macd":
                cols.extend(['macd', 'macd_signal', 'macd_hist'])
            elif ind == "bb":
                cols.extend(['bb_upper', 'bb_middle', 'bb_lower'])
            elif ind == "obv":
                cols.append('obv')

        # Return JSON
        return {
            "meta": {
                "symbol": symbol,
                "count": len(df),
                "indicators": indicator_list,
                "from": str(from_date),
                "to": str(to_date)
            },
            "data": df[[c for c in cols if c in df.columns]].fillna('null').to_dict(orient='records')
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Indicator query failed for {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Indicator calculation failed")

# ============================================================
# ROOT ENDPOINT
# ============================================================

@app.get("/")
async def root():
    """API documentation"""
    return {
        "name": "Market Data API",
        "version": "1.0.0",
        "docs": "/api/v1/docs",
        "endpoints": {
            "health": "/api/v1/health",
            "ohlcv_single": "/api/v1/ohlcv",
            "ohlcv_multi": "/api/v1/ohlcv/multi",
            "symbols": "/api/v1/symbols",
            "chart_daily": "/api/v1/charts/daily",
            "chart_weekly": "/api/v1/charts/weekly",
            "indicators": "/api/v1/indicators"
        }
    }

# ============================================================
# EXCEPTION HANDLERS
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler"""
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )

# ============================================================
# STARTUP MESSAGE
# ============================================================

if __name__ == "__main__":
    import uvicorn
    logger.info("🚀 Starting Market Data API on 0.0.0.0:8000")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        workers=2,
        log_level="info",
        reload=False
    )
