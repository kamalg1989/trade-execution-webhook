"""
Portfolio Router — real Dhan holdings + trade book
"""

from fastapi import APIRouter, HTTPException
from datetime import datetime
import logging

import dhan_client

router = APIRouter()
logger = logging.getLogger(__name__)


def _holdings_to_positions(holdings):
    positions = []
    for h in holdings:
        qty = int(h.get("totalQty") or h.get("availableQty") or 0)
        if qty <= 0:
            continue
        avg = float(h.get("avgCostPrice") or 0)
        ltp = float(h.get("lastTradedPrice") or 0) or avg
        invested = avg * qty
        value = ltp * qty
        pnl = value - invested
        positions.append({
            "symbol": h.get("tradingSymbol", ""),
            "securityId": str(h.get("securityId", "")),
            "quantity": qty,
            "avgCost": round(avg, 2),
            "currentPrice": round(ltp, 2),
            "totalValue": round(value, 2),
            "value": round(value, 2),
            "pnl": round(pnl, 2),
            "pnlPercent": round((pnl / invested) * 100, 2) if invested else 0.0,
            "returnPercent": round((pnl / invested) * 100, 2) if invested else 0.0,
        })
    return positions


@router.get("/portfolio")
async def get_portfolio(timeframe: str = "1m"):
    """P&L summary from real Dhan holdings"""
    try:
        holdings = dhan_client.get_holdings()
    except Exception as e:
        logger.error(f"Dhan holdings failed: {e}")
        raise HTTPException(status_code=502, detail=f"Dhan API error: {str(e)[:120]}")

    positions = _holdings_to_positions(holdings)
    total_invested = sum(p["avgCost"] * p["quantity"] for p in positions)
    total_value = sum(p["totalValue"] for p in positions)

    return {
        "totalInvested": round(total_invested, 2),
        "totalValue": round(total_value, 2),
        "unrealizedPnL": round(total_value - total_invested, 2),
        "realizedPnL": 0.0,
        "positions": positions,
        "performanceHistory": [],  # populated once snapshots accumulate
        "asOf": datetime.now().isoformat(),
    }


@router.get("/portfolio/full")
async def get_portfolio_full():
    """Holdings + today's executed sell trades"""
    try:
        holdings = dhan_client.get_holdings()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Dhan API error: {str(e)[:120]}")

    positions = _holdings_to_positions(holdings)
    for p in positions:
        p["entryDate"] = datetime.now().isoformat()  # Dhan holdings API doesn't expose buy date

    closed = []
    try:
        trades = dhan_client.get_trades()
        for t in trades:
            if str(t.get("transactionType", "")).upper() == "SELL":
                closed.append({
                    "id": t.get("orderId", ""),
                    "symbol": t.get("tradingSymbol", t.get("customSymbol", "")),
                    "quantity": int(t.get("tradedQuantity") or 0),
                    "entryPrice": 0.0,
                    "exitPrice": float(t.get("tradedPrice") or 0),
                    "pnl": 0.0,
                    "returnPercent": 0.0,
                    "exitDate": t.get("exchangeTime", datetime.now().isoformat()),
                })
    except Exception as e:
        logger.warning(f"Trade book fetch failed (non-critical): {e}")

    return {"holdings": positions, "closedTrades": closed}
