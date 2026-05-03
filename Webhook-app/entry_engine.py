# ==============================================
# 🚀 ENTRY ENGINE v2.4 (TOKEN FROM PARENT)
# UPDATED: Tick size logic corrected (multiplier → decimal)
# Accepts token via env var from app.py
# Avoids double token generation = no rate limit!
# ==============================================

import os
import requests
import sqlite3
from datetime import datetime, timezone
import uuid
import pandas as pd
import json
import math

# ==========================
# CONFIG
# ==========================
INSTRUMENT_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.db")

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")

# Get token from parent (app.py) - AVOIDS REGENERATION!
DHAN_TOKEN = os.getenv("DHAN_TOKEN")

# Tick size cache
TICK_SIZE_CACHE = {}

session = requests.Session()


# ==========================
# LOGGER
# ==========================
def log(*args):
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}]", *args, flush=True)


# ==========================
# TICK SIZE LOGIC (CORRECTED)
# ==========================
def convert_tick_multiplier_to_decimal(tick_multiplier):
    """
    Convert SEM_TICK_SIZE multiplier to actual decimal tick value.
    SEM_TICK_SIZE is stored as: 1→0.01, 5→0.05, 10→0.10, etc.
    Formula: decimal_tick = tick_multiplier / 100

    Examples:
    - tick_multiplier=1 → 0.01
    - tick_multiplier=5 → 0.05
    - tick_multiplier=10 → 0.10
    - tick_multiplier=50 → 0.50
    """
    try:
        multiplier = float(tick_multiplier)
        if multiplier <= 0:
            return 0.05  # fallback default
        decimal_tick = multiplier / 100.0
        return round(decimal_tick, 4)
    except (ValueError, TypeError):
        return 0.05  # fallback default


def load_tick_sizes():
    """
    Load tick sizes from Dhan instrument master CSV.
    Converts SEM_TICK_SIZE multiplier to actual decimal values.
    Returns dict: {symbol: tick_size_decimal}
    Caches result globally to avoid repeated downloads.
    """
    global TICK_SIZE_CACHE

    if TICK_SIZE_CACHE:
        log(f"✅ Using cached tick sizes ({len(TICK_SIZE_CACHE)} symbols)")
        return TICK_SIZE_CACHE

    try:
        log("📥 Loading tick sizes from Dhan instrument master...")
        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        df = pd.read_csv(url, low_memory=False)

        # Filter for NSE equities only
        df = df[
            (df['SEM_EXM_EXCH_ID'] == 'NSE') &
            (df['SEM_SEGMENT'] == 'E')
            ]

        # Build cache: symbol → tick size (converted to decimal)
        for _, row in df.iterrows():
            symbol = str(row.get('SEM_TRADING_SYMBOL', '')).strip().upper()
            tick_multiplier = row.get('SEM_TICK_SIZE', 5)  # default 5 → 0.05

            # Convert multiplier to decimal tick value
            tick_decimal = convert_tick_multiplier_to_decimal(tick_multiplier)

            if symbol:
                TICK_SIZE_CACHE[symbol] = tick_decimal

        log(f"✅ Loaded tick sizes for {len(TICK_SIZE_CACHE)} NSE equity symbols")
        return TICK_SIZE_CACHE

    except Exception as e:
        log(f"❌ Failed to load tick sizes from CSV: {e}")
        log(f"⚠️  Falling back to default tick=0.05 for all symbols")
        return {}


def get_tick_size(symbol):
    """
    Get tick size for a symbol (already in decimal form).
    symbol: e.g., "ONGC" (without .NS) or "ONGC.NS"
    Returns: float tick size in decimal form (e.g., 0.01, 0.05, 0.10)
    """
    global TICK_SIZE_CACHE

    # Load if not already cached
    if not TICK_SIZE_CACHE:
        load_tick_sizes()

    symbol_clean = symbol.replace(".NS", "").strip().upper()

    # Return from cache, or default to 0.05
    tick = TICK_SIZE_CACHE.get(symbol_clean, 0.05)

    log(f"   [{symbol}] Tick size: ₹{tick:.4f}")
    return tick


