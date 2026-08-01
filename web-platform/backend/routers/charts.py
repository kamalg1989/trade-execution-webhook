"""
Charts Router
Proxy to the local Market Data API (port 8001) for chart generation.
- Uses localhost (the public HTTPS domain is unreachable from inside the VPS).
- Strips the .NS suffix (Market Data API stores symbols without it).
- from_date/to_date default to the last ~4 months if not supplied.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from datetime import datetime, timedelta
import httpx
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

MARKET_DATA_API = "http://127.0.0.1:8001/api/v1"


def _clean_symbol(symbol: str) -> str:
    return symbol.replace(".NS", "").replace(".ns", "").strip().upper()


def _dates(from_date: str | None, to_date: str | None):
    to_d = to_date or datetime.now().strftime("%Y-%m-%d")
    from_d = from_date or (datetime.now() - timedelta(days=130)).strftime("%Y-%m-%d")
    return from_d, to_d


async def _proxy_chart(kind: str, symbol: str, from_date, to_date, indicators, theme, width=None, height=None):
    sym = _clean_symbol(symbol)
    from_d, to_d = _dates(from_date, to_date)
    params = {"symbol": sym, "from_date": from_d, "to_date": to_d,
              "indicators": indicators, "theme": theme}
    if width:
        params["width"] = width
    if height:
        params["height"] = height
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.get(f"{MARKET_DATA_API}/charts/{kind}", params=params)
        if r.status_code != 200:
            logger.warning(f"{kind} chart for {sym}: upstream {r.status_code} {r.text[:120]}")
            raise HTTPException(status_code=r.status_code,
                                detail=f"No chart data for {sym}")
        # Return the SVG with caching disabled so refreshes get fresh charts
        return Response(
            content=r.content,
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-cache"},
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Chart generation timeout")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching {kind} chart for {sym}: {e}")
        raise HTTPException(status_code=502, detail=f"Chart service error: {str(e)[:120]}")


@router.get("/charts/daily")
async def get_daily_chart(symbol: str, from_date: str = None, to_date: str = None,
                          indicators: str = "ema", theme: str = "dark",
                          width: int = None, height: int = None):
    return await _proxy_chart("daily", symbol, from_date, to_date, indicators, theme, width, height)


@router.get("/charts/weekly")
async def get_weekly_chart(symbol: str, from_date: str = None, to_date: str = None,
                           indicators: str = "ema", theme: str = "dark",
                           width: int = None, height: int = None):
    return await _proxy_chart("weekly", symbol, from_date, to_date, indicators, theme, width, height)


@router.get("/charts/combined")
async def get_combined_chart(symbol: str, from_date: str = None, to_date: str = None,
                             indicators: str = "ema", theme: str = "dark"):
    return await _proxy_chart("combined", symbol, from_date, to_date, indicators, theme)


@router.get("/indicators/{symbol}")
async def get_indicators(symbol: str, from_date: str = None, to_date: str = None):
    sym = _clean_symbol(symbol)
    from_d, to_d = _dates(from_date, to_date)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(f"{MARKET_DATA_API}/indicators",
                                  params={"symbol": sym, "from_date": from_d, "to_date": to_d})
        if r.status_code == 200:
            return r.json()
        raise HTTPException(status_code=r.status_code, detail="Failed to fetch indicators")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)[:120])
