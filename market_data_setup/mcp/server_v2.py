#!/usr/bin/env python3
"""
Market Data API - MCP Server v2 (official FastMCP SDK)

Transports:
  python server_v2.py stdio             -> Claude Desktop (local)
  python server_v2.py streamable-http   -> Claude Web connector (VPS, port 8003, path /mcp)
"""

import sys
import httpx
import resvg_py

from mcp.server.fastmcp import FastMCP, Image

API_BASE_URL = "https://ohmstockvault.duckdns.org/api/v1"

mcp = FastMCP(
    "OHM Stock Vault",
    host="0.0.0.0",
    port=8003,
    streamable_http_path="/mcp",
)


async def _get(endpoint: str, params: dict) -> str:
    """Call the Market Data API and return text (JSON or SVG)."""
    params = {k: v for k, v in params.items() if v is not None}
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(f"{API_BASE_URL}{endpoint}", params=params)
        if resp.status_code != 200:
            return f"Error: API returned {resp.status_code}: {resp.text[:500]}"
        return resp.text


async def _get_chart_png(endpoint: str, params: dict) -> Image:
    """Fetch SVG chart from API, convert to PNG, return as MCP image content
    so the client displays it directly without any processing."""
    svg = await _get(endpoint, params)
    if svg.startswith("Error"):
        raise RuntimeError(svg)
    png_bytes = bytes(resvg_py.svg_to_bytes(svg_string=svg))
    return Image(data=png_bytes, format="png")


@mcp.tool()
async def get_health() -> str:
    """Check Market Data API health status."""
    return await _get("/health", {})


@mcp.tool()
async def get_symbols(search: str = None, limit: int = 50) -> str:
    """Get list of available NSE stock symbols with metadata.

    Args:
        search: Optional search text to filter symbols
        limit: Max number of symbols to return (default 50)
    """
    return await _get("/symbols", {"search": search, "limit": limit})


@mcp.tool()
async def get_ohlcv(symbol: str, from_date: str, to_date: str) -> str:
    """Fetch raw OHLCV daily data (JSON) for one NSE stock.
    ONLY for numeric analysis. DO NOT use this for charts or visualization —
    use get_daily_chart / get_weekly_chart / get_combined_chart instead,
    which return ready-made professional SVG charts.

    Args:
        symbol: NSE stock symbol (e.g., TCS, INFY, RELIANCE)
        from_date: Start date YYYY-MM-DD
        to_date: End date YYYY-MM-DD
    """
    return await _get("/ohlcv", {"symbol": symbol, "from_date": from_date, "to_date": to_date})


@mcp.tool()
async def get_multi_ohlcv(symbols: str, from_date: str, to_date: str) -> str:
    """Fetch OHLCV daily data for multiple NSE stocks.

    Args:
        symbols: Comma-separated NSE symbols (e.g., TCS,INFY,RELIANCE)
        from_date: Start date YYYY-MM-DD
        to_date: End date YYYY-MM-DD
    """
    return await _get("/ohlcv/multi", {"symbols": symbols, "from_date": from_date, "to_date": to_date})


@mcp.tool()
async def get_daily_chart(symbol: str, from_date: str, to_date: str,
                          indicators: str = "ema", theme: str = "light") -> Image:
    """PREFERRED tool for any daily chart request. Returns a ready-made
    candlestick chart IMAGE (PNG) with volume bars and technical indicators,
    rendered by the charting API. The image is final — show it to the user
    as-is. Do NOT fetch OHLCV data or build any chart/HTML/artifact manually.

    Args:
        symbol: NSE stock symbol
        from_date: Start date YYYY-MM-DD
        to_date: End date YYYY-MM-DD
        indicators: ema, rsi, atr, macd, all, or none (default ema)
        theme: light or dark (default light)
    """
    return await _get_chart_png("/charts/daily", {"symbol": symbol, "from_date": from_date,
                                                  "to_date": to_date, "indicators": indicators, "theme": theme})


@mcp.tool()
async def get_weekly_chart(symbol: str, from_date: str, to_date: str,
                           indicators: str = "ema", theme: str = "light") -> Image:
    """PREFERRED tool for any weekly chart request. Returns a ready-made
    candlestick chart IMAGE (PNG) with indicators from the charting API.
    The image is final — show it to the user as-is. Do NOT fetch OHLCV data
    or build any chart/HTML/artifact manually.

    Args:
        symbol: NSE stock symbol
        from_date: Start date YYYY-MM-DD
        to_date: End date YYYY-MM-DD
        indicators: ema, rsi, atr, macd, all, or none (default ema)
        theme: light or dark (default light)
    """
    return await _get_chart_png("/charts/weekly", {"symbol": symbol, "from_date": from_date,
                                                   "to_date": to_date, "indicators": indicators, "theme": theme})


@mcp.tool()
async def get_combined_chart(symbol: str, from_date: str, to_date: str,
                             indicators: str = "ema", theme: str = "light") -> Image:
    """PREFERRED tool for combined daily + weekly analysis. Returns one
    ready-made IMAGE (PNG) containing both charts stacked, from the charting
    API. The image is final — show it to the user as-is. Do NOT fetch OHLCV
    data or build any chart/HTML/artifact manually.

    Args:
        symbol: NSE stock symbol
        from_date: Start date YYYY-MM-DD
        to_date: End date YYYY-MM-DD
        indicators: ema, rsi, atr, macd, all, or none (default ema)
        theme: light or dark (default light)
    """
    return await _get_chart_png("/charts/combined", {"symbol": symbol, "from_date": from_date,
                                                     "to_date": to_date, "indicators": indicators, "theme": theme})


if __name__ == "__main__":
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    mcp.run(transport=transport)
