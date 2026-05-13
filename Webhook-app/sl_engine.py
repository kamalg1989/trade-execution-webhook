# ==============================================
# 🚀 SL ENGINE V12 (CRON SAFE + DEBUG + FIXED ENV)
# WITH TRAILING STOP LOSS + MODIFY_SL FROM V5
# ==============================================

import os
import requests
import pyotp
import logging
import time
import uuid
import yfinance as yf
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# ==========================
# LOAD ENV (CRITICAL FIX)
# ==========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ENV_PATHS = [
    os.path.join(BASE_DIR, ".env"),                                # local
    "/root/trade-execution-webhook/.env",                          # VPS root
]

env_loaded = False
for path in ENV_PATHS:
    if os.path.exists(path):
        load_dotenv(path)
        print(f"✅ Loaded .env from: {path}")
        env_loaded = True
        break

if not env_loaded:
    print("❌ WARNING: .env NOT FOUND")

# ==========================
# CONFIG
# ==========================
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")

BASE_SL_PCT = 0.92              # 8% initial SL
TRAIL_PROFIT_LOCK = 0.5         # Lock 50% of profit (from V5)
MIN_LTP_BUFFER = 0.05           # Maintain 5% gap from LTP (from V5)

session = requests.Session()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CURRENT_TOKEN = None
TOKEN_EXPIRY = datetime.now(timezone.utc)

# ==========================
# ENV VALIDATION (CRITICAL)
# ==========================
def validate_env():
    missing = []

    if not DHAN_CLIENT_ID:
        missing.append("DHAN_CLIENT_ID")
    if not DHAN_PIN:
        missing.append("DHAN_PIN")
    if not DHAN_TOTP_SECRET:
        missing.append("DHAN_TOTP_SECRET")

    if missing:
        raise ValueError(f"❌ Missing ENV: {missing}")

    logger.info(f"✅ ENV OK | CLIENT_ID={DHAN_CLIENT_ID}")

# ==========================
# TOKEN
# ==========================
def get_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY

    if CURRENT_TOKEN and datetime.now(timezone.utc) < TOKEN_EXPIRY:
        return CURRENT_TOKEN

    try:
        if not DHAN_TOTP_SECRET:
            raise ValueError("TOTP secret missing")

        totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()

        logger.info("🔑 Generating token...")

        r = session.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": totp
            },
            timeout=10
        )

        logger.info(f"🔐 Token status: {r.status_code}")

        data = r.json()

        if "accessToken" not in data:
            logger.error(f"❌ Token failed: {data}")
            return None

        CURRENT_TOKEN = data["accessToken"]
        TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(hours=23)

        logger.info("✅ Token generated")
        return CURRENT_TOKEN

    except Exception as e:
        logger.error(f"❌ Token error: {e}")
        return None

# ==========================
# POSITIONS
# ==========================
def get_positions():
    token = get_token()
    if not token:
        return []

    r = session.get(
        "https://api.dhan.co/v2/positions",
        headers={
            "access-token": token,
            "client-id": DHAN_CLIENT_ID
        }
    )

    logger.info(f"📡 Positions status: {r.status_code}")

    data = r.json()
    result = []

    for p in data:
        if p.get("netQty", 0) > 0:
            avg = p.get("buyAvg") or p.get("costPrice")

            result.append({
                "securityId": str(p["securityId"]),
                "symbol": p["tradingSymbol"],
                "qty": p["netQty"],
                "avgPrice": avg
            })

    logger.info(f"📊 Positions: {len(result)}")
    return result

# ==========================
# HOLDINGS
# ==========================
def get_holdings():
    token = get_token()
    if not token:
        return []

    r = session.get(
        "https://api.dhan.co/v2/holdings",
        headers={
            "access-token": token,
            "client-id": DHAN_CLIENT_ID
        }
    )

    logger.info(f"📡 Holdings status: {r.status_code}")

    data = r.json()
    result = []

    for h in data:
        qty = h.get("totalQty", 0)

        if qty > 0:
            result.append({
                "securityId": str(h["securityId"]),
                "symbol": h["tradingSymbol"],
                "qty": qty,
                "avgPrice": h.get("avgCostPrice")
            })

    logger.info(f"📊 Holdings: {len(result)}")
    return result

# ==========================
# FOREVER ORDERS
# ==========================
def get_forever_orders():
    token = get_token()
    if not token:
        return []

    r = session.get(
        "https://api.dhan.co/v2/forever/orders",
        headers={"access-token": token}
    )

    logger.info(f"📡 Forever status: {r.status_code}")

    data = r.json()
    logger.info(f"📊 Forever count: {len(data) if isinstance(data, list) else 'INVALID'}")

    return data if isinstance(data, list) else []

# ==========================
# FETCH LTP USING YFINANCE
# ==========================
def get_ltp(symbol):
    """
    Fetch Last Traded Price using yfinance.
    Symbol should be like 'RELIANCE' (without .NS extension)
    """
    try:
        ticker = yf.Ticker(symbol + ".NS")
        ltp = ticker.fast_info["lastPrice"]
        logger.info(f"📊 {symbol} LTP: {ltp}")
        return ltp
    except Exception as e:
        logger.warning(f"⚠️ LTP fetch failed for {symbol}: {e}")
        return None

