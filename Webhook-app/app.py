# ==============================================
# 🚀 TELEGRAM WEBHOOK → DHAN FOREVER ENTRY (FINAL STABLE)
# ==============================================

import os
import requests
import pandas as pd
import pyotp
import uuid
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
# DEDUP STORAGE
# ==========================
PROCESSED_CALLBACKS = set()
PROCESSED_ORDERS = {}
LOCK = threading.Lock()

ORDER_WINDOW_SECONDS = 300

# ==========================
# LOGGER
# ==========================
def log(*args):
    print(*args, flush=True)

# ==========================
# TOKEN MANAGEMENT
# ==========================
def generate_token():
    global TOKEN_EXPIRY

    totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()

    params = {
        "dhanClientId": DHAN_CLIENT_ID,
        "pin": DHAN_PIN,
        "totp": totp
    }

    r = requests.post(
        "https://auth.dhan.co/app/generateAccessToken",
        params=params,
        timeout=10
    )

    data = r.json()

    token = data.get("accessToken")
    expiry = data.get("expiryTime")

    if token and expiry:
        TOKEN_EXPIRY = datetime.fromisoformat(expiry).replace(tzinfo=timezone.utc)
        log("✅ TOKEN GENERATED")
        return token

    raise Exception(f"Token failed: {data}")


def is_token_expired():
    if not TOKEN_EXPIRY:
        return True
    return datetime.now(timezone.utc) > (TOKEN_EXPIRY - timedelta(minutes=5))


def get_token():
    global CURRENT_TOKEN

    if not CURRENT_TOKEN or is_token_expired():
        log("🔁 Refreshing token...")
        CURRENT_TOKEN = generate_token()

    return CURRENT_TOKEN

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
        log("❌ Mapping NOT FOUND:", symbol)
        return None

    return str(row.iloc[0]['SEM_SMST_SECURITY_ID'])

# ==========================
# TELEGRAM
# ==========================
def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        log("❌ Telegram Error:", e)

# ==========================
# 🔥 FIXED: CHECK PENDING BUY ORDER
# ==========================
def has_pending_buy_order(security_id):
    try:
        url = "https://api.dhan.co/v2/orders"

        headers = {
            "access-token": get_token()
        }

        r = requests.get(url, headers=headers, timeout=10)

        if r.status_code != 200:
            log("❌ Orders API failed:", r.text)
            return False

        data = r.json()

        # 🔥 FIX: ensure list
        if not isinstance(data, list):
            log("❌ Unexpected orders response:", data)
            return False

        for o in data:
            if str(o.get("securityId")) == str(security_id):
                if o.get("transactionType") == "BUY":
                    if o.get("orderStatus") in ["PENDING", "TRANSIT"]:
                        log(f"⚠️ Pending BUY exists: {security_id}")
                        return True

        return False

    except Exception as e:
        log("❌ ORDER CHECK ERROR:", e)
        return False

# ==========================
# POSITION CHECK
# ==========================
def is_already_holding(stock):
    try:
        url = "https://api.dhan.co/v2/positions"

        headers = {
            "access-token": get_token()
        }

        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()

        if not isinstance(data, list):
            log("❌ Unexpected position response:", data)
            return False

        symbol = stock.replace(".NS", "").upper()

        for pos in data:
            if pos.get("tradingSymbol", "").upper() == symbol:
                if int(pos.get("netQty", 0)) > 0:
                    log(f"⚠️ Already holding {symbol}")
                    return True

        return False

    except Exception as e:
        log("❌ POSITION ERROR:", e)
        return False

# ==========================
# ORDER DEDUP
# ==========================
def is_duplicate_order(stock, qty, entry):
    key = f"{stock}_{qty}_{entry}"
    now = datetime.now()

    with LOCK:
        if key in PROCESSED_ORDERS:
            if (now - PROCESSED_ORDERS[key]).seconds < ORDER_WINDOW_SECONDS:
                log("⚠️ Duplicate blocked:", key)
                return True

        PROCESSED_ORDERS[key] = now

    return False

# ==========================
# ORDER
# ==========================
def place_forever_entry(stock, qty, entry):

    if is_duplicate_order(stock, qty, entry):
        return {"error": "duplicate_blocked"}

    sec_id = get_security_id(stock)
    if not sec_id:
        return {"error": "mapping_failed"}

    if has_pending_buy_order(sec_id):
        return {"error": "pending_order_exists"}

    if is_already_holding(stock):
        return {"error": "already_holding"}

    log("\n🚀 ENTRY:", stock, qty, entry)

    # 🔥 FIX: valid correlationId
    correlation_id = str(uuid.uuid4()).replace("-", "")[:20]

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": correlation_id,
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

    url = "https://api.dhan.co/v2/forever/orders"

    headers = {
        "access-token": get_token(),
        "Content-Type": "application/json"
    }

    r = requests.post(url, json=payload, headers=headers, timeout=10)

    log("🌐 STATUS:", r.status_code)
    log("🌐 RESPONSE:", r.text)

    return r.json()

# ==========================
# FLASK
# ==========================
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():

    data = request.get_json(force=True)

    if not data or "callback_query" not in data:
        return "OK"

    query = data["callback_query"]
    callback_id = query["id"]

    with LOCK:
        if callback_id in PROCESSED_CALLBACKS:
            return "OK"
        PROCESSED_CALLBACKS.add(callback_id)

    parts = query.get("data", "").split("|")
    if len(parts) < 3:
        return "OK"

    action, stock, qty = parts[0], parts[1], int(parts[2])

    msg_text = query.get("message", {}).get("text", "")

    entry = None
    for line in msg_text.split("\n"):
        if "Entry" in line:
            try:
                entry = float(line.split(":")[1].strip())
            except:
                pass

    if not entry:
        return "OK"

    if action == "BUY":
        res = place_forever_entry(stock, qty, entry)

        if res.get("error") == "duplicate_blocked":
            log("⚠️ Duplicate blocked")

        elif res.get("error") == "pending_order_exists":
            send_telegram(f"⚠️ SKIPPED (Pending Order Exists): {stock}")

        elif res.get("error") == "already_holding":
            send_telegram(f"⚠️ SKIPPED (Already Holding): {stock}")

        elif "orderId" in str(res):
            send_telegram(f"🟢 ENTRY ORDER PLACED: {stock}")

        else:
            send_telegram(f"❌ ORDER FAILED: {stock}\n{res}")

    return "OK"

@app.route("/")
def home():
    return "Webhook running"
