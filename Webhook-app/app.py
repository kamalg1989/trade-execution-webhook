# ==============================================
# 🚀 TELEGRAM WEBHOOK → DHAN FOREVER ENTRY (FINAL)
# ==============================================

import os
import requests
import pandas as pd
import pyotp
from datetime import datetime, timedelta
from flask import Flask, request

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

# ✅ Duplicate protection
PROCESSED_CALLBACKS = []
MAX_CACHE = 500

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
            params=params
        )

        data = r.json()

        token = data.get("accessToken")
        expiry = data.get("expiryTime")

        if token:
            TOKEN_EXPIRY = datetime.fromisoformat(expiry)
            log("✅ TOKEN GENERATED")
            return token

        log("❌ TOKEN FAILED:", data)

    except Exception as e:
        log("❌ TOKEN ERROR:", e)

    return None


def is_token_expired():
    if not TOKEN_EXPIRY:
        return True

    return datetime.now() > (TOKEN_EXPIRY - timedelta(minutes=5))


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
    try:
        df = pd.read_csv(INSTRUMENT_URL, low_memory=False)

        df = df[
            (df['SEM_EXM_EXCH_ID'] == 'NSE') &
            (df['SEM_SEGMENT'] == 'E')
        ]

        df['SEM_TRADING_SYMBOL'] = (
            df['SEM_TRADING_SYMBOL']
            .astype(str)
            .str.strip()
            .str.upper()
        )

        log("✅ Instruments Loaded:", len(df))
        return df

    except Exception as e:
        log("❌ CSV ERROR:", e)
        return pd.DataFrame()

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
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg}
    )

# ==========================
# FOREVER ENTRY ORDER
# ==========================
def place_forever_entry(stock, qty, entry):

    log("\n🚀 ENTRY:", stock, qty, entry)

    sec_id = get_security_id(stock)

    if not sec_id:
        return {"error": "mapping_failed"}

    correlation_id = f"{stock.replace('.NS','').upper()}_entry"[:30]

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

    try:
        r = requests.post(url, json=payload, headers=headers)

        # Retry if token expired
        if r.status_code == 401:
            log("🔁 Token expired → regenerating")

            global CURRENT_TOKEN
            CURRENT_TOKEN = generate_token()

            headers["access-token"] = CURRENT_TOKEN

            r = requests.post(url, json=payload, headers=headers)

        log("🌐 STATUS:", r.status_code)
        log("🌐 RESPONSE:", r.text)

        return r.json()

    except Exception as e:
        log("❌ ORDER ERROR:", e)
        return {"error": str(e)}

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

    # ✅ DUPLICATE PROTECTION
    if callback_id in PROCESSED_CALLBACKS:
        log("⚠️ Duplicate click ignored:", callback_id)
        return "OK"

    PROCESSED_CALLBACKS.append(callback_id)

    # Cleanup memory
    if len(PROCESSED_CALLBACKS) > MAX_CACHE:
        PROCESSED_CALLBACKS.pop(0)

    # ACK
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
        data={"callback_query_id": callback_id}
    )

    parts = query.get("data", "").split("|")

    if len(parts) < 3:
        return "OK"

    action, stock, qty = parts[0], parts[1], int(parts[2])

    msg_text = query["message"]["text"]

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

        if "orderId" in str(res):
            send_telegram(f"🟢 ENTRY ORDER PLACED: {stock}")
        else:
            send_telegram(f"❌ ORDER FAILED: {stock}\n{res}")

    return "OK"

@app.route("/")
def home():
    return "Webhook running"

# ==========================
# RUN
# ==========================
if __name__ == "__main__":
    log("🚀 APP STARTED")

    CURRENT_TOKEN = generate_token()

    app.run(host="0.0.0.0", port=8000)
