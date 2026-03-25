# ==============================================
# 🚀 TELEGRAM WEBHOOK → DHAN FOREVER ENTRY (FINAL)
# ==============================================

import os
import requests
import pandas as pd
from flask import Flask, request

# ==========================
# CONFIG
# ==========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN")

INSTRUMENT_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

# ==========================
# LOGGER
# ==========================
def log(*args):
    print(*args, flush=True)

# ==========================
# LOAD INSTRUMENTS
# ==========================
def load_instruments():
    try:
        df = pd.read_csv(INSTRUMENT_URL, low_memory=False)

        log("===== CSV LOADED =====")
        log("Rows:", len(df))

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

        log("Filtered NSE EQ:", len(df))

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
    log("🔍 Mapping:", symbol)

    if INSTRUMENT_DF.empty:
        log("❌ DF EMPTY")
        return None

    row = INSTRUMENT_DF[
        INSTRUMENT_DF['SEM_TRADING_SYMBOL'] == symbol
    ]

    if row.empty:
        log("❌ Mapping NOT FOUND:", symbol)
        return None

    sec_id = str(row.iloc[0]['SEM_SMST_SECURITY_ID'])
    log("✅ Security ID:", sec_id)

    return sec_id

# ==========================
# TELEGRAM
# ==========================
def send_telegram(msg):
    log("📤 Telegram:", msg)

    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": msg
        }
    )

# ==========================
# FOREVER ENTRY ORDER
# ==========================
def place_forever_entry(stock, qty, entry):

    log("\n🚀 ENTRY START:", stock, qty, entry)

    sec_id = get_security_id(stock)

    if not sec_id:
        return {"error": "mapping_failed"}

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": f"{stock}_entry",

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

    log("📦 PAYLOAD:", payload)

    headers = {
        "access-token": DHAN_ACCESS_TOKEN.strip(),
        "Content-Type": "application/json"
    }

    try:
        r = requests.post(
            "https://api.dhan.co/v2/forever/orders",
            json=payload,
            headers=headers
        )

        log("🌐 STATUS:", r.status_code)
        log("🌐 RESPONSE:", r.text)

        return r.json()

    except Exception as e:
        log("❌ REQUEST ERROR:", e)
        return {"error": str(e)}

# ==========================
# FLASK
# ==========================
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():

    log("\n==============================")
    log("🔥 WEBHOOK HIT")
    log("==============================")

    raw_body = request.data.decode("utf-8")
    log("RAW BODY:", raw_body)

    try:
        data = request.get_json(force=True)
    except Exception as e:
        log("❌ JSON ERROR:", e)
        return "OK"

    log("PARSED JSON:", data)

    if not data or "callback_query" not in data:
        log("❌ NO CALLBACK")
        return "OK"

    query = data["callback_query"]

    # ✅ ACK BUTTON CLICK
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
            data={"callback_query_id": query["id"]}
        )
    except Exception as e:
        log("❌ ACK ERROR:", e)

    raw = query.get("data", "")
    log("👉 RAW DATA:", raw)

    parts = raw.split("|")
    log("👉 PARTS:", parts)

    if len(parts) < 3:
        log("❌ INVALID FORMAT")
        return "OK"

    action = parts[0]
    stock = parts[1]
    qty = int(parts[2])

    # ==========================
    # EXTRACT ENTRY FROM MESSAGE
    # ==========================
    msg_text = query["message"]["text"]
    log("📩 MESSAGE TEXT:", msg_text)

    entry = None

    for line in msg_text.split("\n"):
        if "Entry" in line:
            try:
                entry = float(line.split(":")[1].strip())
            except:
                pass

    if not entry:
        log("❌ ENTRY NOT FOUND")
        return "OK"

    log("👉 PARSED:", action, stock, qty, entry)

    # ==========================
    # EXECUTE
    # ==========================
    if action == "BUY":

        log("🚀 EXECUTION START")

        res = place_forever_entry(stock, qty, entry)

        log("📊 RESULT:", res)

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
    app.run(host="0.0.0.0", port=8000)
