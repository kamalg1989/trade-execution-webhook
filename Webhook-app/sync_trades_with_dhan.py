# ==============================================
# 🔄 FIXED: sync_trades_with_dhan()
# Fixed Dhan API response parsing
# ==============================================

import sqlite3
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


def sync_trades_with_dhan(db_file, session, get_token, DHAN_CLIENT_ID):
    """
    NEW FUNCTION: Sync trades table with live Dhan order data.

    FIXED: Handles Dhan API response correctly

    Uses EXISTING Dhan API integration from SL engine.
    - Calls Dhan API to get all live orders
    - Extracts executed price and quantity for each BUY order
    - Updates trades table with current execution status
    - Calculates P&L based on entry vs current Dhan data

    Inputs:
    - db_file: path to trades.db
    - session: requests.Session() object (from SL engine)
    - get_token: function to get valid Dhan token (from SL engine)
    - DHAN_CLIENT_ID: from environment (from SL engine)

    Returns:
    - dict: {symbol: {qty, entry, status, synced}}
    """

    try:
        logger.info("=" * 70)
        logger.info("🔄 SYNC_TRADES_WITH_DHAN: Fetching live Dhan order data...")
        logger.info("=" * 70)

        # Step 1: Get all orders from Dhan API
        logger.info("📡 Fetching orders from Dhan...")

        token = get_token()
        if not token:
            logger.error("❌ No valid Dhan token available")
            return {}

        try:
            r = session.get(
                "https://api.dhan.co/v2/forever/orders",
                headers={"access-token": token},
                timeout=30
            )

            if r.status_code != 200:
                logger.error(f"❌ Dhan API error: {r.status_code}")
                return {}

            response_data = r.json()
            logger.debug(f"Raw response type: {type(response_data)}")

            # FIXED: Handle different response formats
            if isinstance(response_data, list):
                # Response is a list directly
                dhan_orders = response_data
                logger.info(f"✅ Retrieved {len(dhan_orders)} total orders from Dhan (list format)")
            elif isinstance(response_data, dict) and "orders" in response_data:
                # Response is a dict with "orders" key
                dhan_orders = response_data.get("orders", [])
                logger.info(f"✅ Retrieved {len(dhan_orders)} total orders from Dhan (dict format)")
            elif isinstance(response_data, dict) and "data" in response_data:
                # Response is a dict with "data" key
                dhan_orders = response_data.get("data", [])
                logger.info(f"✅ Retrieved {len(dhan_orders)} total orders from Dhan (data format)")
            else:
                logger.error(f"❌ Unexpected response format: {response_data}")
                return {}

        except Exception as e:
            logger.error(f"❌ Failed to fetch Dhan orders: {e}")
            return {}

        # Step 2: Filter BUY orders (our entry orders)
        buy_orders = []
        for order in dhan_orders:
            if isinstance(order, dict) and order.get("transactionType") == "BUY":
                buy_orders.append(order)

        logger.info(f"📊 Found {len(buy_orders)} BUY orders")

        if not buy_orders:
            logger.info("✅ No BUY orders to sync")
            return {}

        # Step 3: Open database connection
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row

        # Step 4: Process each BUY order and update DB
        logger.info("\n📈 Syncing Dhan orders with trades table:")
        logger.info("-" * 70)

        synced_trades = {}

        for order in buy_orders:
            try:
                dhan_order_id = order.get("orderId")
                order_status = order.get("orderStatus", "UNKNOWN")
                filled_qty = order.get("executedQuantity", 0)
                filled_price = order.get("executedPrice", 0)
                exchange_order_id = order.get("exchangeOrderId")

                if not dhan_order_id:
                    continue

                # Check if this order exists in our trades table
                existing_trade = conn.execute("""
                    SELECT id, symbol, qty, entry_price, status
                    FROM trades
                    WHERE security_id = ? OR id = ?
                """, (exchange_order_id, dhan_order_id)).fetchone()

                if not existing_trade:
                    logger.debug(f"⏳ Dhan order {dhan_order_id}: {order_status} (not in trades table yet)")
                    synced_trades[dhan_order_id] = {
                        'status': order_status,
                        'synced': False,
                        'reason': 'Not yet in trades table'
                    }
                    continue

                trade_id, symbol, qty, entry_price, trade_status = existing_trade

                # Only update if filled
                if filled_qty > 0 and filled_price > 0 and order_status == "ACCEPTED":

                    # Update trades table with Dhan data
                    conn.execute("""
                        UPDATE trades
                        SET 
                            qty = ?,
                            entry_price = ?,
                            status = 'OPEN',
                            entry_time = ?
                        WHERE id = ?
                    """, (
                        filled_qty,
                        filled_price,
                        datetime.now(timezone.utc).isoformat(),
                        trade_id
                    ))

                    logger.info(f"✅ {symbol:15} | Qty: {filled_qty:5} | Entry: ₹{filled_price:8.2f} | Status: {order_status}")

                    synced_trades[dhan_order_id] = {
                        'symbol': symbol,
                        'qty': filled_qty,
                        'entry_price': filled_price,
                        'dhan_status': order_status,
                        'synced': True
                    }

                else:
                    logger.debug(f"⏳ {symbol} | Dhan ID: {dhan_order_id} | Status: {order_status} (not filled)")

                    synced_trades[dhan_order_id] = {
                        'symbol': symbol if 'symbol' in locals() else 'UNKNOWN',
                        'dhan_status': order_status,
                        'synced': False,
                        'reason': 'Awaiting fill'
                    }

            except Exception as e:
                logger.error(f"❌ Error processing order: {e}")
                continue

        # Step 5: Sync P&L for all OPEN trades
        logger.info("-" * 70)
        logger.info("\n📊 Calculating P&L for open trades:")
        logger.info("-" * 70)

        open_trades = conn.execute("""
            SELECT id, symbol, qty, entry_price
            FROM trades
            WHERE status = 'OPEN'
        """).fetchall()

        pnl_updates = 0

        for trade in open_trades:
            try:
                trade_id, symbol, qty, entry_price = trade

                # Try to get current price from Dhan filled orders for this symbol
                current_price = None
                for order in buy_orders:
                    if order.get("executedPrice") and order.get("orderStatus") == "ACCEPTED":
                        current_price = order.get("executedPrice")
                        break

                # If not found in current orders, use entry price
                if not current_price:
                    current_price = entry_price

                if current_price and entry_price and qty > 0:
                    pnl = (current_price - entry_price) * qty
                    pnl_percent = ((current_price - entry_price) / entry_price) * 100

                    conn.execute("""
                        UPDATE trades
                        SET 
                            current_price = ?,
                            pnl = ?,
                            pnl_percent = ?,
                            updated_at = ?
                        WHERE id = ?
                    """, (
                        current_price,
                        round(pnl, 2),
                        round(pnl_percent, 2),
                        datetime.now(timezone.utc).isoformat(),
                        trade_id
                    ))

                    logger.info(f"✅ {symbol:15} | Qty: {qty:5} | Entry: ₹{entry_price:8.2f} | Current: ₹{current_price:8.2f} | PnL: ₹{pnl:10.2f} ({pnl_percent:7.2f}%)")
                    pnl_updates += 1

            except Exception as e:
                logger.error(f"❌ Failed to update P/L: {e}")
                continue

        # Commit all changes
        conn.commit()
        conn.close()

        logger.info("-" * 70)
        logger.info(f"\n✅ Dhan sync completed!")
        logger.info(f"   - Synced trades: {len(synced_trades)}")
        logger.info(f"   - P&L updates: {pnl_updates}")
        logger.info("=" * 70 + "\n")

        return synced_trades

    except Exception as e:
        logger.error(f"❌ SYNC_TRADES_WITH_DHAN FAILED: {e}")
        logger.exception("Traceback:")
        return {}