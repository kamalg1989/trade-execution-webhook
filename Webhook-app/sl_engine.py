# ==============================================
# 🚀 SL ENGINE V4 (VPS INTEGRATED + STABLE)
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

DB_FILE = os.path.join(BASE_DIR, "trades.db")
BASE_SL_PCT = 0.92

CURRENT_TOKEN = None
TOKEN_EXPIRY = None

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
            requests.post(
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

    response = requests.post(
        "https://auth.dhan.co/app/generateAccessToken",
        params={
            "dhanClientId": DHAN_CLIENT_ID,
            "pin": DHAN_PIN,
            "totp": totp.now()
        },
        timeout=10
    )

    if response.status_code != 200:
        raise Exception(f"Token API failed: {response.text}")

    data = response.json()
    logger.info(f"Token generated successfully.")

    expiry = data.get("expiryTime")
    if expiry:
        try:
            TOKEN_EXPIRY = datetime.fromisoformat(expiry).replace(tzinfo=timezone.utc)
        except Exception:
            TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(minutes=10)
    else:
        TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(minutes=10)

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
def fetch_positions():
    r = requests.get(
        "https://api.dhan.co/v2/positions",
        headers={"access-token": get_token()},
        timeout=10
    )
    return r.json() if r.status_code == 200 else []


def fetch_holdings():
    r = requests.get(
        "https://api.dhan.co/v2/holdings",
        headers={"access-token": get_token()},
        timeout=10
    )
    return r.json() if r.status_code == 200 else []


def fetch_orders():
    r = requests.get(
        "https://api.dhan.co/v2/forever/orders",
        headers={"access-token": get_token()},
        timeout=10
    )
    return r.json() if r.status_code == 200 else []


# ==========================
# SYNC TRADES WITH HOLDINGS
# ==========================
def sync_trades(positions, holdings):
    active = set()

    for p in positions:
        if int(p.get("netQty", 0)) > 0:
            active.add(p.get("tradingSymbol"))

    for h in holdings:
        if int(h.get("totalQty", 0)) > 0:
            active.add(h.get("tradingSymbol"))

    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute(
            "SELECT symbol FROM trades WHERE status='OPEN'"
        ).fetchall()

        for (sym,) in rows:
            if sym not in active:
                logger.info(f"Closing trade for {sym}")
                conn.execute(
                    "UPDATE trades SET status='CLOSED' WHERE symbol=?",
                    (sym,)
                )


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
                (symbol, dhan_order_id, trigger_price, status, placed_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                o.get("tradingSymbol"),
                o.get("orderId"),
                float(o.get("triggerPrice", 0)),
                o.get("orderStatus", "UNKNOWN"),
                datetime.utcnow().isoformat()
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
    try:
        token = get_token()

        payload = {
            "dhanClientId": DHAN_CLIENT_ID,
            "correlationId": str(uuid.uuid4())[:20],
            "orderFlag": "SINGLE",
            "transactionType": "SELL",
            "exchangeSegment": "NSE_EQ",
            "productType": "CNC",
            "orderType": "LIMIT",  # ✅ Correct order type for Forever Orders
            "validity": "DAY",
            "securityId": str(sec_id),
            "quantity": int(qty),
            "price": round(trigger * 0.995, 2),
            "triggerPrice": round(trigger, 2)
        }

        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "access-token": token
        }

        log("\n================ DHAN SL ORDER REQUEST ================")
        log(f"Symbol        : {symbol}")
        log(f"Security ID   : {sec_id}")
        log(f"Quantity      : {qty}")
        log(f"Trigger Price : {payload['triggerPrice']}")
        log(f"Limit Price   : {payload['price']}")
        log(f"Payload       : {payload}")
        log("=======================================================\n")

        response = requests.post(
            "https://api.dhan.co/v2/forever/orders",
            json=payload,
            headers=headers,
            timeout=15
        )

        log("\n================ DHAN SL ORDER RESPONSE ===============")
        log(f"Status Code : {response.status_code}")
        log(f"Response    : {response.text}")
        log("=======================================================\n")

        if response.status_code not in (200, 201):
            send_telegram(
                f"❌ SL ORDER FAILED\n"
                f"{symbol}\n"
                f"Status: {response.status_code}\n"
                f"Response: {response.text}"
            )
            return None

        data = response.json()
        send_telegram(f"🛡️ SL placed for {symbol} at {payload['triggerPrice']}")
        return data

    except Exception as e:
        log(f"❌ Exception while placing SL for {symbol}: {e}")
        send_telegram(f"❌ SL ENGINE ERROR for {symbol}: {e}")
        return None

    
def modify_sl(order_id, qty, trigger):
    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "orderId": order_id,
        "orderFlag": "SINGLE",
        "orderType": "LIMIT",
        "quantity": int(qty),
        "price": round(trigger * 0.995, 2),
        "triggerPrice": round(trigger, 2),
        "validity": "DAY"
    }

    r = requests.put(
        f"https://api.dhan.co/v2/forever/orders/{order_id}",
        json=payload,
        headers={"access-token": get_token()},
        timeout=10
    )

    logger.info(f"Modified SL order: {r.status_code} {r.text}")
    return r.json() if r.status_code in (200, 201) else None


# ==========================
# MAIN EXECUTION
# ==========================
def run():
    try:
        logger.info("🚀 SL ENGINE STARTED")

        init_db()

        positions = fetch_positions()
        holdings = fetch_holdings()
        orders = fetch_orders()

        sync_trades(positions, holdings)
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

            logger.info(
                f"{sym} | LTP={ltp} | SL {current_sl} → {new_sl}"
            )

            if not existing:
                logger.info(f"Placing SL for {sym}")
                place_sl(sec_id, qty, new_sl)
                send_telegram(f"🛡️ SL placed for {sym} at {new_sl}")
            elif new_sl > current_sl:
                logger.info(f"Trailing SL for {sym}")
                modify_sl(existing[1], qty, new_sl)
                send_telegram(f"🔄 SL trailed for {sym} to {new_sl}")

        logger.info("✅ SL ENGINE COMPLETED")

    except Exception as e:
        logger.exception("SL ENGINE CRASHED")
        send_telegram(f"❌ SL ENGINE ERROR: {e}")
        raise


if __name__ == "__main__":
    run()