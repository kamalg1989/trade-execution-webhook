# ==============================================
# 🚀 TELEGRAM WEBHOOK → GITHUB ENTRY ENGINE
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

GITHUB_REPO = os.getenv("GITHUB_REPO")  # e.g. kamalg1989/repo
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
# SYMBOL → SECURITY_ID
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

    if not entry:
        send_telegram(f"❌ Missing Entry: {stock}")
        return "OK"

    if action == "BUY":

        success = trigger_github_trade(stock, qty, entry, exit_price)

        if success:
            send_telegram(f"🟢 SENT TO EXECUTION: {stock}")
        else:
            send_telegram(f"❌ GITHUB TRIGGER FAILED: {stock}")

    return "OK"

@app.route("/")
def home():
    return "Webhook running"
