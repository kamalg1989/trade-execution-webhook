"""
Stop Loss router — reuses the production sl_engine.py (forever-order logic).
Detection reads Dhan FOREVER SELL orders. Actions are button-driven; nothing
is placed/updated automatically here.

Buttons per holding:
  • Place Safety SL (−8%)     -> sl_engine.place_safety_sl
  • Place SL at custom level  -> sl_engine._place_safety_at_level  (pre-filled
                                  with Structural_SL from sheet, and −8%)
  • Structural exit now       -> sl_engine.place_exit_forever
  • Trail SL up               -> sl_engine.trail_safety_sl
  • Cancel SL                 -> sl_engine.cancel_forever_order
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
import logging
import sys

sys.path.insert(0, '/root/trade-execution-webhook')

import dhan_client
import sl_engine

router = APIRouter()
logger = logging.getLogger(__name__)

# Sheet DB is optional — if unavailable, structural SL just won't pre-fill.
try:
    import google_sheets_db as sheet_db
except Exception as e:  # pragma: no cover
    sheet_db = None
    logger.warning(f"google_sheets_db unavailable: {e}")

ACTIVE_STATUSES = ("PENDING", "CONFIRM", "TRANSIT")

SAFETY_PCT = 0.92  # mirrors sl_engine.SAFETY_SL_PCT (−8%)


def _sl_options(entry, ltp, structural_sl):
    """
    Build the suggested-SL dropdown for a position, grounded in the SL-engine
    logic: −8% safety (from buy), −8% trail (from current close), structural
    (from sheet), plus standard %-offsets and breakeven. Each option carries
    its price and % relative to both buy price and current price. Only levels
    strictly below the current price are valid SL triggers.
    """
    opts = []

    def add(label, price, basis):
        if not price or price <= 0 or (ltp > 0 and price >= ltp):
            return
        opts.append({
            "label": label,
            "price": round(price, 1),
            "basis": basis,
            "pctFromEntry": round(((price - entry) / entry) * 100, 1) if entry else None,
            "pctFromCurrent": round(((price - ltp) / ltp) * 100, 1) if ltp else None,
        })

    if entry:
        add("−8% safety (from buy)", entry * SAFETY_PCT, "safety")
    if ltp:
        add("−8% trail (from current)", ltp * SAFETY_PCT, "trail")
    if structural_sl:
        add("Structural SL (sheet)", structural_sl, "structural")
    if ltp:
        add("−5% from current", ltp * 0.95, "pct5")
        add("−10% from current", ltp * 0.90, "pct10")
        add("−12% from current", ltp * 0.88, "pct12")
    if entry and ltp > entry:
        add("Breakeven (buy price)", entry, "breakeven")

    # De-duplicate by price (keep first/most-meaningful label), sort tightest first
    seen, uniq = set(), []
    for o in sorted(opts, key=lambda x: -x["price"]):
        key = o["price"]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(o)
    return uniq


def _forever_sl_map():
    """{securityId: [forever SELL orders]} for active SL orders."""
    out = {}
    for o in sl_engine.get_forever_orders():
        if (str(o.get("transactionType", "")).upper() == "SELL"
                and str(o.get("orderStatus", "")).upper() in ACTIVE_STATUSES):
            out.setdefault(str(o.get("securityId")), []).append(o)
    return out


def _structural_map():
    """{securityId: {structuralSL, entry, target, status}} from the sheet DB."""
    if sheet_db is None:
        return {}
    try:
        trades = sheet_db.get_all_trades()
    except Exception as e:
        logger.warning(f"Sheet read failed: {e}")
        return {}
    m = {}
    for t in trades:
        sec = str(t.get("Security_ID") or "")
        if not sec:
            continue
        m[sec] = {
            "structuralSL": float(t.get("Structural_SL") or 0) or None,
            "entry": float(t.get("Entry_Price") or 0) or None,
            "target": float(t.get("Target_Price") or 0) or None,
            "status": t.get("Status"),
        }
    return m


@router.get("/sl-alerts")
async def get_sl_alerts():
    """Holdings + their FOREVER SL orders + risk zones + suggested SL levels."""
    try:
        holdings = dhan_client.get_holdings()      # has lastTradedPrice
        sl_map = _forever_sl_map()                  # existing forever SLs
        struct = _structural_map()                  # structural SL from sheet
    except Exception as e:
        logger.error(f"SL data fetch failed: {e}")
        raise HTTPException(status_code=502, detail=f"Dhan/sheet error: {str(e)[:140]}")

    positions, alerts = [], []
    for h in holdings:
        qty = int(h.get("totalQty") or h.get("availableQty") or 0)
        if qty <= 0:
            continue
        sec_id = str(h.get("securityId", ""))
        symbol = h.get("tradingSymbol", "")
        avg = float(h.get("avgCostPrice") or 0)
        ltp = float(h.get("lastTradedPrice") or 0) or avg
        pnl = (ltp - avg) * qty

        my_sls = sl_map.get(sec_id, [])
        # Highest trigger = the tightest active protection
        sl_price = max((float(o.get("triggerPrice") or 0) for o in my_sls), default=0.0)
        has_sl = sl_price > 0

        s = struct.get(sec_id, {})
        structural_sl = s.get("structuralSL")
        entry = s.get("entry") or avg   # prefer sheet entry, else avg cost
        safety_sl = round(entry * sl_engine.SAFETY_SL_PCT, 1) if entry else None

        sl_pct_from_entry = round(((sl_price - entry) / entry) * 100, 1) if (has_sl and entry) else None
        sl_options = _sl_options(entry, ltp, structural_sl)

        if has_sl and ltp > 0:
            distance = round(((ltp - sl_price) / ltp) * 100, 2)
            zone = "SAFE" if distance > 10 else "WARNING" if distance > 5 else "CRITICAL"
            if zone == "WARNING":
                alerts.append({"symbol": symbol, "type": "WARNING",
                               "message": f"⚠️ {symbol}: {distance}% above SL ₹{sl_price}"})
            elif zone == "CRITICAL":
                alerts.append({"symbol": symbol, "type": "CRITICAL",
                               "message": f"🚨 {symbol}: only {distance}% above SL ₹{sl_price}"})
        else:
            distance, zone = None, "NO_SL"
            alerts.append({"symbol": symbol, "type": "NO_SL",
                           "message": f"❗ {symbol}: no active stop loss"})

        positions.append({
            "id": sec_id,
            "securityId": sec_id,
            "symbol": symbol,
            "quantity": qty,
            "entry_price": round(entry, 2) if entry else round(avg, 2),
            "buyPrice": round(avg, 2),
            "avgCost": round(avg, 2),
            "current_price": round(ltp, 2),
            "stop_loss": round(sl_price, 2) if has_sl else 0,
            "slPctFromEntry": sl_pct_from_entry,
            "distanceToSL": distance,
            "riskZone": zone,
            "structuralSL": structural_sl,
            "safetySL": safety_sl,
            "hasStructural": structural_sl is not None,
            "slOptions": sl_options,
            "pnl": round(pnl, 2),
            "slOrders": [
                {"orderId": o.get("orderId"),
                 "triggerPrice": float(o.get("triggerPrice") or 0),
                 "price": float(o.get("price") or 0),
                 "quantity": int(o.get("quantity") or 0),
                 "correlationId": o.get("correlationId"),
                 "legName": o.get("legName"),
                 "status": o.get("orderStatus")}
                for o in my_sls
            ],
        })

    return {"positions": positions, "alerts": alerts, "asOf": datetime.now().isoformat()}


# ---------- ACTION MODELS ----------
class PlaceSafetyReq(BaseModel):
    securityId: str
    quantity: int
    symbol: str
    entry: float = 0          # if 0, backend uses avg cost


class PlaceAtLevelReq(BaseModel):
    securityId: str
    quantity: int
    symbol: str
    trigger: float


class StructuralExitReq(BaseModel):
    securityId: str
    quantity: int
    symbol: str
    closePrice: float = 0     # if 0, backend fetches daily close


class TrailReq(BaseModel):
    securityId: str
    symbol: str


class CancelReq(BaseModel):
    orderId: str
    symbol: str


def _entry_for(security_id, fallback):
    s = _structural_map().get(str(security_id), {})
    return s.get("entry") or fallback


@router.post("/sl/place-safety")
async def place_safety(req: PlaceSafetyReq):
    """−8% resting forever SELL (reuses sl_engine.place_safety_sl)."""
    entry = req.entry
    if entry <= 0:
        holdings = dhan_client.get_holdings()
        h = next((x for x in holdings if str(x.get("securityId")) == req.securityId), None)
        entry = float(h.get("avgCostPrice")) if h else 0
    if entry <= 0:
        raise HTTPException(status_code=400, detail="No entry/avg price available")

    ok, order_id, level = sl_engine.place_safety_sl(req.securityId, req.quantity, entry, req.symbol)
    if not ok:
        raise HTTPException(status_code=400, detail="Dhan rejected safety SL order")
    return {"success": True, "orderId": order_id, "trigger": level,
            "message": f"−8% safety SL placed for {req.symbol} @ ₹{level}"}


@router.post("/sl/place-at-level")
async def place_at_level(req: PlaceAtLevelReq):
    """Forever SELL at an explicit trigger (structural / custom). Reuses _place_safety_at_level."""
    if req.trigger <= 0:
        raise HTTPException(status_code=400, detail="Trigger must be positive")
    holdings = dhan_client.get_holdings()
    h = next((x for x in holdings if str(x.get("securityId")) == req.securityId), None)
    if h:
        ltp = float(h.get("lastTradedPrice") or 0)
        if ltp > 0 and req.trigger >= ltp:
            raise HTTPException(status_code=400, detail=f"SL ₹{req.trigger} must be below current ₹{ltp}")

    ok, order_id, level = sl_engine._place_safety_at_level(
        req.securityId, req.quantity, sl_engine._round_down(req.trigger, req.symbol), req.symbol)
    if not ok:
        raise HTTPException(status_code=400, detail="Dhan rejected SL order")
    return {"success": True, "orderId": order_id, "trigger": level,
            "message": f"SL placed for {req.symbol} @ ₹{level}"}


@router.post("/sl/structural-exit")
async def structural_exit(req: StructuralExitReq):
    """Place an exit-forever that fills at next open (reuses place_exit_forever)."""
    close_price = req.closePrice
    if close_price <= 0:
        close_price = sl_engine.get_daily_close(req.securityId, req.symbol) or 0
    if close_price <= 0:
        raise HTTPException(status_code=400, detail="Could not determine close price")

    ok, order_id, trigger = sl_engine.place_exit_forever(
        req.securityId, req.quantity, close_price, req.symbol)
    if not ok:
        raise HTTPException(status_code=400, detail="Dhan rejected exit order")
    return {"success": True, "orderId": order_id, "trigger": trigger,
            "message": f"Exit order placed for {req.symbol} (fills at open @ ~₹{trigger})"}


@router.post("/sl/trail")
async def trail_sl(req: TrailReq):
    """Ratchet the −8% forever order up per ATR/3-phase (reuses trail_safety_sl)."""
    if sheet_db is None:
        raise HTTPException(status_code=400, detail="Sheet DB required for trailing (R basis)")

    trade = sheet_db.get_trade(symbol=req.symbol, security_id=req.securityId)
    if not trade:
        raise HTTPException(status_code=404, detail=f"No trade row for {req.symbol} — cannot compute R basis")
    if not sl_engine.has_valid_risk(trade):
        raise HTTPException(status_code=400, detail="Trade has invalid entry/target — cannot trail")

    holdings = dhan_client.get_holdings()
    h = next((x for x in holdings if str(x.get("securityId")) == req.securityId), None)
    if not h:
        raise HTTPException(status_code=404, detail="Holding not found")
    pos = {"securityId": req.securityId, "symbol": req.symbol,
           "qty": int(h.get("totalQty") or 0), "avgPrice": float(h.get("avgCostPrice") or 0)}

    close_price = sl_engine.get_daily_close(req.securityId, req.symbol)
    if not close_price:
        raise HTTPException(status_code=400, detail="Could not fetch daily close")

    safety_order = next((o for o in _forever_sl_map().get(req.securityId, [])), None)
    r_basis = sl_engine.compute_r_basis(trade)

    result = sl_engine.trail_safety_sl(trade, pos, close_price, safety_order, r_basis)
    if not result:
        one_r = round(r_basis["one_r_price"], 2)
        raise HTTPException(status_code=400,
                            detail=f"No trail: close ₹{close_price} below 1R ₹{one_r}, or SL already higher")
    return {"success": True, "newTrigger": result.get("Safety_SL"),
            "message": f"{req.symbol} SL trailed up to ₹{result.get('Safety_SL')}"}


class MoveReq(BaseModel):
    securityId: str
    quantity: int
    symbol: str
    trigger: float
    oldOrderId: str = ""


@router.post("/sl/move")
async def move_sl(req: MoveReq):
    """
    Move a protected position's SL to a chosen level: place the new forever SL
    FIRST (never leave the position bare), then cancel the old order.
    Reuses sl_engine._place_safety_at_level + cancel_forever_order.
    """
    if req.trigger <= 0:
        raise HTTPException(status_code=400, detail="Trigger must be positive")

    holdings = dhan_client.get_holdings()
    h = next((x for x in holdings if str(x.get("securityId")) == req.securityId), None)
    if h:
        ltp = float(h.get("lastTradedPrice") or 0)
        if ltp > 0 and req.trigger >= ltp:
            raise HTTPException(status_code=400, detail=f"SL ₹{req.trigger} must be below current ₹{ltp}")

    ok, new_id, level = sl_engine._place_safety_at_level(
        req.securityId, req.quantity, sl_engine._round_down(req.trigger, req.symbol), req.symbol)
    if not ok:
        raise HTTPException(status_code=400, detail="Dhan rejected new SL order — old SL kept")

    cancelled = False
    if req.oldOrderId:
        cancelled = sl_engine.cancel_forever_order(req.oldOrderId, req.symbol)

    return {"success": True, "orderId": new_id, "trigger": level, "oldCancelled": cancelled,
            "message": f"{req.symbol}: SL moved to ₹{level}"}


@router.post("/sl/cancel")
async def cancel_sl(req: CancelReq):
    ok = sl_engine.cancel_forever_order(req.orderId, req.symbol)
    if not ok:
        raise HTTPException(status_code=400, detail="Cancel failed")
    return {"success": True, "message": f"SL order cancelled for {req.symbol}"}
