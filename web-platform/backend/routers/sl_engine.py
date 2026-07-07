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
import json
import os
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
RECS_FILE = '/root/trade-execution-webhook/latest_recommendations.json'
MANUAL_SL_FILE = '/root/trade-execution-webhook/manual_structural_sl.json'


def _manual_structural_map():
    """{SYMBOL(no .NS): structuralSL} — user-entered structural levels for
    holdings not present in the sheet/screener."""
    try:
        with open(MANUAL_SL_FILE) as f:
            raw = json.load(f)
        return {str(k).replace('.NS', '').strip().upper(): float(v)
                for k, v in raw.items() if v}
    except Exception:
        return {}


def _save_manual_structural(symbol, value):
    sym = str(symbol).replace('.NS', '').strip().upper()
    try:
        data = {}
        if os.path.exists(MANUAL_SL_FILE):
            with open(MANUAL_SL_FILE) as f:
                data = json.load(f)
    except Exception:
        data = {}
    if value and float(value) > 0:
        data[sym] = float(value)
    else:
        data.pop(sym, None)  # clearing
    with open(MANUAL_SL_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def _sl_options(entry, ltp, structural_sl):
    """
    Suggested-SL dropdown, in display order:
      1) Safety −8% (from buy)
      2) Structural SL
      3) Buy price (breakeven)
      4) Trail ladder from current price: −12%, −10%, −8%, −5%
    Only levels strictly below the current price are valid triggers.
    """
    def mk(label, price, basis):
        if not price or price <= 0 or (ltp > 0 and price >= ltp):
            return None
        return {
            "label": label,
            "price": round(price, 1),
            "basis": basis,
            "pctFromEntry": round(((price - entry) / entry) * 100, 1) if entry else None,
            "pctFromCurrent": round(((price - ltp) / ltp) * 100, 1) if ltp else None,
        }

    ordered = []
    if entry:
        ordered.append(mk("Safety −8% (from buy)", entry * SAFETY_PCT, "safety"))
    if structural_sl:
        ordered.append(mk("Structural SL", structural_sl, "structural"))
    if entry:
        ordered.append(mk("Buy price (breakeven)", entry, "breakeven"))
    if ltp:
        for pct in (0.88, 0.90, 0.92, 0.95):  # −12, −10, −8, −5 %
            ordered.append(mk(f"Trail −{round((1 - pct) * 100)}% (from current)", ltp * pct, f"trail{round((1 - pct) * 100)}"))

    # keep display order, drop invalid and duplicate prices (first label wins)
    seen, out = set(), []
    for o in ordered:
        if not o or o["price"] in seen:
            continue
        seen.add(o["price"])
        out.append(o)
    return out


def _classify_sl(sl_price, entry, structural, safety):
    """Which level the current SL trigger corresponds to."""
    if not sl_price:
        return None
    tol = max(sl_price * 0.005, 0.05)
    if safety and abs(sl_price - safety) <= tol:
        return "Safety −8%"
    if structural and abs(sl_price - structural) <= tol:
        return "Structural"
    if entry and abs(sl_price - entry) <= tol:
        return "Breakeven"
    if entry and sl_price > entry:
        return f"Trail (+{round((sl_price - entry) / entry * 100, 1)}% vs buy)"
    return "Custom"


def _screener_structural_map():
    """{SYMBOL(no .NS): stopLoss} from the latest screener output — used as the
    structural SL for holdings not present in the Google Sheet."""
    try:
        with open(RECS_FILE) as f:
            data = json.load(f)
        m = {}
        for s in data.get('stocks', []):
            sym = str(s.get('symbol', '')).replace('.NS', '').strip().upper()
            sl = s.get('stopLoss') or s.get('stop_loss')
            if sym and sl:
                m[sym] = float(sl)
        return m
    except Exception:
        return {}


def _last_close_map(symbols):
    """{SYMBOL: last daily close} from the local market_data DB (for the
    'closes below structural SL' danger confirmation)."""
    out = {}
    if not symbols:
        return out
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=os.getenv("MD_DB_HOST", "localhost"),
            port=int(os.getenv("MD_DB_PORT", "5432")),
            dbname=os.getenv("MD_DB_NAME", "market_data"),
            user=os.getenv("MD_DB_USER", "market_data_user"),
            password=os.getenv("MD_DB_PASSWORD", os.getenv("DB_PASSWORD", "")),
            connect_timeout=5,
        )
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT ON (symbol) symbol, close
            FROM ohlcv_data
            WHERE symbol = ANY(%s)
            ORDER BY symbol, time DESC
        """, (symbols,))
        for sym, close in cur.fetchall():
            out[str(sym)] = float(close)
        conn.close()
    except Exception as e:
        logger.warning(f"last-close query failed: {e}")
    return out


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
        screener_struct = _screener_structural_map()  # fallback from screener output
        manual_struct = _manual_structural_map()      # user-entered fallback
    except Exception as e:
        logger.error(f"SL data fetch failed: {e}")
        raise HTTPException(status_code=502, detail=f"Dhan/sheet error: {str(e)[:140]}")

    # Last daily close per symbol (for the close-confirmed danger check)
    hold_syms = [str(h.get("tradingSymbol", "")).replace(".NS", "").upper()
                 for h in holdings if int(h.get("totalQty") or h.get("availableQty") or 0) > 0]
    close_map = _last_close_map(hold_syms)

    positions, alerts = [], []
    for h in holdings:
        qty = int(h.get("totalQty") or h.get("availableQty") or 0)
        if qty <= 0:
            continue
        sec_id = str(h.get("securityId", ""))
        symbol = h.get("tradingSymbol", "")
        sym_key = str(symbol).replace(".NS", "").upper()
        avg = float(h.get("avgCostPrice") or 0)
        ltp = float(h.get("lastTradedPrice") or 0) or avg
        pnl = (ltp - avg) * qty

        my_sls = sl_map.get(sec_id, [])
        # Highest trigger = the tightest active protection
        sl_price = max((float(o.get("triggerPrice") or 0) for o in my_sls), default=0.0)
        has_sl = sl_price > 0

        s = struct.get(sec_id, {})
        # Structural SL: sheet first, then user-entered manual, then screener output
        if s.get("structuralSL"):
            structural_sl, structural_src = s.get("structuralSL"), "sheet"
        elif manual_struct.get(sym_key):
            structural_sl, structural_src = manual_struct.get(sym_key), "manual"
        elif screener_struct.get(sym_key):
            structural_sl, structural_src = screener_struct.get(sym_key), "screener"
        else:
            structural_sl, structural_src = None, None
        entry = s.get("entry") or avg   # prefer sheet entry, else avg cost
        safety_sl = round(entry * sl_engine.SAFETY_SL_PCT, 1) if entry else None
        last_close = close_map.get(sym_key)

        # R-multiple of the current move: R = buy − stop (structural, else −8% safety)
        r_stop = structural_sl if structural_sl else safety_sl
        r_unit = (avg - r_stop) if (r_stop and avg > r_stop) else None
        r_multiple = round((ltp - avg) / r_unit, 2) if r_unit else None

        sl_pct_from_entry = round(((sl_price - entry) / entry) * 100, 1) if (has_sl and entry) else None
        sl_options = _sl_options(entry, ltp, structural_sl)
        sl_basis = _classify_sl(sl_price, entry, structural_sl, safety_sl) if has_sl else None

        # Danger: live price below structural (soft watch) and daily close below structural (hard)
        below_live = bool(structural_sl and ltp < structural_sl)
        below_close = bool(structural_sl and last_close and last_close < structural_sl)

        if has_sl and ltp > 0:
            distance = round(((ltp - sl_price) / ltp) * 100, 2)
            zone = "SAFE" if distance > 10 else "WARNING" if distance > 5 else "CRITICAL"
        else:
            distance, zone = None, "NO_SL"

        # Alerts (danger takes priority)
        if below_close:
            zone = "DANGER"
            alerts.append({"symbol": symbol, "type": "DANGER",
                           "message": f"🛑 {symbol}: closed ₹{last_close} below structural SL ₹{structural_sl} — EXIT at next open"})
        elif below_live:
            alerts.append({"symbol": symbol, "type": "WATCH",
                           "message": f"⚠️ {symbol}: live ₹{round(ltp,2)} below structural SL ₹{structural_sl} — watch for a close below"})
        elif not has_sl:
            alerts.append({"symbol": symbol, "type": "NO_SL",
                           "message": f"❗ {symbol}: no active stop loss"})
        elif zone == "CRITICAL":
            alerts.append({"symbol": symbol, "type": "CRITICAL",
                           "message": f"🚨 {symbol}: only {distance}% above SL ₹{sl_price}"})
        elif zone == "WARNING":
            alerts.append({"symbol": symbol, "type": "WARNING",
                           "message": f"⚠️ {symbol}: {distance}% above SL ₹{sl_price}"})

        positions.append({
            "id": sec_id,
            "securityId": sec_id,
            "symbol": symbol,
            "quantity": qty,
            "entry_price": round(entry, 2) if entry else round(avg, 2),
            "buyPrice": round(avg, 2),
            "avgCost": round(avg, 2),
            "current_price": round(ltp, 2),
            "lastClose": round(last_close, 2) if last_close else None,
            "stop_loss": round(sl_price, 2) if has_sl else 0,
            "slPctFromEntry": sl_pct_from_entry,
            "slBasis": sl_basis,                   # which level the current SL sits at
            "distanceToSL": distance,
            "riskZone": zone,
            "structuralSL": round(structural_sl, 2) if structural_sl else None,
            "structuralSrc": structural_src,       # 'sheet' | 'manual' | 'screener' | None
            "structuralEditable": structural_src in (None, "manual"),  # user can set/override
            "safetySL": safety_sl,
            "hasStructural": structural_sl is not None,
            "rMultiple": r_multiple,               # current move in R units (buy − stop basis)
            "belowStructuralLive": below_live,
            "belowStructuralClose": below_close,
            "danger": below_close,
            "watch": below_live and not below_close,
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


class SetStructuralReq(BaseModel):
    symbol: str
    structuralSL: float = 0     # 0 clears the manual value


@router.post("/sl/set-structural")
async def set_structural(req: SetStructuralReq):
    """Save (or clear) a manual structural SL for a symbol not in the sheet/screener."""
    try:
        _save_manual_structural(req.symbol, req.structuralSL)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not save: {str(e)[:120]}")
    return {"success": True,
            "message": (f"Structural SL for {req.symbol} set to ₹{req.structuralSL}"
                        if req.structuralSL else f"Structural SL cleared for {req.symbol}")}


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
