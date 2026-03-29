# ==============================================
# 🚀 ENTRY ENGINE (GITHUB SIDE - FINAL STABLE)
# ==============================================

import os
import requests
import pyotp
import sqlite3
from datetime import datetime
import time
import pandas as pd

# ==========================
# CONFIG
# ==========================
INSTRUMENT_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
DB_FILE = "Webhook-app/trades.db"

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")

CURRENT_TOKEN = None


# ==========================
# LOGGER
# ==========================
def log(*args):
    print(*args, flush=True)


# ==========================
# LOAD INSTRUMENTS
# ==========================
def load_instruments():
    df = pd.read_csv(INSTRUMENT_URL, low_memory=False)

    df = df[
        (df['SEM_EXM_EXCH_ID'] == 'NSE') &
        (df['SEM_SEGMENT'] == 'E')
    ]

    df['SEM_TRADING_SYMBOL'] = df['SEM_TRADING_SYMBOL'].astype(str).str.strip().str.upper()

    log("✅ Instruments Loaded:", len(df))
    return df


INSTRUMENT_DF = load_instruments()


# ==========================
# SYMBOL → SECURITY_ID
# ==========================
def get_security_id(stock):
    symbol = stock.replace(".NS", "").strip().upper()

    row = INSTRUMENT_DF[
        INSTRUMENT_DF['SEM_TRADING_SYMBOL'] == symbol
    ]

    if row.empty:
        log(f"❌ Mapping NOT FOUND: {symbol}")
        return None

    sec_id = str(row.iloc[0]['SEM_SMST_SECURITY_ID'])
    log(f"✅ MAPPED: {symbol} → {sec_id}")
    return sec_id


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


def is_duplicate_trade(symbol, entry):
    conn = sqlite3.connect(DB_FILE)

    row = conn.execute("""
        SELECT id FROM trades 
        WHERE symbol=? AND entry_price=? AND status='OPEN'
    """, (symbol, entry)).fetchone()

    conn.close()
    return row is not None


# ==========================
# TOKEN
# ==========================
def generate_token():
    totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()

    log("🔐 TOTP:", totp)

    r = requests.post(
        "https://auth.dhan.co/app/generateAccessToken",
        params={
            "dhanClientId": DHAN_CLIENT_ID,
            "pin": DHAN_PIN,
            "totp": totp
        },
        timeout=10
    )

    log("🔍 RAW RESPONSE:", r.text)

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
def place_order(sec_id, qty, entry):

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(int(time.time())),
        "orderFlag": "SINGLE",
        "transactionType": "BUY",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",   # ✅ Correct
        "validity": "DAY",
        "securityId": sec_id,
        "quantity": qty,
        "price": round(entry * 1.002, 2)
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

    # ✅ validation
    if not symbol or not qty or not entry:
        log("❌ Missing input values")
        return

    # ✅ duplicate protection
    if is_duplicate_trade(symbol, entry):
        log("⚠️ Duplicate trade blocked")
        return

    log(symbol, qty, entry, exit_price)

    # ✅ mapping
    sec_id = get_security_id(symbol)

    if not sec_id:
        log(f"❌ Security ID not found for {symbol}")
        return

    log("DEBUG →", symbol, sec_id, qty, entry)

    # ✅ place order
    res = place_order(sec_id, qty, entry)

    if "orderId" in str(res):
        insert_trade(symbol, sec_id, qty, entry, exit_price, res.get("orderId"))
        log("✅ TRADE SAVED")
    else:
        log("❌ ORDER FAILED")


if __name__ == "__main__":
    run()
