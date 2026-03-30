# ==============================================
# 🚀 TRAILING SL ENGINE (FINAL - HOLDINGS FIXED)
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
# DB INIT + MIGRATION
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
# CLEAN DB (REMOVE BAD DATA)
# ==========================
def clean_db():
    conn = sqlite3.connect(DB_FILE)

    log("🧹 Cleaning DB...")

    # remove duplicate trades
    conn.execute("""
    DELETE FROM trades
    WHERE id NOT IN (
        SELECT MIN(id)
        FROM trades
        GROUP BY symbol
    )
    """)

    # remove orphan orders
    conn.execute("""
    DELETE FROM orders
    WHERE trade_id NOT IN (SELECT id FROM trades)
    """)

    conn.commit()
    conn.close()

    log("✅ DB Cleaned")


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

        time.sleep(2)

    raise Exception("Token failed")


def get_token():
    global CURRENT_TOKEN

    if not CURRENT_TOKEN or datetime.now(timezone.utc) > TOKEN_EXPIRY:
        CURRENT_TOKEN = generate_token()

    return CURRENT_TOKEN


# ==========================
# FETCH DATA
# ==========================
def fetch_positions():
    r = requests.get(
        "https://api.dhan.co/v2/positions",
        headers={"access-token": get_token()},
        timeout=10
    )
    return r.json() if r.status_code == 200 else []


def fetch_holdings():
    r = requests.get(
        "https://api.dhan.co/v2/holdings",
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
# RECONCILE DHAN FOREVER ORDERS (SOURCE OF TRUTH)
# ==========================
def reconcile_forever_orders():

    orders = fetch_forever_orders()

    if not isinstance(orders, list):
        log("⚠️ No forever orders fetched")
        return

    log(f"📦 FOREVER ORDERS COUNT: {len(orders)}")

    # group by symbol
    symbol_map = {}

    for o in orders:
        if o.get("transactionType") != "SELL":
            continue

        sym = o.get("tradingSymbol")
        symbol_map.setdefault(sym, []).append(o)

    for sym, group in symbol_map.items():

        if len(group) <= 1:
            continue

        log(f"⚠️ DUPLICATES IN DHAN → {sym} ({len(group)})")

        # keep highest trigger price
        group_sorted = sorted(group, key=lambda x: float(x.get("triggerPrice", 0)), reverse=True)

        keep = group_sorted[0]
        keep_id = keep["orderId"]
        keep_tp = keep.get("triggerPrice")

        log(f"✅ KEEPING → {sym} @ {keep_tp} ({keep_id})")

        for o in group_sorted[1:]:
            oid = o["orderId"]
            log(f"❌ CANCEL (DHAN DUP) → {sym} {oid}")
            cancel_order(oid)


# ==========================
# LTP
# ==========================
def get_ltp(symbol):
    try:
        data = yf.Ticker(symbol + ".NS")

        if data.fast_info:
            price = data.fast_info.get("lastPrice")
            if price:
                return round(float(price), 2)

        hist = data.history(period="1d", interval="1m")
        if not hist.empty:
            return round(hist["Close"].iloc[-1], 2)

    except:
        pass

    return None


# ==========================
# DB HELPERS
# ==========================
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


def insert_trade(symbol, sec_id, qty, price):
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    INSERT INTO trades (symbol, security_id, qty, entry_price, entry_time, status)
    VALUES (?, ?, ?, ?, ?, 'OPEN')
    """, (symbol, sec_id, qty, price, datetime.now().isoformat()))

    conn.commit()
    conn.close()


def insert_order(trade_id, order_id, trigger):
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    INSERT INTO orders (trade_id, dhan_order_id, type, side, trigger_price, status, created_at)
    VALUES (?, ?, 'SL', 'SELL', ?, 'PENDING', ?)
    """, (trade_id, order_id, trigger, datetime.now().isoformat()))

    conn.commit()
    conn.close()


