# ==============================================
# 🚀 TELEGRAM WEBHOOK → GITHUB ENTRY ENGINE (FINAL)
# ==============================================

import os
import requests
import pandas as pd
from flask import Flask, request
import threading
import time
import sqlite3
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

GITHUB_REPO = os.getenv("GITHUB_REPO")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN_CUSTOM")

INSTRUMENT_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

# ==========================
# DB CONFIG & HELPER
# ==========================
DB_PATH = os.getenv("DB_PATH", "trades.db")

def fetch_trade_setup(setup_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT * FROM trade_setups WHERE setup_id = ?", (setup_id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            log("❌ Setup not found in DB:", setup_id)
            return None

        return dict(row)

    except Exception as e:
        log("❌ DB fetch error:", e)
        return None

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
def trigger_github_trade(stock, qty, entry, sl, target, strategy, timeframe, score, setup_id):

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
            "sl": sl,
            "target": target,
            "strategy": strategy,
            "timeframe": timeframe,
            "score": score,
            "setup_id": setup_id
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

    if len(parts) < 2:
        send_telegram("❌ Invalid payload")
        return "OK"

    action = parts[0]
    setup_id = parts[1]

    trade = fetch_trade_setup(setup_id)

    if not trade:
        send_telegram(f"❌ Setup not found: {setup_id}")
        return "OK"

    stock = trade.get("symbol")
    qty = int(trade.get("qty"))
    entry = float(trade.get("entry"))
    sl = float(trade.get("sl"))
    target = float(trade.get("target"))
    strategy = trade.get("strategy")
    timeframe = trade.get("timeframe")
    score = float(trade.get("score"))

    log("🧾 DB TRADE:", trade)

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
        success = trigger_github_trade(
            stock, qty, entry, sl, target,
            strategy, timeframe, score, setup_id
        )

        if success:
            send_telegram(f"🟢 SENT: {stock} | SL={sl} | TGT={target}")
        else:
            send_telegram(f"❌ GITHUB TRIGGER FAILED: {stock}")

    return "OK"

@app.route("/")
def home():
    return "Webhook running"
