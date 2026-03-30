# ==============================================
# 🚀 TRAILING SL ENGINE (FINAL - POSITION SYNC)
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
# DB INIT
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
        status TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER,
        dhan_order_id TEXT,
        type TEXT,
        side TEXT,
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
            log("⏳ Rate limit wait")
            time.sleep(130)

        time.sleep(2)

    raise Exception("Token failed")


def get_token():
    global CURRENT_TOKEN
    if not CURRENT_TOKEN or datetime.now(timezone.utc) > TOKEN_EXPIRY:
        CURRENT_TOKEN = generate_token()
    return CURRENT_TOKEN

# ==========================
# FETCH
# ==========================
def fetch_positions():
    r = requests.get(
        "https://api.dhan.co/v2/positions",
        headers={"access-token": get_token()},
        timeout=10
    )
    data = r.json() if r.status_code == 200 else []
    log("📊 POSITIONS:", data)
    return data


def fetch_forever_orders():
    r = requests.get(
        "https://api.dhan.co/v2/forever/orders",
        headers={"access-token": get_token()},
        timeout=10
    )
    data = r.json() if r.status_code == 200 else []
    log("📦 FOREVER:", data)
    return data

# ==========================
# LTP
# ==========================
def get_ltp(symbol):
    try:
        data = yf.Ticker(symbol + ".NS")
        price = data.fast_info.get("lastPrice")
        if price:
            price = round(float(price), 2)
            log(f"🌐 LTP → {symbol} = {price}")
            return price
    except Exception as e:
        log("❌ LTP error:", e)
    return None

# ==========================
# DB SYNC (POSITIONS BASED)
# ==========================
def sync_trades(positions):

    conn = sqlite3.connect(DB_FILE)

    # mark all closed first
    conn.execute("UPDATE trades SET status='CLOSED'")

    for p in positions:

        qty = int(p.get("netQty", 0))
        if qty <= 0:
            continue

        symbol = p["tradingSymbol"]
        sec_id = p["securityId"]
        entry = float(p.get("buyAvg"))

        log(f"🔄 Sync → {symbol}")

        existing = conn.execute("""
            SELECT id FROM trades WHERE symbol=? AND status='OPEN'
        """, (symbol,)).fetchone()

        if existing:
            conn.execute("""
                UPDATE trades
                SET qty=?, entry_price=?, status='OPEN'
                WHERE id=?
            """, (qty, entry, existing[0]))
        else:
            conn.execute("""
                INSERT INTO trades
                (symbol, security_id, qty, entry_price, entry_time, status)
                VALUES (?, ?, ?, ?, ?, 'OPEN')
            """, (symbol, sec_id, qty, entry, datetime.now().isoformat()))

    conn.commit()
    conn.close()


def get_open_trades():
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
    conn.close()
    return rows


def upsert_sl(trade_id, order_id, trigger):
    conn = sqlite3.connect(DB_FILE)

    exists = conn.execute("""
        SELECT id FROM orders WHERE trade_id=? AND type='SL'
    """, (trade_id,)).fetchone()

    if exists:
        conn.execute("""
            UPDATE orders
            SET dhan_order_id=?, trigger_price=?, status='PENDING'
            WHERE trade_id=? AND type='SL'
        """, (order_id, trigger, trade_id))
    else:
        conn.execute("""
            INSERT INTO orders
            (trade_id, dhan_order_id, type, side, trigger_price, status, created_at)
            VALUES (?, ?, 'SL', 'SELL', ?, 'PENDING', ?)
        """, (trade_id, order_id, trigger, datetime.now().isoformat()))

    conn.commit()
    conn.close()

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

    log("🆕 PLACE SL:", payload)

    r = requests.post(
        "https://api.dhan.co/v2/forever/orders",
        json=payload,
        headers={"access-token": get_token()}
    )

    log("📉 PLACE:", r.status_code, r.text)

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

    log("🔄 MODIFY:", payload)

    r = requests.put(
        f"https://api.dhan.co/v2/forever/orders/{order_id}",
        json=payload,
        headers={"access-token": get_token()}
    )

    log("📉 MODIFY:", r.status_code, r.text)

    return r.status_code == 200


def cancel_order(order_id):
    log("🗑️ CANCEL:", order_id)
    requests.delete(
        f"https://api.dhan.co/v2/forever/orders/{order_id}",
        headers={"access-token": get_token()}
    )

# ==========================
# SL CLEANUP
# ==========================
def get_live_sl(sec_id, forever_orders, ltp):

    sl_orders = [
        o for o in forever_orders
        if str(o.get("securityId")) == str(sec_id)
           and o.get("transactionType") == "SELL"
           and o.get("orderStatus") in ["PENDING", "TRANSIT"]
    ]

    if not sl_orders:
        return None

    valid = []

    for o in sl_orders:
        tp = float(o.get("triggerPrice", 0))

        if tp < ltp:
            valid.append(o)
        else:
            cancel_order(o["orderId"])

    if not valid:
        return None

    valid.sort(key=lambda x: float(x.get("triggerPrice")), reverse=True)
    keep = valid[0]

    for o in valid[1:]:
        cancel_order(o["orderId"])

    log(f"✅ ACTIVE SL → {keep['triggerPrice']}")

    return keep

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

    positions = fetch_positions()
    forever_orders = fetch_forever_orders()

    sync_trades(positions)

    trades = get_open_trades()

    for t in trades:

        trade_id, symbol, sec_id, qty, entry, *_ = t

        ltp = get_ltp(symbol)
        if not ltp:
            continue

        log(f"\n➡️ {symbol} | Entry={entry} | LTP={ltp}")

        live_sl = get_live_sl(sec_id, forever_orders, ltp)

        prev_sl = None
        order_id = None

        if live_sl:
            prev_sl = float(live_sl["triggerPrice"])
            order_id = live_sl["orderId"]

            upsert_sl(trade_id, order_id, prev_sl)

        new_sl = calculate_sl(entry, ltp, prev_sl)

        log(f"SL OLD={prev_sl} → NEW={new_sl}")

        if not order_id:
            oid = place_sl(sec_id, qty, new_sl)
            if oid:
                upsert_sl(trade_id, oid, new_sl)

        else:
            if new_sl > prev_sl:
                if modify_sl(order_id, qty, new_sl):
                    upsert_sl(trade_id, order_id, new_sl)

    log("\n✅ DONE\n")


if __name__ == "__main__":
    run()