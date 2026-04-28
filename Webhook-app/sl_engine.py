# ==============================================
# 🚀 SL ENGINE V6 - DIAGNOSTIC VERSION
# Prints ALL orders from Dhan + DB in detail
# ==============================================

import os
import requests
import pyotp
import sqlite3
import uuid
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from sync_trades_with_dhan import sync_trades_with_dhan

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
BASE_SL_PCT = 0.92
TRAIL_PROFIT_LOCK = 0.5
MIN_LTP_BUFFER = 0.05

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
# DHAN API CALLS
# ==========================
def fetch_all_orders_detailed():
    """
    Fetch ALL orders from Dhan and print them in FULL DETAIL.
    This is for DIAGNOSTICS - to see what Dhan actually has.
    """
    try:
        token = get_token()
        if not token:
            logger.error("❌ No valid token")
            return []

        logger.info("\n" + "=" * 80)
        logger.info("🔍 DIAGNOSTIC: Fetching ALL orders from Dhan API")
        logger.info("=" * 80)

        r = session.get(
            "https://api.dhan.co/v2/forever/orders",
            headers={"access-token": token},
            timeout=30
        )

        if r.status_code != 200:
            logger.error(f"❌ Dhan API error: {r.status_code}")
            return []

        response_data = r.json()

        # Handle different formats
        if isinstance(response_data, list):
            dhan_orders = response_data
        elif isinstance(response_data, dict):
            dhan_orders = response_data.get("orders") or response_data.get("data") or []
        else:
            logger.error(f"❌ Unexpected response type: {type(response_data)}")
            return []

        logger.info(f"✅ Total orders from Dhan: {len(dhan_orders)}\n")

        # Print ALL orders in detail
        logger.info("📋 DETAILED ORDER LIST FROM DHAN:")
        logger.info("-" * 80)

        for idx, order in enumerate(dhan_orders):
            if not isinstance(order, dict):
                logger.warning(f"   [{idx}] SKIPPED: Not a dict ({type(order)})")
                continue

            order_id = order.get("orderId", "N/A")
            symbol = order.get("symbol", "N/A")
            trans_type = order.get("transactionType", "N/A")
            order_status = order.get("orderStatus", "N/A")
            exec_qty = order.get("executedQuantity", 0)
            exec_price = order.get("executedPrice", 0)
            order_qty = order.get("quantity", 0)
            order_price = order.get("price", 0)

            logger.info(f"\n   [{idx}] Order ID: {order_id}")
            logger.info(f"       Symbol: {symbol}")
            logger.info(f"       Type: {trans_type} (BUY/SELL)")
            logger.info(f"       Status: {order_status}")
            logger.info(f"       Order Qty: {order_qty}")
            logger.info(f"       Order Price: ₹{order_price}")
            logger.info(f"       Executed Qty: {exec_qty}")
            logger.info(f"       Executed Price: ₹{exec_price}")
            logger.info(f"       Trigger Price: {order.get('triggerPrice', 'N/A')}")

            # Color-code based on status
            if order_status == "ACCEPTED" and exec_qty > 0:
                logger.info(f"       ✅ FILLED - Ready for SL")
            elif order_status == "TRIGGERED":
                logger.info(f"       ⏳ TRIGGERED - Waiting for fill")
            elif order_status == "PENDING":
                logger.info(f"       ⏳ PENDING - Not triggered")
            else:
                logger.info(f"       ❌ STATUS: {order_status}")

        logger.info("\n" + "-" * 80)

        # Summary by type
        buy_orders = [o for o in dhan_orders if isinstance(o, dict) and o.get("transactionType") == "BUY"]
        sell_orders = [o for o in dhan_orders if isinstance(o, dict) and o.get("transactionType") == "SELL"]

        logger.info(f"\n📊 ORDER SUMMARY:")
        logger.info(f"   Total Orders: {len(dhan_orders)}")
        logger.info(f"   BUY Orders: {len(buy_orders)}")
        logger.info(f"   SELL Orders: {len(sell_orders)}")

        # Count by status
        filled = sum(1 for o in buy_orders if isinstance(o, dict) and o.get("orderStatus") == "ACCEPTED" and o.get("executedQuantity", 0) > 0)
        triggered = sum(1 for o in buy_orders if isinstance(o, dict) and o.get("orderStatus") == "TRIGGERED")
        pending = sum(1 for o in buy_orders if isinstance(o, dict) and o.get("orderStatus") == "PENDING")

        logger.info(f"\n   BUY Orders by Status:")
        logger.info(f"      FILLED (ACCEPTED + executed): {filled}")
        logger.info(f"      TRIGGERED (waiting): {triggered}")
        logger.info(f"      PENDING: {pending}")

        logger.info("=" * 80 + "\n")

        return dhan_orders

    except Exception as e:
        logger.error(f"❌ Exception fetching orders: {e}")
        logger.exception("Traceback:")
        return []


