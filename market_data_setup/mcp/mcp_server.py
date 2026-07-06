#!/usr/bin/env python3
"""
Market Data API - MCP Protocol Server
Implements the Model Context Protocol for Claude integration
"""

import asyncio
import httpx
import json
import sys
from typing import Any

from mcp.server.models import InitializationOptions
from mcp.types import Tool, TextContent
from mcp.server import Server

# Configuration
API_BASE_URL = "https://ohmstockvault.duckdns.org/api/v1"  # Direct API endpoint
http_client = None

# Initialize MCP Server
server = Server("market-data-api")

async def get_http_client():
    global http_client
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=30.0)
    return http_client

# Tool definitions
TOOLS = [
    Tool(
        name="get_health",
        description="Check Market Data API health status",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": []
        }
    ),
    Tool(
        name="get_symbols",
        description="Get list of available NSE stock symbols with metadata",
        inputSchema={
            "type": "object",
            "properties": {
                "sector": {
                    "type": "string",
                    "description": "Optional: Filter by sector (e.g., IT, FINANCE, PHARMA)"
                }
            },
            "required": []
        }
    ),
    Tool(
        name="get_ohlcv",
        description="Fetch OHLCV (Open, High, Low, Close, Volume) data for a single NSE stock",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "NSE stock symbol (e.g., TCS, INFY, RELIANCE)"
                },
                "from_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format"
                },
                "to_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format"
                }
            },
            "required": ["symbol", "from_date", "to_date"]
        }
    ),
    Tool(
        name="get_multi_ohlcv",
        description="Fetch OHLCV data for multiple NSE stocks",
        inputSchema={
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "string",
                    "description": "Comma-separated stock symbols (e.g., TCS,INFY,RELIANCE)"
                },
                "from_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format"
                },
                "to_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format"
                }
            },
            "required": ["symbols", "from_date", "to_date"]
        }
    ),
    Tool(
        name="get_daily_chart",
        description="Generate a daily candlestick chart with technical indicators in SVG format",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "NSE stock symbol"
                },
                "from_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format"
                },
                "to_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format"
                },
                "indicators": {
                    "type": "string",
                    "description": "Technical indicators to display: ema, rsi, atr, macd, all, or none (default: ema)"
                },
                "theme": {
                    "type": "string",
                    "description": "Chart theme: light or dark (default: light)"
                }
            },
            "required": ["symbol", "from_date", "to_date"]
        }
    ),
    Tool(
        name="get_weekly_chart",
        description="Generate a weekly candlestick chart with technical indicators in SVG format",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "NSE stock symbol"
                },
                "from_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format"
                },
                "to_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format"
                },
                "indicators": {
                    "type": "string",
                    "description": "Technical indicators: ema, rsi, atr, macd, all, or none (default: ema)"
                },
                "theme": {
                    "type": "string",
                    "description": "Chart theme: light or dark (default: light)"
                }
            },
            "required": ["symbol", "from_date", "to_date"]
        }
    ),
    Tool(
        name="get_combined_chart",
        description="Generate combined daily and weekly candlestick charts in a single SVG",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "NSE stock symbol"
                },
                "from_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format"
                },
                "to_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format"
                },
                "indicators": {
                    "type": "string",
                    "description": "Technical indicators: ema, rsi, atr, macd, all, or none (default: ema)"
                },
                "theme": {
                    "type": "string",
                    "description": "Chart theme: light or dark (default: light)"
                }
            },
            "required": ["symbol", "from_date", "to_date"]
        }
    ),
]

@server.list_tools()
async def list_tools():
    """List all available tools"""
    return TOOLS

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    """Handle tool calls by proxying to HTTP API"""
    try:
        client = await get_http_client()

        # Map tool names to API endpoints
        endpoint_map = {
            "get_health": ("/health", "GET", {}),
            "get_symbols": ("/symbols", "GET", {"sector": None}),
            "get_ohlcv": ("/ohlcv", "GET", {"symbol", "from_date", "to_date"}),
            "get_multi_ohlcv": ("/ohlcv/multi", "GET", {"symbols", "from_date", "to_date"}),
            "get_daily_chart": ("/charts/daily", "GET", {"symbol", "from_date", "to_date", "indicators", "theme"}),
            "get_weekly_chart": ("/charts/weekly", "GET", {"symbol", "from_date", "to_date", "indicators", "theme"}),
            "get_combined_chart": ("/charts/combined", "GET", {"symbol", "from_date", "to_date", "indicators", "theme"}),
        }

        if name not in endpoint_map:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

        endpoint, method, _ = endpoint_map[name]

        # Build query params from arguments
        params = {k: v for k, v in arguments.items() if v is not None}

        # Make API call
        if method == "GET":
            response = await client.get(
                f"{API_BASE_URL}{endpoint}",
                params=params,
                timeout=60.0
            )
        else:
            response = await client.post(
                f"{API_BASE_URL}{endpoint}",
                json=params,
                timeout=60.0
            )

        # Handle response
        if response.status_code == 200:
            # Check if response is SVG (for charts) or JSON
            content_type = response.headers.get("content-type", "")
            if "svg" in content_type or response.text.strip().startswith("<svg"):
                # Return SVG directly
                content = response.text
            else:
                # Try to parse as JSON
                try:
                    result = response.json()
                    content = json.dumps(result, indent=2)
                except:
                    content = response.text
        else:
            content = f"Error: API returned status {response.status_code}"

        return [TextContent(type="text", text=content)]

    except Exception as e:
        return [TextContent(type="text", text=f"Error calling tool: {str(e)}")]

async def main():
    """Run MCP server"""
    opts = InitializationOptions(
        server_name="market-data-api",
        server_version="1.0.0",
        capabilities={}
    )
    await server.initialize(opts)
    print("Market Data API MCP Server running...", file=sys.stderr)
    await server.wait_for_shutdown()

if __name__ == "__main__":
    asyncio.run(main())
