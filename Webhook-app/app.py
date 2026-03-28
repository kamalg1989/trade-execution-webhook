# ==============================================
# 🚀 TELEGRAM WEBHOOK + ENTRY + DB
# ==============================================

import os
import requests
import pandas as pd
import pyotp
import uuid
import sqlite3
from datetime import datetime, timedelta, timezone
from flask import Flask, request
import threading

# ==========================
# CONFIG
# ==========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")

INSTRUMENT_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

CURRENT_TOKEN = None
TOKEN_EXPIRY = None

# ==========================
# DB
# ==========================
DB_FILE = "trades.db"

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

def insert_trade(symbol, sec_id, qty, entry, exit_price):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
    INSERT INTO trades 
    (symbol, security_id, qty, entry_price, planned_exit, trailing_sl, status, entry_time)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        symbol,
        sec_id,
        qty,
        entry,
        exit_price,
        entry * 0.92,
        "OPEN",
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()

init_db()

# ==========================
# LOGGER
# ==========================
def log(*args):
    print(*args, flush=True)

# ==========================
# TOKEN
# ==========================
def generate_token():
    global TOKEN_EXPIRY

    totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()

    r = requests.post(
        "https://auth.dhan.co/app/generateAccessToken",
        params={
            "dhanClientId": DHAN_CLIENT_ID,
            "pin": DHAN_PIN,
            "totp": totp
        },
        timeout=10
    )

    data = r.json()

    if "accessToken" in data:
        TOKEN_EXPIRY = datetime.fromisoformat(data["expiryTime"]).replace(tzinfo=timezone.utc)
        return data["accessToken"]

    raise Exception(f"Token failed: {data}")

def get_token():
    global CURRENT_TOKEN
    if not CURRENT_TOKEN or datetime.now(timezone.utc) > TOKEN_EXPIRY:
        CURRENT_TOKEN = generate_token()
    return CURRENT_TOKEN

# ==========================
# INSTRUMENTS
# ==========================
def load_instruments():
    df = pd.read_csv(INSTRUMENT_URL, low_memory=False)
    df = df[(df['SEM_EXM_EXCH_ID'] == 'NSE') & (df['SEM_SEGMENT'] == 'E')]
    df['SEM_TRADING_SYMBOL'] = df['SEM_TRADING_SYMBOL'].astype(str).str.strip().str.upper()
    return df

INSTRUMENT_DF = load_instruments()

def get_security_id(stock):
    symbol = stock.replace(".NS", "").upper()
    row = INSTRUMENT_DF[INSTRUMENT_DF['SEM_TRADING_SYMBOL'] == symbol]
    if row.empty:
        return None
    return str(row.iloc[0]['SEM_SMST_SECURITY_ID'])

# ==========================
# TELEGRAM
# ==========================
def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg}
        )
    except:
        pass

# ==========================
# ORDER
# ==========================
def place_forever_entry(stock, qty, entry):

    sec_id = get_security_id(stock)
    if not sec_id:
        return {"error": "mapping_failed"}

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(uuid.uuid4())[:20],
        "orderFlag": "SINGLE",
        "transactionType": "BUY",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",
        "validity": "DAY",
        "securityId": sec_id,
        "quantity": qty,
        "price": round(entry * 1.001, 2),
        "triggerPrice": round(entry, 2)
    }

    r = requests.post(
        "https://api.dhan.co/v2/forever/orders",
        json=payload,
        headers={"access-token": get_token()}
    )

    return r.json(), sec_id

# ==========================
# FLASK
# ==========================
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():

    data = request.get_json(force=True)
    query = data.get("callback_query", {})
    parts = query.get("data", "").split("|")

    if len(parts) < 3:
        return "OK"

    action, stock, qty = parts[0], parts[1], int(parts[2])

    msg_text = query.get("message", {}).get("text", "")

    entry = None
    exit_price = None

    for line in msg_text.split("\n"):
        if "Entry" in line:
            entry = float(line.split(":")[1])
        if "Exit" in line:
            exit_price = float(line.split(":")[1])

    if action == "BUY":
        res, sec_id = place_forever_entry(stock, qty, entry)

        if "orderId" in str(res):
            insert_trade(stock.replace(".NS",""), sec_id, qty, entry, exit_price)
            send_telegram(f"🟢 ENTRY + DB: {stock}")
        else:
            send_telegram(f"❌ FAILED: {stock}")

    return "OK"
