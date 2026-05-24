# ==============================================
# 🚀 ENTRY ENGINE v3.0 (CONSOLIDATED)
# - Uses shared google_sheets_db (24-col schema, Structural_SL, PENDING)
# - Uses shared tick_utils (no private tick copies)
# - Per-order 0.25% / 10% risk re-validation
# - Accepts token via env var from app.py
# ==============================================

import os
import sys
import requests
import uuid
import json
from datetime import datetime, timezone

# Ensure sibling modules (google_sheets_db, tick_utils) are importable
# whether run directly or as a subprocess from app.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import google_sheets_db as db
from tick_utils import get_tick_size, round_to_tick, get_security_id as tu_get_security_id

# ==========================
# CONFIG
# ==========================
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_TOKEN = os.getenv("DHAN_TOKEN")

CAPITAL = float(os.getenv("CAPITAL", "400000"))

# Risk caps (mirror the scanner's create_trade sizing rules).
RISK_PER_TRADE_PCT = 0.0025      # 0.25% base risk unit
MAX_STAGE_MULTIPLE = 4           # stage 1 = 1x ... worst case 4x base unit
MAX_ALLOCATION_PCT = 0.10        # 10% of capital per trade

# −8% catastrophe backstop, pre-seeded so the risk gate + intraday cron
# see a Safety_SL immediately (real broker order placed later by sl_engine
# / intraday cron).
SAFETY_SL_PCT = 0.92

session = requests.Session()


# ==========================
# LOGGER
# ==========================
def log(*args):
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}]", *args, flush=True)


# ==========================
# GET SECURITY ID  (via shared tick_utils instrument master)
# ==========================
def get_security_id(stock):
    sec_id = tu_get_security_id(stock)
    if not sec_id:
        log(f"❌ Security ID NOT FOUND: {stock}")
        return None
    log(f"✅ {stock} → Security ID: {sec_id}")
    return sec_id

# ==========================
# GET DHAN TOKEN
# ==========================
def get_dhan_token():
    """Generate a fresh Dhan access token using credentials from env."""
    try:
        import pyotp
        dhan_client_id = os.getenv("DHAN_CLIENT_ID")
        dhan_pin = os.getenv("DHAN_PIN")
        dhan_totp_secret = os.getenv("DHAN_TOTP_SECRET")

        if not all([dhan_client_id, dhan_pin, dhan_totp_secret]):
            log(f"❌ Missing Dhan credentials")
            return None

        totp = pyotp.TOTP(dhan_totp_secret).now()
        log(f"🔑 Generating Dhan token...")

        r = session.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={"dhanClientId": dhan_client_id, "pin": dhan_pin, "totp": totp},
            timeout=10,
        )

        if r.status_code != 200:
            log(f"❌ Token generation failed: {r.status_code}")
            return None

        data = r.json()
        token = data.get("accessToken")
        if not token:
            log(f"❌ No accessToken in response: {data}")
            return None

        log(f"✅ Token generated (valid 23h)")
        return token

    except Exception as e:
        log(f"❌ Token generation error: {e}")
        return None


# ==========================
# RISK VALIDATION  (per-order, defends against bad/replayed payloads)
# ==========================
def validate_risk(symbol, entry, sl, qty):
    """
    Re-validate position size at execution time. The scanner sizes trades,
    but the callback qty is otherwise trusted blindly — this is the gate.

    Returns True if the order is within risk + allocation caps.
    """
    risk_per_share = entry - sl
    if risk_per_share <= 0:
        log(f"❌ {symbol} invalid risk/share (entry {entry} <= sl {sl})")
        return False

    trade_risk = risk_per_share * qty
    max_trade_risk = CAPITAL * RISK_PER_TRADE_PCT * MAX_STAGE_MULTIPLE
    if trade_risk > max_trade_risk:
        log(f"❌ {symbol} risk ₹{trade_risk:,.0f} exceeds per-trade cap "
            f"₹{max_trade_risk:,.0f} — rejecting")
        return False

    allocation = entry * qty
    max_allocation = CAPITAL * MAX_ALLOCATION_PCT
    if allocation > max_allocation:
        log(f"❌ {symbol} allocation ₹{allocation:,.0f} exceeds 10% cap "
            f"₹{max_allocation:,.0f} — rejecting")
        return False

    log(f"✅ {symbol} risk OK | risk=₹{trade_risk:,.0f} "
        f"(cap ₹{max_trade_risk:,.0f}) | alloc=₹{allocation:,.0f} "
        f"(cap ₹{max_allocation:,.0f})")
    return True


