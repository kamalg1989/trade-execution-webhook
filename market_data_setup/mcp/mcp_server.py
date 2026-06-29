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
from mcp.types import (
    Tool,
    TextContent,
    ToolResult,
    CallToolRequest,
)
from mcp.server import Server

# Configuration
API_BASE_URL = "http://localhost:8002"  # Local HTTP wrapper
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
async def call_tool(name: str, arguments: dict) -> ToolResult:
    """Handle tool calls by proxying to HTTP API"""
    try:
        client = await get_http_client()

        # Build API call
        response = await client.post(
            f"{API_BASE_URL}/call",
            params={"tool_name": name},
            json=arguments,
            timeout=60.0
        )

        result = response.json()

        # Extract content from result
        if isinstance(result, dict):
            if result.get("success"):
                content = json.dumps(result.get("data", result), indent=2)
            else:
                content = f"Error: {result.get('error', 'Unknown error')}"
        else:
            content = json.dumps(result, indent=2)

        return ToolResult(
            content=[TextContent(type="text", text=content)],
            isError=not result.get("success", True) if isinstance(result, dict) else False
        )

    except Exception as e:
        return ToolResult(
            content=[TextContent(type="text", text=f"Error calling tool: {str(e)}")],
            isError=True
        )

async def main():
    """Run MCP server"""
    async with server:
        opts = InitializationOptions(server_name="market-data-api", server_version="1.0.0")
        await server.initialize(opts)
        print("Market Data API MCP Server running...", file=sys.stderr)
        await server.wait_for_shutdown()

if __name__ == "__main__":
    asyncio.run(main())
