# ==============================================
# 🚀 SL ENGINE V10.1 (DEBUG ENABLED)
# ==============================================

import os
import requests
import pyotp
import sqlite3
import uuid
import logging
import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# ==========================
# CONFIG
# ==========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")

DB_FILE = os.path.join(BASE_DIR, "trades.db")

CURRENT_TOKEN = None
TOKEN_EXPIRY = datetime.now(timezone.utc)

session = requests.Session()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ==========================
# TOKEN
# ==========================
def generate_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY

    try:
        totp = pyotp.TOTP(DHAN_TOTP_SECRET)

        logger.info("🔑 Generating token...")
        r = session.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": totp.now()
            },
            timeout=20
        )

        logger.info(f"🔐 Token API status: {r.status_code}")

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

    logger.info("⏳ Token expired or missing")
    return generate_token()

# ==========================
# 🔍 DEBUG LTP FETCH
# ==========================
def get_ltp_batch(security_ids):

    logger.info("======================================")
    logger.info("📡 LTP BATCH REQUEST START")
    logger.info(f"👉 Requested Security IDs: {security_ids}")
    logger.info("======================================")

    try:
        token = get_token()
        if not token:
            return {}

        payload = {
            "securityId": [int(x) for x in security_ids],
            "exchangeSegment": "NSE_EQ"
        }

        r = session.post(
            "https://api.dhan.co/v2/marketfeed/ltp",
            json=payload,
            headers={
                "access-token": token,
                "client-id": DHAN_CLIENT_ID,
                "Content-Type": "application/json"
            },
            timeout=10
        )

        logger.info(f"📡 LTP HTTP Status: {r.status_code}")

        if r.status_code != 200:
            logger.error(f"❌ LTP API ERROR: {r.text}")
            return {}

        full_response = r.json()

        logger.info("🔍 FULL LTP RESPONSE:")
        logger.info(full_response)

        data = full_response.get("data", {})

        logger.info(f"📊 Returned keys: {list(data.keys())}")

        # Convert to usable map
        ltp_map = {}

        for k, v in data.items():
            try:
                sec_id = int(k)
                ltp = v.get("lastPrice", 0)

                logger.info(f"✅ Parsed LTP → {sec_id} = {ltp}")

                ltp_map[sec_id] = ltp
            except Exception as e:
                logger.error(f"❌ Parse error for key {k}: {e}")

        logger.info("======================================")
        return ltp_map

    except Exception as e:
        logger.error(f"❌ LTP batch failed: {e}")
        return {}

# ==========================
# DB
# ==========================
def get_open_trades():
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, symbol, security_id, qty_ordered, entry_price,
                   sl_price, sl_order_id
            FROM executed_orders
            WHERE status='OPEN'
        """).fetchall()

    logger.info(f"📋 Open trades count: {len(rows)}")

    for r in rows:
        logger.info(dict(r))

    return rows

# ==========================
# MAIN
# ==========================
def run():
    logger.info("🚀 SL ENGINE START")

    trades = get_open_trades()
    if not trades:
        return

    security_ids = [t["security_id"] for t in trades]

    ltp_map = get_ltp_batch(security_ids)

    logger.info("======================================")
    logger.info(f"📊 FINAL LTP MAP: {ltp_map}")
    logger.info("======================================")

    for t in trades:
        trade_id = t["id"]
        symbol = t["symbol"]
        sec_id = int(t["security_id"])

        logger.info(f"\n📍 PROCESSING: {symbol}")
        logger.info(f"   Security ID: {sec_id}")

        ltp = ltp_map.get(sec_id)

        if not ltp:
            logger.error("❌ No LTP FOUND for this security ID")
            logger.error(f"   Expected key: {sec_id}")
            logger.error(f"   Available keys: {list(ltp_map.keys())}")
            continue

        logger.info(f"✅ LTP FOUND: {ltp}")

    logger.info("✅ DEBUG RUN COMPLETE")


if __name__ == "__main__":
    run()