# ==========================
# CHECK DHAN FOR EXISTING ORDERS
# ==========================
def check_dhan_for_existing_buy(symbol, token):
    """Check /v2/forever/orders for existing BUY orders on this symbol."""
    try:
        if not token:
            log("❌ No token provided from parent")
            return False

        log(f"📡 GET /v2/forever/orders (using parent token)...")

        r = session.get(
            "https://api.dhan.co/v2/forever/orders",
            headers={"access-token": token},
            timeout=30
        )

        if r.status_code != 200:
            log(f"⚠️ API error: {r.status_code}")
            return False

        orders = r.json()
        if not isinstance(orders, list):
            log(f"⚠️ Expected list, got {type(orders)}")
            return False

        log(f"   Total orders: {len(orders)}")

        symbol_upper = symbol.upper().replace(".NS", "")

        for order in orders:
            if not isinstance(order, dict):
                continue

            order_symbol = order.get("tradingSymbol", "").strip().upper()
            trans_type = order.get("transactionType", "")
            status = order.get("orderStatus", "")

            if order_symbol == symbol_upper and trans_type == "BUY":
                if status in ["PENDING", "TRIGGERED", "CONFIRM", "ACCEPTED"]:
                    log(f"⚠️ Found open BUY: Status={status}")
                    return True

        log(f"✅ No open BUY orders for {symbol}")
        return False

    except Exception as e:
        log(f"❌ Error checking Dhan: {e}")
        return False


# ==========================
# PLACE ORDER
# ==========================
def place_order(sec_id, qty, entry, symbol, token):
    """Place BUY forever-order on Dhan using token from parent."""
    try:
        tick = get_tick_size(symbol)
        trigger = round_to_tick(entry, tick, mode="down")
        price = round_to_tick(entry * 1.002, tick, mode="up")

        if price <= trigger:
            price = round_to_tick(trigger + tick, tick, mode="up")

        log(f"   Tick size: ₹{tick:.4f}")
        log(f"   Raw entry: {entry}")
        log(f"   Rounded trigger: {trigger}")
        log(f"   Rounded price: {price}")

        payload = {
            "dhanClientId": DHAN_CLIENT_ID,
            "correlationId": str(uuid.uuid4()).replace("-", "")[:20],
            "orderFlag": "SINGLE",
            "transactionType": "BUY",
            "exchangeSegment": "NSE_EQ",
            "productType": "CNC",
            "orderType": "LIMIT",
            "validity": "DAY",
            "securityId": sec_id,
            "quantity": qty,
            "price": price,
            "triggerPrice": trigger
        }

        if not token:
            log("❌ No token provided from parent")
            return False, {"error": "no_token"}

        log(f"📤 Placing BUY: Qty={qty}, Entry={entry}, Trigger={trigger}, Price={price}")

        r = session.post(
            "https://api.dhan.co/v2/forever/orders",
            json=payload,
            headers={
                "access-token": token,
                "Content-Type": "application/json"
            },
            timeout=15
        )

        if r.status_code not in (200, 201):
            log(f"❌ Order placement failed: {r.status_code}")
            log(f"   Response: {r.text[:200]}")
            return False, {"error": f"http_{r.status_code}"}

        data = r.json()
        log(f"   Response: {data}")
        return True, data

    except Exception as e:
        log(f"❌ Error in place_order: {e}")
        return False, {"error": "exception"}


