# ==============================================
# 🚀 TELEGRAM WEBHOOK → GITHUB ENTRY ENGINE (FINAL)
# ==============================================

import os
import requests
import pandas as pd
from flask import Flask, request
import threading
import time
import subprocess
import json

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

INSTRUMENT_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

# Absolute path to entry engine
ENTRY_ENGINE_PATH = "/root/trade-execution-webhook/Webhook-app/entry_engine.py"
PROJECT_ROOT = "/root/trade-execution-webhook"

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

INSTRUMENT_DF = None

# ==========================
# SYMBOL → SECURITY_ID (optional)
# ==========================
def get_security_id(stock):
    global INSTRUMENT_DF
    if INSTRUMENT_DF is None:
        try:
            INSTRUMENT_DF = load_instruments()
        except Exception as e:
            log(f"⚠️ Failed to load instruments: {e}")
            return None

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
def trigger_github_trade(payload):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/dispatches"

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {GITHUB_TOKEN}"
    }

    if not payload.get("sl") or not payload.get("target"):
        log("❌ BLOCKED: SL/Target missing in payload", payload)
        return False

    data = {
        "event_type": "trade_entry",
        "client_payload": payload
    }

    r = requests.post(url, json=data, headers=headers)

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
    log("RAW CALLBACK:", query.get("data"))

    if len(parts) < 8:
        send_telegram("❌ Invalid payload")
        return "OK"

    action, setup_id, symbol, qty, entry, sl, target, score = parts

    def safe_float(x):
        try:
            return float(x)
        except:
            return None

    stock = symbol
    qty = int(qty)
    entry = safe_float(entry)
    sl = safe_float(sl)
    target = safe_float(target)
    score = safe_float(score)

    if not setup_id:
        send_telegram(f"❌ Missing setup_id for {stock}")
        return "OK"

    if sl is None or target is None:
        send_telegram(f"❌ Missing SL/Target: {stock}")
        return "OK"

    if action == "BUY":

        key = setup_id
        now = time.time()

        # ✅ ORDER DEDUP (GitHub trigger protection)
        with LOCK:
            if key in PROCESSED_ORDERS:
                if (now - PROCESSED_ORDERS[key]) < ORDER_WINDOW:
                    log("⚠️ Duplicate order blocked:", key)
                    return "OK"

            PROCESSED_ORDERS[key] = now

        # 🚀 SINGLE trigger (FIXED)
        success = trigger_github_trade({
            "setup_id": setup_id,
            "symbol": stock.replace(".NS", ""),
            "qty": qty,
            "entry": entry,
            "sl": sl,
            "target": target,
            "score": score
        })

        if success:
            send_telegram(f"🟢 SENT: {stock} | SL={sl} | TGT={target}")
        else:
            send_telegram(f"❌ GITHUB TRIGGER FAILED: {stock}")

    return "OK"

@app.route("/")
def home():
    return "Webhook running"
