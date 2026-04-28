# ==============================================
# 🚀 SL ENGINE V12 (FINAL FIXED - CORRECT API)
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
load_dotenv(os.path.join(BASE_DIR, ".env"))

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

        r = session.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": totp.now()
            },
            timeout=15
        )

        token = r.json().get("accessToken")

        if token:
            CURRENT_TOKEN = token
            TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(hours=23)
            logger.info("✅ Token generated")
            return token

    except Exception as e:
        logger.error(f"Token error: {e}")

    return None


def get_token():
    if CURRENT_TOKEN and datetime.now(timezone.utc) < TOKEN_EXPIRY:
        return CURRENT_TOKEN
    return generate_token()

# ==========================
# ✅ CORRECT FOREVER FETCH
# ==========================
def fetch_forever_orders():
    try:
        r = session.get(
            "https://api.dhan.co/v2/forever/orders",
            headers={"access-token": get_token()},
            timeout=15
        )

        logger.info(f"📡 Forever API status: {r.status_code}")

        if r.status_code != 200:
            logger.error(f"❌ Forever API failed: {r.text}")
            return []

        data = r.json()

        logger.info(f"📊 Orders fetched: {len(data)}")

        return data

    except Exception as e:
        logger.error(f"Forever fetch error: {e}")
        return []

# ==========================
# BUILD LTP MAP
# ==========================
def build_ltp_map(orders):
    ltp_map = {}

    for o in orders:
        symbol = o.get("tradingSymbol")
        trigger = o.get("triggerPrice", 0)
        price = o.get("price", 0)

        if symbol:
            ltp = trigger if trigger > 0 else price
            if ltp > 0:
                ltp_map[symbol] = ltp

    logger.info(f"📊 LTP map: {ltp_map}")
    return ltp_map

# ==========================
# DB
# ==========================
def get_open_trades():
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, symbol, security_id, qty_ordered,
                   entry_price, sl_price, sl_order_id, dhan_order_id
            FROM executed_orders
            WHERE status='OPEN'
        """).fetchall()

    logger.info(f"📋 Open trades: {len(rows)}")
    return rows

# ==========================
# DB SYNC
# ==========================
def sync_db(dhan_orders):
    dhan_ids = set([o.get("orderId") for o in dhan_orders])

    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute("""
            SELECT id, dhan_order_id FROM executed_orders
            WHERE status='OPEN'
        """).fetchall()

        for r in rows:
            if r[1] not in dhan_ids:
                logger.warning(f"❌ Closing stale trade: {r[1]}")
                conn.execute("""
                    UPDATE executed_orders
                    SET status='CLOSED'
                    WHERE id=?
                """, (r[0],))

        conn.commit()

# ==========================
# SL CALC
# ==========================
def calculate_sl(entry, ltp, current_sl):
    base = entry * 0.92
    new_sl = max(current_sl or 0, base)

    if ltp > entry:
        profit = ltp - entry
        trail = entry + profit * 0.5
        cap = ltp * 0.95
        new_sl = max(new_sl, min(trail, cap))

    return round(new_sl, 2)

# ==========================
# PLACE SL
# ==========================
def place_sl(sec_id, qty, trigger, symbol, trade_id):

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(uuid.uuid4()).replace("-", "")[:20],
        "transactionType": "SELL",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",
        "validity": "DAY",
        "securityId": str(sec_id),
        "quantity": int(qty),
        "price": round(trigger * 0.995, 2),
        "triggerPrice": trigger
    }

    try:
        r = session.post(
            "https://api.dhan.co/v2/forever/orders",
            json=payload,
            headers={"access-token": get_token()},
            timeout=10
        )

        if r.status_code not in (200, 201):
            logger.error(f"❌ SL failed: {r.text}")
            return None

        order_id = r.json().get("orderId")

        if order_id:
            logger.info(f"✅ SL placed: {order_id}")

            with sqlite3.connect(DB_FILE) as conn:
                conn.execute("""
                    UPDATE executed_orders
                    SET sl_order_id=?, sl_price=?
                    WHERE id=?
                """, (order_id, trigger, trade_id))
                conn.commit()

            return order_id

    except Exception as e:
        logger.error(f"SL error: {e}")

    return None

# ==========================
# MAIN
# ==========================
def run():
    logger.info("🚀 SL ENGINE START")

    dhan_orders = fetch_forever_orders()

    if not dhan_orders:
        logger.error("❌ No Dhan orders")
        return

    sync_db(dhan_orders)

    ltp_map = build_ltp_map(dhan_orders)

    trades = get_open_trades()

    for t in trades:
        symbol = t["symbol"]
        trade_id = t["id"]

        ltp = ltp_map.get(symbol)

        logger.info(f"\n📍 {symbol} | LTP={ltp}")

        if not ltp:
            continue

        if not t["sl_order_id"]:
            new_sl = calculate_sl(t["entry_price"], ltp, t["sl_price"])
            place_sl(t["security_id"], t["qty_ordered"], new_sl, symbol, trade_id)
            time.sleep(0.3)
        else:
            logger.info("SL already exists")

    logger.info("✅ DONE")


if __name__ == "__main__":
    run()