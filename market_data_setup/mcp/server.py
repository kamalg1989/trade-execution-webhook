#!/usr/bin/env python3
"""
Market Data API - MCP Server (HTTP Wrapper)
Exposes all market data endpoints as Claude-compatible tools
"""

import asyncio
import httpx
import json
from typing import Optional
from fastapi import FastAPI, Body
from fastapi.responses import JSONResponse
import uvicorn

# Configuration
API_BASE_URL = "http://165.232.187.97/api/v1"
MCP_SERVER_HOST = "0.0.0.0"
MCP_SERVER_PORT = 8002

app = FastAPI(title="Market Data API - MCP Server")

# HTTP client
http_client: Optional[httpx.AsyncClient] = None

async def get_http_client():
    global http_client
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=30.0)
    return http_client

# ============================================================
# TOOL DEFINITIONS (OpenAI tool format for Claude)
# ============================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_ohlcv",
            "description": "Fetch OHLCV (Open, High, Low, Close, Volume) data for a single NSE symbol",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE symbol (e.g., INFY, TCS, RELIANCE)"},
                    "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                    "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"}
                },
                "required": ["symbol", "from_date", "to_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_multi_ohlcv",
            "description": "Fetch OHLCV data for multiple NSE symbols",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbols": {"type": "string", "description": "Comma-separated symbols (e.g., INFY,TCS,RELIANCE)"},
                    "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                    "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"}
                },
                "required": ["symbols", "from_date", "to_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_symbols",
            "description": "Get list of available NSE symbols with metadata",
            "parameters": {
                "type": "object",
                "properties": {
                    "sector": {"type": "string", "description": "Optional: Filter by sector (e.g., IT, FINANCE)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_daily_chart",
            "description": "Generate daily candlestick chart with technical indicators (SVG)",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE symbol"},
                    "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                    "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                    "indicators": {"type": "string", "description": "Indicators: ema, rsi, atr, macd, all, or none (default: ema)"},
                    "theme": {"type": "string", "description": "Chart theme: light or dark (default: light)"}
                },
                "required": ["symbol", "from_date", "to_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_weekly_chart",
            "description": "Generate weekly candlestick chart with technical indicators (SVG)",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE symbol"},
                    "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                    "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                    "indicators": {"type": "string", "description": "Indicators: ema, rsi, atr, macd, all, or none (default: ema)"},
                    "theme": {"type": "string", "description": "Chart theme: light or dark (default: light)"}
                },
                "required": ["symbol", "from_date", "to_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_combined_chart",
            "description": "Generate combined daily + weekly charts (SVG, vertically stacked)",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE symbol"},
                    "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                    "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                    "indicators": {"type": "string", "description": "Indicators: ema, rsi, atr, macd, all, or none (default: ema)"},
                    "theme": {"type": "string", "description": "Chart theme: light or dark (default: light)"}
                },
                "required": ["symbol", "from_date", "to_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_health",
            "description": "Check API health status",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
]

# ============================================================
# TOOL HANDLERS
# ============================================================

async def handle_get_ohlcv(symbol: str, from_date: str, to_date: str) -> dict:
    try:
        client = await get_http_client()
        response = await client.get(
            f"{API_BASE_URL}/ohlcv",
            params={"symbol": symbol, "from_date": from_date, "to_date": to_date}
        )
        return {"success": True, "data": response.json()}
    except Exception as e:
        return {"success": False, "error": str(e)}

async def handle_get_multi_ohlcv(symbols: str, from_date: str, to_date: str) -> dict:
    try:
        client = await get_http_client()
        response = await client.get(
            f"{API_BASE_URL}/ohlcv/multi",
            params={"symbols": symbols, "from_date": from_date, "to_date": to_date}
        )
        return {"success": True, "data": response.json()}
    except Exception as e:
        return {"success": False, "error": str(e)}

async def handle_get_symbols(sector: Optional[str] = None) -> dict:
    try:
        client = await get_http_client()
        params = {}
        if sector:
            params["sector"] = sector
        response = await client.get(f"{API_BASE_URL}/symbols", params=params)
        return {"success": True, "data": response.json()}
    except Exception as e:
        return {"success": False, "error": str(e)}

async def handle_get_daily_chart(symbol: str, from_date: str, to_date: str,
                                 indicators: str = "ema", theme: str = "light") -> dict:
    try:
        client = await get_http_client()
        response = await client.get(
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
            return {"success": True, "data": response.text, "format": "svg"}
        return {"success": True, "data": response.json()}
    except Exception as e:
        return {"success": False, "error": str(e)}

async def handle_get_weekly_chart(symbol: str, from_date: str, to_date: str,
                                  indicators: str = "ema", theme: str = "light") -> dict:
    try:
        client = await get_http_client()
        response = await client.get(
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
            return {"success": True, "data": response.text, "format": "svg"}
        return {"success": True, "data": response.json()}
    except Exception as e:
        return {"success": False, "error": str(e)}

async def handle_get_combined_chart(symbol: str, from_date: str, to_date: str,
                                    indicators: str = "ema", theme: str = "light") -> dict:
    try:
        client = await get_http_client()
        response = await client.get(
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
            return {"success": True, "data": response.text, "format": "svg"}
        return {"success": True, "data": response.json()}
    except Exception as e:
        return {"success": False, "error": str(e)}

async def handle_get_health() -> dict:
    try:
        client = await get_http_client()
        response = await client.get(f"{API_BASE_URL}/health")
        return {"success": True, "data": response.json()}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ============================================================
# API ENDPOINTS
# ============================================================

@app.get("/tools")
async def list_tools():
    """Get all available tools"""
    return {"tools": TOOLS}

@app.post("/call")
async def call_tool(tool_name: str, body: Optional[dict] = Body(None)):
    """Call a tool by name with arguments

    Example:
    POST /call?tool_name=get_daily_chart
    {
        "symbol": "TCS",
        "from_date": "2024-06-01",
        "to_date": "2024-12-31",
        "indicators": "ema",
        "theme": "light"
    }
    """
    handlers = {
        "get_ohlcv": handle_get_ohlcv,
        "get_multi_ohlcv": handle_get_multi_ohlcv,
        "get_symbols": handle_get_symbols,
        "get_daily_chart": handle_get_daily_chart,
        "get_weekly_chart": handle_get_weekly_chart,
        "get_combined_chart": handle_get_combined_chart,
        "get_health": handle_get_health,
    }

    if tool_name not in handlers:
        return {"success": False, "error": f"Unknown tool: {tool_name}"}

    try:
        kwargs = body or {}
        result = await handlers[tool_name](**kwargs)
        return result
    except TypeError as e:
        # Handle missing required parameters
        return {"success": False, "error": f"Missing required parameters: {str(e)}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/health")
async def health():
    """Health check"""
    api_health = await handle_get_health()
    return {"status": "running", "api": api_health}

@app.get("/")
async def root():
    """Root endpoint with info"""
    return {
        "service": "Market Data API - MCP Server",
        "version": "1.0.0",
        "endpoints": [
            "GET /tools - List available tools",
            "POST /call?tool_name=<name> - Call a tool",
            "GET /health - Health check"
        ],
        "docs": "See /tools for available tools"
    }

# ============================================================
# MAIN
# ============================================================

def main():
    print(f"🚀 Market Data API - MCP Server")
    print(f"📍 Listening on {MCP_SERVER_HOST}:{MCP_SERVER_PORT}")
    print(f"📚 Tools available: {len(TOOLS)}")
    print(f"🔗 Public URL: http://165.232.187.97:8002")
    print("")

    uvicorn.run(
        app,
        host=MCP_SERVER_HOST,
        port=MCP_SERVER_PORT,
        log_level="info"
    )

if __name__ == "__main__":
    main()
