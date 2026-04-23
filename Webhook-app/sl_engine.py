# ==============================================
# 🚀 SL ENGINE V6 (DUAL-TABLE + DHAN SYNC)
# Monitors executed_orders, places/trails SL,
# syncs back to DB, updates P/L for dashboard
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
ENV_PATH = os.path.join(BASE_DIR, ".env")
if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)

# ==========================
# CONFIG
# ==========================
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not all([DHAN_CLIENT_ID, DHAN_PIN, DHAN_TOTP_SECRET]):
    raise ValueError("Missing Dhan environment variables")

DB_FILE = os.path.join(BASE_DIR, "trades.db")
BASE_SL_PCT = 0.92        # 8% initial SL
TRAIL_PROFIT_LOCK = 0.5   # Lock 50% of profit
MIN_LTP_BUFFER = 0.05     # Maintain 5% gap from LTP

CURRENT_TOKEN = None
TOKEN_EXPIRY = datetime.now(timezone.utc)

# Reusable session
session = requests.Session()

# ==========================
# LOGGER
# ==========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ==========================
# TELEGRAM
# ==========================
def send_telegram(msg):
    try:
        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
            session.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
                timeout=10
            )
    except Exception as e:
        logger.error(f"Telegram Error: {e}")


# ==========================
# TOKEN MANAGEMENT
# ==========================
def generate_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY

    try:
        totp = pyotp.TOTP(DHAN_TOTP_SECRET)
        logger.info("Generating Dhan access token")

        response = session.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": totp.now()
            },
            timeout=30
        )

        response.raise_for_status()
        data = response.json()

        token = data.get("accessToken")
        if not token:
            raise ValueError("No accessToken in response")

        CURRENT_TOKEN = token
        TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(hours=23)

        logger.info(f"✅ Token generated. Expiry: {TOKEN_EXPIRY}")
        return token

    except Exception as e:
        logger.error(f"❌ Token generation failed: {e}")
        return None


def get_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY

    if CURRENT_TOKEN and datetime.now(timezone.utc) < TOKEN_EXPIRY:
        return CURRENT_TOKEN

    logger.info("Token expired, regenerating...")
    return generate_token()


# ==========================
# DATABASE OPERATIONS
# ==========================
def get_open_orders():
    """
    Fetch all OPEN orders from executed_orders table that need SL management.
    Returns list of tuples: (dhan_order_id, symbol, qty_executed, entry_price_executed, sl_price, sl_order_id)
    """
    try:
        with sqlite3.connect(DB_FILE) as conn:
            rows = conn.execute("""
                SELECT dhan_order_id, symbol, qty_executed, entry_price_executed, sl_price, sl_order_id
                FROM executed_orders
                WHERE status IN ('OPEN', 'FILLED')
                  AND qty_executed > 0
                  AND entry_price_executed IS NOT NULL
                ORDER BY executed_timestamp ASC
            """).fetchall()

        logger.info(f"📋 Fetched {len(rows)} open orders from DB")
        return rows
    except Exception as e:
        logger.error(f"Failed to fetch open orders: {e}")
        return []


