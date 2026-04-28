# ==============================================
# 🚀 SL ENGINE V7 (PRODUCTION)
# Works with ACTUAL trades table
# Uses Dhan API for prices (no yfinance)
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
        logger.info("🔑 Generating Dhan access token")

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

        logger.info(f"✅ Token generated successfully")
        return token

    except Exception as e:
        logger.error(f"❌ Token generation failed: {e}")
        return None


def get_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY

    if CURRENT_TOKEN and datetime.now(timezone.utc) < TOKEN_EXPIRY:
        return CURRENT_TOKEN

    logger.info("🔄 Token expired, regenerating...")
    return generate_token()


# ==========================
# GET LIVE PRICE FROM DHAN
# ==========================
def get_ltp_from_dhan(symbol):
    """
    Fetch LTP from Dhan API (not yfinance).
    Uses quote endpoint if available, falls back to order data.
    """
    try:
        token = get_token()
        if not token:
            logger.debug(f"❌ No token for {symbol}")
            return None

        # Try Dhan quote endpoint
        r = session.get(
            "https://api.dhan.co/v2/quotes",
            headers={"access-token": token},
            params={
                "mode": "LTP",
                "exchangeTokens": f"NSE_EQ|{symbol}"
            },
            timeout=15
        )

        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict) and "data" in data:
                quote_data = data["data"]
                if isinstance(quote_data, list) and len(quote_data) > 0:
                    ltp = quote_data[0].get("ltp") or quote_data[0].get("lastPrice")
                    if ltp:
                        logger.debug(f"✅ {symbol} LTP from Dhan: ₹{ltp}")
                        return float(ltp)

        logger.debug(f"⚠️ No LTP for {symbol} from Dhan")
        return None

    except Exception as e:
        logger.debug(f"⚠️ LTP fetch failed for {symbol}: {e}")
        return None


# ==========================
# DATABASE OPERATIONS
# ==========================
def get_open_trades():
    """
    Fetch all OPEN trades from trades table.
    This is where your actual trades are stored.
    """
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT id, symbol, security_id, qty, entry_price, entry_time, sl_price, status
                FROM trades
                WHERE status = 'OPEN'
                ORDER BY entry_time ASC
            """).fetchall()

        logger.info(f"📋 Fetched {len(rows)} open trades from trades table")

        for row in rows:
            logger.debug(f"   → {row['symbol']:15} Qty:{row['qty']:5} Entry:₹{row['entry_price']:8.2f}")

        return rows
    except Exception as e:
        logger.error(f"❌ Failed to fetch open trades: {e}")
        return []


def get_trade_by_id(trade_id):
    """Get single trade details"""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT * FROM trades WHERE id = ?
            """, (trade_id,)).fetchone()
        return row
    except Exception as e:
        logger.error(f"Failed to fetch trade {trade_id}: {e}")
        return None


def update_trade_pnl(trade_id, current_price):
    """Update P&L in trades table"""
    try:
        trade = get_trade_by_id(trade_id)
        if not trade:
            return False

        qty = trade['qty']
        entry_price = trade['entry_price']
        symbol = trade['symbol']

        pnl = (current_price - entry_price) * qty
        pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0

        with sqlite3.connect(DB_FILE) as conn:
            # Add columns if they don't exist
            try:
                conn.execute("ALTER TABLE trades ADD COLUMN current_price REAL")
            except:
                pass
            try:
                conn.execute("ALTER TABLE trades ADD COLUMN pnl REAL")
            except:
                pass
            try:
                conn.execute("ALTER TABLE trades ADD COLUMN pnl_percent REAL")
            except:
                pass
            try:
                conn.execute("ALTER TABLE trades ADD COLUMN updated_at TEXT")
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
    """Record placed SL order in sl_orders table"""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            # Try to insert into sl_orders
            try:
                conn.execute("""
                    INSERT INTO sl_orders (trade_id, dhan_order_id, trigger_price, created_at)
                    VALUES (?, ?, ?, ?)
                """, (trade_id, sl_order_id, trigger_price, datetime.now(timezone.utc).isoformat()))
                conn.commit()
                logger.info(f"✅ SL order recorded: {sl_order_id}")
                return True
            except:
                # If table doesn't exist, update trades table instead
                conn.execute("""
                    UPDATE trades
                    SET sl_price = ?, target_price = ?
                    WHERE id = ?
                """, (trigger_price, None, trade_id))
                conn.commit()
                logger.info(f"✅ SL price recorded in trades table")
                return True
    except Exception as e:
        logger.error(f"Failed to record SL order: {e}")
        return False