def round_to_tick(price, tick, mode="up"):
    """
    Round price to nearest tick.

    Args:
        price (float): Price to round
        tick (float): Tick size in decimal form (e.g., 0.05, 0.01, 0.10)
        mode (str): "up" for entry (buy above signal),
                    "down" for SL (sell below signal),
                    "nearest" for standard rounding

    Returns:
        float: Price rounded to tick precision

    Examples:
        round_to_tick(100.47, 0.05, mode="up") → 100.50
        round_to_tick(100.47, 0.05, mode="down") → 100.45
        round_to_tick(100.47, 0.01, mode="up") → 100.47
    """
    if tick <= 0:
        return round(price, 4)

    # Calculate number of steps: price / tick
    steps = price / tick

    if mode == "up":
        # Ceiling: round up to next tick
        rounded_price = math.ceil(steps) * tick
    elif mode == "down":
        # Floor: round down to previous tick
        rounded_price = math.floor(steps) * tick
    else:  # mode == "nearest" or any other
        # Standard rounding: round to nearest tick
        rounded_price = round(steps) * tick

    # Return with 4 decimal precision (enough for 0.01 tick size)
    return round(rounded_price, 4)


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
# GET SECURITY ID
# ==========================
def get_security_id(stock):
    symbol = stock.replace(".NS", "").strip().upper()
    row = INSTRUMENT_DF[INSTRUMENT_DF['SEM_TRADING_SYMBOL'] == symbol]

    if row.empty:
        log(f"❌ Security ID NOT FOUND: {symbol}")
        return None

    sec_id = str(row.iloc[0]['SEM_SMST_SECURITY_ID'])
    log(f"✅ {symbol} → Security ID: {sec_id}")
    return sec_id


# ==========================
# DATABASE
# ==========================
def init_db():
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            security_id TEXT NOT NULL,
            qty INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            entry_time TEXT NOT NULL,
            status TEXT DEFAULT 'OPEN',
            sl_price REAL,
            target_price REAL,
            setup_id TEXT,
            current_price REAL,
            pnl REAL,
            pnl_percent REAL,
            updated_at TEXT,
            dhan_order_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
    log("✅ Database initialized")


# ==========================
# CHECK DHAN FOR EXISTING ORDERS
# ==========================
def check_dhan_for_existing_buy(symbol, token):
    """
    Check /v2/forever/orders for existing BUY orders on this symbol.
    Uses token from parent (app.py) - avoids regeneration!
    """
    try:
        if not token:
            log("❌ No token provided from parent")
            return False

        log(f"📡 GET /v2/forever/orders (using parent token)...")

        r = session.get(
            "https://api.dhan.co/v2/forever/orders",
            headers={"access-token": token},
            timeout=30
        )

        if r.status_code != 200:
            log(f"⚠️ API error: {r.status_code}")
            return False

        orders = r.json()
        if not isinstance(orders, list):
            log(f"⚠️ Expected list, got {type(orders)}")
            return False

        log(f"   Total orders: {len(orders)}")

        symbol_upper = symbol.upper().replace(".NS", "")

        for order in orders:
            if not isinstance(order, dict):
                continue

            order_symbol = order.get("tradingSymbol", "").strip().upper()
            trans_type = order.get("transactionType", "")
            status = order.get("orderStatus", "")

            if order_symbol == symbol_upper and trans_type == "BUY":
                if status in ["PENDING", "TRIGGERED", "CONFIRM", "ACCEPTED"]:
                    log(f"⚠️ Found open BUY: Status={status}")
                    return True

        log(f"✅ No open BUY orders for {symbol}")
        return False

    except Exception as e:
        log(f"❌ Error checking Dhan: {e}")
        return False


