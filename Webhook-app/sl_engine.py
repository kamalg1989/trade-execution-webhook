# ==============================================
# 🚀 SL ENGINE V7.2 (FINAL - WORKING)
# Uses /v2/forever/all endpoint (correct API)
# Gets tradingSymbol and prices from forever orders
# Places SL orders successfully
# ==============================================

import os
import requests
import pyotp
import sqlite3
import uuid
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# ==========================
# CONFIG
# ==========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not all([DHAN_CLIENT_ID, DHAN_PIN, DHAN_TOTP_SECRET]):
    raise ValueError("Missing Dhan environment variables")

DB_FILE = os.path.join(BASE_DIR, "trades.db")
BASE_SL_PCT = 0.92
TRAIL_PROFIT_LOCK = 0.5
MIN_LTP_BUFFER = 0.05

CURRENT_TOKEN = None
TOKEN_EXPIRY = datetime.now(timezone.utc)

session = requests.Session()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
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
        logger.info("🔑 Generating Dhan access token")
        response = session.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={"dhanClientId": DHAN_CLIENT_ID, "pin": DHAN_PIN, "totp": totp.now()},
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        token = data.get("accessToken")
        if token:
            CURRENT_TOKEN = token
            TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(hours=23)
            logger.info(f"✅ Token generated")
            return token
    except Exception as e:
        logger.error(f"❌ Token generation failed: {e}")
    return None

def get_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY
    if CURRENT_TOKEN and datetime.now(timezone.utc) < TOKEN_EXPIRY:
        return CURRENT_TOKEN
    return generate_token()


# ==========================
# GET PRICE FROM FOREVER ORDERS
# ==========================
def get_prices_from_forever_all():
    """
    Fetch all forever orders using /v2/forever/all endpoint.
    Extract trading symbols and their current prices from order data.
    SELL orders show trigger price (approximate current market price).
    Returns: dict {symbol: current_price}
    """
    try:
        token = get_token()
        if not token:
            logger.error("❌ No token")
            return {}

        logger.info("📡 Fetching forever orders from /v2/forever/all...")

        r = session.get(
            "https://api.dhan.co/v2/forever/all",
            headers={"access-token": token},
            timeout=30
        )

        if r.status_code != 200:
            logger.error(f"❌ API error: {r.status_code}")
            return {}

        orders = r.json()
        if not isinstance(orders, list):
            logger.error(f"❌ Unexpected response type: {type(orders)}")
            return {}

        logger.info(f"📊 Retrieved {len(orders)} forever orders")

        # Extract symbols and prices from orders
        symbol_prices = {}
        symbol_details = {}  # For logging

        for order in orders:
            if not isinstance(order, dict):
                continue

            symbol = order.get("tradingSymbol")
            trans_type = order.get("transactionType")
            trigger_price = order.get("triggerPrice", 0)
            price = order.get("price", 0)
            status = order.get("orderStatus")

            if not symbol:
                continue

            # SELL orders (SL orders) show current market approximate price
            if trans_type == "SELL" and status == "PENDING":
                # Trigger price is approximately current market price
                estimated_price = trigger_price if trigger_price > 0 else price

                if estimated_price > 0:
                    symbol_prices[symbol] = estimated_price
                    symbol_details[symbol] = {
                        "price": estimated_price,
                        "trigger": trigger_price,
                        "limit": price,
                        "status": status
                    }

        logger.info(f"✅ Extracted {len(symbol_prices)} symbols with prices")
        for symbol, price in symbol_prices.items():
            logger.debug(f"   {symbol:15} @ ₹{price:8.2f}")

        return symbol_prices

    except Exception as e:
        logger.error(f"❌ Failed to fetch forever all: {e}")
        logger.exception("Traceback:")
        return {}