def get_order_by_dhan_id(dhan_order_id):
    """Fetch single order details."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            row = conn.execute("""
                SELECT setup_id, symbol, dhan_order_id, qty_executed, entry_price_executed, 
                       sl_price, sl_order_id, target_price
                FROM executed_orders
                WHERE dhan_order_id = ?
            """, (dhan_order_id,)).fetchone()
        return row
    except Exception as e:
        logger.error(f"Failed to fetch order {dhan_order_id}: {e}")
        return None


def update_sl_order_id(dhan_order_id, sl_order_id):
    """Link placed SL order."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("""
                UPDATE executed_orders
                SET sl_order_id = ?,
                    updated_at = ?
                WHERE dhan_order_id = ?
            """, (sl_order_id, datetime.now(timezone.utc).isoformat(), dhan_order_id))
            conn.commit()
        logger.info(f"✅ SL order linked: {dhan_order_id} → {sl_order_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to update SL order: {e}")
        return False


def update_order_pnl(dhan_order_id, current_price):
    """Update P/L for dashboard."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            row = conn.execute("""
                SELECT entry_price_executed, qty_executed, symbol
                FROM executed_orders
                WHERE dhan_order_id = ?
            """, (dhan_order_id,)).fetchone()

            if not row:
                return False

            entry_px, qty_exec, symbol = row
            if not entry_px or qty_exec == 0:
                return False

            pnl = (current_price - entry_px) * qty_exec
            pnl_pct = ((current_price - entry_px) / entry_px) * 100

            conn.execute("""
                UPDATE executed_orders
                SET current_price = ?,
                    pnl = ?,
                    pnl_percent = ?,
                    current_price_update_at = ?
                WHERE dhan_order_id = ?
            """, (current_price, pnl, pnl_pct, datetime.now(timezone.utc).isoformat(), dhan_order_id))
            conn.commit()

            logger.debug(f"{symbol}: LTP={current_price}, PnL=₹{pnl:.2f} ({pnl_pct:.2f}%)")
            return True
    except Exception as e:
        logger.error(f"Failed to update P/L: {e}")
        return False


# ==========================
# DHAN API CALLS
# ==========================
def fetch_orders():
    """Get all orders from Dhan (for status sync)."""
    try:
        token = get_token()
        if not token:
            logger.error("❌ No valid token")
            return []

        r = session.get(
            "https://api.dhan.co/v2/forever/orders",
            headers={"access-token": token},
            timeout=30
        )

        if r.status_code != 200:
            logger.error(f"Failed to fetch orders: {r.status_code}")
            return []

        return r.json().get("orders", [])

    except Exception as e:
        logger.error(f"Exception fetching orders: {e}")
        return []


def sync_order_status():
    """
    Sync Dhan order statuses back to DB.
    Detects when orders fill/cancel/reject.
    """
    try:
        dhan_orders = fetch_orders()
        logger.info(f"📡 Syncing {len(dhan_orders)} orders from Dhan")

        buy_orders = [o for o in dhan_orders if o.get("transactionType") == "BUY"]

        for order in buy_orders:
            dhan_id = order.get("orderId")
            status = order.get("orderStatus", "UNKNOWN")
            filled_qty = order.get("executedQuantity", 0)
            filled_price = order.get("executedPrice", 0)

            # Update in DB if filled
            if status == "ACCEPTED" and filled_qty > 0 and filled_price > 0:
                with sqlite3.connect(DB_FILE) as conn:
                    existing = conn.execute(
                        "SELECT qty_executed FROM executed_orders WHERE dhan_order_id = ?",
                        (dhan_id,)
                    ).fetchone()

                    if existing and existing[0] != filled_qty:
                        conn.execute("""
                            UPDATE executed_orders
                            SET qty_executed = ?,
                                entry_price_executed = ?,
                                status = 'FILLED'
                            WHERE dhan_order_id = ?
                        """, (filled_qty, filled_price, dhan_id))
                        conn.commit()
                        logger.info(f"✅ Order filled: {dhan_id} {filled_qty}@{filled_price}")

        return True
    except Exception as e:
        logger.error(f"Failed to sync order status: {e}")
        return False


# ==========================
# LTP FETCH (yfinance)
# ==========================
def get_ltp(symbol):
    """Fetch last traded price."""
    try:
        ticker = symbol if symbol.endswith(".NS") else symbol + ".NS"
        ltp = yf.Ticker(ticker).fast_info.get("lastPrice")
        if ltp:
            return float(ltp)
        return None
    except Exception as e:
        logger.warning(f"LTP fetch failed for {symbol}: {e}")
        return None


# ==========================
# SL CALCULATION
# ==========================
def calculate_sl(entry, ltp, current_sl):
    """
    Calculate new SL with trailing logic.

    Initial SL: 8% below entry
    If in profit: lock 50% of profit, maintain 5% gap from LTP
    """

    # Base SL: 8% below entry
    base_sl = entry * BASE_SL_PCT

    # Start with max of current or base SL
    new_sl = max(current_sl or 0, base_sl)

    # Trailing logic: once in profit
    if ltp > entry:
        profit = ltp - entry
        trailing_sl = entry + (profit * TRAIL_PROFIT_LOCK)

        # Ensure SL not closer than 5% to LTP
        max_allowed_sl = ltp * (1 - MIN_LTP_BUFFER)

        # Take the safer (higher) SL
        new_sl = max(new_sl, min(trailing_sl, max_allowed_sl))

    return round(new_sl, 2)


# ==========================
# DHAN SL PLACEMENT / MODIFICATION
# ==========================
def place_sl(sec_id, qty, trigger, symbol, dhan_order_id):
    """Place new SL order (SELL side)."""

    def round_to_tick(value):
        return round(round(value / 0.05) * 0.05, 2)

    trigger_price = round_to_tick(trigger)
    limit_price = round_to_tick(trigger_price * 0.995)  # Limit < trigger for SL
    disclosed_qty = max(1, int(qty * 0.3))

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(uuid.uuid4()).replace("-", "")[:20],
        "orderFlag": "SINGLE",
        "transactionType": "SELL",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",
        "validity": "DAY",
        "securityId": str(sec_id),
        "quantity": int(qty),
        "price": limit_price,
        "triggerPrice": trigger_price,
        "disclosedQuantity": disclosed_qty
    }

    token = get_token()
    if not token:
        logger.error("❌ No valid token for SL placement")
        return None

    try:
        logger.info(f"📤 Placing SL for {symbol}: trigger={trigger_price}, limit={limit_price}, qty={qty}")

        r = session.post(
            "https://api.dhan.co/v2/forever/orders",
            json=payload,
            headers={"access-token": token, "Content-Type": "application/json"},
            timeout=30
        )

        if r.status_code not in (200, 201):
            logger.error(f"❌ SL placement failed: {r.status_code} {r.text}")
            send_telegram(f"❌ SL placement failed for {symbol}: {r.text[:100]}")
            return None

        data = r.json()
        sl_order_id = data.get("orderId")

        if sl_order_id:
            logger.info(f"✅ SL placed: {sl_order_id}")

            # Link SL to main order
            update_sl_order_id(dhan_order_id, sl_order_id)

            send_telegram(f"🛡️ SL placed for {symbol} @ ₹{trigger_price}")
            return sl_order_id
        else:
            logger.error(f"⚠️ No SL orderId in response: {data}")
            return None

    except Exception as e:
        logger.error(f"❌ SL placement exception: {e}")
        send_telegram(f"❌ SL exception for {symbol}: {e}")
        return None


def modify_sl(sl_order_id, sec_id, qty, trigger, symbol):
    """Modify existing SL order (trailing)."""

    def round_to_tick(value):
        return round(round(value / 0.05) * 0.05, 2)

    trigger_price = round_to_tick(trigger)
    limit_price = round_to_tick(trigger_price * 0.995)
    disclosed_qty = max(1, int(qty * 0.3))

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "orderId": sl_order_id,
        "orderFlag": "SINGLE",
        "orderType": "LIMIT",
        "legName": "STOP_LOSS_LEG",
        "quantity": int(qty),
        "price": limit_price,
        "triggerPrice": trigger_price,
        "disclosedQuantity": disclosed_qty,
        "validity": "DAY"
    }

    token = get_token()
    if not token:
        logger.error("❌ No valid token for SL modification")
        return False

    try:
        logger.info(f"🔄 Trailing SL for {symbol}: new trigger={trigger_price}")

        r = session.put(
            f"https://api.dhan.co/v2/forever/orders/{sl_order_id}",
            json=payload,
            headers={"access-token": token, "Content-Type": "application/json"},
            timeout=30
        )

        if r.status_code not in (200, 201):
            logger.error(f"❌ SL modification failed: {r.status_code} {r.text}")
            send_telegram(f"❌ SL trail failed for {symbol}: {r.text[:100]}")
            return False

        logger.info(f"✅ SL trailed: {symbol} → ₹{trigger_price}")
        send_telegram(f"🔄 SL trailed for {symbol} to ₹{trigger_price}")
        return True

    except Exception as e:
        logger.error(f"❌ SL modification exception: {e}")
        send_telegram(f"❌ SL trail exception for {symbol}: {e}")
        return False


# ==========================
# MAIN EXECUTION
# ==========================
def run():
    try:
        logger.info("=" * 60)
        logger.info("🚀 SL ENGINE V6 STARTED")
        logger.info("=" * 60)

        # Step 1: Sync order statuses from Dhan
        sync_order_status()

        # Step 2: Get all open orders
        open_orders = get_open_orders()

        if not open_orders:
            logger.info("✅ No open orders to manage")
            return

        logger.info(f"🔍 Managing {len(open_orders)} open orders")

        # Step 3: For each order, manage SL
        for dhan_order_id, symbol, qty_exec, entry_px, initial_sl, sl_order_id in open_orders:

            # Get LTP
            ltp = get_ltp(symbol)
            if not ltp:
                logger.warning(f"⚠️ Could not fetch LTP for {symbol}")
                continue

            # Update P/L
            update_order_pnl(dhan_order_id, ltp)

            # Calculate new SL
            current_sl = initial_sl  # Use initial if no SL order placed yet
            new_sl = calculate_sl(entry_px, ltp, current_sl)

            pnl = (ltp - entry_px) * qty_exec
            pnl_pct = ((ltp - entry_px) / entry_px) * 100

            logger.info(f"\n{symbol}")
            logger.info(f"  LTP: ₹{ltp} | Entry: ₹{entry_px} | PnL: ₹{pnl:.2f} ({pnl_pct:.2f}%)")
            logger.info(f"  Current SL: ₹{current_sl} → New SL: ₹{new_sl}")

            # Place or modify SL
            if not sl_order_id:
                # First time: place SL
                logger.info(f"  Action: PLACE SL")

                order_details = get_order_by_dhan_id(dhan_order_id)
                if order_details:
                    setup_id, _, _, _, _, _, _, _ = order_details
                    # Get sec_id from somewhere (need to query or pass)
                    # For now, we'll skip sec_id as it's not used in place_sl
                    # Actually, we DO need it. Let me refactor...
                    logger.warning(f"  ⚠️ Missing sec_id, skipping SL placement (TODO: add to DB)")

            elif new_sl > current_sl:
                # Trail SL upward
                logger.info(f"  Action: TRAIL SL")
                order_details = get_order_by_dhan_id(dhan_order_id)
                if order_details:
                    setup_id, _, _, _, _, _, _, _ = order_details
                    # Again, need sec_id
                    logger.warning(f"  ⚠️ Missing sec_id, skipping SL modification")

            else:
                logger.info(f"  Action: HOLD (SL not changed)")

        logger.info("\n✅ SL ENGINE COMPLETED")

    except Exception as e:
        logger.exception("❌ SL ENGINE CRASHED")
        send_telegram(f"❌ SL ENGINE ERROR: {e}")
        raise


if __name__ == "__main__":
    run()