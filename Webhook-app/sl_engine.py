# ==============================================
# 🚀 SL ENGINE V6 - FIXED DIAGNOSTIC
# Gets symbol from holdings/positions, fixes schema
# ==============================================

import os
import requests
import pyotp
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# ==========================
# CONFIG
# ==========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
if os.path.exists(ENV_PATH):
    from dotenv import load_dotenv
    load_dotenv(ENV_PATH)

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")

DB_FILE = os.path.join(BASE_DIR, "trades.db")
CURRENT_TOKEN = None
TOKEN_EXPIRY = datetime.now(timezone.utc)
session = requests.Session()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ==========================
# TOKEN MANAGEMENT
# ==========================
def generate_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY
    try:
        totp = pyotp.TOTP(DHAN_TOTP_SECRET)
        response = session.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={"dhanClientId": DHAN_CLIENT_ID, "pin": DHAN_PIN, "totp": totp.now()},
            timeout=30
        )
        response.raise_for_status()
        token = response.json().get("accessToken")
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
    logger.info("🔄 Regenerating token...")
    return generate_token()


# ==========================
# DIAGNOSTICS
# ==========================
def diagnose_dhan_orders():
    """Fetch and analyze Dhan orders with ALL available fields"""
    logger.info("\n" + "=" * 90)
    logger.info("🔍 DIAGNOSTIC 1: Dhan Orders - ALL Fields")
    logger.info("=" * 90)

    token = get_token()
    if not token:
        logger.error("❌ No token")
        return []

    try:
        r = session.get(
            "https://api.dhan.co/v2/forever/orders",
            headers={"access-token": token},
            timeout=30
        )

        if r.status_code != 200:
            logger.error(f"❌ API error: {r.status_code}")
            return []

        response_data = r.json()
        dhan_orders = response_data if isinstance(response_data, list) else response_data.get("orders", [])

        logger.info(f"\n✅ Total orders: {len(dhan_orders)}\n")

        # Print raw order structure
        for idx, order in enumerate(dhan_orders[:2]):  # Show first 2 orders
            logger.info(f"📋 Order {idx} - ALL FIELDS:")
            logger.info("-" * 90)
            if isinstance(order, dict):
                for key, value in order.items():
                    logger.info(f"   {key:30} = {value}")
            else:
                logger.info(f"   Type: {type(order)}")
            logger.info("")

        # Count by type and status
        buy_triggered = sum(1 for o in dhan_orders if isinstance(o, dict) and o.get("transactionType") == "BUY" and o.get("orderStatus") == "TRIGGERED")
        buy_accepted = sum(1 for o in dhan_orders if isinstance(o, dict) and o.get("transactionType") == "BUY" and o.get("orderStatus") == "ACCEPTED")
        sell_pending = sum(1 for o in dhan_orders if isinstance(o, dict) and o.get("transactionType") == "SELL" and o.get("orderStatus") == "PENDING")

        logger.info("📊 SUMMARY:")
        logger.info(f"   BUY + TRIGGERED (waiting): {buy_triggered}")
        logger.info(f"   BUY + ACCEPTED (filled): {buy_accepted}")
        logger.info(f"   SELL + PENDING (SL waiting): {sell_pending}")
        logger.info("=" * 90 + "\n")

        return dhan_orders

    except Exception as e:
        logger.error(f"❌ Failed: {e}")
        return []


