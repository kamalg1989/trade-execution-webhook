# ==============================================
# 🚀 TELEGRAM WEBHOOK → DHAN FOREVER ENTRY (FINAL SAFE)
# ==============================================

import os
import requests
import pandas as pd
import pyotp
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

MAX_CACHE = 500
ORDER_WINDOW_SECONDS = 60

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

    try:
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

        log("❌ TOKEN FAILED:", data)

    except Exception as e:
        log("❌ TOKEN ERROR:", e)

    return None


def is_token_expired():
    if not TOKEN_EXPIRY:
        return True

    return datetime.now(timezone.utc) > (TOKEN_EXPIRY - timedelta(minutes=5))


def get_token():
    global CURRENT_TOKEN

    if not CURRENT_TOKEN or is_token_expired():
        log("🔁 Refreshing token...")
        CURRENT_TOKEN = generate_token()

    if not CURRENT_TOKEN:
        raise Exception("Token generation failed")

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
# NEW: POSITION CHECK
# ==========================
def is_already_holding(stock):
    try:
        url = "https://api.dhan.co/v2/positions"

        headers = {
            "access-token": get_token(),
            "Content-Type": "application/json"
        }

        r = requests.get(url, headers=headers, timeout=10)

        if r.status_code == 401:
            log("🔁 Token expired (positions)")
            global CURRENT_TOKEN
            CURRENT_TOKEN = generate_token()

            headers["access-token"] = CURRENT_TOKEN
            r = requests.get(url, headers=headers, timeout=10)

        data = r.json()

        symbol = stock.replace(".NS", "").upper()

        for pos in data:
            if pos.get("tradingSymbol", "").upper() == symbol:
                qty = int(pos.get("netQty", 0))

                if qty > 0:
                    log(f"⚠️ Already holding {symbol}, qty={qty}")
                    return True

        return False

    except Exception as e:
        log("❌ POSITION CHECK ERROR:", e)
        return False

# ==========================
# ORDER DEDUP CHECK
# ==========================
def is_duplicate_order(stock, qty, entry):
    key = f"{stock}_{qty}_{entry}"
    now = datetime.now()

    with LOCK:
        if key in PROCESSED_ORDERS:
            if (now - PROCESSED_ORDERS[key]).seconds < ORDER_WINDOW_SECONDS:
                log("⚠️ Duplicate ORDER blocked:", key)
                return True

        PROCESSED_ORDERS[key] = now

        if len(PROCESSED_ORDERS) > MAX_CACHE:
            PROCESSED_ORDERS.clear()

    return False

# ==========================
# ORDER
# ==========================
def place_forever_entry(stock, qty, entry):

    if is_duplicate_order(stock, qty, entry):
        return {"error": "duplicate_blocked"}

    # NEW: position check
    if is_already_holding(stock):
        return {"error": "already_holding"}

    log("\n🚀 ENTRY:", stock, qty, entry)

    sec_id = get_security_id(stock)
    if not sec_id:
        return {"error": "mapping_failed"}

    correlation_id = f"{stock.replace('.NS','')}_{int(datetime.now().timestamp())}"

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": correlation_id[:30],
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

    if r.status_code == 401:
        log("🔁 Token expired → regenerating")
        global CURRENT_TOKEN
        CURRENT_TOKEN = generate_token()

        headers["access-token"] = CURRENT_TOKEN
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
