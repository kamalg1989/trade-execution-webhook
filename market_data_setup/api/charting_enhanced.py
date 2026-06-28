"""
Enhanced Charting Module - Updated main.py charting endpoints
Supports: Single/Multiple symbols, SVG/PNG, Daily/Weekly

Replace the charting sections in main.py with these enhanced functions.
"""

import pandas as pd
import numpy as np
import io
import asyncpg
from datetime import date, datetime
from typing import Optional, List, Dict
from fastapi import HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse
import zipfile
import tempfile
import logging

logger = logging.getLogger(__name__)

# ============================================================
# ENHANCED SVG CHART GENERATION (Same as before, optimized)
# ============================================================

def create_svg_chart(
    symbol: str,
    df: pd.DataFrame,
    indicators: dict = None,
    width: int = 1200,
    height: int = 600
) -> str:
    """Generate lightweight SVG candlestick chart with indicators"""

    if len(df) == 0:
        return "<svg></svg>"

    prices = pd.concat([df['open'], df['high'], df['low'], df['close']])
    min_price = prices.min()
    max_price = prices.max()
    price_range = max_price - min_price or 1

    padding = 50
    chart_width = width - 2 * padding
    chart_height = height - 2 * padding

    def x_coord(i):
        return padding + (i / max(len(df) - 1, 1)) * chart_width

    def y_coord(price):
        if price_range == 0:
            return height - padding - chart_height / 2
        return height - padding - ((price - min_price) / price_range) * chart_height

    svg_lines = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
        '<rect width="100%" height="100%" fill="#1a1a1a"/>',
        f'<text x="10" y="25" font-size="16" fill="#fff" font-family="Arial">{symbol} - Daily</text>',
    ]

    # Grid
    for i in range(10):
        y = padding + (i / 10) * chart_height
        svg_lines.append(
            f'<line x1="{padding}" y1="{y}" x2="{width-padding}" y2="{y}" '
            f'stroke="#333" stroke-width="0.5"/>'
        )

    # Candlesticks
    candle_width = chart_width / max(len(df), 1) * 0.6
    for i, (_, row) in enumerate(df.iterrows()):
        x = x_coord(i)
        y_open = y_coord(row['open'])
        y_close = y_coord(row['close'])
        y_high = y_coord(row['high'])
        y_low = y_coord(row['low'])
        color = '#00ff00' if row['close'] > row['open'] else '#ff0000'

        svg_lines.append(
            f'<line x1="{x}" y1="{y_high}" x2="{x}" y2="{y_low}" '
            f'stroke="{color}" stroke-width="1" opacity="0.7"/>'
        )

        body_height = abs(y_open - y_close) or 1
        svg_lines.append(
            f'<rect x="{x - candle_width/2}" y="{min(y_open, y_close)}" '
            f'width="{candle_width}" height="{body_height}" '
            f'fill="{color}" stroke="{color}" stroke-width="0.5"/>'
        )

    # Indicators
    if indicators:
        ema_styles = {
            'ema_10': {'color': '#0066ff', 'width': 2},
            'ema_21': {'color': '#00ff00', 'width': 2},
            'ema_50': {'color': '#ffaa00', 'width': 2},
            'ema_200': {'color': '#ff0000', 'width': 2}
        }

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

    svg_lines.append('</svg>')
    return '\n'.join(svg_lines)


# ============================================================
# PNG CHART GENERATION (Using mplfinance)
# ============================================================

