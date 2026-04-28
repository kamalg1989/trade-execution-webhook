# ==============================================
# 🚀 SL ENGINE V8.0 (FIXED & RELIABLE)
# Uses LTP API (correct approach)
# Places SL orders correctly
# ==============================================

import os
import requests
import pyotp
import sqlite3
import uuid
import logging
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

if not all([DHAN_CLIENT_ID, DHAN_PIN, DHAN_TOTP_SECRET]):
    raise ValueError("Missing Dhan environment variables")

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
        logger.error(f"Telegram Error: {e}")

# ==========================
# TOKEN
# ==========================
def generate_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY
    try:
        totp = pyotp.TOTP(DHAN_TOTP_SECRET)
        logger.info("🔑 Generating Dhan access token")

        r = session.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": totp.now()
            },
            timeout=30
        )

        r.raise_for_status()
        token = r.json().get("accessToken")

        if token:
            CURRENT_TOKEN = token
            TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(hours=23)
            logger.info("✅ Token generated")
            return token

    except Exception as e:
        logger.error(f"❌ Token generation failed: {e}")

    return None


def get_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY
    if CURRENT_TOKEN and datetime.now(timezone.utc) < TOKEN_EXPIRY:
        return CURRENT_TOKEN
    return generate_token()

# ==========================
# ✅ LTP FETCH (CRITICAL FIX)
# ==========================
def get_ltp(security_id):
    try:
        token = get_token()
        if not token:
            return 0

        r = session.get(
            f"https://api.dhan.co/v2/marketfeed/ltp?securityId={security_id}&exchangeSegment=NSE_EQ",
            headers={"access-token": token},
            timeout=10
        )

        if r.status_code == 200:
            return r.json().get("ltp", 0)

        logger.error(f"LTP API error: {r.status_code}")
    except Exception as e:
        logger.error(f"LTP fetch failed: {e}")

    return 0

# ==========================
# DB
# ==========================
def get_open_trades():
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT id, symbol, security_id, qty, entry_price, sl_price
                FROM trades
                WHERE status='OPEN'
            """).fetchall()

        logger.info(f"📋 Open trades: {len(rows)}")
        return rows

    except Exception as e:
        logger.error(f"DB error: {e}")
        return []

def update_trade_pnl(trade_id, ltp):
    try:
        with sqlite3.connect(DB_FILE) as conn:
            trade = conn.execute("SELECT entry_price, qty FROM trades WHERE id=?", (trade_id,)).fetchone()
            if not trade:
                return

            pnl = (ltp - trade[0]) * trade[1]

            conn.execute("""
                UPDATE trades
                SET current_price=?, pnl=?
                WHERE id=?
            """, (ltp, pnl, trade_id))
            conn.commit()

    except Exception as e:
        logger.error(f"P&L update failed: {e}")

def record_sl_order(trade_id, sl_price):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("UPDATE trades SET sl_price=? WHERE id=?", (sl_price, trade_id))
        conn.commit()

# ==========================
# PLACE SL
# ==========================
def place_sl_order(security_id, qty, trigger_price, symbol, trade_id):

    def round_to_tick(v):
        return round(round(v / 0.05) * 0.05, 2)

    trigger_price = round_to_tick(trigger_price)
    limit_price = round_to_tick(trigger_price * 0.995)

    logger.info(f"DEBUG → security_id={security_id}, qty={qty}, trigger={trigger_price}")

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
        "price": limit_price,
        "triggerPrice": trigger_price
    }

    try:
        r = session.post(
            "https://api.dhan.co/v2/forever/orders",
            json=payload,
            headers={"access-token": get_token()},
            timeout=30
        )

        if r.status_code not in (200, 201):
            logger.error(f"❌ SL failed: {r.text}")
            return None

        order_id = r.json().get("orderId")

        if order_id:
            logger.info(f"✅ SL placed: {order_id}")
            record_sl_order(trade_id, trigger_price)
            send_telegram(f"🛡️ SL {symbol} @ ₹{trigger_price}")
            return order_id

    except Exception as e:
        logger.error(f"SL exception: {e}")

    return None

# ==========================
# SL CALC
# ==========================
def calculate_sl(entry, ltp, current_sl):
    base_sl = entry * BASE_SL_PCT
    new_sl = max(current_sl or 0, base_sl)

    if ltp > entry:
        profit = ltp - entry
        trailing = entry + profit * TRAIL_PROFIT_LOCK
        max_sl = ltp * (1 - MIN_LTP_BUFFER)
        new_sl = max(new_sl, min(trailing, max_sl))

    return round(new_sl, 2)

# ==========================
# MAIN
# ==========================
def run():
    logger.info("🚀 SL ENGINE START")

    trades = get_open_trades()
    if not trades:
        return

    for t in trades:
        trade_id = t["id"]
        symbol = t["symbol"]
        sec_id = t["security_id"]
        qty = t["qty"]
        entry = t["entry_price"]
        current_sl = t["sl_price"]

        logger.info(f"\n📍 {symbol}")

        ltp = get_ltp(sec_id)

        if not ltp:
            logger.error("❌ LTP fetch failed")
            continue

        update_trade_pnl(trade_id, ltp)

        new_sl = calculate_sl(entry, ltp, current_sl)

        logger.info(f"LTP={ltp} | SL={new_sl}")

        if not current_sl or current_sl == 0:
            place_sl_order(sec_id, qty, new_sl, symbol, trade_id)
        else:
            logger.info("SL already exists")

    logger.info("✅ DONE")

if __name__ == "__main__":
    run()