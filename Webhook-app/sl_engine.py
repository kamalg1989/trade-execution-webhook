# ==============================================
# 🚀 SL ENGINE V2 (STATE-DRIVEN - FINAL)
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

BASE_SL_PCT = 0.92

CURRENT_TOKEN = None
TOKEN_EXPIRY = None


def log(*args):
    print(*args, flush=True)


# ==========================
# DB INIT
# ==========================
def init_db():
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY,
        symbol TEXT,
        security_id TEXT,
        qty INTEGER,
        entry_price REAL,
        status TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY,
        symbol TEXT,
        dhan_order_id TEXT,
        trigger_price REAL,
        status TEXT
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
        r = requests.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": totp.now()
            },
            timeout=10
        )

        data = r.json()

        if "accessToken" in data:
            TOKEN_EXPIRY = datetime.fromisoformat(data["expiryTime"]).replace(tzinfo=timezone.utc)
            return data["accessToken"]

        time.sleep(2)

    raise Exception("Token failed")


def get_token():
    global CURRENT_TOKEN

    if not CURRENT_TOKEN or datetime.now(timezone.utc) > TOKEN_EXPIRY:
        CURRENT_TOKEN = generate_token()

    return CURRENT_TOKEN


# ==========================
# DHAN FETCH
# ==========================
def fetch_positions():
    r = requests.get("https://api.dhan.co/v2/positions",
                     headers={"access-token": get_token()}, timeout=10)
    return r.json() if r.status_code == 200 else []


def fetch_holdings():
    r = requests.get("https://api.dhan.co/v2/holdings",
                     headers={"access-token": get_token()}, timeout=10)
    return r.json() if r.status_code == 200 else []


def fetch_orders():
    r = requests.get("https://api.dhan.co/v2/forever/orders",
                     headers={"access-token": get_token()}, timeout=10)
    return r.json() if r.status_code == 200 else []


# ==========================
# STATE SYNC
# ==========================
def sync_trades_with_dhan(positions, holdings):

    conn = sqlite3.connect(DB_FILE)

    active_symbols = set()

    for p in positions:
        if int(p.get("netQty", 0)) > 0:
            sym = p["tradingSymbol"]
            active_symbols.add(sym)

            conn.execute("""
            INSERT OR REPLACE INTO trades(symbol, security_id, qty, entry_price, status)
            VALUES (?, ?, ?, ?, 'OPEN')
            """, (sym, p["securityId"], p["netQty"], p["buyAvg"]))

    for h in holdings:
        if int(h.get("totalQty", 0)) > 0:
            sym = h["tradingSymbol"]
            active_symbols.add(sym)

            conn.execute("""
            INSERT OR REPLACE INTO trades(symbol, security_id, qty, entry_price, status)
            VALUES (?, ?, ?, ?, 'OPEN')
            """, (sym, h["securityId"], h["totalQty"], h["avgCostPrice"]))

    rows = conn.execute("SELECT symbol FROM trades WHERE status='OPEN'").fetchall()

    for (sym,) in rows:
        if sym not in active_symbols:
            log(f"🔒 Closing trade → {sym}")
            conn.execute("UPDATE trades SET status='CLOSED' WHERE symbol=?", (sym,))

    conn.commit()
    conn.close()


def sync_orders_with_dhan(dhan_orders):

    conn = sqlite3.connect(DB_FILE)

    conn.execute("DELETE FROM orders")

    for o in dhan_orders:
        if o.get("transactionType") != "SELL":
            continue

        conn.execute("""
        INSERT INTO orders(symbol, dhan_order_id, trigger_price, status)
        VALUES (?, ?, ?, ?)
        """, (
            o.get("tradingSymbol"),
            o.get("orderId"),
            float(o.get("triggerPrice", 0)),
            o.get("orderStatus", "UNKNOWN")
        ))

    conn.commit()
    conn.close()


# ==========================
# LTP
# ==========================
def get_ltp(symbol):
    try:
        data = yf.Ticker(symbol + ".NS")
        return round(data.fast_info["lastPrice"], 2)
    except:
        return None


# ==========================
# SL CALC
# ==========================
def calculate_sl(entry, ltp, current_sl):

    base = entry * BASE_SL_PCT

    if not current_sl:
        return base

    if ltp <= entry:
        return base

    profit = ltp - entry
    new_sl = entry + profit * 0.5
    new_sl = min(new_sl, ltp * 0.995)

    return max(current_sl, new_sl)


# ==========================
# ORDER API
# ==========================
def place_sl(sec_id, qty, trigger):

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(uuid.uuid4())[:20],
        "transactionType": "SELL",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",
        "securityId": sec_id,
        "quantity": qty,
        "price": round(trigger * 0.995, 2),
        "triggerPrice": trigger
    }

    r = requests.post("https://api.dhan.co/v2/forever/orders",
                      json=payload,
                      headers={"access-token": get_token()},
                      timeout=10)

    if r.status_code == 200:
        return r.json().get("orderId")

    return None


def modify_sl(order_id, qty, trigger):

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "orderId": order_id,
        "quantity": qty,
        "price": round(trigger * 0.995, 2),
        "triggerPrice": trigger
    }

    r = requests.put(
        f"https://api.dhan.co/v2/forever/orders/{order_id}",
        json=payload,
        headers={"access-token": get_token()},
        timeout=10
    )

    return r.status_code == 200


# ==========================
# MAIN
# ==========================
def run():

    log("\n🚀 SL ENGINE V2 START\n")

    init_db()

    positions = fetch_positions()
    holdings = fetch_holdings()
    orders = fetch_orders()

    sync_trades_with_dhan(positions, holdings)
    sync_orders_with_dhan(orders)

    conn = sqlite3.connect(DB_FILE)

    trades = conn.execute("SELECT symbol, security_id, qty, entry_price FROM trades WHERE status='OPEN'").fetchall()
    order_rows = conn.execute("SELECT symbol, dhan_order_id, trigger_price FROM orders").fetchall()

    order_map = {o[0]: o for o in order_rows}

    for sym, sec_id, qty, entry in trades:

        ltp = get_ltp(sym)
        if not ltp:
            continue

        existing = order_map.get(sym)
        current_sl = existing[2] if existing else None

        new_sl = calculate_sl(entry, ltp, current_sl)

        log(f"{sym} | LTP={ltp} | SL={current_sl} → {new_sl}")

        if not existing:
            log(f"🆕 Place SL → {sym}")
            place_sl(sec_id, qty, new_sl)
            continue

        order_id = existing[1]

        if new_sl > current_sl:
            log(f"🔄 Trail SL → {sym}")
            modify_sl(order_id, qty, new_sl)

    conn.close()

    log("\n✅ DONE\n")


if __name__ == "__main__":
    run()