def create_png_chart(
    symbol: str,
    df: pd.DataFrame,
    indicators_to_plot: List[str] = None,
    width: int = 12,
    height: int = 6
) -> bytes:
    """
    Generate PNG chart using mplfinance
    Returns PNG as bytes
    """
    try:
        import mplfinance as mpf
        from datetime import datetime

        # Prepare data for mplfinance (requires specific format)
        df_plot = df.copy()
        df_plot.index = pd.to_datetime(df_plot['trading_date'])
        df_plot = df_plot[['open', 'high', 'low', 'close', 'volume']]

        # Prepare additional plots (indicators)
        apd = []

        if indicators_to_plot:
            if 'ema_10' in df.columns:
                apd.append(mpf.make_addplot(df['ema_10'], color='blue', width=1.5))
            if 'ema_21' in df.columns:
                apd.append(mpf.make_addplot(df['ema_21'], color='green', width=1.5))
            if 'ema_50' in df.columns:
                apd.append(mpf.make_addplot(df['ema_50'], color='orange', width=1.5))
            if 'ema_200' in df.columns:
                apd.append(mpf.make_addplot(df['ema_200'], color='red', width=1.5))

            if 'rsi_14' in df.columns:
                apd.append(mpf.make_addplot(
                    df['rsi_14'],
                    panel=1,
                    color='purple',
                    secondary_y=False
                ))

        # Style
        style = mpf.make_mpf_style(
            base_mpf_style='charles',
            gridcolor='#444444',
            y_on_right=True
        )

        # Generate PNG to bytes buffer
        buffer = io.BytesIO()

        mpf.plot(
            df_plot,
            type='candle',
            volume=True,
            addplot=apd if apd else None,
            style=style,
            figsize=(width, height),
            title=f"{symbol} - Daily Chart",
            ylabel="Price",
            ylabel_lower="Volume",
            savefig=dict(fname=buffer, dpi=100, pad_inches=0.5)
        )

        buffer.seek(0)
        return buffer.getvalue()

    except ImportError:
        logger.warning("mplfinance not installed, returning SVG instead")
        return None
    except Exception as e:
        logger.error(f"PNG generation failed: {e}")
        return None


# ============================================================
# ENHANCED ENDPOINT: Daily Chart (Single Symbol)
# ============================================================