# ==========================
# MAIN
# ==========================
def run():
    log("=" * 80)
    log("🚀 ENTRY ENGINE v3.0 (CONSOLIDATED)")
    log("=" * 80)

    # ==== INIT SHARED DATA LAYER ====
    try:
        db.init_sheets()
        db.ensure_schema()
    except Exception as e:
        log(f"❌ Failed to initialize Google Sheets: {e}")
        return

    # ==== READ ENV VARS ====
    symbol = os.getenv("SYMBOL", "").strip()
    qty = int(os.getenv("QTY", "0") or "0")
    entry = float(os.getenv("ENTRY", "0") or "0.0")
    sl = float(os.getenv("SL", "0") or "0.0")
    target = float(os.getenv("TARGET", "0") or "0.0")
    score = float(os.getenv("SCORE", "0") or "0.0")
    setup_id = os.getenv("SETUP_ID", "")

    token = get_dhan_token()

    log(f"Input: {symbol} | Qty={qty} | Entry={entry} | SL={sl} | Target={target}")
    log(f"Token from parent: {token[:30] if token else 'NOT PROVIDED'}...")

    # ==== VALIDATION ====
    if not symbol or qty <= 0 or entry <= 0:
        log("❌ Invalid inputs")
        return

    if sl <= 0 or target <= 0:
        log("❌ SL or TARGET missing")
        return

    if not (sl < entry < target):
        log(f"❌ Invalid price order: SL={sl} < ENTRY={entry} < TARGET={target}")
        return

    if not token:
        log("❌ No token provided from parent!")
        return

    # ==== PER-ORDER RISK VALIDATION ====
    if not validate_risk(symbol, entry, sl, qty):
        return

    # ==== GET SECURITY ID ====
    sec_id = get_security_id(symbol)
    if not sec_id:
        log(f"❌ Security ID not found for {symbol}")
        return

    # ==== CHECK DHAN FOR EXISTING ORDERS ====
    log(f"\n🔍 Checking Dhan for existing orders on {symbol}...")

    if check_dhan_for_existing_buy(symbol, token):
        log(f"⚠️ {symbol} already has open BUY order - SKIPPING")
        return

    log(f"✅ {symbol} is clear on Dhan\n")

    # ==== PLACE ORDER ====
    log("=" * 80)
    log("📤 PLACING ORDER ON DHAN")
    log("=" * 80)

    success, response = place_order(sec_id, qty, entry, symbol, token)

    if not success:
        log(f"❌ Order placement failed")
        log(f"   Error: {response.get('error')}")
        return

    # ==== SUCCESS ====
    dhan_order_id = response.get("orderId")
    order_status = response.get("orderStatus")

    if not dhan_order_id:
        log(f"❌ No orderId in response")
        return

    log(f"\n✅ ORDER PLACED SUCCESSFULLY!")
    log(f"   Order ID: {dhan_order_id}")
    log(f"   Status: {order_status}")
    log(f"   Symbol: {symbol}")
    log(f"   Qty: {qty}")

    # ==== RECORD VIA SHARED DATA LAYER ====
    # add_trade writes Structural_SL (NOT SL_Price), seeds Remaining_Qty
    # and Trail_Phase, sets Status=PENDING. Pre-seed Safety_SL at −8% so
    # the risk gate / intraday cron can read it immediately.
    safety_sl = round(entry * SAFETY_SL_PCT, 2)

    trade_id = db.add_trade(
        symbol=symbol,
        sec_id=sec_id,
        qty=qty,
        entry_price=entry,
        structural_sl=sl,
        target_price=target,
        setup_id=setup_id,
        dhan_order_id=dhan_order_id,
        safety_sl=safety_sl,
    )

    if trade_id:
        log(f"✅ Trade recorded in Google Sheets (ID: {trade_id})")

        result = {
            "success": True,
            "order_id": dhan_order_id,
            "trade_id": trade_id,
            "symbol": symbol,
            "qty": qty,
            "entry": entry,
            "message": "Order placed successfully"
        }
        print(json.dumps(result))
    else:
        log(f"⚠️ Order placed on Dhan but sheet write FAILED — reconcile manually")
        log(f"   Order ID: {dhan_order_id}, Symbol: {symbol}")

    log("=" * 80)


if __name__ == "__main__":
    run()