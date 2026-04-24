# ==============================================
# 🔄 SYNC_TRADES_WITH_DHAN - REWRITTEN
# Directly matches Dhan orders with trades & updates P&L
# ==============================================

import sqlite3
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


def sync_trades_with_dhan(db_file, session, get_token, DHAN_CLIENT_ID):
    """
    Sync trades table with live Dhan order data and update P&L.

    LOGIC:
    1. Fetch all BUY orders from Dhan
    2. For each trade in DB, find matching Dhan order by symbol
    3. If Dhan order is FILLED, update trade with execution price
    4. Calculate P&L = (execution_price - entry_price) * qty
    5. Update trades table with current_price and pnl

    Returns: count of trades updated
    """

    try:
        logger.info("=" * 70)
        logger.info("🔄 SYNC_TRADES_WITH_DHAN: Syncing P&L from Dhan...")
        logger.info("=" * 70)

        # Step 1: Fetch orders from Dhan
        logger.info("📡 Fetching live orders from Dhan...")

        token = get_token()
        if not token:
            logger.error("❌ No valid Dhan token")
            return {}

        try:
            r = session.get(
                "https://api.dhan.co/v2/forever/orders",
                headers={"access-token": token},
                timeout=30
            )

            if r.status_code != 200:
                logger.error(f"❌ Dhan API failed: {r.status_code}")
                return {}

            response_data = r.json()

            # Handle different response formats
            if isinstance(response_data, list):
                dhan_orders = response_data
            elif isinstance(response_data, dict):
                dhan_orders = response_data.get("orders") or response_data.get("data") or []
            else:
                logger.error(f"❌ Unexpected response: {type(response_data)}")
                return {}

            logger.info(f"✅ Retrieved {len(dhan_orders)} total orders from Dhan")

        except Exception as e:
            logger.error(f"❌ Failed to fetch Dhan orders: {e}")
            return {}

        # Step 2: Filter BUY orders
        buy_orders = [o for o in dhan_orders if isinstance(o, dict) and o.get("transactionType") == "BUY"]
        logger.info(f"📊 Found {len(buy_orders)} BUY orders from Dhan")

        if not buy_orders:
            logger.warning("⚠️ No BUY orders in Dhan")
            return {}

        # Step 3: Build map of symbol → Dhan order data
        dhan_by_symbol = {}
        for order in buy_orders:
            symbol = order.get("symbol", "").strip()
            exec_price = order.get("executedPrice")
            exec_qty = order.get("executedQuantity")
            status = order.get("orderStatus")

            if symbol and exec_price and exec_qty and status == "ACCEPTED":
                dhan_by_symbol[symbol] = {
                    'price': exec_price,
                    'qty': exec_qty,
                    'status': status,
                    'order_id': order.get("orderId")
                }

        logger.info(f"📋 Dhan symbols with fills: {list(dhan_by_symbol.keys())}")

        # Step 4: Get trades from DB and match with Dhan orders
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row

        trades = conn.execute("""
            SELECT id, symbol, qty, entry_price, status
            FROM trades
            WHERE status = 'OPEN'
        """).fetchall()

        logger.info(f"📊 Found {len(trades)} OPEN trades in database")

        if not trades:
            logger.warning("⚠️ No open trades in database")
            conn.close()
            return {}

        # Step 5: Update trades with current prices from Dhan
        logger.info("\n📈 Updating P&L:")
        logger.info("-" * 70)

        updated_trades = {}
        updated_count = 0

        for trade in trades:
            trade_id = trade['id']
            symbol = trade['symbol']
            qty = trade['qty']
            entry_price = trade['entry_price']

            # Find matching Dhan order by symbol
            dhan_data = dhan_by_symbol.get(symbol)

            if not dhan_data:
                logger.warning(f"⏳ {symbol:15} - No fill from Dhan yet")
                continue

            current_price = dhan_data['price']

            # Calculate P&L
            pnl = (current_price - entry_price) * qty
            pnl_percent = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0

            # Update database
            try:
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

                logger.info(f"✅ {symbol:15} | Qty: {qty:5} | Entry: ₹{entry_price:8.2f} | Current: ₹{current_price:8.2f} | P&L: ₹{pnl:10.2f} ({pnl_percent:7.2f}%)")

                updated_trades[symbol] = {
                    'entry': entry_price,
                    'current': current_price,
                    'pnl': round(pnl, 2),
                    'pnl_pct': round(pnl_percent, 2),
                    'qty': qty
                }
                updated_count += 1

            except Exception as e:
                logger.error(f"❌ Failed to update {symbol}: {e}")

        # Commit all changes
        conn.commit()
        conn.close()

        logger.info("-" * 70)
        logger.info(f"\n✅ Sync completed!")
        logger.info(f"   - Trades updated: {updated_count}")
        logger.info(f"   - Dashboard refresh: auto (next 30 sec)")
        logger.info("=" * 70 + "\n")

        return updated_trades

    except Exception as e:
        logger.error(f"❌ SYNC_TRADES_WITH_DHAN FAILED: {e}")
        logger.exception("Traceback:")
        return {}