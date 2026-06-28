#!/usr/bin/env python3
"""
Market Data API - MCP Server
Exposes all market data endpoints as Claude-compatible tools
"""

import asyncio
import httpx
import json
from datetime import datetime, timedelta
from typing import Optional
from mcp.server import Server
from mcp.types import Tool, TextContent, ToolResult

# Configuration
API_BASE_URL = "http://165.232.187.97/api/v1"
SERVER_NAME = "market-data-api"

# Initialize server
server = Server(SERVER_NAME)

# HTTP client
client = None

async def get_http_client():
    global client
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
    return client

# ============================================================
# TOOL DEFINITIONS
# ============================================================

TOOLS = [
    {
        "name": "get_ohlcv",
        "description": "Fetch OHLCV (Open, High, Low, Close, Volume) data for a single NSE symbol",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "NSE symbol (e.g., INFY, TCS, RELIANCE)"},
                "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"}
            },
            "required": ["symbol", "from_date", "to_date"]
        }
    },
    {
        "name": "get_multi_ohlcv",
        "description": "Fetch OHLCV data for multiple NSE symbols",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbols": {"type": "string", "description": "Comma-separated symbols (e.g., INFY,TCS,RELIANCE)"},
                "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"}
            },
            "required": ["symbols", "from_date", "to_date"]
        }
    },
    {
        "name": "get_symbols",
        "description": "Get list of available NSE symbols with metadata",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sector": {"type": "string", "description": "Optional: Filter by sector (e.g., IT, FINANCE)"}
            },
            "required": []
        }
    },
    {
        "name": "get_daily_chart",
        "description": "Generate daily candlestick chart with technical indicators (SVG)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "NSE symbol"},
                "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "indicators": {
                    "type": "string",
                    "description": "Indicators to show: ema, rsi, atr, macd, all, or none (default: ema)",
                    "default": "ema"
                },
                "theme": {
                    "type": "string",
                    "description": "Chart theme: light or dark (default: light)",
                    "enum": ["light", "dark"],
                    "default": "light"
                }
            },
            "required": ["symbol", "from_date", "to_date"]
        }
    },
    {
        "name": "get_weekly_chart",
        "description": "Generate weekly candlestick chart with technical indicators (SVG)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "NSE symbol"},
                "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "indicators": {
                    "type": "string",
                    "description": "Indicators: ema, rsi, atr, macd, all, or none (default: ema)",
                    "default": "ema"
                },
                "theme": {
                    "type": "string",
                    "description": "Chart theme: light or dark (default: light)",
                    "enum": ["light", "dark"],
                    "default": "light"
                }
            },
            "required": ["symbol", "from_date", "to_date"]
        }
    },
    {
        "name": "get_combined_chart",
        "description": "Generate combined daily + weekly charts (SVG, vertically stacked)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "NSE symbol"},
                "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "indicators": {
                    "type": "string",
                    "description": "Indicators: ema, rsi, atr, macd, all, or none (default: ema)",
                    "default": "ema"
                },
                "theme": {
                    "type": "string",
                    "description": "Chart theme: light or dark (default: light)",
                    "enum": ["light", "dark"],
                    "default": "light"
                }
            },
            "required": ["symbol", "from_date", "to_date"]
        }
    },
    {
        "name": "get_health",
        "description": "Check API health status",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]

# ============================================================
# TOOL HANDLERS
# ============================================================