# ==========================
# SAVE TRADE
# ==========================
def save_trade(symbol, sec_id, qty, entry_price, sl_price, target_price, setup_id, dhan_order_id):
    try:
        conn = sqlite3.connect(DB_FILE)
        ts = datetime.now(timezone.utc).isoformat()

        conn.execute("""
            INSERT INTO trades
            (symbol, security_id, qty, entry_price, entry_time, status, sl_price, target_price, setup_id, dhan_order_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, sec_id, qty, entry_price, ts, "OPEN",
            sl_price, target_price, setup_id, dhan_order_id
        ))
        conn.commit()
        conn.close()

        log(f"✅ Trade saved: {symbol} (Dhan ID: {dhan_order_id})")
        return True
    except Exception as e:
        log(f"❌ Failed to save trade: {e}")
        return False


# ==========================
# PLACE ORDER
# ==========================
def place_order(sec_id, qty, entry, symbol, token):
    """
    Place BUY order on Dhan using token from parent.
    Uses correct tick size rounding (SEM_TICK_SIZE multiplier → decimal).
    """
    try:
        # Get tick size for this symbol (corrected logic)
        tick = get_tick_size(symbol)

        # Round prices to correct tick precision
        trigger = round_to_tick(entry, tick, mode="down")
        price = round_to_tick(entry * 1.002, tick, mode="up")

        if price <= trigger:
            price = round_to_tick(trigger + tick, tick, mode="up")

        log(f"   Tick size: ₹{tick:.4f}")
        log(f"   Raw entry: {entry}")
        log(f"   Rounded trigger: {trigger}")
        log(f"   Rounded price: {price}")

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

        if not token:
            log("❌ No token provided from parent")
            return False, {"error": "no_token"}

        try:
            log(f"📤 Placing BUY: Qty={qty}, Entry={entry}, Trigger={trigger}, Price={price}")

            r = session.post(
                "https://api.dhan.co/v2/forever/orders",
                json=payload,
                headers={
                    "access-token": token,
                    "Content-Type": "application/json"
                },
                timeout=15
            )

            if r.status_code not in (200, 201):
                log(f"❌ Order placement failed: {r.status_code}")
                log(f"   Response: {r.text[:200]}")
                return False, {"error": f"http_{r.status_code}"}

            data = r.json()
            log(f"   Response: {data}")

            return True, data

        except Exception as e:
            log(f"❌ Order placement exception: {e}")
            return False, {"error": "exception"}

    except Exception as e:
        log(f"❌ Error in place_order: {e}")
        return False, {"error": "exception"}


# ==========================
# MAIN
# ==========================
def run():
    init_db()

    log("=" * 80)
    log("🚀 ENTRY ENGINE v2.4 (TOKEN FROM PARENT) - TICK SIZE CORRECTED")
    log("=" * 80)

    # Read env vars
    symbol = os.getenv("SYMBOL", "").strip()
    qty = int(os.getenv("QTY", "0") or "0")
    entry = float(os.getenv("ENTRY", "0") or "0.0")
    sl = float(os.getenv("SL", "0") or "0.0")
    target = float(os.getenv("TARGET", "0") or "0.0")
    score = float(os.getenv("SCORE", "0") or "0.0")
    setup_id = os.getenv("SETUP_ID", "")

    # GET TOKEN FROM PARENT - KEY FIX!
    token = os.getenv("DHAN_TOKEN")

    log(f"Input: {symbol} | Qty={qty} | Entry={entry} | SL={sl} | Target={target}")
    log(f"Token from parent: {token[:30] if token else 'NOT PROVIDED'}...")

    # ==== VALIDATION ====
    if not symbol or qty <= 0 or entry <= 0:
        log("❌ Invalid inputs")
        return

    if sl <= 0 or target <= 0:
        log("❌ SL or TARGET missing")
        return

    if not (sl < entry < target):
        log(f"❌ Invalid price order: SL={sl} < ENTRY={entry} < TARGET={target}")
        return

    if not token:
        log("❌ No token provided from parent!")
        return

    # ==== GET SECURITY ID ====
    sec_id = get_security_id(symbol)
    if not sec_id:
        log(f"❌ Security ID not found for {symbol}")
        return

    # ==== CHECK DHAN FOR EXISTING ORDERS ====
    log(f"\n🔍 Checking Dhan for existing orders on {symbol}...")

    if check_dhan_for_existing_buy(symbol, token):
        log(f"⚠️ {symbol} already has open BUY order - SKIPPING")
        return

    log(f"✅ {symbol} is clear on Dhan\n")

    # ==== PLACE ORDER ====
    log("=" * 80)
    log("📤 PLACING ORDER ON DHAN")
    log("=" * 80)

    success, response = place_order(sec_id, qty, entry, symbol, token)

    if not success:
        log(f"❌ Order placement failed")
        log(f"   Error: {response.get('error')}")
        return

    # ==== SUCCESS ====
    dhan_order_id = response.get("orderId")
    order_status = response.get("orderStatus")

    if not dhan_order_id:
        log(f"❌ No orderId in response")
        return

    log(f"\n✅ ORDER PLACED SUCCESSFULLY!")
    log(f"   Order ID: {dhan_order_id}")
    log(f"   Status: {order_status}")
    log(f"   Symbol: {symbol}")
    log(f"   Qty: {qty}")

    # Save to database
    if save_trade(symbol, sec_id, qty, entry, sl, target, setup_id, dhan_order_id):
        log(f"✅ Trade recorded in database")

        # Output JSON
        result = {
            "success": True,
            "order_id": dhan_order_id,
            "symbol": symbol,
            "qty": qty,
            "entry": entry,
            "message": "Order placed successfully"
        }
        print(json.dumps(result))

    log("=" * 80)


if __name__ == "__main__":
    run()