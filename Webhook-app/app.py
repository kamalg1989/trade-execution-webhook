# ==============================================
# 🚀 TELEGRAM WEBHOOK → DHAN EXECUTION (FINAL DEBUG FIXED)
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
        df = pd.read_csv(INSTRUMENT_URL, low_memory=False)

        print("\n===== CSV LOADED =====")
        print("Rows:", len(df))
        print("Columns:", df.columns.tolist())

        df = df[
            (df['SEM_EXM_EXCH_ID'] == 'NSE') &
            (df['SEM_SEGMENT'] == 'E')
        ]

        print("Filtered NSE EQ:", len(df))

        df['SEM_TRADING_SYMBOL'] = (
            df['SEM_TRADING_SYMBOL']
            .astype(str)
            .str.strip()
            .str.upper()
        )

        return df

    except Exception as e:
        print("❌ CSV ERROR:", e)
        return pd.DataFrame()

INSTRUMENT_DF = load_instruments()

# ==========================
# SYMBOL → SECURITY_ID
# ==========================
def get_security_id(stock):

    symbol = stock.replace(".NS", "").strip().upper()

    print("\n🔍 MAPPING:", symbol)

    if INSTRUMENT_DF.empty:
        print("❌ DF EMPTY")
        return None

    row = INSTRUMENT_DF[
        INSTRUMENT_DF['SEM_TRADING_SYMBOL'] == symbol
    ]

    print("Match count:", len(row))

    if row.empty:
        print("❌ NOT FOUND")

        similar = INSTRUMENT_DF[
            INSTRUMENT_DF['SEM_TRADING_SYMBOL'].str.contains(symbol[:3], na=False)
        ][['SEM_TRADING_SYMBOL','SEM_SMST_SECURITY_ID']].head(10)

        print("Similar:", similar)
        return None

    sec_id = str(row.iloc[0]['SEM_SMST_SECURITY_ID'])
    print("✅ FOUND:", sec_id)

    return sec_id

# ==========================
# TELEGRAM
# ==========================
def send_telegram(msg):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": msg}
    )

# ==========================
# DHAN ORDER
# ==========================
def place_order(stock, qty):

    print("\n==============================")
    print("🚀 ORDER START")
    print("==============================")

    print("Stock:", stock)
    print("Qty:", qty)

    sec_id = get_security_id(stock)

    if not sec_id:
        print("❌ SECURITY ID FAIL")
        return {"error": "mapping_failed"}

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "transactionType": "BUY",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "MARKET",
        "securityId": sec_id,
        "quantity": qty
    }

    print("\n📦 PAYLOAD:", payload)

    headers = {
        "access-token": DHAN_ACCESS_TOKEN.strip(),
        "Content-Type": "application/json"
    }

    print("Token length:", len(DHAN_ACCESS_TOKEN.strip()))

    try:
        r = requests.post(
            "https://api.dhan.co/orders",
            json=payload,
            headers=headers
        )

        print("\n🌐 RESPONSE STATUS:", r.status_code)
        print("🌐 RESPONSE BODY:", r.text)

        try:
            return r.json()
        except:
            return {"raw": r.text}

    except Exception as e:
        print("❌ REQUEST ERROR:", e)
        return {"error": str(e)}

# ==========================
# FLASK
# ==========================
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():

    print("\n==============================")
    print("🔥 WEBHOOK HIT")
    print("==============================")

    # ✅ Always capture raw body
    raw_body = request.data.decode("utf-8")
    print("RAW BODY:", raw_body)

    # ✅ Force JSON parsing
    try:
        data = request.get_json(force=True)
    except Exception as e:
        print("❌ JSON PARSE ERROR:", e)
        return "OK"

    print("PARSED JSON:", data)

    if not data:
        print("❌ EMPTY DATA")
        return "OK"

    # ======================
    # Detect payload type
    # ======================
    if "callback_query" in data:
        print("✅ CALLBACK QUERY DETECTED")
        raw = data["callback_query"].get("data")

    elif "message" in data:
        print("⚠️ MESSAGE DETECTED")
        raw = data["message"].get("text")

    else:
        print("❌ UNKNOWN PAYLOAD")
        return "OK"

    print("RAW FIELD:", raw)

    if not raw:
        print("❌ EMPTY RAW FIELD")
        return "OK"

    parts = raw.split("|")
    print("PARTS:", parts)

    if len(parts) < 2:
        print("❌ INVALID FORMAT")
        return "OK"

    action = parts[0]
    stock = parts[1]
    qty = int(parts[2]) if len(parts) > 2 else 1

    print("\n===== PARSED =====")
    print("Action:", action)
    print("Stock:", stock)
    print("Qty:", qty)

    if action == "BUY":
        print("🚀 EXECUTING ORDER")
        res = place_order(stock, qty)

        print("\n📊 FINAL RESULT:", res)

        if "orderId" in str(res):
            send_telegram(f"🟢 ORDER PLACED: {stock}")
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
    app.run(host="0.0.0.0", port=8000)
