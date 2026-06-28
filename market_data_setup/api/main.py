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
    description="OHLCV queries + Technical charting for NSE stocks",
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
    height: int = 700,
    title_suffix: str = "Daily",
    theme: str = "light"
) -> str:
    """
    Generate SVG candlestick chart with axes, price labels, and legend

    Args:
        theme: "light" or "dark" (default: light)
    """
    # Theme colors
    if theme == "light":
        bg_color = "#ffffff"
        text_color = "#000000"
        grid_color = "#e0e0e0"
        axis_color = "#333333"
        wick_opacity = "0.5"
    else:
        bg_color = "#1a1a1a"
        text_color = "#ffffff"
        grid_color = "#333333"
        axis_color = "#666666"
        wick_opacity = "0.7"
    if len(df) == 0:
        return "<svg></svg>"

    # Get price range
    prices = pd.concat([df['open'], df['high'], df['low'], df['close']])
    min_price = float(prices.min())
    max_price = float(prices.max())
    price_range = max_price - min_price or 1

    # Canvas setup (with margins for axes and legend)
    left_margin = 80
    right_margin = 30
    top_margin = 50
    bottom_margin = 80
    legend_height = 60

    chart_width = width - left_margin - right_margin
    chart_height = height - top_margin - bottom_margin - legend_height

    def x_coord(i):
        return left_margin + (i / max(len(df) - 1, 1)) * chart_width

    def y_coord(price):
        if price_range == 0:
            return height - bottom_margin - legend_height - chart_height / 2
        return height - bottom_margin - legend_height - ((price - min_price) / price_range) * chart_height

    # SVG header
    svg_lines = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
        f'<rect width="100%" height="100%" fill="{bg_color}"/>',
        f'<text x="20" y="30" font-size="18" font-weight="bold" fill="{text_color}" font-family="Arial">{symbol} - {title_suffix}</text>',
    ]

    # Y-axis (prices on left)
    svg_lines.append(f'<line x1="{left_margin}" y1="{top_margin}" x2="{left_margin}" y2="{height-bottom_margin-legend_height}" stroke="{axis_color}" stroke-width="2"/>')

    # Y-axis labels and grid
    for i in range(11):
        price = min_price + (i / 10) * price_range
        y = y_coord(price)
        svg_lines.append(
            f'<line x1="{left_margin-5}" y1="{y}" x2="{left_margin}" y2="{y}" stroke="{axis_color}" stroke-width="1"/>'
        )
        svg_lines.append(
            f'<text x="10" y="{y+5}" font-size="11" fill="{text_color}" font-family="Arial" text-anchor="end">${price:.0f}</text>'
        )
        # Grid lines
        svg_lines.append(
            f'<line x1="{left_margin}" y1="{y}" x2="{width-right_margin}" y2="{y}" stroke="{grid_color}" stroke-width="0.5" opacity="0.5"/>'
        )

    # X-axis (dates at bottom)
    svg_lines.append(f'<line x1="{left_margin}" y1="{height-bottom_margin-legend_height}" x2="{width-right_margin}" y2="{height-bottom_margin-legend_height}" stroke="{axis_color}" stroke-width="2"/>')

    # X-axis labels (show every Nth date)
    step = max(1, len(df) // 8)  # Show ~8 dates
    if hasattr(df, 'index') and hasattr(df.index[0], 'strftime'):
        for i in range(0, len(df), step):
            x = x_coord(i)
            date_str = df.index[i].strftime("%m/%d")
            svg_lines.append(
                f'<text x="{x}" y="{height-bottom_margin-legend_height+20}" font-size="11" fill="{text_color}" font-family="Arial" text-anchor="middle">{date_str}</text>'
            )

    # Draw grid
    for i in range(10):
        y = top_margin + (i / 10) * chart_height
        svg_lines.append(
            f'<line x1="{left_margin}" y1="{y}" x2="{width-right_margin}" y2="{y}" '
            f'stroke="{grid_color}" stroke-width="0.5"/>'
        )

    # Draw candlesticks
    candle_width = chart_width / max(len(df), 1) * 0.6
    for i, (_, row) in enumerate(df.iterrows()):
        x = x_coord(i)
        y_open = y_coord(row['open'])
        y_close = y_coord(row['close'])
        y_high = y_coord(row['high'])
        y_low = y_coord(row['low'])
        color = '#00ff00' if row['close'] > row['open'] else '#ff0000'

        # Wick
        svg_lines.append(
            f'<line x1="{x}" y1="{y_high}" x2="{x}" y2="{y_low}" '
            f'stroke="{color}" stroke-width="1" opacity="0.7"/>'
        )

        # Body
        body_height = abs(y_open - y_close) or 1
        svg_lines.append(
            f'<rect x="{x - candle_width/2}" y="{min(y_open, y_close)}" '
            f'width="{candle_width}" height="{body_height}" '
            f'fill="{color}" stroke="{color}" stroke-width="0.5"/>'
        )

    # Draw EMAs (if present)
    ema_styles = {
        'ema_10': {'color': '#0066ff', 'width': 2, 'label': 'EMA-10'},
        'ema_21': {'color': '#00ff00', 'width': 2, 'label': 'EMA-21'},
        'ema_50': {'color': '#ffaa00', 'width': 2, 'label': 'EMA-50'},
        'ema_200': {'color': '#ff0000', 'width': 2, 'label': 'EMA-200'}
    }

    legend_x = left_margin + 20
    legend_y = height - bottom_margin - legend_height + 20

    if indicators:
        for col, style in ema_styles.items():
            if col in df.columns:
                points = []
                for i, val in enumerate(df[col]):
                    if not pd.isna(val):
                        points.append(f"{x_coord(i)},{y_coord(val)}")

                if points:
                    points_str = ' '.join(points)
                    svg_lines.append(
                        f'<polyline points="{points_str}" fill="none" '
                        f'stroke="{style["color"]}" stroke-width="{style["width"]}" opacity="0.8"/>'
                    )

                    # Legend entry
                    svg_lines.append(f'<line x1="{legend_x}" y1="{legend_y}" x2="{legend_x+20}" y2="{legend_y}" stroke="{style["color"]}" stroke-width="2"/>')
                    svg_lines.append(f'<text x="{legend_x+30}" y="{legend_y+4}" font-size="12" fill="{text_color}" font-family="Arial">{style["label"]}</text>')
                    legend_x += 130

    # Close SVG
    svg_lines.append('</svg>')

    return '\n'.join(svg_lines)

@app.get("/api/v1/charts/daily")
async def get_daily_chart(
    symbol: str = Query(..., description="NSE symbol (e.g., INFY, TCS)"),
    from_date: date = Query(..., description="Start date (YYYY-MM-DD)"),
    to_date: date = Query(..., description="End date (YYYY-MM-DD)"),
    indicators: str = Query("ema", regex="^(ema|rsi|atr|macd|all|none)$", description="Indicators: ema, rsi, atr, macd, all, none"),
    format: str = Query("svg", regex="^(svg|json)$", description="Output format: svg or json"),
    theme: str = Query("light", regex="^(light|dark)$", description="Chart theme: light (default) or dark")
):
    """
    Generate daily candlestick chart with technical indicators

    Example: /api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31&indicators=ema&format=svg

    Returns: SVG image or JSON with indicator values
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

        # Convert to DataFrame
        df = pd.DataFrame([dict(r) for r in rows])

        # Convert Decimal columns to float (from database NUMERIC type)
        for col in ['open', 'high', 'low', 'close']:
            if col in df.columns:
                df[col] = df[col].astype(float)

        # Calculate indicators (on-demand)
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

        # Return format
        if format == "svg":
            calc_indicators = {col: df[col] for col in df.columns if col.startswith('ema_')}
            svg = create_svg_chart(symbol, df, calc_indicators, title_suffix="Daily", theme=theme)
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
    theme: str = Query("light", regex="^(light|dark)$", description="Chart theme: light (default) or dark")
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
        calc_indicators = {col: weekly[col] for col in weekly.columns if col.startswith('ema_')}
        svg = create_svg_chart(symbol, weekly, calc_indicators, title_suffix="Weekly", theme=theme)

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

        # Daily DataFrame
        df_daily = pd.DataFrame([dict(r) for r in rows])
        for col in ['open', 'high', 'low', 'close']:
            if col in df_daily.columns:
                df_daily[col] = df_daily[col].astype(float)

        # Weekly aggregation
        df_weekly = df_daily.copy()
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

        if indicators != "none":
            # Daily
            tech_daily = TechnicalIndicators(df_daily)
            if "ema" in indicators or indicators == "all":
                tech_daily.calculate_ema([10, 21, 50, 200])
            df_daily = tech_daily.df
            calc_indicators_daily = {col: df_daily[col] for col in df_daily.columns if col.startswith('ema_')}

            # Weekly
            tech_weekly = TechnicalIndicators(weekly)
            if "ema" in indicators or indicators == "all":
                tech_weekly.calculate_ema([10, 21, 50, 200])
            weekly = tech_weekly.df
            calc_indicators_weekly = {col: weekly[col] for col in weekly.columns if col.startswith('ema_')}

        # Theme colors for labels
        if theme == "light":
            bg_color = "#ffffff"
            text_color = "#000000"
        else:
            bg_color = "#1a1a1a"
            text_color = "#ffffff"

        # Generate SVGs (full width for vertical stacking)
        svg_daily = create_svg_chart(symbol, df_daily, calc_indicators_daily, width=1400, height=550, title_suffix="Daily", theme=theme)
        svg_weekly = create_svg_chart(symbol, weekly, calc_indicators_weekly, width=1400, height=550, title_suffix="Weekly", theme=theme)

        # Combine into one SVG (vertical layout - Daily on top, Weekly on bottom)
        combined = f'''<svg width="1400" height="1150" xmlns="http://www.w3.org/2000/svg">
            <rect width="100%" height="100%" fill="{bg_color}"/>
            <text x="20" y="25" font-size="20" font-weight="bold" fill="{text_color}" font-family="Arial">Daily Chart</text>
            <g transform="translate(0,0)">{svg_daily.replace('<svg width="1400" height="550"', '<svg width="1400" height="550"').replace('</svg>', '')}</g>
            <line x1="0" y1="580" x2="1400" y2="580" stroke="#cccccc" stroke-width="1"/>
            <text x="20" y="610" font-size="20" font-weight="bold" fill="{text_color}" font-family="Arial">Weekly Chart</text>
            <g transform="translate(0,600)">{svg_weekly.replace('<svg width="1400" height="550"', '<svg width="1400" height="550"').replace('</svg>', '')}</g>
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
