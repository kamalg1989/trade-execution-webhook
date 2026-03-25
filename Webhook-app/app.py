import os
import json
import requests
from flask import Flask, request

# ==========================
# CONFIG
# ==========================
STATE_FILE = "trades.json"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN")

# ==========================
# STATE
# ==========================
def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE))
    return {}

def save_state(data):
    json.dump(data, open(STATE_FILE, "w"), indent=2)

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
        "securityId": stock.replace(".NS",""),  # ⚠️ may need mapping
        "quantity": qty
    }

    headers = {
        "access-token": DHAN_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    r = requests.post(url, json=payload, headers=headers)

    return r.json()

# ==========================
# FLASK
# ==========================
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():

    data = request.json

    if "callback_query" not in data:
        return "OK"

    query = data["callback_query"]
    action, stock = query["data"].split("|")

    state = load_state()
    trade = state.get(stock)

    if not trade:
        send_telegram(f"⚠️ Trade not found: {stock}")
        return "OK"

    if action == "BUY":

        res = place_order(stock, trade["qty"])

        # Basic validation
        if "orderId" in str(res):
            trade["status"] = "ACTIVE"
            state[stock] = trade
            save_state(state)

            send_telegram(f"🟢 ORDER PLACED: {stock}")
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
