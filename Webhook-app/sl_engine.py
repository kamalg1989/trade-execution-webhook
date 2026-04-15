# ==============================================
# 🚀 SL ENGINE V5 (VPS INTEGRATED + API ALIGNED)
# ==============================================

import os
import requests
import pyotp
import sqlite3
import uuid
import logging
import yfinance as yf
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# ==========================
# LOAD ENVIRONMENT VARIABLES
# ==========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, "..", ".env")
load_dotenv(ENV_PATH)

# ==========================
# CONFIG
# ==========================
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not all([DHAN_CLIENT_ID, DHAN_PIN, DHAN_TOTP_SECRET]):
    raise ValueError("Missing required Dhan environment variables.")

DB_FILE = os.path.join(BASE_DIR, "trades.db")
BASE_SL_PCT = 0.92

CURRENT_TOKEN = None
TOKEN_EXPIRY = datetime.now(timezone.utc)

# Reusable HTTP session
session = requests.Session()

# ==========================
# LOGGER CONFIGURATION
# ==========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ==========================
# TELEGRAM NOTIFICATIONS
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
# TOKEN MANAGEMENT
# ==========================
def generate_token():
    global TOKEN_EXPIRY

    totp = pyotp.TOTP(DHAN_TOTP_SECRET)
    logger.info("Generating Dhan access token")

    response = session.post(
        "https://auth.dhan.co/app/generateAccessToken",
        params={
            "dhanClientId": DHAN_CLIENT_ID,
            "pin": DHAN_PIN,
            "totp": totp.now()
        },
        timeout=30
    )

    response.raise_for_status()
    data = response.json()

    expiry = data.get("expiryTime")
    if expiry:
        TOKEN_EXPIRY = datetime.fromisoformat(expiry).replace(tzinfo=timezone.utc)
    else:
        TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(minutes=10)

    logger.info(f"Token generated. Expiry: {TOKEN_EXPIRY}")
    return data["accessToken"]

def get_token():
    global CURRENT_TOKEN
    if not CURRENT_TOKEN or datetime.now(timezone.utc) >= TOKEN_EXPIRY:
        CURRENT_TOKEN = generate_token()
    return CURRENT_TOKEN

# ==========================
# DATABASE INITIALIZATION
# ==========================
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sl_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT UNIQUE,
                security_id TEXT,
                dhan_order_id TEXT,
                trigger_price REAL,
                status TEXT,
                placed_at TEXT,
                modified_at TEXT
            )
        """)

# ==========================
# DHAN API FETCH FUNCTIONS
# ==========================
def fetch_orders():
    response = session.get(
        "https://api.dhan.co/v2/forever/orders",
        headers={"access-token": get_token()},
        timeout=30
    )
    response.raise_for_status()
    return response.json()

# ==========================
# SYNC EXISTING SL ORDERS
# ==========================
def sync_orders(dhan_orders):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM sl_orders")
        for o in dhan_orders:
            if o.get("transactionType") != "SELL":
                continue

            conn.execute("""
                INSERT OR REPLACE INTO sl_orders
                (symbol, security_id, dhan_order_id, trigger_price, status, placed_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                o.get("tradingSymbol"),
                o.get("securityId"),
                o.get("orderId"),
                float(o.get("triggerPrice", 0)),
                o.get("orderStatus", "UNKNOWN"),
                datetime.now(timezone.utc).isoformat()
            ))

# ==========================
# FETCH LTP USING YFINANCE
# ==========================
def get_ltp(symbol):
    try:
        return yf.Ticker(symbol + ".NS").fast_info["lastPrice"]
    except Exception as e:
        logger.warning(f"LTP fetch failed for {symbol}: {e}")
        return None

# ==========================
# SL CALCULATION LOGIC
# ==========================
def calculate_sl(entry, ltp, current_sl):
    base_sl = entry * BASE_SL_PCT

    if current_sl is None:
        return round(base_sl, 2)

    if ltp <= entry:
        return round(base_sl, 2)

    profit = ltp - entry
    trailing_sl = entry + (profit * 0.5)

    return round(max(current_sl, min(trailing_sl, ltp * 0.995)), 2)

# ==========================
# DHAN ORDER ACTIONS
# ==========================
def place_sl(sec_id, qty, trigger, symbol):
    token = get_token()
    trigger_price = round(trigger, 2)
    limit_price = round(trigger_price * 0.995, 2)
    disclosed_qty = max(1, int(qty * 0.3))

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(uuid.uuid4())[:20],
        "orderFlag": "SINGLE",
        "transactionType": "SELL",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",
        "validity": "DAY",
        "securityId": str(sec_id),
        "quantity": int(qty),
        "price": limit_price,
        "triggerPrice": trigger_price,
        "disclosedQuantity": disclosed_qty
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access-token": token
    }

    logger.info(f"Placing SL for {symbol}: {payload}")

    response = session.post(
        "https://api.dhan.co/v2/forever/orders",
        json=payload,
        headers=headers,
        timeout=30
    )

    logger.info(f"Dhan SL Response ({symbol}): {response.status_code} {response.text}")

    if response.status_code not in (200, 201):
        send_telegram(f"❌ SL ORDER FAILED for {symbol}: {response.text}")
        return None

    send_telegram(f"🛡️ SL placed for {symbol} at {trigger_price}")
    return response.json()


# ==========================
# MAIN EXECUTION
# ==========================
def run():
    try:
        logger.info("🚀 SL ENGINE STARTED")

        init_db()

        orders = fetch_orders()
        sync_orders(orders)

        with sqlite3.connect(DB_FILE) as conn:
            trades = conn.execute("""
                SELECT symbol, security_id, qty, entry_price, sl_price
                FROM trades WHERE status='OPEN'
            """).fetchall()

            order_rows = conn.execute("""
                SELECT symbol, dhan_order_id, trigger_price
                FROM sl_orders
            """).fetchall()

        order_map = {o[0]: o for o in order_rows}

        for sym, sec_id, qty, entry, initial_sl in trades:
            ltp = get_ltp(sym)
            if not ltp:
                continue

            existing = order_map.get(sym)
            current_sl = existing[2] if existing else initial_sl
            new_sl = calculate_sl(entry, ltp, current_sl)

            logger.info(f"{sym} | LTP={ltp} | SL {current_sl} → {new_sl}")

            if not existing:
                place_sl(sec_id, qty, new_sl, sym)
            elif new_sl > current_sl:
                modify_sl(existing[1], qty, new_sl, sym)

        logger.info("✅ SL ENGINE COMPLETED")

    except Exception as e:
        logger.exception("SL ENGINE CRASHED")
        send_telegram(f"❌ SL ENGINE ERROR: {e}")
        raise


if __name__ == "__main__":
    run()