"""
Orders Router — real Dhan buy orders with automatic SL-M placement
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
import logging

import dhan_client

router = APIRouter()
logger = logging.getLogger(__name__)


class BuyOrderRequest(BaseModel):
    symbol: str
    quantity: int
    price: float = 0        # entry / "buy above" trigger price
    stopLoss: float = 0     # informational (SL is placed from the SL tab after fill)


@router.post("/buy")
async def place_buy_order(order: BuyOrderRequest):
    """
    Place a BUY **forever order** (rests until price triggers, persists across
    sessions) — same mechanism as the production entry engine and the SL tab.
    Trigger = entry price ("buy above"); the order sits on Dhan until hit.
    """
    if order.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be positive")

    security_id = dhan_client.get_security_id(order.symbol)
    if not security_id:
        raise HTTPException(status_code=404, detail=f"Symbol {order.symbol} not found in scrip master")

    entry = order.price
    if entry <= 0:
        raise HTTPException(status_code=400, detail="Entry (trigger) price is required for a forever BUY order")

    # Guard against a duplicate resting BUY for the same symbol
    try:
        if dhan_client.has_open_forever_buy(order.symbol):
            raise HTTPException(status_code=409,
                                detail=f"A resting BUY forever order already exists for {order.symbol}")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"duplicate-buy check skipped: {e}")

    logger.info(f"Placing forever BUY {order.symbol} x{order.quantity} @ trigger ₹{entry}")
    buy = dhan_client.place_forever_buy(security_id, order.quantity, entry, order.symbol)
    if not buy.get("success"):
        raise HTTPException(status_code=400, detail=f"Buy failed: {buy.get('error')}")

    return {
        "success": True,
        "orderId": buy.get("orderId"),
        "orderStatus": buy.get("orderStatus"),
        "trigger": buy.get("trigger"),
        "limitPrice": buy.get("price"),
        "message": f"Forever BUY placed for {order.symbol} x{order.quantity} "
                   f"(buy above ₹{buy.get('trigger')}, limit ₹{buy.get('price')}). "
                   f"Set a stop loss from the SL tab once it fills.",
    }


@router.post("/close-position/{security_id}")
async def close_position(security_id: str, payload: dict = None):
    """Cancel active SL orders for the holding, then market-sell the full quantity."""
    try:
        holdings = dhan_client.get_holdings()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)[:120])

    holding = next((h for h in holdings if str(h.get("securityId")) == security_id), None)
    if not holding:
        raise HTTPException(status_code=404, detail="Holding not found")

    qty = int(holding.get("availableQty") or holding.get("totalQty") or 0)
    if qty <= 0:
        raise HTTPException(status_code=400, detail="No sellable quantity")

    # Cancel any active SL orders first (they lock the quantity)
    try:
        for o in dhan_client.get_orders():
            if (str(o.get("securityId")) == security_id
                    and str(o.get("orderType", "")).upper() in ("STOP_LOSS", "STOP_LOSS_MARKET")
                    and str(o.get("orderStatus", "")).upper() in ("PENDING", "TRANSIT", "CONFIRM")):
                dhan_client.cancel_order(o.get("orderId"))
    except Exception as e:
        logger.warning(f"SL cleanup failed: {e}")

    sell = dhan_client.place_order(
        security_id=security_id, quantity=qty,
        transaction_type="SELL", order_type="MARKET", product_type="CNC",
    )
    if not sell.get("success"):
        raise HTTPException(status_code=400, detail=f"Sell failed: {sell.get('error')}")

    return {"success": True, "orderId": sell.get("orderId"),
            "message": f"Sell order placed for {holding.get('tradingSymbol')} x{qty}"}


@router.get("/orders")
async def list_orders():
    """Today's Dhan order book"""
    try:
        return {"orders": dhan_client.get_orders()}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)[:120])
