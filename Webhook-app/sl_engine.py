# ==============================================
# 🚀 SL ENGINE V4 (VPS INTEGRATED + STABLE)
# ==============================================

import os
import requests
import pyotp
import sqlite3
import uuid
import logging
import yfinance as yf
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# ==========================
# LOAD ENVIRONMENT VARIABLES
# ==========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, "..", ".env")
load_dotenv(ENV_PATH)

# ==========================
# CONFIG
# ==========================
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.db")

BASE_SL_PCT = 0.92

CURRENT_TOKEN = None
TOKEN_EXPIRY = None


def log(*args):
    print(*args, flush=True)


# ==========================
# TELEGRAM
# ==========================
def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{os.getenv('TELEGRAM_TOKEN')}/sendMessage",
            json={"chat_id": os.getenv("TELEGRAM_CHAT_ID"), "text": msg},
            timeout=10
        )
    except Exception as e:
        log(f"❌ Telegram Error: {e}")


# ==========================
# TOKEN
# ==========================
def generate_token():
    global TOKEN_EXPIRY

    totp = pyotp.TOTP(DHAN_TOTP_SECRET)

    r = requests.post(
        "https://auth.dhan.co/app/generateAccessToken",
        params={
            "dhanClientId": DHAN_CLIENT_ID,
            "pin": DHAN_PIN,
            "totp": totp.now()
        },
        timeout=10
    )

    if r.status_code != 200:
        raise Exception(f"Token API failed: {r.text}")

    data = r.json()

    # DEBUG
    print("TOKEN RESPONSE:", data)

    if "accessToken" not in data:
        raise Exception(f"Invalid token response: {data}")

    expiry = data.get("expiryTime")

    if expiry:
        try:
            TOKEN_EXPIRY = datetime.fromisoformat(expiry).replace(tzinfo=timezone.utc)
        except Exception:
            TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(minutes=10)
    else:
        # fallback if expiry not provided
        TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(minutes=10)

    return data["accessToken"]


def get_token():
    global CURRENT_TOKEN

    if not CURRENT_TOKEN or datetime.now(timezone.utc) > TOKEN_EXPIRY:
        CURRENT_TOKEN = generate_token()

    return CURRENT_TOKEN


# ==========================
# DB INIT
# ==========================
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sl_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT UNIQUE,
                security_id TEXT,
                dhan_order_id TEXT,
                trigger_price REAL,
                status TEXT,
                placed_at TEXT,
                modified_at TEXT
            )
        """)
        # Migrate trades table — add columns that may be missing from older DBs
        for col in ["sl_price REAL", "target_price REAL", "setup_id TEXT"]:
            try:
                conn.execute(f"ALTER TABLE trades ADD COLUMN {col}")
            except Exception:
                pass


# ==========================
# DHAN FETCH
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


def fetch_orders():
    r = requests.get(
        "https://api.dhan.co/v2/forever/orders",
        headers={"access-token": get_token()},
        timeout=10
    )
    return r.json() if r.status_code == 200 else []


# ==========================
# SYNC TRADES
# ==========================
def sync_trades(positions, holdings):
    active = set()

    for p in positions:
        if int(p.get("netQty", 0)) > 0:
            active.add(p["tradingSymbol"])

    for h in holdings:
        if int(h.get("totalQty", 0)) > 0:
            active.add(h["tradingSymbol"])

    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute("SELECT symbol FROM trades WHERE status='OPEN'").fetchall()
        for (sym,) in rows:
            if sym not in active:
                log(f"🔒 Closing → {sym}")
                conn.execute("UPDATE trades SET status='CLOSED' WHERE symbol=?", (sym,))


# ==========================
# SYNC ORDERS
# ==========================
def sync_orders(dhan_orders):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM sl_orders")
        for o in dhan_orders:
            if o.get("transactionType") != "SELL":
                continue
            conn.execute("""
                INSERT INTO sl_orders(symbol, dhan_order_id, trigger_price, status)
                VALUES (?, ?, ?, ?)
            """, (
                o.get("tradingSymbol"),
                o.get("orderId"),
                float(o.get("triggerPrice", 0)),
                o.get("orderStatus", "UNKNOWN")
            ))


# ==========================
# LTP
# ==========================
def get_ltp(symbol):
    try:
        return yf.Ticker(symbol + ".NS").fast_info["lastPrice"]
    except Exception as e:
        log(f"⚠️ LTP fetch failed for {symbol}: {e}")
        return None


# ==========================
# SL LOGIC
# ==========================
def calculate_sl(entry, ltp, current_sl):

    base = entry * BASE_SL_PCT

    if current_sl is None:
        return base

    if ltp <= entry:
        return base

    profit = ltp - entry
    trail = entry + (profit * 0.5)

    return max(current_sl, min(trail, ltp * 0.995))


# ==========================
# ORDER ACTIONS
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

    requests.post(
        "https://api.dhan.co/v2/forever/orders",
        json=payload,
        headers={"access-token": get_token()},
        timeout=10
    )


def modify_sl(order_id, qty, trigger):

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "orderId": order_id,
        "quantity": qty,
        "price": round(trigger * 0.995, 2),
        "triggerPrice": trigger
    }

    requests.put(
        f"https://api.dhan.co/v2/forever/orders/{order_id}",
        json=payload,
        headers={"access-token": get_token()},
        timeout=10
    )


# ==========================
# MAIN
# ==========================
def run():
    try:
        log("\n🚀 SL ENGINE START\n")

        init_db()

        positions = fetch_positions()
        holdings = fetch_holdings()
        orders = fetch_orders()

        sync_trades(positions, holdings)
        sync_orders(orders)

        with sqlite3.connect(DB_FILE) as conn:
            trades = conn.execute("""
                SELECT symbol, security_id, qty, entry_price, sl_price
                FROM trades WHERE status='OPEN'
            """).fetchall()

            order_rows = conn.execute("""
                SELECT symbol, dhan_order_id, trigger_price
                FROM sl_orders
            """).fetchall()

        order_map = {o[0]: o for o in order_rows}

        for sym, sec_id, qty, entry, initial_sl in trades:

            ltp = get_ltp(sym)
            if not ltp:
                continue

            existing = order_map.get(sym)
            current_sl = existing[2] if existing else initial_sl

            new_sl = calculate_sl(entry, ltp, current_sl)

            log(f"{sym} | LTP={ltp} | SL {current_sl} → {new_sl}")

            if not existing:
                log(f"🆕 Place SL → {sym}")
                place_sl(sec_id, qty, new_sl)
                continue

            if new_sl > current_sl:
                log(f"🔄 Trail SL → {sym}")
                modify_sl(existing[1], qty, new_sl)

        log("\n✅ DONE\n")

    except Exception as e:
        log(f"❌ SL ENGINE CRASHED: {e}")
        send_telegram(f"❌ SL ENGINE ERROR: {e}")
        raise


if __name__ == "__main__":
    run()
