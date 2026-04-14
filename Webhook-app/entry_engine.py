# ==============================================
# 🚀 ENTRY ENGINE (GITHUB SIDE - FINAL STABLE)
# ==============================================

import os
import requests
import pyotp
import sqlite3
from datetime import datetime
import time
import uuid
import pandas as pd

# ==========================
# CONFIG
# ==========================
INSTRUMENT_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.db")

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")

CURRENT_TOKEN = None


# ==========================
# LOGGER
# ==========================
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
        log(f"❌ Mapping NOT FOUND: {symbol}")
        return None

    sec_id = str(row.iloc[0]['SEM_SMST_SECURITY_ID'])
    log(f"✅ MAPPED: {symbol} → {sec_id}")
    return sec_id


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
        sl_price REAL,
        target_price REAL,
        score REAL,
        setup_id TEXT,
        order_id TEXT,
        status TEXT,
        entry_time TEXT
    )
    """)
    conn.commit()
    conn.close()


def insert_trade(symbol, sec_id, qty, entry, sl, target, score, setup_id, order_id):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
    INSERT INTO trades 
    (symbol, security_id, qty, entry_price, sl_price, target_price, score, setup_id, order_id, status, entry_time)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        symbol,
        sec_id,
        qty,
        entry,
        sl,
        target,
        score,
        setup_id,
        order_id,
        "OPEN",
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()


def is_duplicate_trade(setup_id):
    if not setup_id:
        return False

    conn = sqlite3.connect(DB_FILE)

    # check if column exists
    cols = conn.execute("PRAGMA table_info(trades)").fetchall()
    col_names = [c[1] for c in cols]

    if "setup_id" not in col_names:
        # auto-migrate (add column safely)
        conn.execute("ALTER TABLE trades ADD COLUMN setup_id TEXT")
        conn.commit()

    row = conn.execute("""
        SELECT id FROM trades 
        WHERE setup_id=?
    """, (setup_id,)).fetchone()

    conn.close()
    return row is not None


# ==========================
# TOKEN (RETRY ADDED)
# ==========================
def generate_token():

    for i in range(3):

        totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()
        log("🔐 TOTP:", totp)

        r = requests.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": totp
            },
            timeout=10
        )

        log("🔍 RAW RESPONSE:", r.text)

        data = r.json()

        if "accessToken" in data:
            return data["accessToken"]

        # wait for next TOTP window
        time.sleep(35)

    raise Exception("Token failed after retries")


def get_token():
    global CURRENT_TOKEN
    if not CURRENT_TOKEN:
        CURRENT_TOKEN = generate_token()
    return CURRENT_TOKEN


# ==========================
# PLACE ORDER
# ==========================
def place_order(sec_id, qty, entry):

    def round_to_tick(value):
        return round(round(value / 0.05) * 0.05, 2)

    trigger = round_to_tick(entry)
    price = round_to_tick(entry * 1.002)  # must be > trigger

    if price <= trigger:
        price = round_to_tick(trigger + 0.05)

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(uuid.uuid4()).replace("-", "")[:20],
        "orderFlag": "SINGLE",
        "transactionType": "BUY",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",   # ✅ ONLY VALID TYPE
        "validity": "DAY",
        "securityId": sec_id,
        "quantity": qty,
        "price": price,
        "triggerPrice": trigger
    }

    try:
        r = requests.post(
            "https://api.dhan.co/v2/forever/orders",
            json=payload,
            headers={
                "access-token": get_token(),
                "Content-Type": "application/json"
            },
            timeout=15
        )
        r.raise_for_status()
        log("📉 ORDER:", r.text)
        return r.json()
    except Exception as e:
        log(f"❌ ORDER REQUEST FAILED: {e}")
        return {}

# ==========================
# MAIN
# ==========================
def run():

    init_db()

    symbol = os.getenv("SYMBOL")

    def to_int(val):
        try:
            return int(val)
        except:
            return 0

    def to_float(val):
        try:
            return float(val)
        except:
            return 0.0

    qty = to_int(os.getenv("QTY"))
    entry = to_float(os.getenv("ENTRY"))
    sl = to_float(os.getenv("SL"))
    target = to_float(os.getenv("TARGET"))
    score = to_float(os.getenv("SCORE"))
    setup_id = os.getenv("SETUP_ID", "")

    log("DEBUG INPUT →", {
        "symbol": symbol,
        "qty": qty,
        "entry": entry,
        "sl": sl,
        "target": target,
        "score": score,
        "setup_id": setup_id
    })

    # ✅ validation
    if not symbol or qty <= 0 or entry <= 0:
        log("❌ Invalid input values")
        return

    # ❗ STRICT VALIDATION: SL & TARGET must be present
    if sl <= 0 or target <= 0:
        log("❌ SL or TARGET missing → Order blocked")
        return

    # ✅ duplicate protection
    if is_duplicate_trade(setup_id):
        log("⚠️ Duplicate trade blocked")
        return

    # ✅ price relationship validation
    if not (sl < entry < target):
        log(f"❌ Invalid prices: SL={sl} Entry={entry} Target={target}")
        send_telegram(f"❌ Invalid price order for {symbol}: SL={sl} Entry={entry} Target={target}")
        return

    log(symbol, qty, entry, sl, target, score)

    # ✅ mapping
    sec_id = get_security_id(symbol)

    if not sec_id:
        log(f"❌ Security ID not found for {symbol}")
        send_telegram(f"❌ Security ID not found for {symbol}")
        return

    log("DEBUG →", symbol, sec_id, qty, entry)

    # ✅ place order
    res = place_order(sec_id, qty, entry)

    # ✅ strict success check
    if isinstance(res, dict) and res.get("orderId"):
        insert_trade(
            symbol, sec_id, qty, entry, sl, target,
            score, setup_id,
            res["orderId"]
        )
        log("✅ TRADE SAVED")
    else:
        log("❌ ORDER FAILED", res)
        send_telegram(f"❌ ORDER FAILED for {symbol}: {res}")


if __name__ == "__main__":
    run()
