# ==============================================
# 🚀 TELEGRAM WEBHOOK → DHAN EXECUTION (FULL DEBUG)
# ==============================================

import os
import requests
import pandas as pd
from flask import Flask, request

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
        print("Total rows:", len(df))
        print("Columns:", df.columns.tolist())

        df = df[
            (df['SEM_EXM_EXCH_ID'] == 'NSE') &
            (df['SEM_SEGMENT'] == 'E')
        ]

        print("Filtered NSE EQ rows:", len(df))

        df['SEM_TRADING_SYMBOL'] = (
            df['SEM_TRADING_SYMBOL']
            .astype(str)
            .str.strip()
            .str.upper()
        )

        return df

    except Exception as e:
        print("❌ CSV LOAD ERROR:", e)
        return pd.DataFrame()

INSTRUMENT_DF = load_instruments()

# ==========================
# MAPPING
# ==========================
def get_security_id(stock):

    symbol = stock.replace(".NS", "").strip().upper()

    print(f"\n🔍 MAPPING START → {stock}")
    print("Normalized symbol:", symbol)

    if INSTRUMENT_DF.empty:
        print("❌ DF EMPTY")
        return None

    row = INSTRUMENT_DF[
        INSTRUMENT_DF['SEM_TRADING_SYMBOL'] == symbol
    ]

    print("Exact match rows:", len(row))

    if not row.empty:
        sec_id = str(row.iloc[0]['SEM_SMST_SECURITY_ID'])
        print(f"✅ EXACT MATCH → {symbol} → {sec_id}")
        return sec_id

    # fallback debug
    print("❌ EXACT MATCH FAILED")

    similar = INSTRUMENT_DF[
        INSTRUMENT_DF['SEM_TRADING_SYMBOL'].str.contains(symbol[:3], na=False)
    ][['SEM_TRADING_SYMBOL','SEM_SMST_SECURITY_ID']].head(10)

    print("🔎 SIMILAR MATCHES:")
    print(similar)

    return None

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
    print("🚀 ORDER FLOW START")
    print("==============================")

    print("Stock:", stock)
    print("Qty:", qty)

    # Step 1: Mapping
    security_id = get_security_id(stock)

    if not security_id:
        print("❌ SECURITY ID NOT FOUND")
        return {"error": "mapping_failed"}

    print("Security ID:", security_id)

    # Step 2: Payload
    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "transactionType": "BUY",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "MARKET",
        "securityId": security_id,
        "quantity": qty
    }

    print("\n📦 PAYLOAD:")
    print(payload)

    headers = {
        "access-token": DHAN_ACCESS_TOKEN.strip(),
        "Content-Type": "application/json"
    }

    print("\n🔐 HEADERS CHECK:")
    print("Token length:", len(DHAN_ACCESS_TOKEN.strip()))
    print("Client ID:", DHAN_CLIENT_ID)

    # Step 3: API Call
    try:
        r = requests.post(
            "https://api.dhan.co/orders",
            json=payload,
            headers=headers
        )

        print("\n🌐 RESPONSE STATUS:", r.status_code)
        print("🌐 RESPONSE TEXT:", r.text)

        try:
            res = r.json()
        except:
            res = {"raw": r.text}

        return res

    except Exception as e:
        print("❌ REQUEST EXCEPTION:", e)
        return {"error": str(e)}

# ==========================
# FLASK
# ==========================
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():

    data = request.get_json(silent=True) or {}

    print("\n===== INCOMING REQUEST =====")
    print(data)

    if "callback_query" not in data:
        print("No callback_query")
        return "OK"

    try:
        parts = data["callback_query"]["data"].split("|")
        action = parts[0]
        stock = parts[1]
        qty = int(parts[2])
    except Exception as e:
        print("❌ PARSE ERROR:", e)
        return "OK"

    if action == "BUY":

        result = place_order(stock, qty)

        print("\n📊 FINAL RESULT:", result)

        if "orderId" in str(result):
            send_telegram(f"🟢 ORDER PLACED: {stock}")
        else:
            send_telegram(f"❌ ORDER FAILED: {stock}\n{result}")

    return "OK"

@app.route("/")
def home():
    return "Webhook running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