async def get_daily_chart_enhanced(
    pool: asyncpg.pool.Pool,
    symbol: str = Query(..., description="NSE symbol"),
    from_date: date = Query(..., description="Start date"),
    to_date: date = Query(..., description="End date"),
    indicators: str = Query("ema", regex="^(ema|rsi|atr|macd|bb|all|none)$"),
    format: str = Query("svg", regex="^(svg|png)$"),
    width: int = Query(1200, ge=400, le=2400),
    height: int = Query(600, ge=300, le=1200)
):
    """
    Generate daily chart for single symbol

    Example: /api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31&indicators=ema,rsi&format=png
    """
    symbol = symbol.upper()

    try:
        # Fetch OHLCV
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

        # Calculate indicators
        from .indicators import TechnicalIndicators
        tech = TechnicalIndicators(df)

        if "ema" in indicators or indicators == "all":
            tech.calculate_ema([10, 21, 50, 200])
        if "rsi" in indicators or indicators == "all":
            tech.calculate_rsi(14)
        if "atr" in indicators or indicators == "all":
            tech.calculate_atr(14)
        if "macd" in indicators or indicators == "all":
            tech.calculate_macd()
        if "bb" in indicators or indicators == "all":
            tech.calculate_bollinger_bands()

        df = tech.df

        # Generate chart
        if format == "svg":
            calc_indicators = {col: df[col] for col in df.columns if col.startswith('ema_')}
            svg = create_svg_chart(symbol, df, calc_indicators, width, height)
            return StreamingResponse(
                iter([svg]),
                media_type="image/svg+xml",
                headers={"Cache-Control": "public, max-age=3600"}
            )

        elif format == "png":
            indicator_list = [x.strip() for x in indicators.split(",")]
            png_bytes = create_png_chart(
                symbol, df, indicator_list,
                width=width/100,  # Convert pixels to inches
                height=height/100
            )

            if png_bytes:
                return StreamingResponse(
                    iter([png_bytes]),
                    media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"}
                )
            else:
                # Fallback to SVG if PNG generation fails
                svg = create_svg_chart(symbol, df, None, width, height)
                return StreamingResponse(
                    iter([svg]),
                    media_type="image/svg+xml"
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chart generation failed for {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Chart generation failed")


# ============================================================
# ENHANCED ENDPOINT: Daily Chart (Multiple Symbols)
# ============================================================

async def get_daily_chart_multi_enhanced(
    pool: asyncpg.pool.Pool,
    symbols: str = Query(..., description="Comma-separated symbols"),
    from_date: date = Query(..., description="Start date"),
    to_date: date = Query(..., description="End date"),
    indicators: str = Query("ema", regex="^(ema|rsi|atr|macd|bb|all|none)$"),
    format: str = Query("svg", regex="^(svg|png)$"),
    batch: str = Query(None, regex="^(zip|tar)$"),
    width: int = Query(1200, ge=400, le=2400),
    height: int = Query(600, ge=300, le=1200)
):
    """
    Generate charts for multiple symbols (batch request)

    Example: /api/v1/charts/daily/multi?symbols=INFY,TCS,RELIANCE&from=2024-01-01&format=png&batch=zip

    Returns:
    - SVG: Multipart response (one SVG per symbol)
    - PNG: ZIP archive (one PNG per symbol)
    """
    symbol_list = [s.strip().upper() for s in symbols.split(",")]

    if len(symbol_list) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 symbols per request")

    charts = {}

    try:
        # Generate chart for each symbol (parallel)
        from .indicators import TechnicalIndicators

        for symbol in symbol_list:
            try:
                # Fetch OHLCV
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
                    logger.warning(f"No data for {symbol}")
                    continue

                df = pd.DataFrame([dict(r) for r in rows])

                # Calculate indicators
                tech = TechnicalIndicators(df)
                if "ema" in indicators or indicators == "all":
                    tech.calculate_ema([10, 21, 50, 200])
                if "rsi" in indicators or indicators == "all":
                    tech.calculate_rsi(14)

                df = tech.df

                # Generate chart
                if format == "svg":
                    calc_indicators = {col: df[col] for col in df.columns if col.startswith('ema_')}
                    svg = create_svg_chart(symbol, df, calc_indicators, width, height)
                    charts[symbol] = ('svg', svg)

                elif format == "png":
                    indicator_list = [x.strip() for x in indicators.split(",")]
                    png = create_png_chart(symbol, df, indicator_list, width/100, height/100)
                    if png:
                        charts[symbol] = ('png', png)
                    else:
                        svg = create_svg_chart(symbol, df, None, width, height)
                        charts[symbol] = ('svg', svg)

            except Exception as e:
                logger.error(f"Chart failed for {symbol}: {e}")
                continue

        if not charts:
            raise HTTPException(status_code=404, detail="No charts generated")

        # Return format: Single file, multipart, or batch
        if format == "svg" and not batch:
            # Multipart response (multiple SVGs)
            from starlette.datastructures import MutableHeaders

            async def multipart_generator():
                boundary = "boundary123456789"
                for symbol, (fmt, content) in charts.items():
                    yield f"--{boundary}\r\n".encode()
                    yield f'Content-Type: image/svg+xml\r\n'.encode()
                    yield f'Content-Disposition: inline; filename="{symbol}_daily.svg"\r\n\r\n'.encode()
                    if isinstance(content, str):
                        yield content.encode()
                    else:
                        yield content
                    yield "\r\n".encode()
                yield f"--{boundary}--\r\n".encode()

            return StreamingResponse(
                multipart_generator(),
                media_type="multipart/mixed; boundary=boundary123456789",
                headers={"Cache-Control": "public, max-age=1800"}
            )

        elif batch == "zip":
            # ZIP archive
            zip_buffer = io.BytesIO()

            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for symbol, (fmt, content) in charts.items():
                    filename = f"{symbol}_daily.{fmt}"
                    if isinstance(content, str):
                        zf.writestr(filename, content.encode())
                    else:
                        zf.writestr(filename, content)

                # Add manifest
                manifest = {
                    "generated_at": datetime.now().isoformat(),
                    "symbols": list(charts.keys()),
                    "from_date": str(from_date),
                    "to_date": str(to_date),
                    "format": format,
                    "indicators": indicators
                }
                zf.writestr('manifest.json', str(manifest).encode())

            zip_buffer.seek(0)

            return StreamingResponse(
                iter([zip_buffer.getvalue()]),
                media_type="application/zip",
                headers={
                    "Cache-Control": "public, max-age=1800",
                    "Content-Disposition": f"attachment; filename=charts_{from_date}_to_{to_date}.zip"
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Multi-chart generation failed: {e}")
        raise HTTPException(status_code=500, detail="Chart generation failed")


# ============================================================
# WEEKLY CHART ENDPOINTS (Same pattern as daily)
# ============================================================

async def get_weekly_chart_enhanced(
    pool: asyncpg.pool.Pool,
    symbol: str = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...),
    indicators: str = Query("ema", regex="^(ema|rsi|atr|macd|bb|all|none)$"),
    format: str = Query("svg", regex="^(svg|png)$"),
    width: int = Query(1200, ge=400, le=2400),
    height: int = Query(600, ge=300, le=1200)
):
    """Generate weekly chart (aggregated from daily)"""
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

        df = pd.DataFrame([dict(r) for r in rows])
        df['trading_date'] = pd.to_datetime(df['trading_date'])
        df.set_index('trading_date', inplace=True)

        # Aggregate to weekly
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
        from .indicators import TechnicalIndicators
        tech = TechnicalIndicators(weekly)

        if "ema" in indicators or indicators == "all":
            tech.calculate_ema([10, 21, 50, 200])
        if "rsi" in indicators or indicators == "all":
            tech.calculate_rsi(14)

        weekly = tech.df

        # Generate chart
        if format == "svg":
            svg = create_svg_chart(symbol, weekly, None, width, height)
            return StreamingResponse(
                iter([svg]),
                media_type="image/svg+xml",
                headers={"Cache-Control": "public, max-age=86400"}
            )
        else:
            png = create_png_chart(symbol, weekly, None, width/100, height/100)
            if png:
                return StreamingResponse(iter([png]), media_type="image/png")
            else:
                svg = create_svg_chart(symbol, weekly, None, width, height)
                return StreamingResponse(iter([svg]), media_type="image/svg+xml")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Weekly chart failed: {e}")
        raise HTTPException(status_code=500, detail="Chart generation failed")


# ============================================================
# HOW TO INTEGRATE INTO main.py
# ============================================================

"""
In main.py, replace the existing chart endpoints with these:

@app.get("/api/v1/charts/daily")
async def get_daily_chart(
    symbol: str = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...),
    indicators: str = Query("ema", regex="^(ema|rsi|atr|macd|bb|all|none)$"),
    format: str = Query("svg", regex="^(svg|png)$"),
    width: int = Query(1200, ge=400, le=2400),
    height: int = Query(600, ge=300, le=1200)
):
    return await get_daily_chart_enhanced(pool, symbol, from_date, to_date, indicators, format, width, height)

@app.get("/api/v1/charts/daily/multi")
async def get_daily_chart_multi(
    symbols: str = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...),
    indicators: str = Query("ema", regex="^(ema|rsi|atr|macd|bb|all|none)$"),
    format: str = Query("svg", regex="^(svg|png)$"),
    batch: str = Query(None, regex="^(zip|tar)$"),
    width: int = Query(1200, ge=400, le=2400),
    height: int = Query(600, ge=300, le=1200)
):
    return await get_daily_chart_multi_enhanced(pool, symbols, from_date, to_date, indicators, format, batch, width, height)

@app.get("/api/v1/charts/weekly")
async def get_weekly_chart(
    symbol: str = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...),
    indicators: str = Query("ema", regex="^(ema|rsi|atr|macd|bb|all|none)$"),
    format: str = Query("svg", regex="^(svg|png)$"),
    width: int = Query(1200, ge=400, le=2400),
    height: int = Query(600, ge=300, le=1200)
):
    return await get_weekly_chart_enhanced(pool, symbol, from_date, to_date, indicators, format, width, height)
"""
