# ==============================================
# 🚀 SL ENGINE V10.0 (FINAL — FULLY SYNCED)
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

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DB_FILE = os.path.join(BASE_DIR, "trades.db")

BASE_SL_PCT = 0.92
TRAIL_PROFIT_LOCK = 0.5
MIN_LTP_BUFFER = 0.05

CURRENT_TOKEN = None
TOKEN_EXPIRY = datetime.now(timezone.utc)

session = requests.Session()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ==========================
# TELEGRAM
# ==========================
def send_telegram(msg):
    try:
        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
            session.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
                timeout=10
            )
    except Exception as e:
        logger.error(f"Telegram error: {e}")

# ==========================
# TOKEN
# ==========================
def generate_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY

    try:
        totp = pyotp.TOTP(DHAN_TOTP_SECRET)
        logger.info("🔑 Generating Dhan token")

        r = session.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": totp.now()
            },
            timeout=20
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

    logger.info("⏳ Token expired or missing")
    return generate_token()

# ==========================
# ✅ BATCH LTP FETCH (RATE LIMIT SAFE)
# ==========================
def get_ltp_batch(security_ids):
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

        logger.info(f"📡 LTP batch status: {r.status_code}")

        if r.status_code != 200:
            logger.error(f"LTP error: {r.text}")
            return {}

        data = r.json().get("data", {})
        return {
            int(k): v.get("lastPrice", 0)
            for k, v in data.items()
        }

    except Exception as e:
        logger.error(f"LTP batch failed: {e}")
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

    logger.info(f"📋 Open trades: {len(rows)}")
    return rows


def update_trade_pnl(trade_id, ltp):
    with sqlite3.connect(DB_FILE) as conn:
        trade = conn.execute(
            "SELECT entry_price, qty_ordered FROM executed_orders WHERE id=?",
            (trade_id,)
        ).fetchone()

        if not trade:
            return

        pnl = (ltp - trade[0]) * trade[1]

        conn.execute("""
            UPDATE executed_orders
            SET current_price=?, pnl=?, updated_at=?
            WHERE id=?
        """, (ltp, pnl, datetime.now(timezone.utc).isoformat(), trade_id))

        conn.commit()


def record_sl(trade_id, sl_price, order_id):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            UPDATE executed_orders
            SET sl_price=?, sl_order_id=?, updated_at=?
            WHERE id=?
        """, (sl_price, order_id, datetime.now(timezone.utc).isoformat(), trade_id))
        conn.commit()

# ==========================
# SL ORDER
# ==========================
def place_sl_order(security_id, qty, trigger_price, symbol, trade_id):

    def round_tick(x):
        return round(round(x / 0.05) * 0.05, 2)

    trigger = round_tick(trigger_price)
    limit = round_tick(trigger * 0.995)

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(uuid.uuid4()).replace("-", "")[:20],
        "transactionType": "SELL",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",
        "validity": "DAY",
        "securityId": str(security_id),
        "quantity": int(qty),
        "price": limit,
        "triggerPrice": trigger
    }

    try:
        r = session.post(
            "https://api.dhan.co/v2/forever/orders",
            json=payload,
            headers={
                "access-token": get_token(),
                "client-id": DHAN_CLIENT_ID
            },
            timeout=15
        )

        if r.status_code not in (200, 201):
            logger.error(f"SL failed: {r.text}")
            return None

        order_id = r.json().get("orderId")

        if order_id:
            logger.info(f"✅ SL placed: {order_id}")
            record_sl(trade_id, trigger, order_id)
            send_telegram(f"🛡️ SL {symbol} @ ₹{trigger}")
            return order_id

    except Exception as e:
        logger.error(f"SL error: {e}")

    return None

# ==========================
# SL CALC
# ==========================
def calculate_sl(entry, ltp, current_sl):
    base = entry * BASE_SL_PCT
    new_sl = max(current_sl or 0, base)

    if ltp > entry:
        profit = ltp - entry
        trail = entry + profit * TRAIL_PROFIT_LOCK
        cap = ltp * (1 - MIN_LTP_BUFFER)
        new_sl = max(new_sl, min(trail, cap))

    return round(new_sl, 2)

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

    placed = 0

    for t in trades:
        trade_id = t["id"]
        symbol = t["symbol"]
        sec_id = int(t["security_id"])
        qty = t["qty_ordered"]
        entry = t["entry_price"]
        current_sl = t["sl_price"]
        sl_order_id = t["sl_order_id"]

        logger.info(f"\n📍 {symbol}")

        ltp = ltp_map.get(sec_id)

        if not ltp:
            logger.error("❌ No LTP")
            continue

        update_trade_pnl(trade_id, ltp)

        new_sl = calculate_sl(entry, ltp, current_sl)

        logger.info(f"LTP={ltp} | SL={new_sl}")

        # ✅ PLACE SL ONLY IF NOT ALREADY PLACED
        if not sl_order_id:
            logger.info("🛡️ Placing SL")
            if place_sl_order(sec_id, qty, new_sl, symbol, trade_id):
                placed += 1
                time.sleep(0.3)  # prevent rate limit
        else:
            logger.info("SL already exists")

    logger.info(f"✅ DONE | SL placed: {placed}")


if __name__ == "__main__":
    run()