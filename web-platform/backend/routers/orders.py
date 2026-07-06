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
    price: float = 0       # 0 = market order
    stopLoss: float = 0    # 0 = skip SL placement


@router.post("/buy")
async def place_buy_order(order: BuyOrderRequest):
    """
    1. Resolve symbol -> Dhan securityId (local scrip master)
    2. Place BUY (market by default, limit if price given)
    3. Place SL-M SELL at stopLoss (if provided)
    """
    if order.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be positive")
    if order.stopLoss and order.price and order.stopLoss >= order.price:
        raise HTTPException(status_code=400, detail="Stop loss must be below entry price")

    security_id = dhan_client.get_security_id(order.symbol)
    if not security_id:
        raise HTTPException(status_code=404, detail=f"Symbol {order.symbol} not found in scrip master")

    order_type = "LIMIT" if order.price > 0 else "MARKET"
    logger.info(f"Placing BUY {order.symbol} x{order.quantity} ({order_type})")

    buy = dhan_client.place_order(
        security_id=security_id, quantity=order.quantity,
        transaction_type="BUY", order_type=order_type,
        price=order.price, product_type="CNC",
    )
    if not buy.get("success"):
        raise HTTPException(status_code=400, detail=f"Buy failed: {buy.get('error')}")

    sl_result = None
    if order.stopLoss > 0:
        sl_result = dhan_client.place_order(
            security_id=security_id, quantity=order.quantity,
            transaction_type="SELL", order_type="STOP_LOSS_MARKET",
            trigger_price=order.stopLoss, product_type="CNC",
        )
        if not sl_result.get("success"):
            logger.warning(f"SL placement failed: {sl_result.get('error')}")

    return {
        "success": True,
        "orderId": buy.get("orderId"),
        "orderStatus": buy.get("orderStatus"),
        "slOrderId": sl_result.get("orderId") if sl_result and sl_result.get("success") else None,
        "slError": sl_result.get("error") if sl_result and not sl_result.get("success") else None,
        "message": f"Buy order placed for {order.symbol} x{order.quantity}"
                   + (f" with SL @ ₹{order.stopLoss}" if sl_result and sl_result.get("success") else ""),
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
