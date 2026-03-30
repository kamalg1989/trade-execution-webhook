# ==============================================
# 🚀 TRAILING SL ENGINE (VERSIONED DB + MIGRATION)
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
DB_VERSION = 2   # 🔥 increment when schema changes

CURRENT_TOKEN = None
TOKEN_EXPIRY = None

BASE_SL_PCT = 0.92
MIN_TRAIL_PCT = 0.005


def log(*args):
    print(*args, flush=True)

# ==========================
# DB INIT + VERSIONING
# ==========================
def init_db():

    conn = sqlite3.connect(DB_FILE)

    # meta table for versioning
    conn.execute("""
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    version_row = conn.execute(
        "SELECT value FROM meta WHERE key='db_version'"
    ).fetchone()

    current_version = int(version_row[0]) if version_row else 0

    if current_version < DB_VERSION:
        log(f"⚙️ Migrating DB {current_version} → {DB_VERSION}")
        migrate_db(conn, current_version)

        conn.execute("""
            INSERT OR REPLACE INTO meta (key, value)
            VALUES ('db_version', ?)
        """, (DB_VERSION,))

    conn.commit()
    conn.close()


# ==========================
# MIGRATION LOGIC
# ==========================
def migrate_db(conn, old_version):

    # Drop old tables (safe reset)
    conn.execute("DROP TABLE IF EXISTS trades")
    conn.execute("DROP TABLE IF EXISTS orders")

    # Recreate clean schema
    conn.execute("""
    CREATE TABLE trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT UNIQUE,
        security_id TEXT,
        qty INTEGER,
        entry_price REAL,
        entry_time TEXT,
        status TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE orders (
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

    log("✅ DB schema reset complete")

# ==========================
# DB CLEANUP
# ==========================
def cleanup_db():

    conn = sqlite3.connect(DB_FILE)

    log("🧹 Cleaning DB...")

    # remove orphan orders
    conn.execute("""
        DELETE FROM orders
        WHERE trade_id NOT IN (SELECT id FROM trades)
    """)

    # ensure single SL per trade
    conn.execute("""
        DELETE FROM orders
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM orders
            WHERE type='SL'
            GROUP BY trade_id
        )
        AND type='SL'
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
# DHAN APIs
# ==========================
def fetch_positions():
    r = requests.get(
        "https://api.dhan.co/v2/positions",
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

        if not price:
            hist = data.history(period="1d", interval="1m")
            if not hist.empty:
                price = hist["Close"].iloc[-1]

        if price:
            return round(float(price), 2)

    except:
        pass

    return None

# ==========================
# DB SYNC
# ==========================
def sync_trades(positions):

    conn = sqlite3.connect(DB_FILE)

    conn.execute("UPDATE trades SET status='CLOSED'")

    for p in positions:

        qty = int(p.get("netQty", 0))
        if qty <= 0:
            continue

        symbol = p["tradingSymbol"]
        sec_id = p["securityId"]
        entry = float(p.get("buyAvg"))

        existing = conn.execute("""
            SELECT id FROM trades WHERE symbol=?
        """, (symbol,)).fetchone()

        if existing:
            conn.execute("""
                UPDATE trades
                SET qty=?, entry_price=?, status='OPEN'
                WHERE symbol=?
            """, (qty, entry, symbol))
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
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
    conn.close()
    return rows

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
# MAIN
# ==========================
def run():

    log("\n🚀 START\n")

    init_db()
    cleanup_db()

    positions = fetch_positions()
    forever_orders = fetch_forever_orders()

    sync_trades(positions)

    trades = get_open_trades()

    updated = 0

    for t in trades:

        trade_id = t["id"]
        symbol = t["symbol"]
        sec_id = t["security_id"]
        qty = t["qty"]
        entry = t["entry_price"]

        ltp = get_ltp(symbol)

        if not ltp:
            continue

        log(f"{symbol} | Entry={entry} | LTP={ltp}")

        sl_orders = [
            o for o in forever_orders
            if str(o.get("securityId")) == str(sec_id)
               and o.get("transactionType") == "SELL"
               and o.get("orderStatus") in ["PENDING", "TRANSIT"]
        ]

        # cleanup invalid SL
        valid = []
        for o in sl_orders:
            tp = float(o.get("triggerPrice", 0))

            if tp < ltp:
                valid.append(o)
            else:
                cancel_order(o["orderId"])

        if len(valid) > 1:
            valid.sort(key=lambda x: float(x.get("triggerPrice")), reverse=True)
            for o in valid[1:]:
                cancel_order(o["orderId"])

        order_id = valid[0]["orderId"] if valid else None
        prev_sl = float(valid[0]["triggerPrice"]) if valid else None

        new_sl = calculate_sl(entry, ltp, prev_sl)

        if not order_id:
            place_sl(sec_id, qty, new_sl)
            updated += 1
            continue

        if new_sl <= prev_sl:
            continue

        if modify_sl(order_id, qty, new_sl):
            updated += 1

    log(f"\n✅ DONE | Updated: {updated}")


if __name__ == "__main__":
    run()