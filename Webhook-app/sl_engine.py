# ==============================================
# 🚀 TRAILING SL ENGINE (DB BASED)
# ==============================================

import os
import requests
import pyotp
import sqlite3
import uuid
import time
import yfinance as yf
from datetime import datetime, timezone

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")

DB_FILE = "Webhook-app/trades.db"

CURRENT_TOKEN = None
TOKEN_EXPIRY = None
MIN_TRAIL_PCT = 0.005


def log(*args):
    print(*args, flush=True)

# ==========================
# DB
# ==========================

def init_db():
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        security_id TEXT,
        qty INTEGER,
        entry_price REAL,
        planned_exit REAL,
        trailing_sl REAL,
        order_id TEXT,
        status TEXT,
        entry_time TEXT
    )
    """)

    conn.commit()
    conn.close()

def get_open_trades():
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
    conn.close()
    return rows

def update_sl(trade_id, sl):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("UPDATE trades SET trailing_sl=? WHERE id=?", (sl, trade_id))
    conn.commit()
    conn.close()

# ==========================
# TOKEN
# ==========================
def generate_token():
    global TOKEN_EXPIRY

    totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()

    r = requests.post(
        "https://auth.dhan.co/app/generateAccessToken",
        params={
            "dhanClientId": DHAN_CLIENT_ID,
            "pin": DHAN_PIN,
            "totp": totp
        }
    )

    data = r.json()

    if "accessToken" in data:
        TOKEN_EXPIRY = datetime.fromisoformat(data["expiryTime"]).replace(tzinfo=timezone.utc)
        return data["accessToken"]

    raise Exception("Token failed")

def get_token():
    global CURRENT_TOKEN
    if not CURRENT_TOKEN or datetime.now(timezone.utc) > TOKEN_EXPIRY:
        CURRENT_TOKEN = generate_token()
    return CURRENT_TOKEN

# ==========================
# LTP
# ==========================
def get_ltp(symbol):
    try:
        data = yf.Ticker(symbol + ".NS")
        price = data.fast_info.get("lastPrice")
        if price:
            return round(float(price), 2)
    except:
        pass
    return None

# ==========================
# SL LOGIC
# ==========================
def calculate_sl(entry, ltp, prev_sl):

    base = entry * 0.92

    if ltp <= entry:
        return base

    profit = ltp - entry
    new_sl = entry + profit * 0.5
    new_sl = min(new_sl, ltp * 0.995)

    return max(prev_sl, new_sl)

# ==========================
# ORDER
# ==========================
def modify_sl(order_id, qty, trigger):

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "orderId": order_id,
        "orderFlag": "SINGLE",
        "orderType": "LIMIT",
        "legName": "STOP_LOSS_LEG",
        "quantity": qty,
        "price": round(trigger * 0.995, 2),
        "triggerPrice": trigger,
        "validity": "DAY"
    }

    r = requests.put(
        f"https://api.dhan.co/v2/forever/orders/{order_id}",
        json=payload,
        headers={"access-token": get_token()}
    )

    return r.status_code == 200

# ==========================
# MAIN
# ==========================
def run():

    log("\n🚀 TRAILING SL ENGINE START\n")
    init_db() 
    trades = get_open_trades()
    updated = 0

    for t in trades:

        trade_id, symbol, sec_id, qty, entry, exit_price, prev_sl, order_id, status, _ = t

        ltp = get_ltp(symbol)

        if not ltp:
            continue

        log(f"{symbol} | Entry={entry} | LTP={ltp}")

        # EXIT override
        if exit_price and ltp < exit_price:
            new_sl = exit_price
        else:
            new_sl = calculate_sl(entry, ltp, prev_sl)

        if new_sl <= prev_sl:
            continue

        if ((new_sl - prev_sl) / prev_sl) < MIN_TRAIL_PCT:
            continue

        if order_id and modify_sl(order_id, qty, new_sl):
            update_sl(trade_id, new_sl)
            updated += 1

    log(f"\n✅ DONE | Updated: {updated}")


if __name__ == "__main__":
    run()