# ==========================
# SL CALCULATION LOGIC (FROM V5)
# ==========================
def calculate_sl(entry, ltp, current_sl):
    """
    Calculate trailing stop-loss with a minimum 5% buffer from LTP.

    Args:
        entry: Entry price of the position
        ltp: Current Last Traded Price
        current_sl: Current stop-loss price (None if new position)

    Returns:
        New calculated stop-loss price
    """

    # Initial SL (8% below entry)
    base_sl = entry * BASE_SL_PCT

    # Start with the highest of current or base SL
    new_sl = max(current_sl or 0, base_sl)

    # Apply trailing logic only when in profit
    if ltp > entry:
        profit = ltp - entry
        # Trail by locking in 50% of profit
        trailing_sl = entry + (profit * TRAIL_PROFIT_LOCK)

        # Ensure SL is not closer than 5% to LTP
        max_allowed_sl = ltp * (1 - MIN_LTP_BUFFER)

        # Choose the safer SL (lower value, but respects buffer)
        new_sl = max(new_sl, min(trailing_sl, max_allowed_sl))

    return round(new_sl, 2)

# ==========================
# PLACE SL (FROM V12)
# ==========================
def place_sl(sec_id, qty, avg, symbol):
    """
    Place initial stop-loss order for a position.
    """
    if not avg:
        logger.error(f"❌ Invalid avg price for {sec_id}")
        return False

    # Calculate SL using new trailing logic (initial placement)
    trigger = calculate_sl(avg, avg, None)  # LTP = avg on first placement
    price = round(trigger * 0.995, 2)

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(uuid.uuid4())[:20],
        "orderFlag": "SINGLE",
        "transactionType": "SELL",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",
        "validity": "DAY",
        "securityId": sec_id,
        "quantity": qty,
        "price": price,
        "triggerPrice": trigger
    }

    logger.info(f"📤 SL → {symbol} ({sec_id}) | trigger={trigger} | price={price}")

    token = get_token()
    if not token:
        logger.error(f"❌ Failed to get token for placing SL on {symbol}")
        return False

    r = session.post(
        "https://api.dhan.co/v2/forever/orders",
        json=payload,
        headers={
            "access-token": token,
            "client-id": DHAN_CLIENT_ID
        },
        timeout=30
    )

    logger.info(f"📡 SL Place status ({symbol}): {r.status_code} | {r.text}")

    return r.status_code in (200, 201)

# ==========================
# MODIFY SL (FROM V5)
# ==========================
def modify_sl(order_id, qty, trigger, symbol):
    """
    Modify existing stop-loss order with new trigger price.
    Used for trailing stop-loss adjustments.
    """
    token = get_token()
    if not token:
        logger.error(f"❌ Failed to get token for modifying SL on {symbol}")
        return False

    price = round(trigger * 0.995, 2)  # Ensure SELL price < trigger
    disclosed_qty = max(1, int(qty * 0.3))

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "orderId": order_id,
        "orderFlag": "SINGLE",
        "orderType": "LIMIT",
        "legName": "STOP_LOSS_LEG",
        "quantity": int(qty),
        "price": price,
        "triggerPrice": round(trigger, 2),
        "disclosedQuantity": disclosed_qty,
        "validity": "DAY"
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access-token": token
    }

    logger.info(f"🔄 Modifying SL for {symbol}: trigger={trigger} | price={price}")

    r = session.put(
        f"https://api.dhan.co/v2/forever/orders/{order_id}",
        json=payload,
        headers=headers,
        timeout=15
    )

    logger.info(f"📡 SL Modify status ({symbol}): {r.status_code} | {r.text}")

    if r.status_code not in (200, 201):
        logger.error(f"❌ SL MODIFY FAILED for {symbol} | Response: {r.text}")
        return False

    logger.info(f"✅ SL trailed for {symbol} to {trigger}")
    return True

# ==========================
# MAIN
# ==========================
def run():
    logger.info("🚀 SL ENGINE START")

    validate_env()

    positions = get_positions()
    holdings = get_holdings()

    all_pos = {p["securityId"]: p for p in positions}

    for h in holdings:
        all_pos.setdefault(h["securityId"], h)

    logger.info(f"📊 TOTAL POSITIONS: {len(all_pos)}")

    forever = get_forever_orders()

    sl_map = {
        o["securityId"]: o
        for o in forever
        if o.get("transactionType") == "SELL"
           and o.get("orderStatus") == "PENDING"
    }

    logger.info(f"📊 Existing SL Orders: {len(sl_map)}")

    placed = 0
    modified = 0

    for sec_id, pos in all_pos.items():

        symbol = pos['symbol']
        logger.info(f"\n📍 Processing: {symbol}")

        # Get current LTP
        ltp = get_ltp(symbol)
        if not ltp:
            logger.warning(f"⚠️ Could not fetch LTP for {symbol}, skipping")
            continue

        # Check if SL exists
        if sec_id not in sl_map:
            logger.warning(f"⚠️ Missing SL for {symbol} → placing new SL")
            if place_sl(sec_id, pos["qty"], pos["avgPrice"], symbol):
                placed += 1
                time.sleep(0.3)
            continue

        # SL exists - check if it needs trailing adjustment
        existing_order = sl_map[sec_id]
        current_trigger = existing_order.get("triggerPrice")

        new_trigger = calculate_sl(pos["avgPrice"], ltp, current_trigger)

        logger.info(f"   Entry: {pos['avgPrice']} | LTP: {ltp} | Current SL: {current_trigger} → New SL: {new_trigger}")

        # Only modify if new SL is higher (beneficial for trader)
        if new_trigger > current_trigger:
            logger.warning(f"⚠️ Trailing SL up for {symbol}")
            if modify_sl(existing_order["orderId"], pos["qty"], new_trigger, symbol):
                modified += 1
                time.sleep(0.3)
        else:
            logger.info(f"✅ SL optimal for {symbol} (no change needed)")

    logger.info(f"\n✅ SL ENGINE COMPLETED")
    logger.info(f"   📊 SL Placed: {placed}")
    logger.info(f"   🔄 SL Modified: {modified}")

# ==========================
# ENTRY
# ==========================
if __name__ == "__main__":
    run()