def print_database_tables():
    """
    Print contents of ALL relevant tables in database.
    For diagnostics - to see what's in the DB.
    """
    try:
        logger.info("=" * 80)
        logger.info("🗄️  DIAGNOSTIC: Database Contents")
        logger.info("=" * 80)

        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row

        # Check trades table
        logger.info("\n📊 TRADES TABLE:")
        logger.info("-" * 80)
        trades = conn.execute("SELECT * FROM trades").fetchall()
        logger.info(f"Total rows: {len(trades)}\n")

        if trades:
            for trade in trades:
                logger.info(f"ID: {trade['id']} | Symbol: {trade['symbol']} | Status: {trade['status']}")
                logger.info(f"  Qty: {trade['qty']} | Entry: ₹{trade['entry_price']} | Current: ₹{trade['current_price']}")
                logger.info(f"  P&L: ₹{trade['pnl']} ({trade['pnl_percent']}%) | Updated: {trade['updated_at']}")
                logger.info("")
        else:
            logger.warning("⚠️ No trades in table!")

        # Check executed_orders table
        logger.info("\n📋 EXECUTED_ORDERS TABLE:")
        logger.info("-" * 80)
        try:
            exec_orders = conn.execute("SELECT * FROM executed_orders").fetchall()
            logger.info(f"Total rows: {len(exec_orders)}\n")

            if exec_orders:
                for order in exec_orders:
                    logger.info(f"ID: {order['dhan_order_id']} | Symbol: {order['symbol']} | Status: {order['status']}")
                    logger.info(f"  Qty: {order['qty_executed']} | Entry: ₹{order['entry_price_executed']}")
                    logger.info(f"  SL Price: ₹{order['sl_price']} | SL Order ID: {order['sl_order_id']}")
                    logger.info("")
            else:
                logger.warning("⚠️ No executed orders in table!")
        except Exception as e:
            logger.warning(f"⚠️ Could not read executed_orders: {e}")

        # Check trade_setups table
        logger.info("\n🎯 TRADE_SETUPS TABLE:")
        logger.info("-" * 80)
        setups = conn.execute("SELECT setup_id, symbol, status, entry, sl, target, pnl FROM trade_setups LIMIT 10").fetchall()
        logger.info(f"Total rows: {len(setups)} (showing first 10)\n")

        if setups:
            for setup in setups:
                logger.info(f"ID: {setup['setup_id']} | Symbol: {setup['symbol']} | Status: {setup['status']}")
                logger.info(f"  Entry: ₹{setup['entry']} | SL: ₹{setup['sl']} | Target: ₹{setup['target']} | P&L: {setup['pnl']}")
                logger.info("")

        conn.close()
        logger.info("=" * 80 + "\n")

    except Exception as e:
        logger.error(f"❌ Failed to read database: {e}")
        logger.exception("Traceback:")


# ==========================
# MAIN EXECUTION
# ==========================
def run():
    try:
        logger.info("=" * 80)
        logger.info("🚀 SL ENGINE V6 - DIAGNOSTIC MODE")
        logger.info("=" * 80)
        logger.info(f"Database: {DB_FILE}")
        logger.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
        logger.info("=" * 80)

        # STEP 1: Print ALL Dhan orders with details
        dhan_orders = fetch_all_orders_detailed()

        # STEP 2: Print ALL database contents
        print_database_tables()

        # STEP 3: Analysis
        logger.info("=" * 80)
        logger.info("📈 ANALYSIS")
        logger.info("=" * 80)

        # Find filled BUY orders
        filled_buys = []
        for order in dhan_orders:
            if isinstance(order, dict):
                if (order.get("transactionType") == "BUY" and
                        order.get("orderStatus") == "ACCEPTED" and
                        order.get("executedQuantity", 0) > 0):
                    filled_buys.append(order)

        logger.info(f"\n✅ FILLED BUY ORDERS (Ready for SL):")
        if filled_buys:
            for order in filled_buys:
                logger.info(f"   - {order.get('symbol')}: {order.get('executedQuantity')} @ ₹{order.get('executedPrice')}")
        else:
            logger.warning("   ⚠️ NO FILLED BUY ORDERS")

        # Check if trades table has OPEN trades
        conn = sqlite3.connect(DB_FILE)
        open_trades = conn.execute("SELECT symbol FROM trades WHERE status = 'OPEN'").fetchall()
        conn.close()

        logger.info(f"\n📊 OPEN TRADES IN DATABASE:")
        if open_trades:
            for trade in open_trades:
                logger.info(f"   - {trade[0]}")
        else:
            logger.warning("   ⚠️ NO OPEN TRADES")

        logger.info("\n" + "=" * 80)
        logger.info("✅ DIAGNOSTIC COMPLETE")
        logger.info("=" * 80)
        logger.info("\nNEXT STEPS:")
        logger.info("1. Check if BUY orders are FILLED in Dhan (look for 'ACCEPTED' status)")
        logger.info("2. Check if symbols in Dhan match symbols in trades table")
        logger.info("3. If no filled orders, need to place NEW buy orders first")
        logger.info("=" * 80 + "\n")

    except Exception as e:
        logger.exception("❌ DIAGNOSTIC FAILED")
        raise


if __name__ == "__main__":
    run()