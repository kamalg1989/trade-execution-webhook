"""
Integration snippet for saving Structural SL to DB when creating positions.
Add this to your entry/buy flow (e.g., entry_engine.py).
"""

import psycopg2
import json
import os
import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

# DB connection params
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "trade_execution_platform")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")


def get_structural_sl_for_entry(symbol: str, security_id: str) -> Tuple[Optional[float], Optional[str]]:
    """
    Resolve structural SL for a symbol from all sources.
    Returns: (structural_sl_price, source) where source is 'sheet', 'screener', 'manual', or None.
    """
    sym_upper = str(symbol).replace(".NS", "").strip().upper()

    # Priority 1: Google Sheets
    try:
        import google_sheets_db as sheet_db
        trades = sheet_db.get_all_trades()
        for t in trades:
            if str(t.get("Security_ID") or "").strip() == str(security_id).strip():
                sl = t.get("Structural_SL")
                if sl:
                    try:
                        return float(sl), "sheet"
                    except (ValueError, TypeError):
                        pass
    except Exception as e:
        logger.debug(f"Sheet lookup failed for {symbol}: {e}")

    # Priority 2: Manual override file
    try:
        manual_file = "/root/trade-execution-webhook/manual_structural_sl.json"
        if os.path.exists(manual_file):
            with open(manual_file) as f:
                manual = json.load(f) or {}
            if sym_upper in manual and manual[sym_upper]:
                return float(manual[sym_upper]), "manual"
    except Exception as e:
        logger.debug(f"Manual file lookup failed: {e}")

    # Priority 3: Screener history
    try:
        hist_file = "/root/trade-execution-webhook/structural_sl_history.json"
        if os.path.exists(hist_file):
            with open(hist_file) as f:
                hist = json.load(f) or {}
            if sym_upper in hist:
                sl = hist[sym_upper].get("structuralSL")
                if sl:
                    return float(sl), "screener"
    except Exception as e:
        logger.debug(f"History file lookup failed: {e}")

    # Priority 4: Latest recommendations
    try:
        recs_file = "/root/trade-execution-webhook/latest_recommendations.json"
        if os.path.exists(recs_file):
            with open(recs_file) as f:
                recs = json.load(f) or {}
            for s in recs.get("stocks", []):
                if str(s.get("symbol", "")).replace(".NS", "").upper() == sym_upper:
                    sl = s.get("stopLoss") or s.get("stop_loss")
                    if sl:
                        return float(sl), "screener"
    except Exception as e:
        logger.debug(f"Recommendations file lookup failed: {e}")

    return None, None