async def handle_get_ohlcv(symbol: str, from_date: str, to_date: str) -> str:
    """Fetch OHLCV data"""
    try:
        http_client = await get_http_client()
        response = await http_client.get(
            f"{API_BASE_URL}/ohlcv",
            params={"symbol": symbol, "from_date": from_date, "to_date": to_date}
        )
        return json.dumps(response.json(), indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

async def handle_get_multi_ohlcv(symbols: str, from_date: str, to_date: str) -> str:
    """Fetch multi-symbol OHLCV data"""
    try:
        http_client = await get_http_client()
        response = await http_client.get(
            f"{API_BASE_URL}/ohlcv/multi",
            params={"symbols": symbols, "from_date": from_date, "to_date": to_date}
        )
        return json.dumps(response.json(), indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

async def handle_get_symbols(sector: Optional[str] = None) -> str:
    """Get symbol list"""
    try:
        http_client = await get_http_client()
        params = {}
        if sector:
            params["sector"] = sector
        response = await http_client.get(f"{API_BASE_URL}/symbols", params=params)
        return json.dumps(response.json(), indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

async def handle_get_daily_chart(symbol: str, from_date: str, to_date: str,
                                 indicators: str = "ema", theme: str = "light") -> str:
    """Fetch daily chart (SVG)"""
    try:
        http_client = await get_http_client()
        response = await http_client.get(
            f"{API_BASE_URL}/charts/daily",
            params={
                "symbol": symbol,
                "from_date": from_date,
                "to_date": to_date,
                "indicators": indicators,
                "theme": theme,
                "format": "svg"
            }
        )
        if response.headers.get("content-type") == "image/svg+xml":
            return response.text
        return json.dumps(response.json(), indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

async def handle_get_weekly_chart(symbol: str, from_date: str, to_date: str,
                                  indicators: str = "ema", theme: str = "light") -> str:
    """Fetch weekly chart (SVG)"""
    try:
        http_client = await get_http_client()
        response = await http_client.get(
            f"{API_BASE_URL}/charts/weekly",
            params={
                "symbol": symbol,
                "from_date": from_date,
                "to_date": to_date,
                "indicators": indicators,
                "theme": theme,
                "format": "svg"
            }
        )
        if response.headers.get("content-type") == "image/svg+xml":
            return response.text
        return json.dumps(response.json(), indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

async def handle_get_combined_chart(symbol: str, from_date: str, to_date: str,
                                    indicators: str = "ema", theme: str = "light") -> str:
    """Fetch combined chart (SVG)"""
    try:
        http_client = await get_http_client()
        response = await http_client.get(
            f"{API_BASE_URL}/charts/combined",
            params={
                "symbol": symbol,
                "from_date": from_date,
                "to_date": to_date,
                "indicators": indicators,
                "theme": theme
            }
        )
        if response.headers.get("content-type") == "image/svg+xml":
            return response.text
        return json.dumps(response.json(), indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

async def handle_get_health() -> str:
    """Check API health"""
    try:
        http_client = await get_http_client()
        response = await http_client.get(f"{API_BASE_URL}/health")
        return json.dumps(response.json(), indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

# ============================================================
# MCP HANDLERS
# ============================================================

@server.list_tools()
async def list_tools():
    return TOOLS

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> ToolResult:
    """Route tool calls to handlers"""
    try:
        if name == "get_ohlcv":
            result = await handle_get_ohlcv(**arguments)
        elif name == "get_multi_ohlcv":
            result = await handle_get_multi_ohlcv(**arguments)
        elif name == "get_symbols":
            result = await handle_get_symbols(**arguments)
        elif name == "get_daily_chart":
            result = await handle_get_daily_chart(**arguments)
        elif name == "get_weekly_chart":
            result = await handle_get_weekly_chart(**arguments)
        elif name == "get_combined_chart":
            result = await handle_get_combined_chart(**arguments)
        elif name == "get_health":
            result = await handle_get_health()
        else:
            result = f"Unknown tool: {name}"

        return ToolResult(
            content=[TextContent(type="text", text=result)],
            isError=False
        )
    except Exception as e:
        return ToolResult(
            content=[TextContent(type="text", text=f"Error: {str(e)}")],
            isError=True
        )

# ============================================================
# MAIN
# ============================================================

async def main():
    async with server:
        print("Market Data API MCP Server running...")
        await server.wait_for_shutdown()

if __name__ == "__main__":
    asyncio.run(main())
