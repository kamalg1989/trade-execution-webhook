# ==============================================
# 🚀 SL ENGINE V11 (FINAL FIXED)
# ==============================================

import os
import requests
import pyotp
import logging
import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# ==========================
# CONFIG
# ==========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")

BASE_SL_PCT = 0.92

session = requests.Session()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CURRENT_TOKEN = None
TOKEN_EXPIRY = datetime.now(timezone.utc)

# ==========================
# TOKEN
# ==========================
def get_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY

    if CURRENT_TOKEN and datetime.now(timezone.utc) < TOKEN_EXPIRY:
        return CURRENT_TOKEN

    totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()

    r = session.post(
        "https://auth.dhan.co/app/generateAccessToken",
        params={
            "dhanClientId": DHAN_CLIENT_ID,
            "pin": DHAN_PIN,
            "totp": totp
        }
    )

    token = r.json().get("accessToken")
    CURRENT_TOKEN = token
    TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(hours=23)

    logger.info("✅ Token generated")
    return token


# ==========================
# POSITIONS
# ==========================
def get_positions():
    r = session.get(
        "https://api.dhan.co/v2/positions",
        headers={
            "access-token": get_token(),
            "client-id": DHAN_CLIENT_ID
        }
    )

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
    r = session.get(
        "https://api.dhan.co/v2/holdings",
        headers={
            "access-token": get_token(),
            "client-id": DHAN_CLIENT_ID
        }
    )

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
    r = session.get(
        "https://api.dhan.co/v2/forever/orders",
        headers={"access-token": get_token()}
    )

    return r.json()


# ==========================
# PLACE SL
# ==========================
def place_sl(sec_id, qty, avg):

    if not avg:
        logger.error(f"❌ Invalid avg price for {sec_id}")
        return False

    trigger = round(avg * BASE_SL_PCT, 2)
    price = round(trigger * 0.995, 2)

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(int(time.time())),
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

    logger.info(f"📤 SL → {sec_id} | trigger={trigger}")

    r = session.post(
        "https://api.dhan.co/v2/forever/orders",
        json=payload,
        headers={
            "access-token": get_token(),
            "client-id": DHAN_CLIENT_ID
        }
    )

    logger.info(f"📡 SL status: {r.status_code} | {r.text}")
    return r.status_code in (200, 201)


# ==========================
# MAIN
# ==========================
def run():
    logger.info("🚀 SL ENGINE START")

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

    logger.info(f"📊 Existing SL: {len(sl_map)}")

    placed = 0

    for sec_id, pos in all_pos.items():

        logger.info(f"\n📍 {pos['symbol']}")

        if sec_id in sl_map:
            logger.info("✅ SL exists")
            continue

        logger.warning("⚠️ Missing SL → placing")

        if place_sl(sec_id, pos["qty"], pos["avgPrice"]):
            placed += 1
            time.sleep(0.3)

    logger.info(f"✅ DONE | SL placed: {placed}")


if __name__ == "__main__":
    run()