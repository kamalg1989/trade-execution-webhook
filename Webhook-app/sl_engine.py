# ==============================================
# 🚀 TRAILING SL ENGINE (2-TABLE MODEL)
# ==============================================

import os
import requests
import pyotp
import sqlite3
import uuid
import time
import yfinance as yf
from datetime import datetime, timezone

# ==========================
# CONFIG
# ==========================
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")

DB_FILE = "Webhook-app/trades.db"

CURRENT_TOKEN = None
TOKEN_EXPIRY = None

BASE_SL_PCT = 0.92
MIN_TRAIL_PCT = 0.005


def log(*args):
    print(*args, flush=True)

# ==========================
# DB INIT (2 TABLES)
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
        entry_time TEXT,
        exit_price REAL,
        exit_time TEXT,
        pnl REAL,
        status TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER,
        dhan_order_id TEXT,
        type TEXT,              -- BUY / SL
        side TEXT,              -- BUY / SELL
        price REAL,
        trigger_price REAL,
        status TEXT,
        created_at TEXT
    )
    """)

    conn.commit()
    conn.close()

# ==========================
# TOKEN
# ==========================
def generate_token():
    global TOKEN_EXPIRY

    totp = pyotp.TOTP(DHAN_TOTP_SECRET)

    for _ in range(3):
        code = totp.now()
        r = requests.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": code
            },
            timeout=10
        )

        data = r.json()
        log("🔍 TOKEN:", data)

        if "accessToken" in data:
            TOKEN_EXPIRY = datetime.fromisoformat(
                data["expiryTime"]
            ).replace(tzinfo=timezone.utc)
            return data["accessToken"]

        if "2 minutes" in str(data):
            time.sleep(130)

        time.sleep(2)

    raise Exception("Token failed")


def get_token():
    global CURRENT_TOKEN
    if not CURRENT_TOKEN or datetime.now(timezone.utc) > TOKEN_EXPIRY:
        CURRENT_TOKEN = generate_token()
    return CURRENT_TOKEN

# ==========================
# FETCH ORDERS
# ==========================
def fetch_all_orders():
    r = requests.get(
        "https://api.dhan.co/v2/orders",
        headers={"access-token": get_token()},
        timeout=10
    )
    return r.json() if r.status_code == 200 else []


def fetch_forever_orders():
    r = requests.get(
        "https://api.dhan.co/v2/forever/orders",
        headers={"access-token": get_token()},
        timeout=10
    )
    return r.json() if r.status_code == 200 else []

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
# DB HELPERS
# ==========================
def insert_trade(symbol, sec_id, qty, price):
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    INSERT INTO trades (symbol, security_id, qty, entry_price, entry_time, status)
    VALUES (?, ?, ?, ?, ?, 'OPEN')
    """, (symbol, sec_id, qty, price, datetime.now().isoformat()))

    conn.commit()
    conn.close()


def insert_order(trade_id, order_id, typ, side, price, trigger, status):
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    INSERT INTO orders 
    (trade_id, dhan_order_id, type, side, price, trigger_price, status, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade_id, order_id, typ, side, price, trigger,
        status, datetime.now().isoformat()
    ))

    conn.commit()
    conn.close()


def get_open_trades():
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
    conn.close()
    return rows


def get_trade_orders(trade_id):
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("SELECT * FROM orders WHERE trade_id=?", (trade_id,)).fetchall()
    conn.close()
    return rows

# ==========================
# ORDER OPS
# ==========================
def place_sl(sec_id, qty, trigger):

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(uuid.uuid4())[:20],
        "orderFlag": "SINGLE",
        "transactionType": "SELL",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",
        "validity": "DAY",
        "securityId": sec_id,
        "quantity": qty,
        "price": round(trigger * 0.995, 2),
        "triggerPrice": trigger
    }

    r = requests.post(
        "https://api.dhan.co/v2/forever/orders",
        json=payload,
        headers={"access-token": get_token()}
    )

    if r.status_code == 200:
        return r.json().get("orderId")

    return None


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


def cancel_order(order_id):
    requests.delete(
        f"https://api.dhan.co/v2/forever/orders/{order_id}",
        headers={"access-token": get_token()}
    )

# ==========================
# BUILD TRADES FROM DHAN
# ==========================
def sync_trades_from_dhan(all_orders):

    conn = sqlite3.connect(DB_FILE)

    for o in all_orders:

        if o.get("transactionType") != "BUY":
            continue

        if o.get("orderStatus") not in ["TRADED", "COMPLETE"]:
            continue

        order_id = o.get("orderId")

        exists = conn.execute(
            "SELECT 1 FROM orders WHERE dhan_order_id=?",
            (order_id,)
        ).fetchone()

        if exists:
            continue

        symbol = o["tradingSymbol"]
        sec_id = o["securityId"]
        qty = o["quantity"]
        price = o["price"]

        log(f"🆕 New trade → {symbol}")

        conn.execute("""
        INSERT INTO trades (symbol, security_id, qty, entry_price, entry_time, status)
        VALUES (?, ?, ?, ?, ?, 'OPEN')
        """, (symbol, sec_id, qty, price, datetime.now().isoformat()))

        trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute("""
        INSERT INTO orders 
        (trade_id, dhan_order_id, type, side, price, status, created_at)
        VALUES (?, ?, 'BUY', 'BUY', ?, 'TRADED', ?)
        """, (trade_id, order_id, price, datetime.now().isoformat()))

    conn.commit()
    conn.close()

# ==========================
# SL LOGIC
# ==========================
def calculate_sl(entry, ltp, prev):

    base = entry * BASE_SL_PCT

    if not prev:
        return base

    if ltp <= entry:
        return base

    profit = ltp - entry
    new_sl = entry + profit * 0.5
    new_sl = min(new_sl, ltp * 0.995)

    return max(prev, new_sl)

# ==========================
# MAIN
# ==========================
def run():

    log("\n🚀 START\n")

    init_db()

    all_orders = fetch_all_orders()
    forever_orders = fetch_forever_orders()

    # STEP 1: Build trades
    sync_trades_from_dhan(all_orders)

    trades = get_open_trades()

    for t in trades:

        trade_id, symbol, sec_id, qty, entry, _, _, _, _, _ = t

        ltp = get_ltp(symbol)
        if not ltp:
            continue

        log(f"{symbol} | Entry={entry} | LTP={ltp}")

        trade_orders = get_trade_orders(trade_id)

        sl_orders = [o for o in trade_orders if o[3] == "SL"]

        prev_sl = None
        order_id = None

        if sl_orders:
            prev_sl = sl_orders[0][6]
            order_id = sl_orders[0][2]

        new_sl = calculate_sl(entry, ltp, prev_sl)

        if not order_id:
            oid = place_sl(sec_id, qty, new_sl)

            if oid:
                insert_order(trade_id, oid, "SL", "SELL", None, new_sl, "PENDING")

        else:
            if new_sl > prev_sl:
                modify_sl(order_id, qty, new_sl)

    log("\n✅ DONE\n")


if __name__ == "__main__":
    run()
