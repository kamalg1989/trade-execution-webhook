# ==============================================
# 🚀 TELEGRAM WEBHOOK → DHAN EXECUTION (STATELESS)
# ==============================================

import os
import requests
from flask import Flask, request

# ==========================
# CONFIG
# ==========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN")

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

    url = "https://api.dhan.co/orders"

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "transactionType": "BUY",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "MARKET",
        "securityId": stock.replace(".NS",""),  # ⚠️ may need fix later
        "quantity": qty
    }

    headers = {
        "access-token": DHAN_ACCESS_TOKEN,
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