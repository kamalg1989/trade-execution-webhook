# ==============================================
# 🚀 TELEGRAM WEBHOOK → DHAN EXECUTION (STATELESS)
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
# LOAD INSTRUMENTS
# ==========================
def load_instruments():
    try:
        df = pd.read_csv(INSTRUMENT_URL)

        # ✅ Correct filters based on your CSV
        df = df[
            (df['SEM_EXCH_ID'] == 'NSE') &
            (df['SEM_SEGMENT'] == 'E')
        ]

        print("✅ Instruments loaded:", len(df))
        return df

    except Exception as e:
        print("❌ Instrument load failed:", e)
        return pd.DataFrame()

INSTRUMENT_DF = load_instruments()

# ==========================
# SYMBOL → SECURITY_ID
# ==========================
def get_security_id(stock):

    if INSTRUMENT_DF.empty:
        return None

    symbol = stock.replace(".NS", "").upper()

    row = INSTRUMENT_DF[
        INSTRUMENT_DF['SEM_TRADING_SYMBOL'].str.upper() == symbol
    ]

    if row.empty:
        print(f"❌ Mapping not found: {stock}")
        return None

    sec_id = str(row.iloc[0]['SEM_SMST_SECURITY_ID'])

    print(f"Mapping: {stock} → {sec_id}")
    return sec_id

# ==========================
# TELEGRAM
# ==========================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": msg
    })

# ==========================
# DHAN ORDER
# ==========================
def place_order(stock, qty):

    security_id = get_security_id(stock)

    if not security_id:
        return {"error": f"SecurityId not found for {stock}"}

    url = "https://api.dhan.co/orders"

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "transactionType": "BUY",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "MARKET",
        "securityId": security_id,
        "quantity": qty
    }

    headers = {
        "access-token": DHAN_ACCESS_TOKEN.strip(),
        "Content-Type": "application/json"
    }

    try:
        r = requests.post(url, json=payload, headers=headers)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

# ==========================
# FLASK
# ==========================
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():

    data = request.get_json(silent=True) or {}

    print("Incoming:", data)

    if "callback_query" not in data:
        return "OK"

    query = data["callback_query"]

    try:
        parts = query["data"].split("|")
        action = parts[0]
        stock = parts[1]
        qty = int(parts[2])
    except:
        return "OK"

    if action == "BUY":

        res = place_order(stock, qty)

        if "orderId" in str(res):
            send_telegram(f"🟢 ORDER PLACED: {stock} | Qty: {qty}")
        else:
            send_telegram(f"❌ ORDER FAILED: {stock}\n{res}")

    return "OK"

# ==========================
# HEALTH CHECK
# ==========================
@app.route("/")
def home():
    return "Webhook running"

# ==========================
# RUN
# ==========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
