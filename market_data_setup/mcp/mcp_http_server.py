#!/usr/bin/env python3
"""
Market Data API - MCP HTTP Server
Exposes MCP Protocol via HTTP for Claude web integration
"""

import asyncio
import httpx
import json
import logging
from typing import Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
API_BASE_URL = "http://localhost:8002"
app = FastAPI(title="Market Data API - MCP HTTP Server")

# Tool definitions matching MCP spec
TOOLS = [
    {
        "name": "get_health",
        "description": "Check Market Data API health status",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_symbols",
        "description": "Get list of available NSE stock symbols with metadata",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sector": {"type": "string", "description": "Optional: Filter by sector"}
            },
            "required": []
        }
    },
    {
        "name": "get_ohlcv",
        "description": "Fetch OHLCV data for a single NSE stock",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "NSE stock symbol"},
                "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"}
            },
            "required": ["symbol", "from_date", "to_date"]
        }
    },
    {
        "name": "get_multi_ohlcv",
        "description": "Fetch OHLCV data for multiple NSE stocks",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbols": {"type": "string", "description": "Comma-separated symbols"},
                "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"}
            },
            "required": ["symbols", "from_date", "to_date"]
        }
    },
    {
        "name": "get_daily_chart",
        "description": "Generate daily candlestick chart with technical indicators",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "NSE stock symbol"},
                "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "indicators": {"type": "string", "description": "Indicators (ema, rsi, atr, macd, all, none)"},
                "theme": {"type": "string", "description": "Theme (light or dark)"}
            },
            "required": ["symbol", "from_date", "to_date"]
        }
    },
    {
        "name": "get_weekly_chart",
        "description": "Generate weekly candlestick chart with technical indicators",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "NSE stock symbol"},
                "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "indicators": {"type": "string", "description": "Indicators (ema, rsi, atr, macd, all, none)"},
                "theme": {"type": "string", "description": "Theme (light or dark)"}
            },
            "required": ["symbol", "from_date", "to_date"]
        }
    },
    {
        "name": "get_combined_chart",
        "description": "Generate combined daily and weekly candlestick charts",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "NSE stock symbol"},
                "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "indicators": {"type": "string", "description": "Indicators (ema, rsi, atr, macd, all, none)"},
                "theme": {"type": "string", "description": "Theme (light or dark)"}
            },
            "required": ["symbol", "from_date", "to_date"]
        }
    },
]

@app.post("/mcp/initialize")
async def initialize(request: Request):
    """MCP Initialize request"""
    body = await request.json()
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {}
        },
        "serverInfo": {
            "name": "market-data-api",
            "version": "1.0.0"
        }
    }

@app.post("/mcp/tools/list")
async def list_tools():
    """List available tools"""
    return {
        "tools": TOOLS
    }

@app.post("/mcp/tools/call")
async def call_tool(request: Request):
    """Call a tool"""
    body = await request.json()
    tool_name = body.get("name")
    arguments = body.get("arguments", {})

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{API_BASE_URL}/call",
                params={"tool_name": tool_name},
                json=arguments
            )

            result = response.json()

            if result.get("success"):
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result.get("data", result), indent=2)
                        }
                    ]
                }
            else:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Error: {result.get('error', 'Unknown error')}"
                        }
                    ],
                    "isError": True
                }

    except Exception as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error calling tool: {str(e)}"
                }
            ],
            "isError": True
        }

@app.get("/health")
async def health():
    """Health check"""
    return {"status": "running", "service": "market-data-api-mcp-http"}

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Market Data API - MCP HTTP Server",
        "version": "1.0.0",
        "mcp_endpoints": [
            "POST /mcp/initialize",
            "POST /mcp/tools/list",
            "POST /mcp/tools/call"
        ]
    }

def main():
    print("🚀 Market Data API - MCP HTTP Server starting on port 8003...")
    uvicorn.run(app, host="0.0.0.0", port=8003, log_level="info")

if __name__ == "__main__":
    main()
