# ==============================================
# 🚀 ENTRY ENGINE (GITHUB SIDE)
# ==============================================

import os
import requests
import pyotp
import sqlite3
import uuid
from datetime import datetime, timezone

DB_FILE = "Webhook-app/trades.db"

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")

CURRENT_TOKEN = None
TOKEN_EXPIRY = None


def log(*args):
    print(*args, flush=True)


# ==========================
# DB
# ==========================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        security_id TEXT,
        qty INTEGER,
        entry_price REAL,
        planned_exit REAL,
        trailing_sl REAL,
        order_id TEXT,
        status TEXT,
        entry_time TEXT
    )
    """)
    conn.commit()
    conn.close()


def insert_trade(symbol, sec_id, qty, entry, exit_price, order_id):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
    INSERT INTO trades 
    (symbol, security_id, qty, entry_price, planned_exit, trailing_sl, order_id, status, entry_time)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        symbol,
        sec_id,
        qty,
        entry,
        exit_price,
        entry * 0.92,
        order_id,
        "OPEN",
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()


# ==========================
# TOKEN
# ==========================
def generate_token():
    totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()

    print("🔐 TOTP:", totp)

    r = requests.post(
        "https://auth.dhan.co/app/generateAccessToken",
        params={
            "dhanClientId": DHAN_CLIENT_ID,
            "pin": DHAN_PIN,
            "totp": totp
        },
        timeout=10
    )

    print("🔍 RAW RESPONSE:", r.text)

    data = r.json()

    if "accessToken" in data:
        return data["accessToken"]

    raise Exception(f"Token failed → {data}")


def get_token():
    global CURRENT_TOKEN
    if not CURRENT_TOKEN:
        CURRENT_TOKEN = generate_token()
    return CURRENT_TOKEN


# ==========================
# PLACE ORDER
# ==========================
def place_order(symbol, qty, entry):

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(uuid.uuid4())[:20],
        "orderFlag": "SINGLE",
        "transactionType": "BUY",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",
        "validity": "DAY",
        "securityId": symbol,
        "quantity": qty,
        "price": round(entry * 1.001, 2),
        "triggerPrice": round(entry, 2)
    }

    r = requests.post(
        "https://api.dhan.co/v2/forever/orders",
        json=payload,
        headers={"access-token": get_token()}
    )

    log("📉 ORDER:", r.text)

    return r.json()


# ==========================
# MAIN
# ==========================
def run():

    init_db()

    symbol = os.getenv("SYMBOL")
    qty = int(os.getenv("QTY"))
    entry = float(os.getenv("ENTRY"))
    exit_price = float(os.getenv("EXIT"))

    log(symbol, qty, entry, exit_price)

    # NOTE: securityId must be mapped (simplified here)
    sec_id = symbol  # replace with mapping if needed

    res = place_order(sec_id, qty, entry)

    if "orderId" in str(res):
        insert_trade(symbol, sec_id, qty, entry, exit_price, res.get("orderId"))
        log("✅ TRADE SAVED")
    else:
        log("❌ ORDER FAILED")


if __name__ == "__main__":
    run()
