# ==============================================
# 🚀 ENTRY ENGINE V2 (FINAL) — Called by app.py
# Receives env vars from Flask webhook, places order on Dhan
# ==============================================

import os
import requests
import pyotp
import sqlite3
from datetime import datetime, timezone
import time
import uuid
import pandas as pd
import json

# ==========================
# CONFIG
# ==========================
INSTRUMENT_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.db")

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")

CURRENT_TOKEN = None
TOKEN_EXPIRY = None

# Reusable session
session = requests.Session()


# ==========================
# LOGGER
# ==========================
def log(*args):
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}]", *args, flush=True)


# ==========================
# LOAD INSTRUMENTS
# ==========================
def load_instruments():
    try:
        df = pd.read_csv(INSTRUMENT_URL, low_memory=False)
        df = df[
            (df['SEM_EXM_EXCH_ID'] == 'NSE') &
            (df['SEM_SEGMENT'] == 'E')
            ]
        df['SEM_TRADING_SYMBOL'] = df['SEM_TRADING_SYMBOL'].astype(str).str.strip().str.upper()
        log(f"✅ Instruments Loaded: {len(df)}")
        return df
    except Exception as e:
        log(f"❌ Failed to load instruments: {e}")
        return pd.DataFrame()


INSTRUMENT_DF = load_instruments()


# ==========================
# SYMBOL → SECURITY_ID
# ==========================
def get_security_id(stock):
    symbol = stock.replace(".NS", "").strip().upper()
    row = INSTRUMENT_DF[INSTRUMENT_DF['SEM_TRADING_SYMBOL'] == symbol]

    if row.empty:
        log(f"❌ Mapping NOT FOUND: {symbol}")
        return None

    sec_id = str(row.iloc[0]['SEM_SMST_SECURITY_ID'])
    log(f"✅ MAPPED: {symbol} → {sec_id}")
    return sec_id


# ==========================
# DATABASE INITIALIZATION
# ==========================
def init_db():
    """
    Creates dual-table schema for order tracking.

    Flow: pending_orders (retry queue) → executed_orders (Dhan confirmed)
    """
    conn = sqlite3.connect(DB_FILE)

    # Table 1: PENDING ORDERS (awaiting Dhan confirmation)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setup_id TEXT UNIQUE NOT NULL,
            symbol TEXT NOT NULL,
            security_id TEXT NOT NULL,
            qty INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            sl_price REAL NOT NULL,
            target_price REAL NOT NULL,
            score REAL,
            base_stage INTEGER,
            base_quality_score REAL,
            tick_size REAL,
            placed_at TEXT NOT NULL,
            placed_timestamp REAL NOT NULL,
            status TEXT DEFAULT 'PENDING',
            attempt_count INTEGER DEFAULT 1,
            last_error TEXT,
            retry_at TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Table 2: EXECUTED ORDERS (Dhan confirmed, open/closed)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS executed_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setup_id TEXT UNIQUE NOT NULL,
            symbol TEXT NOT NULL,
            security_id TEXT NOT NULL,
            dhan_order_id TEXT UNIQUE NOT NULL,
            qty_ordered INTEGER NOT NULL,
            qty_executed INTEGER DEFAULT 0,
            entry_price REAL NOT NULL,
            entry_price_executed REAL,
            sl_price REAL NOT NULL,
            sl_order_id TEXT,
            target_price REAL NOT NULL,
            score REAL,
            base_stage INTEGER,
            base_quality_score REAL,
            tick_size REAL,
            placed_at TEXT NOT NULL,
            placed_timestamp REAL NOT NULL,
            executed_at TEXT,
            executed_timestamp REAL,
            status TEXT DEFAULT 'OPEN',
            current_price REAL,
            current_price_update_at TEXT,
            pnl REAL DEFAULT 0,
            pnl_percent REAL DEFAULT 0,
            order_status_dhan TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
    log("✅ Database initialized with dual-table schema")