def save_position_with_structural_sl(
    user_id: int,
    order_id: str,
    symbol: str,
    security_id: str,
    quantity: int,
    entry_price: float,
    structural_sl: Optional[float] = None,
    structural_sl_source: Optional[str] = None,
    stop_loss: float = 0,
    target_price: Optional[float] = None,
    status: str = "OPEN"
) -> bool:
    """
    Save a position to sl_positions table with structural SL persisted.

    If structural_sl not provided, automatically fetches it.
    Returns: True if successful, False otherwise.
    """

    # Auto-fetch structural_sl if not provided
    if structural_sl is None or structural_sl == 0:
        structural_sl, structural_sl_source = get_structural_sl_for_entry(symbol, security_id)

    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=5,
        )
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO sl_positions (
                user_id, order_id, symbol, exchange_token,
                quantity, entry_price, stop_loss,
                structural_sl, structural_sl_source,
                target_price, status,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (order_id) DO UPDATE SET
                structural_sl = EXCLUDED.structural_sl,
                structural_sl_source = EXCLUDED.structural_sl_source,
                stop_loss = EXCLUDED.stop_loss,
                target_price = EXCLUDED.target_price,
                updated_at = NOW()
        """, (
            user_id, order_id, symbol, security_id,
            quantity, entry_price, stop_loss,
            structural_sl, structural_sl_source,
            target_price, status
        ))

        conn.commit()
        cur.close()
        conn.close()

        logger.info(
            f"Position saved: {symbol} qty={quantity} entry={entry_price} "
            f"structural_sl={structural_sl} src={structural_sl_source}"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to save position {symbol}: {e}")
        return False


def update_position_structural_sl(
    order_id: str,
    structural_sl: float,
    structural_sl_source: str
) -> bool:
    """Update structural SL for an existing position."""
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=5,
        )
        cur = conn.cursor()

        cur.execute("""
            UPDATE sl_positions
            SET structural_sl = %s,
                structural_sl_source = %s,
                updated_at = NOW()
            WHERE order_id = %s
        """, (structural_sl, structural_sl_source, order_id))

        conn.commit()
        cur.close()
        conn.close()

        logger.info(f"Position updated: order_id={order_id} structural_sl={structural_sl}")
        return True

    except Exception as e:
        logger.error(f"Failed to update position {order_id}: {e}")
        return False


def get_position_structural_sl(order_id: str) -> Tuple[Optional[float], Optional[str]]:
    """Fetch structural SL for a position from DB."""
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=5,
        )
        cur = conn.cursor()

        cur.execute("""
            SELECT structural_sl, structural_sl_source
            FROM sl_positions
            WHERE order_id = %s
        """, (order_id,))

        result = cur.fetchone()
        cur.close()
        conn.close()

        if result:
            return result[0], result[1]
        return None, None

    except Exception as e:
        logger.error(f"Failed to fetch position {order_id}: {e}")
        return None, None


# ============================================================================
# USAGE EXAMPLE: In your buy/entry endpoint
# ============================================================================

def buy_order_with_structural_sl(user_id, symbol, security_id, quantity, entry_price, order_id):
    """
    Example: Place a buy order and save structural SL to DB.
    Call this from your /place-order or similar endpoint.
    """

    # 1. Place the actual buy order via Dhan API
    # ok = dhan_client.place_buy_order(...)
    # if not ok:
    #     return {"success": False, "error": "Dhan rejected order"}

    # 2. Save position with structural SL auto-fetched and persisted to DB
    success = save_position_with_structural_sl(
        user_id=user_id,
        order_id=order_id,
        symbol=symbol,
        security_id=security_id,
        quantity=quantity,
        entry_price=entry_price,
        # structural_sl and source auto-fetched from all sources
        status="OPEN"
    )

    if not success:
        logger.warning(f"Position save failed for {symbol}, but order placed")
        # Optionally return a warning, but don't fail the buy

    return {"success": True, "orderId": order_id, "symbol": symbol}


# ============================================================================
# USAGE EXAMPLE: In your SL router (sl_engine.py)
# ============================================================================

def _structural_map_with_db_priority():
    """
    Updated version of _structural_map() that queries DB first.
    Use this in /sl-alerts endpoint.
    """
    m = {}

    # Query DB for all open positions with structural SL
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=5,
        )
        cur = conn.cursor()

        cur.execute("""
            SELECT DISTINCT ON (exchange_token)
                exchange_token, structural_sl, structural_sl_source, entry_price
            FROM sl_positions
            WHERE status IN ('OPEN', 'PARTIAL')
                AND structural_sl IS NOT NULL
            ORDER BY exchange_token, updated_at DESC
        """)

        for sec_id, sl, src, entry in cur.fetchall():
            if sec_id and sl:
                m[str(sec_id)] = {
                    "structuralSL": float(sl),
                    "entry": float(entry) if entry else None,
                    "source": src or "db",
                }

        cur.close()
        conn.close()
        logger.info(f"Loaded {len(m)} positions with structural SL from DB")

    except Exception as e:
        logger.warning(f"DB structural SL query failed: {e}")

    # Fallback: Google Sheets for positions not yet in DB
    try:
        import google_sheets_db as sheet_db
        trades = sheet_db.get_all_trades()

        for t in trades:
            sec = str(t.get("Security_ID") or "")
            if sec and sec not in m:  # don't override DB values
                sl = float(t.get("Structural_SL") or 0) or None
                if sl:
                    m[sec] = {
                        "structuralSL": sl,
                        "entry": float(t.get("Entry_Price") or 0) or None,
                        "target": float(t.get("Target_Price") or 0) or None,
                        "status": t.get("Status"),
                        "source": "sheet",
                    }
    except Exception as e:
        logger.debug(f"Sheet fallback failed: {e}")

    return m


if __name__ == "__main__":
    # Test: fetch structural SL for a symbol
    sl, src = get_structural_sl_for_entry("RELIANCE", "NSE_EQ_RELIANCE")
    print(f"RELIANCE structural SL: {sl} (from {src})")

    # Test: save a position
    # success = save_position_with_structural_sl(
    #     user_id=1,
    #     order_id="ORD-123",
    #     symbol="RELIANCE",
    #     security_id="NSE_EQ_RELIANCE",
    #     quantity=10,
    #     entry_price=2500.5,
    # )
    # print(f"Save success: {success}")