# ==========================
# DATABASE OPERATIONS
# ==========================
def get_open_trades():
    """Fetch all OPEN trades"""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT id, symbol, security_id, qty, entry_price, entry_time, sl_price, status
                FROM trades
                WHERE status = 'OPEN'
                ORDER BY entry_time ASC
            """).fetchall()

        logger.info(f"📋 Fetched {len(rows)} open trades")
        for row in rows:
            logger.debug(f"   {row['symbol']:15} Qty:{row['qty']:5} Entry:₹{row['entry_price']:8.2f} SL:₹{row['sl_price'] or 0}")

        return rows
    except Exception as e:
        logger.error(f"❌ Failed to fetch trades: {e}")
        return []


def update_trade_pnl(trade_id, current_price):
    """Update P&L in trades table"""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()

            if not trade:
                return False

            qty = trade['qty']
            entry_price = trade['entry_price']
            symbol = trade['symbol']

            pnl = (current_price - entry_price) * qty
            pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0

            # Add missing columns if needed
            for col in ['current_price', 'pnl', 'pnl_percent', 'updated_at']:
                try:
                    col_type = 'TEXT' if col == 'updated_at' else 'REAL'
                    conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_type}")
                except:
                    pass

            conn.execute("""
                UPDATE trades
                SET current_price = ?, pnl = ?, pnl_percent = ?, updated_at = ?
                WHERE id = ?
            """, (current_price, round(pnl, 2), round(pnl_pct, 2), datetime.now(timezone.utc).isoformat(), trade_id))
            conn.commit()

        logger.info(f"💰 {symbol}: LTP=₹{current_price:.2f} | P&L=₹{pnl:.2f} ({pnl_pct:.2f}%)")
        return True
    except Exception as e:
        logger.error(f"Failed to update P&L: {e}")
        return False


def record_sl_order(trade_id, sl_order_id, trigger_price):
    """Record placed SL order in database"""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("""
                UPDATE trades
                SET sl_price = ?
                WHERE id = ?
            """, (trigger_price, trade_id))
            conn.commit()
        logger.info(f"✅ SL recorded: Trade {trade_id} @ ₹{trigger_price}")
        return True
    except Exception as e:
        logger.error(f"Failed to record SL: {e}")
        return False


# ==========================
# PLACE SL ORDER ON DHAN
# ==========================
def place_sl_order(security_id, qty, trigger_price, symbol, trade_id):
    """
    Place SL (SELL) order on Dhan.
    Uses Forever Order API endpoint.
    """

    def round_to_tick(value):
        """Round to nearest 0.05 rupees"""
        return round(round(value / 0.05) * 0.05, 2)

    trigger_price = round_to_tick(trigger_price)
    limit_price = round_to_tick(trigger_price * 0.995)  # 0.5% below trigger
    disclosed_qty = max(1, int(qty * 0.3))  # At least 30% disclosed

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(uuid.uuid4()).replace("-", "")[:20],
        "orderFlag": "SINGLE",
        "transactionType": "SELL",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",
        "validity": "DAY",
        "securityId": str(security_id),
        "quantity": int(qty),
        "price": limit_price,
        "triggerPrice": trigger_price,
        "disclosedQuantity": disclosed_qty
    }

    token = get_token()
    if not token:
        logger.error("❌ No token for SL placement")
        return None

    try:
        logger.info(f"📤 Placing SL: {symbol}")
        logger.debug(f"   Qty={qty}, Trigger=₹{trigger_price}, Limit=₹{limit_price}, Disclosed={disclosed_qty}")

        r = session.post(
            "https://api.dhan.co/v2/forever/orders",
            json=payload,
            headers={"access-token": token, "Content-Type": "application/json"},
            timeout=30
        )

        logger.debug(f"   HTTP {r.status_code}")

        if r.status_code not in (200, 201):
            logger.error(f"❌ Failed: {r.status_code}")
            logger.debug(f"   Response: {r.text[:200]}")
            send_telegram(f"❌ SL failed for {symbol}: HTTP {r.status_code}")
            return None

        data = r.json()
        sl_order_id = data.get("orderId")

        if sl_order_id:
            logger.info(f"✅ SL PLACED! Order: {sl_order_id}")
            record_sl_order(trade_id, sl_order_id, trigger_price)
            send_telegram(f"🛡️ SL {symbol} @ ₹{trigger_price} | Order: {sl_order_id}")
            return sl_order_id
        else:
            logger.error(f"❌ No orderId: {data}")
            return None

    except Exception as e:
        logger.error(f"❌ Exception: {e}")
        send_telegram(f"❌ SL Exception {symbol}: {str(e)[:100]}")
        return None


# ==========================
# SL CALCULATION
# ==========================
def calculate_sl(entry, ltp, current_sl):
    """Calculate new SL with trailing logic"""
    base_sl = entry * BASE_SL_PCT
    new_sl = max(current_sl or 0, base_sl)

    if ltp > entry:
        profit = ltp - entry
        trailing_sl = entry + (profit * TRAIL_PROFIT_LOCK)
        max_allowed_sl = ltp * (1 - MIN_LTP_BUFFER)
        new_sl = max(new_sl, min(trailing_sl, max_allowed_sl))

    return round(new_sl, 2)


# ==========================
# MAIN EXECUTION
# ==========================
def run():
    try:
        logger.info("=" * 90)
        logger.info("🚀 SL ENGINE V7.2 (FINAL - WORKING)")
        logger.info("=" * 90)
        logger.info(f"Database: {DB_FILE}")
        logger.info(f"Time: {datetime.now(timezone.utc).isoformat()}")
        logger.info("=" * 90)

        # Step 1: Get prices from forever orders
        logger.info("\n[STEP 1] Fetching prices from /v2/forever/all...")
        symbol_prices = get_prices_from_forever_all()

        if not symbol_prices:
            logger.warning("⚠️ No prices extracted from forever orders")
            logger.info("=" * 90)
            return

        # Step 2: Get open trades
        logger.info("\n[STEP 2] Fetching open trades...")
        open_trades = get_open_trades()

        if not open_trades:
            logger.info("✅ No open trades to manage")
            return

        logger.info(f"🔍 Managing {len(open_trades)} open trades\n")

        # Step 3: Process each trade
        placed_count = 0

        for trade in open_trades:
            trade_id = trade['id']
            symbol = trade['symbol']
            security_id = trade['security_id']
            qty = trade['qty']
            entry_price = trade['entry_price']
            current_sl = trade['sl_price']

            logger.info(f"\n📍 {symbol} (Trade ID: {trade_id})")
            logger.info(f"   Entry: ₹{entry_price:8.2f} | Qty: {qty}")

            # Get current price
            ltp = symbol_prices.get(symbol)

            if not ltp:
                logger.warning(f"   ⚠️ No price data in forever orders")
                continue

            # Update P&L
            update_trade_pnl(trade_id, ltp)

            # Calculate SL
            new_sl = calculate_sl(entry_price, ltp, current_sl)
            pnl = (ltp - entry_price) * qty
            pnl_pct = ((ltp - entry_price) / entry_price) * 100

            logger.info(f"   LTP: ₹{ltp:8.2f} | P&L: ₹{pnl:10.2f} ({pnl_pct:7.2f}%)")
            logger.info(f"   SL: ₹{current_sl or 'NOT SET'} → ₹{new_sl}")

            # Place SL if not already placed
            if not current_sl or current_sl == 0:
                logger.info(f"   Action: 🛡️ PLACE SL")
                if security_id:
                    sl_id = place_sl_order(security_id, qty, new_sl, symbol, trade_id)
                    if sl_id:
                        placed_count += 1
                else:
                    logger.warning(f"   ⚠️ Missing security_id, cannot place SL")
            else:
                logger.info(f"   Action: ✅ SL already placed")

        logger.info("\n" + "=" * 90)
        logger.info(f"✅ ENGINE COMPLETED")
        logger.info(f"   SL Orders Placed: {placed_count}")
        logger.info("=" * 90)

    except Exception as e:
        logger.exception("❌ CRASH")
        send_telegram(f"❌ SL ENGINE CRASH: {e}")
        raise


if __name__ == "__main__":
    run()