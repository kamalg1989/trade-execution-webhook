# ==============================================
# 🚀 TELEGRAM WEBHOOK → DHAN FOREVER ENTRY (SINGLE)
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

    print("✅ Instruments loaded:", len(df))
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
        print(f"❌ Mapping failed: {stock}")
        return None

    return str(row.iloc[0]['SEM_SMST_SECURITY_ID'])

# ==========================
# TELEGRAM
# ==========================
def send_telegram(msg, buttons=None):

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": msg,
        "parse_mode": "Markdown"
    }

    if buttons:
        payload["reply_markup"] = {
            "inline_keyboard": buttons
        }

    requests.post(url, json=payload)

# ==========================
# FOREVER ENTRY ORDER
# ==========================
def place_forever_entry(stock, qty, entry):

    print("\n🚀 FOREVER ENTRY:", stock, qty, entry)

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

        # ENTRY LOGIC
        "price": round(entry * 1.001, 2),
        "triggerPrice": round(entry, 2)
    }

    headers = {
        "access-token": DHAN_ACCESS_TOKEN.strip(),
        "Content-Type": "application/json"
    }

    r = requests.post(
        "https://api.dhan.co/v2/forever/orders",
        json=payload,
        headers=headers
    )

    print("STATUS:", r.status_code)
    print("BODY:", r.text)

    try:
        return r.json()
    except:
        return {"raw": r.text}

# ==========================
# ALERT FORMAT (FROM SCANNER)
# ==========================
def send_trade_alert(stock, entry, l1, qty):

    msg = f"""
📈 *TRADE ALERT*

{stock}

Entry (H1): {entry}
L1: {l1}
Qty: {qty}
"""

    buttons = [[
        {
            "text": "✅ Confirm Buy",
            "callback_data": f"BUY|{stock}|{qty}|{entry}|{l1}"
        }
    ]]

    send_telegram(msg, buttons)

# ==========================
# FLASK WEBHOOK
# ==========================
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():

    data = request.get_json(silent=True) or {}

    print("\n🔥 WEBHOOK:", data)

    if "callback_query" not in data:
        return "OK"

    raw = data["callback_query"]["data"]
    parts = raw.split("|")

    if len(parts) < 4:
        print("❌ Invalid payload:", raw)
        return "OK"

    action = parts[0]
    stock = parts[1]
    qty = int(parts[2])
    entry = float(parts[3])
    l1 = float(parts[4]) if len(parts) > 4 else None

    print("Parsed:", action, stock, qty, entry)

    if action == "BUY":

        res = place_forever_entry(stock, qty, entry)

        if "orderId" in str(res):
            send_telegram(
                f"🟢 ENTRY ORDER PLACED\n{stock}\nQty: {qty}\nEntry: {entry}"
            )
        else:
            send_telegram(
                f"❌ ENTRY FAILED\n{stock}\n{res}"
            )

    return "OK"

# ==========================
# HEALTH
# ==========================
@app.route("/")
def home():
    return "Webhook running"

# ==========================
# RUN
# ==========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
