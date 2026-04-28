# ==============================================
# 🚀 SL ENGINE V10 (POSITIONS + FOREVER SYNC)
# ==============================================

import os
import requests
import pyotp
import sqlite3
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

DB_FILE = os.path.join(BASE_DIR, "trades.db")

BASE_SL_PCT = 0.92

session = requests.Session()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CURRENT_TOKEN = None
TOKEN_EXPIRY = datetime.now(timezone.utc)

# ==========================
# TOKEN
# ==========================
def generate_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY
    try:
        totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()

        r = session.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": totp
            },
            timeout=15
        )

        r.raise_for_status()
        token = r.json().get("accessToken")

        if token:
            CURRENT_TOKEN = token
            TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(hours=23)
            logger.info("✅ Token generated")
            return token

    except Exception as e:
        logger.error(f"❌ Token error: {e}")

    return None


def get_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY
    if CURRENT_TOKEN and datetime.now(timezone.utc) < TOKEN_EXPIRY:
        return CURRENT_TOKEN
    return generate_token()


# ==========================
# GET POSITIONS
# ==========================
def get_positions():
    token = get_token()
    if not token:
        return []

    try:
        logger.info("📡 Fetching positions...")

        r = session.get(
            "https://api.dhan.co/v2/positions",
            headers={
                "access-token": token,
                "client-id": DHAN_CLIENT_ID
            },
            timeout=15
        )

        logger.info(f"📡 Positions status: {r.status_code}")

        if r.status_code != 200:
            logger.error(r.text)
            return []

        data = r.json()

        logger.info(f"📊 Raw positions: {data}")

        positions = []

        for p in data:
            qty = p.get("netQty", 0)

            if qty > 0:
                positions.append({
                    "securityId": str(p["securityId"]),
                    "symbol": p.get("tradingSymbol"),
                    "qty": qty,
                    "avgPrice": p.get("avgPrice")
                })

        logger.info(f"✅ Active positions: {len(positions)}")
        return positions

    except Exception as e:
        logger.error(f"❌ Positions fetch failed: {e}")
        return []


# ==========================
# GET FOREVER SL ORDERS
# ==========================
def get_forever_orders():
    token = get_token()
    if not token:
        return []

    try:
        logger.info("📡 Fetching forever orders...")

        r = session.get(
            "https://api.dhan.co/v2/forever/orders",
            headers={"access-token": token},
            timeout=15
        )

        logger.info(f"📡 Forever status: {r.status_code}")

        if r.status_code != 200:
            logger.error(r.text)
            return []

        data = r.json()

        logger.info(f"📊 Total forever orders: {len(data)}")

        return data

    except Exception as e:
        logger.error(f"❌ Forever fetch failed: {e}")
        return []


# ==========================
# PLACE SL
# ==========================
def place_sl(security_id, qty, avg_price):

    def round_tick(x):
        return round(round(x / 0.05) * 0.05, 2)

    trigger = round_tick(avg_price * BASE_SL_PCT)
    price = round_tick(trigger * 0.995)

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(int(time.time())),
        "orderFlag": "SINGLE",
        "transactionType": "SELL",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",
        "validity": "DAY",
        "securityId": security_id,
        "quantity": qty,
        "price": price,
        "triggerPrice": trigger
    }

    try:
        logger.info(f"📤 PLACING SL → {security_id} @ {trigger}")

        r = session.post(
            "https://api.dhan.co/v2/forever/orders",
            json=payload,
            headers={
                "access-token": get_token(),
                "client-id": DHAN_CLIENT_ID
            },
            timeout=15
        )

        logger.info(f"📡 SL status: {r.status_code}")

        if r.status_code not in (200, 201):
            logger.error(r.text)
            return False

        logger.info(f"✅ SL PLACED: {r.json()}")
        return True

    except Exception as e:
        logger.error(f"❌ SL placement failed: {e}")
        return False


# ==========================
# SYNC DB (CLEANUP)
# ==========================
def sync_db_with_positions(positions):
    try:
        ids = [p["securityId"] for p in positions]

        with sqlite3.connect(DB_FILE) as conn:
            if not ids:
                conn.execute("DELETE FROM executed_orders")
            else:
                q = ",".join("?" * len(ids))
                conn.execute(f"""
                    DELETE FROM executed_orders
                    WHERE security_id NOT IN ({q})
                """, ids)

            conn.commit()

        logger.info("🧹 DB synced with positions")

    except Exception as e:
        logger.error(f"❌ DB sync failed: {e}")


# ==========================
# MAIN LOGIC
# ==========================
def run():
    logger.info("🚀 SL ENGINE START")

    positions = get_positions()
    if not positions:
        logger.error("❌ No positions found")
        return

    forever_orders = get_forever_orders()

    sl_map = {
        o["securityId"]: o
        for o in forever_orders
        if o.get("transactionType") == "SELL"
           and o.get("orderStatus") == "PENDING"
    }

    logger.info(f"📊 Existing SL orders: {len(sl_map)}")

    placed = 0

    for pos in positions:
        sec_id = pos["securityId"]
        symbol = pos["symbol"]

        logger.info(f"\n📍 {symbol}")

        if sec_id in sl_map:
            logger.info("✅ SL already exists")
            continue

        logger.warning("⚠️ Missing SL → placing")

        if place_sl(sec_id, pos["qty"], pos["avgPrice"]):
            placed += 1
            time.sleep(0.3)  # rate limit safety

    sync_db_with_positions(positions)

    logger.info(f"✅ DONE | SL placed: {placed}")


if __name__ == "__main__":
    run()