# ==============================================
# 🚀 TRAILING SL ENGINE (DB BASED - FINAL)
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

MIN_TRAIL_PCT = 0.005   # 0.5%
BASE_SL_PCT = 0.92      # 8% SL


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
# TOKEN (RETRY + STABLE)
# ==========================
def generate_token():
    global TOKEN_EXPIRY

    totp = pyotp.TOTP(DHAN_TOTP_SECRET)

    for _ in range(3):
        code = totp.now()

        log("🔐 TOTP:", code)

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

        time.sleep(3)

    raise Exception("Token failed")


def get_token():
    global CURRENT_TOKEN

    if not CURRENT_TOKEN or datetime.now(timezone.utc) > TOKEN_EXPIRY:
        CURRENT_TOKEN = generate_token()

    return CURRENT_TOKEN


# ==========================
# LTP (Yahoo + fallback)
# ==========================
def get_ltp(symbol, entry=None, pnl=None, qty=None):

    try:
        data = yf.Ticker(symbol + ".NS")

        price = None

        if hasattr(data, "fast_info") and data.fast_info:
            price = data.fast_info.get("lastPrice")

        if not price:
            hist = data.history(period="1d", interval="1m")
            if not hist.empty:
                price = hist["Close"].iloc[-1]

        if price:
            price = round(float(price), 2)
            log(f"🌐 LTP → {symbol} = {price}")
            return price

    except Exception as e:
        log("❌ Yahoo error:", e)

    # fallback
    if entry and pnl and qty:
        try:
            ltp = entry + (pnl / qty)
            ltp = round(ltp, 2)
            log(f"🧮 LTP fallback → {ltp}")
            return ltp
        except:
            pass

    return None


# ==========================
# SL LOGIC
# ==========================
def calculate_sl(entry, ltp, prev_sl):

    base = entry * BASE_SL_PCT

    if not prev_sl:
        return base

    if ltp <= entry:
        return base

    profit = ltp - entry

    new_sl = entry + profit * 0.5
    new_sl = min(new_sl, ltp * 0.995)

    return max(prev_sl, new_sl)


# ==========================
# ORDER APIs
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
        headers={"access-token": get_token()},
        timeout=10
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
        headers={"access-token": get_token()},
        timeout=10
    )

    log("📉 MODIFY:", r.status_code, r.text)

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

        (trade_id, symbol, sec_id, qty,
         entry, planned_exit, prev_sl,
         order_id, status, _) = t

        ltp = get_ltp(symbol)

        if not ltp:
            log(f"⚠️ Missing LTP → {symbol}")
            continue

        log(f"\n➡️ {symbol} | Entry={entry} | LTP={ltp}")

        # ==========================
        # INITIAL SL (CRITICAL FIX)
        # ==========================
        if not prev_sl or not order_id:

            base_sl = entry * BASE_SL_PCT

            log(f"🆕 Initial SL → {base_sl}")

            new_order_id = place_sl(sec_id, qty, base_sl)

            if new_order_id:
                update_sl(trade_id, base_sl)

                # also update order_id
                conn = sqlite3.connect(DB_FILE)
                conn.execute(
                    "UPDATE trades SET order_id=? WHERE id=?",
                    (new_order_id, trade_id)
                )
                conn.commit()
                conn.close()

                updated += 1

            continue

        # ==========================
        # TRAILING SL
        # ==========================
        if planned_exit and ltp < planned_exit:
            new_sl = planned_exit
        else:
            new_sl = calculate_sl(entry, ltp, prev_sl)

        log(f"SL OLD={prev_sl} → NEW={new_sl}")

        if new_sl <= prev_sl:
            log("⏭️ No improvement")
            continue

        # avoid noise updates
        if prev_sl > 0:
            change_pct = (new_sl - prev_sl) / prev_sl
            if change_pct < MIN_TRAIL_PCT:
                log("⏭️ Too small move")
                continue

        if modify_sl(order_id, qty, new_sl):
            update_sl(trade_id, new_sl)
            updated += 1

    log(f"\n✅ DONE | Updated: {updated}")


if __name__ == "__main__":
    run()
