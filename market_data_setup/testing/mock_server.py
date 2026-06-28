#!/usr/bin/env python3
"""
Mock Market Data API Server - For Local Testing
Simulates the full API without needing PostgreSQL
Useful for testing endpoints and integration before deployment

Usage:
    python market_data_setup/testing/mock_server.py

Then test with:
    curl http://localhost:8000/api/v1/health
    curl http://localhost:8000/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
import uvicorn
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Market Data API (Mock)",
    description="Mock API for testing - uses simulated data",
    version="1.0.0-mock"
)

# ============================================================
# MOCK DATA GENERATOR
# ============================================================

def generate_mock_ohlcv(symbol: str, from_date: date, to_date: date) -> pd.DataFrame:
    """
    Generate realistic mock OHLCV data for testing
    """
    num_days = (to_date - from_date).days + 1
    dates = pd.date_range(start=from_date, end=to_date, freq='D')

    # Generate realistic price movements
    np.random.seed(hash(symbol) % 2**32)  # Reproducible per symbol

    # Base prices for different symbols
    base_prices = {
        'INFY': 1450,
        'TCS': 3800,
        'RELIANCE': 2500,
        'HDFCBANK': 1650,
        'ICICIBANK': 950,
        'SBIN': 550,
        'BHARTIARTL': 1400,
        'WIPRO': 410,
        'AXISBANK': 1050,
        'HINDUNILVR': 2600
    }

    base_price = base_prices.get(symbol, 1000)

    # Simulate price movement with random walk
    returns = np.random.normal(0.0005, 0.02, num_days)  # Realistic daily returns
    prices = base_price * np.exp(np.cumsum(returns))

    # Generate OHLC
    data = []
    for i, date_val in enumerate(dates):
        price = prices[i]

        # Add intraday variation
        open_price = price * (1 + np.random.uniform(-0.01, 0.01))
        close_price = price * (1 + np.random.uniform(-0.01, 0.01))
        high_price = max(open_price, close_price) * (1 + np.random.uniform(0, 0.02))
        low_price = min(open_price, close_price) * (1 - np.random.uniform(0, 0.02))

        # Generate volume (in millions)
        volume = int(np.random.lognormal(15, 0.8))

        data.append({
            'date': date_val,
            'open': round(open_price, 2),
            'high': round(high_price, 2),
            'low': round(low_price, 2),
            'close': round(close_price, 2),
            'volume': volume
        })

    return pd.DataFrame(data)


def generate_mock_indicators(symbol: str, from_date: date, to_date: date) -> pd.DataFrame:
    """Generate mock OHLCV with indicators"""
    df = generate_mock_ohlcv(symbol, from_date, to_date)

    # Calculate EMA
    for period in [10, 21, 50, 200]:
        df[f'ema_{period}'] = df['close'].ewm(span=period).mean().round(2)

    # Calculate RSI
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df['rsi_14'] = (100 - (100 / (1 + rs))).round(2)

    # Calculate ATR
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    tr = np.max(ranges.values, axis=1)
    df['atr'] = pd.Series(tr).rolling(14).mean().round(2)

    return df


# ============================================================
# MOCK SVG CHART GENERATOR
# ============================================================

def create_mock_svg(symbol: str, df: pd.DataFrame, width=1200, height=600) -> str:
    """Generate simple SVG chart from mock data"""

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

    svg = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
        '<rect width="100%" height="100%" fill="#1a1a1a"/>',
        f'<text x="10" y="25" font-size="16" fill="#fff">{symbol} - Daily Chart (Mock Data)</text>',
    ]

    # Grid
    for i in range(10):
        y = padding + (i / 10) * chart_height
        svg.append(f'<line x1="{padding}" y1="{y}" x2="{width-padding}" y2="{y}" stroke="#333" stroke-width="0.5"/>')

    # Candlesticks
    candle_width = chart_width / max(len(df), 1) * 0.6
    for i, (_, row) in enumerate(df.iterrows()):
        x = x_coord(i)
        y_open = y_coord(row['open'])
        y_close = y_coord(row['close'])
        y_high = y_coord(row['high'])
        y_low = y_coord(row['low'])
        color = '#00ff00' if row['close'] > row['open'] else '#ff0000'

        svg.append(f'<line x1="{x}" y1="{y_high}" x2="{x}" y2="{y_low}" stroke="{color}" stroke-width="1"/>')
        svg.append(f'<rect x="{x - candle_width/2}" y="{min(y_open, y_close)}" width="{candle_width}" height="{abs(y_open - y_close) or 1}" fill="{color}"/>')

    # EMA lines
    if 'ema_21' in df.columns:
        points = ' '.join([f"{x_coord(i)},{y_coord(val)}" for i, val in enumerate(df['ema_21']) if pd.notna(val)])
        if points:
            svg.append(f'<polyline points="{points}" fill="none" stroke="#00ff00" stroke-width="2" opacity="0.8"/>')

    svg.append('</svg>')
    return '\n'.join(svg)


# ============================================================
# API ENDPOINTS
# ============================================================

@app.get("/api/v1/health")
async def health_check():
    """Health check"""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "mode": "MOCK (testing only)",
        "database": "simulated"
    }

@app.get("/api/v1/ohlcv")
async def get_ohlcv(
    symbol: str = Query(..., description="NSE symbol"),
    from_date: date = Query(..., description="Start date"),
    to_date: date = Query(..., description="End date")
):
    """Fetch mock OHLCV data"""
    if from_date > to_date:
        raise HTTPException(status_code=400, detail="from_date must be <= to_date")

    symbol = symbol.upper()

    try:
        df = generate_mock_ohlcv(symbol, from_date, to_date)

        return {
            "meta": {
                "symbol": symbol,
                "count": len(df),
                "from": str(from_date),
                "to": str(to_date),
                "mode": "MOCK DATA (for testing)"
            },
            "data": [
                {
                    "date": str(row['date'].date()),
                    "open": float(row['open']),
                    "high": float(row['high']),
                    "low": float(row['low']),
                    "close": float(row['close']),
                    "volume": int(row['volume']),
                    "oi": None
                }
                for _, row in df.iterrows()
            ]
        }
    except Exception as e:
        logger.error(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/ohlcv/multi")
async def get_ohlcv_multi(
    symbols: str = Query(..., description="Comma-separated symbols"),
    from_date: date = Query(...),
    to_date: date = Query(...)
):
    """Fetch mock OHLCV for multiple symbols"""
    symbol_list = [s.strip().upper() for s in symbols.split(",")]

    if len(symbol_list) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 symbols")

    grouped = {}
    for symbol in symbol_list:
        df = generate_mock_ohlcv(symbol, from_date, to_date)
        grouped[symbol] = [
            {
                "date": str(row['date'].date()),
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "volume": int(row['volume'])
            }
            for _, row in df.iterrows()
        ]

    return {
        "meta": {
            "symbols": symbol_list,
            "count": sum(len(v) for v in grouped.values()),
            "from": str(from_date),
            "to": str(to_date),
            "mode": "MOCK DATA"
        },
        "data": grouped
    }

@app.get("/api/v1/symbols")
async def get_symbols(is_active: bool = Query(True)):
    """Get mock symbol list"""
    symbols = [
        {'symbol': 'INFY', 'name': 'Infosys Limited', 'sector': 'IT'},
        {'symbol': 'TCS', 'name': 'Tata Consultancy Services', 'sector': 'IT'},
        {'symbol': 'RELIANCE', 'name': 'Reliance Industries', 'sector': 'Energy'},
        {'symbol': 'HDFCBANK', 'name': 'HDFC Bank', 'sector': 'Banking'},
        {'symbol': 'ICICIBANK', 'name': 'ICICI Bank', 'sector': 'Banking'},
        {'symbol': 'SBIN', 'name': 'State Bank of India', 'sector': 'Banking'},
        {'symbol': 'BHARTIARTL', 'name': 'Bharti Airtel', 'sector': 'Telecom'},
        {'symbol': 'WIPRO', 'name': 'Wipro Limited', 'sector': 'IT'},
        {'symbol': 'AXISBANK', 'name': 'Axis Bank', 'sector': 'Banking'},
        {'symbol': 'HINDUNILVR', 'name': 'Hindustan Unilever', 'sector': 'FMCG'},
    ]

    return {
        "count": len(symbols),
        "data": symbols
    }

@app.get("/api/v1/charts/daily")
async def get_daily_chart(
    symbol: str = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...),
    indicators: str = Query("ema"),
    format: str = Query("svg")
):
    """Generate mock daily chart"""
    symbol = symbol.upper()

    try:
        df = generate_mock_indicators(symbol, from_date, to_date)
        svg = create_mock_svg(symbol, df)

        return StreamingResponse(
            iter([svg]),
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=3600"}
        )
    except Exception as e:
        logger.error(f"Chart error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/charts/weekly")
async def get_weekly_chart(
    symbol: str = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...)
):
    """Generate mock weekly chart"""
    symbol = symbol.upper()

    try:
        df = generate_mock_ohlcv(symbol, from_date, to_date)

        # Aggregate to weekly
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        weekly = df.resample('W-FRI').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
        weekly.reset_index(inplace=True)

        svg = create_mock_svg(symbol, weekly)

        return StreamingResponse(
            iter([svg]),
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"}
        )
    except Exception as e:
        logger.error(f"Chart error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/indicators")
async def get_indicators(
    symbol: str = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...),
    indicators: str = Query("ema,rsi,atr")
):
    """Get mock indicator values"""
    symbol = symbol.upper()

    try:
        df = generate_mock_indicators(symbol, from_date, to_date)

        cols = ['date', 'open', 'high', 'low', 'close', 'volume']
        if 'ema' in indicators:
            cols.extend(['ema_10', 'ema_21', 'ema_50', 'ema_200'])
        if 'rsi' in indicators:
            cols.append('rsi_14')
        if 'atr' in indicators:
            cols.append('atr')

        return {
            "meta": {
                "symbol": symbol,
                "count": len(df),
                "mode": "MOCK DATA"
            },
            "data": df[[c for c in cols if c in df.columns]].to_dict(orient='records')
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def root():
    """API documentation"""
    return {
        "name": "Market Data API (MOCK)",
        "mode": "TESTING - Using simulated data",
        "version": "1.0.0-mock",
        "endpoints": {
            "health": "/api/v1/health",
            "ohlcv": "/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31",
            "ohlcv_multi": "/api/v1/ohlcv/multi?symbols=INFY,TCS&from=2024-01-01&to=2024-12-31",
            "symbols": "/api/v1/symbols",
            "chart_daily": "/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31",
            "chart_weekly": "/api/v1/charts/weekly?symbol=INFY&from=2020-01-01&to=2024-12-31",
            "indicators": "/api/v1/indicators?symbol=INFY&from=2024-01-01&to=2024-12-31",
            "docs": "/docs"
        },
        "test_commands": {
            "health": "curl http://localhost:8000/api/v1/health",
            "ohlcv": "curl 'http://localhost:8000/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31'",
            "chart": "curl 'http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31' > chart.svg",
            "all_symbols": "Available: INFY, TCS, RELIANCE, HDFCBANK, ICICIBANK, SBIN, BHARTIARTL, WIPRO, AXISBANK, HINDUNILVR"
        }
    }

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("\n" + "="*60)
    print("🚀 Market Data API (MOCK - Testing Mode)")
    print("="*60)
    print("\n📊 Mock Server Starting on http://localhost:8000")
    print("\n📋 Available Endpoints:")
    print("   GET /api/v1/health                              - Health check")
    print("   GET /api/v1/ohlcv                               - Single symbol OHLCV")
    print("   GET /api/v1/ohlcv/multi                         - Multiple symbols")
    print("   GET /api/v1/symbols                             - Symbol list")
    print("   GET /api/v1/charts/daily                        - Daily chart")
    print("   GET /api/v1/charts/weekly                       - Weekly chart")
    print("   GET /api/v1/indicators                          - Indicator values")
    print("\n🔗 Test with curl:")
    print('   curl "http://localhost:8000/api/v1/health"')
    print('   curl "http://localhost:8000/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31"')
    print('   curl "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31" > chart.svg')
    print("\n📖 API Docs:")
    print("   http://localhost:8000/docs              - Swagger UI")
    print("   http://localhost:8000/openapi.json      - OpenAPI spec")
    print("\n⚠️  Note: Using MOCK DATA (randomly generated)")
    print("        Not connected to real database")
    print("\n" + "="*60 + "\n")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