# ==========================
# PENDING ORDERS TABLE OPS
# ==========================
def insert_pending_order(setup_id, symbol, sec_id, qty, entry, sl, target, score,
                         base_stage, base_quality_score, tick_size):
    """Insert order into pending queue."""
    conn = sqlite3.connect(DB_FILE)
    ts = datetime.now(timezone.utc)
    try:
        conn.execute("""
            INSERT INTO pending_orders 
            (setup_id, symbol, security_id, qty, entry_price, sl_price, target_price,
             score, base_stage, base_quality_score, tick_size, placed_at, placed_timestamp, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            setup_id, symbol, sec_id, qty, entry, sl, target,
            score, base_stage, base_quality_score, tick_size,
            ts.isoformat(), ts.timestamp(), "PENDING"
        ))
        conn.commit()
        log(f"✅ Pending order saved: {setup_id} ({symbol})")
        return True
    except sqlite3.IntegrityError as e:
        log(f"⚠️ Duplicate pending order (setup_id={setup_id}): {e}")
        return False
    except Exception as e:
        log(f"❌ Failed to save pending order: {e}")
        return False
    finally:
        conn.close()


def mark_pending_placed(setup_id, dhan_order_id):
    """Move pending → executed when Dhan confirms order."""
    conn = sqlite3.connect(DB_FILE)
    ts = datetime.now(timezone.utc)
    try:
        # Fetch pending order
        pending = conn.execute("""
            SELECT symbol, security_id, qty, entry_price, sl_price, target_price,
                   score, base_stage, base_quality_score, tick_size, placed_at, placed_timestamp
            FROM pending_orders
            WHERE setup_id = ?
        """, (setup_id,)).fetchone()

        if not pending:
            log(f"⚠️ Pending order not found: {setup_id}")
            return False

        (symbol, sec_id, qty, entry, sl, target, score, stage, bq_score, tick,
         placed_at, placed_ts) = pending

        # Insert into executed_orders
        conn.execute("""
            INSERT INTO executed_orders
            (setup_id, symbol, security_id, dhan_order_id, qty_ordered, entry_price,
             sl_price, target_price, score, base_stage, base_quality_score, tick_size,
             placed_at, placed_timestamp, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            setup_id, symbol, sec_id, dhan_order_id, qty, entry, sl, target,
            score, stage, bq_score, tick, placed_at, placed_ts, "OPEN"
        ))

        # Delete from pending_orders
        conn.execute("DELETE FROM pending_orders WHERE setup_id = ?", (setup_id,))

        conn.commit()
        log(f"✅ Order moved to executed: {setup_id} (Dhan ID={dhan_order_id})")
        return True
    except Exception as e:
        log(f"❌ Failed to mark pending as placed: {e}")
        return False
    finally:
        conn.close()


def mark_pending_failed(setup_id, error_msg, retry_after_seconds=300):
    """Increment retry count and schedule retry."""
    conn = sqlite3.connect(DB_FILE)
    ts = datetime.now(timezone.utc)
    retry_ts = ts.timestamp() + retry_after_seconds

    try:
        conn.execute("""
            UPDATE pending_orders
            SET attempt_count = attempt_count + 1,
                last_error = ?,
                retry_at = ?,
                status = CASE 
                    WHEN attempt_count >= 3 THEN 'FAILED'
                    ELSE 'PENDING'
                END
            WHERE setup_id = ?
        """, (error_msg, datetime.fromtimestamp(retry_ts, timezone.utc).isoformat(), setup_id))
        conn.commit()

        # Check final status
        row = conn.execute(
            "SELECT attempt_count, status FROM pending_orders WHERE setup_id = ?",
            (setup_id,)
        ).fetchone()

        if row:
            attempt, final_status = row
            if final_status == "FAILED":
                log(f"❌ Order {setup_id} failed after {attempt} attempts: {error_msg}")
            else:
                log(f"⚠️ Order {setup_id} retry #{attempt} scheduled")

        return True
    except Exception as e:
        log(f"❌ Failed to update pending order status: {e}")
        return False
    finally:
        conn.close()


def is_duplicate_trade(setup_id):
    """Check if order already exists (pending or executed)."""
    conn = sqlite3.connect(DB_FILE)

    pending = conn.execute(
        "SELECT id FROM pending_orders WHERE setup_id = ?", (setup_id,)
    ).fetchone()

    executed = conn.execute(
        "SELECT id FROM executed_orders WHERE setup_id = ?", (setup_id,)
    ).fetchone()

    conn.close()
    return pending is not None or executed is not None


# ==========================
# TOKEN MANAGEMENT
# ==========================
def generate_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY

    try:
        totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()
        log(f"🔐 Generating token (TOTP={totp})")

        r = requests.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": totp
            },
            timeout=15
        )

        if r.status_code != 200:
            log(f"❌ Token generation failed: {r.status_code} {r.text}")
            return None

        data = r.json()
        token = data.get("accessToken")

        if not token:
            log(f"❌ No token in response: {data}")
            return None

        CURRENT_TOKEN = token
        TOKEN_EXPIRY = datetime.now(timezone.utc).timestamp() + (23 * 3600)

        log(f"✅ Token generated: {token[:20]}...")
        return token
    except Exception as e:
        log(f"❌ Token generation exception: {e}")
        return None


def get_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY

    now = datetime.now(timezone.utc).timestamp()

    if CURRENT_TOKEN and TOKEN_EXPIRY and now < TOKEN_EXPIRY:
        return CURRENT_TOKEN

    log("⏳ Token expired/missing, regenerating...")
    return generate_token()