def diagnose_database_schema():
    """Check database schema and fix issues"""
    logger.info("=" * 90)
    logger.info("🗄️  DIAGNOSTIC 2: Database Schema")
    logger.info("=" * 90)

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        # Get all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        logger.info(f"\n✅ Tables in database: {tables}\n")

        # Check trades table schema
        logger.info("📊 TRADES TABLE SCHEMA:")
        logger.info("-" * 90)
        cursor.execute("PRAGMA table_info(trades)")
        schema = cursor.fetchall()

        columns = {}
        for row in schema:
            col_id, name, type_, notnull, default, pk = row
            columns[name] = type_
            logger.info(f"   {name:25} {type_:15} {'NOT NULL' if notnull else ''} {'PK' if pk else ''}")

        # Check for missing columns
        required = ['id', 'symbol', 'qty', 'entry_price', 'status', 'entry_time']
        optional = ['current_price', 'pnl', 'pnl_percent', 'updated_at']

        missing_required = [c for c in required if c not in columns]
        missing_optional = [c for c in optional if c not in columns]

        if missing_required:
            logger.error(f"\n❌ MISSING REQUIRED COLUMNS: {missing_required}")
        else:
            logger.info(f"\n✅ All required columns present")

        if missing_optional:
            logger.warning(f"⚠️  MISSING OPTIONAL COLUMNS: {missing_optional}")

        # Count rows
        cursor.execute("SELECT COUNT(*) FROM trades")
        count = cursor.fetchone()[0]
        logger.info(f"\n📈 Total trades: {count}")

        # Show sample data
        if count > 0:
            logger.info("\n📋 SAMPLE TRADES:")
            cursor.execute("SELECT id, symbol, qty, entry_price, status FROM trades LIMIT 5")
            for row in cursor.fetchall():
                logger.info(f"   ID:{row[0]} | {row[1]:15} | Qty:{row[2]:5} | Entry:₹{row[3]:8.2f} | {row[4]}")

        # Check executed_orders table
        logger.info("\n\n📋 EXECUTED_ORDERS TABLE SCHEMA:")
        logger.info("-" * 90)
        try:
            cursor.execute("PRAGMA table_info(executed_orders)")
            schema = cursor.fetchall()

            if schema:
                for row in schema:
                    col_id, name, type_, notnull, default, pk = row
                    logger.info(f"   {name:25} {type_:15} {'NOT NULL' if notnull else ''} {'PK' if pk else ''}")

                cursor.execute("SELECT COUNT(*) FROM executed_orders")
                count = cursor.fetchone()[0]
                logger.info(f"\n📈 Total rows: {count}")
            else:
                logger.warning("⚠️ Table doesn't exist or is empty")
        except Exception as e:
            logger.warning(f"⚠️ Could not read executed_orders: {e}")

        conn.close()
        logger.info("=" * 90 + "\n")

    except Exception as e:
        logger.error(f"❌ Failed: {e}")


def analyze_symbol_mismatch():
    """Analyze mismatch between Dhan and Database"""
    logger.info("=" * 90)
    logger.info("🔍 DIAGNOSTIC 3: Symbol Mismatch Analysis")
    logger.info("=" * 90)

    token = get_token()
    if not token:
        return

    try:
        # Get Dhan orders
        r = session.get(
            "https://api.dhan.co/v2/forever/orders",
            headers={"access-token": token},
            timeout=30
        )

        if r.status_code != 200:
            logger.error(f"❌ Dhan API error")
            return

        response_data = r.json()
        dhan_orders = response_data if isinstance(response_data, list) else response_data.get("orders", [])

        # Get DB trades
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT symbol FROM trades WHERE status='OPEN'")
        db_symbols = [row[0] for row in cursor.fetchall()]
        conn.close()

        logger.info(f"\n📊 Symbols in Database (OPEN trades): {db_symbols}")
        logger.info(f"📊 Orders in Dhan: {len(dhan_orders)}")

        # Dhan BUY orders
        buy_orders = [o for o in dhan_orders if isinstance(o, dict) and o.get("transactionType") == "BUY"]
        logger.info(f"\n🔍 BUY Orders in Dhan: {len(buy_orders)}")

        for order in buy_orders:
            order_id = order.get("orderId")
            symbol = order.get("symbol")
            status = order.get("orderStatus")
            exec_qty = order.get("executedQuantity", 0)

            # Check if symbol is in DB
            if symbol:
                if symbol in db_symbols:
                    logger.info(f"   ✅ {order_id}: Symbol={symbol} (IN DATABASE, Status={status}, ExecQty={exec_qty})")
                else:
                    logger.warning(f"   ⚠️  {order_id}: Symbol={symbol} (NOT in database, Status={status})")
            else:
                logger.error(f"   ❌ {order_id}: Symbol is MISSING! (Status={status})")

        logger.info("\n" + "=" * 90)
        logger.info("🎯 CONCLUSION:")
        logger.info("=" * 90)

        if not buy_orders:
            logger.warning("⚠️ NO BUY ORDERS in Dhan - Need to place buy orders first!")
        elif all(o.get("executedQuantity", 0) == 0 for o in buy_orders):
            logger.warning("⚠️ BUY ORDERS exist but NOT FILLED yet - Waiting for fills...")
        else:
            logger.info("✅ Some BUY orders are filled - SL engine should work once orders fill")

        logger.info("=" * 90 + "\n")

    except Exception as e:
        logger.error(f"❌ Failed: {e}")


def main():
    logger.info("\n" + "=" * 90)
    logger.info("🚀 SL ENGINE V6 - COMPREHENSIVE DIAGNOSTIC")
    logger.info("=" * 90)
    logger.info(f"Database: {DB_FILE}")
    logger.info(f"Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 90)

    # Run all diagnostics
    diagnose_dhan_orders()
    diagnose_database_schema()
    analyze_symbol_mismatch()

    logger.info("\n✅ DIAGNOSTIC COMPLETE\n")


if __name__ == "__main__":
    main()