# ==========================
# DHAN API - PLACE SL ORDER
# ==========================
def place_sl_order(security_id, qty, trigger_price, symbol, trade_id):
    """
    Place SL order on Dhan for a trade.
    """
    def round_to_tick(value):
        return round(round(value / 0.05) * 0.05, 2)

    trigger_price = round_to_tick(trigger_price)
    limit_price = round_to_tick(trigger_price * 0.995)
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
        "securityId": str(security_id),
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
        logger.info(f"📤 Placing SL for {symbol}: Qty={qty}, Trigger=₹{trigger_price}, Limit=₹{limit_price}")

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
            record_sl_order(trade_id, sl_order_id, trigger_price)
            send_telegram(f"🛡️ SL placed for {symbol} @ ₹{trigger_price}")
            return sl_order_id
        else:
            logger.error(f"⚠️ No orderId in response: {data}")
            return None

    except Exception as e:
        logger.error(f"❌ SL placement exception: {e}")
        send_telegram(f"❌ SL exception for {symbol}: {e}")
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
        logger.info("=" * 80)
        logger.info("🚀 SL ENGINE V7 (PRODUCTION)")
        logger.info("=" * 80)
        logger.info(f"Database: {DB_FILE}")
        logger.info(f"Time: {datetime.now(timezone.utc).isoformat()}")
        logger.info("=" * 80)

        # Step 1: Get open trades from trades table
        logger.info("\n[STEP 1] Fetching open trades from database...")
        open_trades = get_open_trades()

        if not open_trades:
            logger.info("✅ No open trades to manage")
            logger.info("=" * 80)
            return

        logger.info(f"🔍 Managing {len(open_trades)} open trades\n")

        # Step 2: For each trade, manage SL
        for trade in open_trades:
            trade_id = trade['id']
            symbol = trade['symbol']
            security_id = trade['security_id']
            qty = trade['qty']
            entry_price = trade['entry_price']
            current_sl = trade['sl_price']

            logger.info(f"\n📍 Processing {symbol} (Trade ID: {trade_id})")
            logger.info(f"   Entry: ₹{entry_price:8.2f} | Qty: {qty}")

            # Get LTP from Dhan
            ltp = get_ltp_from_dhan(symbol)
            if not ltp:
                logger.warning(f"   ⚠️ Could not fetch LTP, skipping...")
                continue

            # Update P&L
            update_trade_pnl(trade_id, ltp)

            # Calculate new SL
            new_sl = calculate_sl(entry_price, ltp, current_sl)
            pnl = (ltp - entry_price) * qty
            pnl_pct = ((ltp - entry_price) / entry_price) * 100

            logger.info(f"   LTP: ₹{ltp:8.2f} | P&L: ₹{pnl:10.2f} ({pnl_pct:7.2f}%)")
            logger.info(f"   SL: ₹{current_sl} → ₹{new_sl}")

            # Place SL if not already placed
            if not current_sl or current_sl == 0:
                logger.info(f"   Action: PLACE SL")
                if security_id:
                    place_sl_order(security_id, qty, new_sl, symbol, trade_id)
                else:
                    logger.warning(f"   ⚠️ Missing security_id, cannot place SL")

            elif new_sl > current_sl:
                logger.info(f"   Action: TRAIL SL (would modify if implemented)")
                # TODO: Implement SL modification

            else:
                logger.info(f"   Action: HOLD")

        logger.info("\n" + "=" * 80)
        logger.info("✅ SL ENGINE COMPLETED SUCCESSFULLY")
        logger.info("=" * 80)

    except Exception as e:
        logger.exception("❌ SL ENGINE CRASHED")
        send_telegram(f"❌ SL ENGINE ERROR: {e}")
        raise


if __name__ == "__main__":
    run()