# ==========================
# PLACE ORDER ON DHAN
# ==========================
def place_order(sec_id, qty, entry):
    """
    Place BUY order on Dhan.
    Returns (success: bool, order_response: dict)
    """

    def round_to_tick(value):
        return round(round(value / 0.05) * 0.05, 2)

    trigger = round_to_tick(entry)
    price = round_to_tick(entry * 1.002)

    if price <= trigger:
        price = round_to_tick(trigger + 0.05)

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(uuid.uuid4()).replace("-", "")[:20],
        "orderFlag": "SINGLE",
        "transactionType": "BUY",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",
        "validity": "DAY",
        "securityId": sec_id,
        "quantity": qty,
        "price": price,
        "triggerPrice": trigger
    }

    token = get_token()
    if not token:
        log("❌ No valid token available")
        return False, {"error": "no_token"}

    try:
        log(f"📤 Placing order: qty={qty}, entry={entry}, trigger={trigger}, price={price}")

        r = requests.post(
            "https://api.dhan.co/v2/forever/orders",
            json=payload,
            headers={
                "access-token": token,
                "Content-Type": "application/json"
            },
            timeout=15
        )

        log(f"📡 Response ({r.status_code}): {r.text[:200]}")

        if r.status_code not in (200, 201):
            return False, {"error": f"http_{r.status_code}", "details": r.text}

        data = r.json()
        return True, data

    except Exception as e:
        log(f"❌ Order placement exception: {e}")
        return False, {"error": "exception", "details": str(e)}


# ==========================
# MAIN ENTRY ROUTINE
# ==========================
def run():
    """
    Called by app.py with environment variables set.
    Receives trade parameters via os.getenv()
    """
    init_db()

    log("=" * 60)
    log("🚀 ENTRY ENGINE — Called by app.py webhook")
    log("=" * 60)

    # Read environment variables (set by app.py before calling subprocess)
    symbol = os.getenv("SYMBOL", "").strip()
    qty = int(os.getenv("QTY", "0") or "0")
    entry = float(os.getenv("ENTRY", "0") or "0.0")
    sl = float(os.getenv("SL", "0") or "0.0")
    target = float(os.getenv("TARGET", "0") or "0.0")
    score = float(os.getenv("SCORE", "0") or "0.0")
    setup_id = os.getenv("SETUP_ID", "")
    base_stage = int(os.getenv("BASE_STAGE", "0") or "0")
    base_quality_score = float(os.getenv("BASE_QUALITY_SCORE", "0") or "0.0")
    tick_size = float(os.getenv("TICK_SIZE", "0.05") or "0.05")

    log("🔍 ENV DEBUG ------------------------")
    for key in ["SYMBOL", "QTY", "ENTRY", "SL", "TARGET", "SCORE", "SETUP_ID",
                "BASE_STAGE", "BASE_QUALITY_SCORE", "TICK_SIZE"]:
        log(f"{key} =", os.getenv(key))
    log("-------------------------------------")
    log(f"Input: symbol={symbol}, qty={qty}, entry={entry}, sl={sl}, target={target}")
    log(f"       setup_id={setup_id}, base_stage={base_stage}, score={score}")

    # ==== VALIDATION ====
    if not symbol or qty <= 0 or entry <= 0:
        log("❌ Invalid inputs")
        return

    # SL & TARGET validation (app.py already checks, but double-check here)
    if sl <= 0 or target <= 0:
        log("❌ SL or TARGET missing")
        return

    if not (sl < entry < target):
        log(f"❌ Invalid price order: SL={sl} < ENTRY={entry} < TARGET={target}")
        return

    # Duplicate check
    if is_duplicate_trade(setup_id):
        log(f"⚠️ Duplicate trade detected: {setup_id}")
        return

    # ==== MAPPING ====
    sec_id = get_security_id(symbol)
    if not sec_id:
        log(f"❌ Security ID not found for {symbol}")
        return

    # ==== INSERT TO PENDING ====
    if not insert_pending_order(
            setup_id, symbol, sec_id, qty, entry, sl, target, score,
            base_stage, base_quality_score, tick_size
    ):
        return

    # ==== PLACE ORDER ON DHAN ====
    success, response = place_order(sec_id, qty, entry)

    if not success:
        error_msg = response.get("error", "unknown")
        details = response.get("details", "")
        log(f"❌ Dhan order placement failed: {error_msg}")
        log(f"   Details: {details}")

        mark_pending_failed(setup_id, f"{error_msg}: {details}", retry_after_seconds=300)
        return

    # ==== SUCCESS: MOVE TO EXECUTED ====
    dhan_order_id = response.get("orderId")
    if not dhan_order_id:
        log(f"⚠️ Order placed but no orderId in response: {response}")
        mark_pending_failed(setup_id, "No orderId in Dhan response", retry_after_seconds=60)
        return

    if mark_pending_placed(setup_id, dhan_order_id):
        log(f"✅ Order placed successfully: {dhan_order_id}")
        log(f"   Telegram should show: Order placed | Dhan ID: {dhan_order_id}")
    else:
        log(f"⚠️ Failed to move order to executed table")


if __name__ == "__main__":
    run()