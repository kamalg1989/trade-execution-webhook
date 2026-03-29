# ==============================================
# 🚀 TELEGRAM WEBHOOK → GITHUB ENTRY ENGINE (FINAL)
# ==============================================

import os
import requests
import pandas as pd
from flask import Flask, request
import threading
import time

# ==========================
# GLOBAL DEDUP STORAGE
# ==========================
PROCESSED_CALLBACKS = set()
PROCESSED_ORDERS = {}

LOCK = threading.Lock()
ORDER_WINDOW = 300  # 5 minutes

# ==========================
# CONFIG
# ==========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

GITHUB_REPO = os.getenv("GITHUB_REPO")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN_CUSTOM")

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
# SYMBOL → SECURITY_ID (optional)
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
# 🚀 GITHUB TRIGGER
# ==========================
def trigger_github_trade(stock, qty, entry, exit_price):

    url = f"https://api.github.com/repos/{GITHUB_REPO}/dispatches"

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {GITHUB_TOKEN}"
    }

    payload = {
        "event_type": "trade_entry",
        "client_payload": {
            "symbol": stock.replace(".NS", ""),
            "qty": qty,
            "entry": entry,
            "exit": exit_price
        }
    }

    r = requests.post(url, json=payload, headers=headers)

    log("🚀 GITHUB TRIGGER:", r.status_code, r.text)

    return r.status_code == 204

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

    # ✅ CALLBACK DEDUP (Telegram duplicate protection)
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
    exit_price = None

    for line in msg_text.split("\n"):
        if "Entry" in line:
            try:
                entry = float(line.split(":")[1].strip())
            except:
                pass

        if "Exit" in line:
            try:
                exit_price = float(line.split(":")[1].strip())
            except:
                pass

    if not entry or not exit_price:
        send_telegram(f"❌ Missing Entry/Exit: {stock}")
        return "OK"

    if action == "BUY":

        key = f"{stock}_{qty}_{entry}_{exit_price}"
        now = time.time()

        # ✅ ORDER DEDUP (GitHub trigger protection)
        with LOCK:
            if key in PROCESSED_ORDERS:
                if (now - PROCESSED_ORDERS[key]) < ORDER_WINDOW:
                    log("⚠️ Duplicate order blocked:", key)
                    return "OK"

            PROCESSED_ORDERS[key] = now

        # 🚀 SINGLE trigger (FIXED)
        success = trigger_github_trade(stock, qty, entry, exit_price)

        if success:
            send_telegram(f"🟢 SENT TO EXECUTION: {stock}")
        else:
            send_telegram(f"❌ GITHUB TRIGGER FAILED: {stock}")

    return "OK"

@app.route("/")
def home():
    return "Webhook running"