# ==========================
# SYNC TRADES (KEY FIX)
# ==========================
def sync_trades(positions, holdings):

    conn = sqlite3.connect(DB_FILE)

    combined = []

    for p in positions:
        if int(p.get("netQty", 0)) > 0:
            combined.append({
                "symbol": p["tradingSymbol"],
                "securityId": p["securityId"],
                "qty": p["netQty"],
                "price": p["buyAvg"]
            })

    for h in holdings:
        if int(h.get("totalQty", 0)) > 0:
            combined.append({
                "symbol": h["tradingSymbol"],
                "securityId": h["securityId"],
                "qty": h["totalQty"],
                "price": h["avgCostPrice"]
            })

    for p in combined:

        exists = conn.execute("""
            SELECT 1 FROM trades WHERE symbol=? AND status='OPEN'
        """, (p["symbol"],)).fetchone()

        if exists:
            continue

        log(f"🔄 Sync → {p['symbol']}")

        conn.execute("""
        INSERT INTO trades (symbol, security_id, qty, entry_price, entry_time, status)
        VALUES (?, ?, ?, ?, ?, 'OPEN')
        """, (
            p["symbol"],
            p["securityId"],
            p["qty"],
            p["price"],
            datetime.now().isoformat()
        ))

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
# ORDER API
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
        headers={"access-token": get_token()},
        timeout=10
    )

    log("📉 PLACE:", r.status_code, r.text)

    if r.status_code == 200:
        return r.json().get("orderId")

    return None


def cancel_order(order_id):
    r = requests.delete(
        f"https://api.dhan.co/v2/forever/orders/{order_id}",
        headers={"access-token": get_token()},
        timeout=10
    )
    log("🗑️ CANCEL:", order_id, r.status_code, r.text)


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
        headers={"access-token": get_token()},
        timeout=10
    )

    log("📉 MODIFY:", r.status_code, r.text)

    return r.status_code == 200


# ==========================
# MAIN
# ==========================
def run():

    log("\n🚀 START\n")

    init_db()
    clean_db()

    positions = fetch_positions()
    holdings = fetch_holdings()

    # STEP 0: Clean duplicates directly in Dhan
    reconcile_forever_orders()

    sync_trades(positions, holdings)

    trades = get_open_trades()

    for t in trades:

        trade_id, symbol, sec_id, qty, entry, _, status = t

        ltp = get_ltp(symbol)
        if not ltp:
            continue

        log(f"{symbol} | Entry={entry} | LTP={ltp}")

        orders = get_trade_orders(trade_id)

        # ==========================
        # HANDLE DUPLICATE SL (DB LEVEL)
        # ==========================
        if orders:

            log(f"📊 DB ORDERS → {symbol}: {len(orders)}")

            orders_sorted = sorted(orders, key=lambda x: x[5] or 0, reverse=True)

            keep_order = orders_sorted[0]
            keep_order_id = keep_order[2]
            keep_sl = keep_order[5]

            log(f"✅ KEEP DB SL → {keep_sl} ({keep_order_id})")

            for o in orders_sorted[1:]:
                dup_id = o[2]
                dup_sl = o[5]

                log(f"❌ REMOVE DB DUP → {symbol} {dup_id} @ {dup_sl}")
                cancel_order(dup_id)

            conn = sqlite3.connect(DB_FILE)
            conn.execute("""
                DELETE FROM orders 
                WHERE trade_id=? AND dhan_order_id!=?
            """, (trade_id, keep_order_id))
            conn.commit()
            conn.close()

        else:
            log(f"⚠️ NO SL FOUND IN DB → {symbol}")
            keep_order_id = None
            keep_sl = None

        # ==========================
        # PLACE SL IF MISSING
        # ==========================
        if not keep_order_id:
            sl = entry * BASE_SL_PCT

            log(f"🆕 PLACING INITIAL SL → {symbol} @ {sl}")

            oid = place_sl(sec_id, qty, sl)

            if oid:
                insert_order(trade_id, oid, sl)

            continue

        # ==========================
        # TRAILING LOGIC
        # ==========================
        new_sl = calculate_sl(entry, ltp, keep_sl)

        log(f"SL CHECK → OLD={keep_sl} NEW={new_sl}")

        if new_sl > keep_sl:
            log(f"🔄 TRAIL SL → {symbol} {keep_sl} → {new_sl}")
            modify_sl(keep_order_id, qty, new_sl)
        else:
            log(f"⏭️ NO TRAIL → {symbol}")

    log("\n✅ DONE\n")


if __name__ == "__main__":
    run()