"""
position_db.py — shared DB helpers for the SL tracker's position lifecycle.

Two tables (trading_platform DB):
  • sl_order_log       — audit log of every forever-order we place, tagged
                         with WHY it was placed (SAFETY / STRUCTURAL / CUSTOM /
                         EXIT / HALF_EXIT / TRAIL / MOVE). Dhan's GET /forever/
                         orders does NOT echo back correlationId, so this is
                         the only reliable way to know "is this resting order
                         an exit order?" after the fact.
  • position_snapshot  — one row per open security_id: buy price, structural
                         SL, current SL basis/price, status, exit info, best-
                         effort sell price. Upserted on every /sl-alerts poll
                         and on every action.

Import this the same way sl_engine/dhan_client/google_sheets_db are imported
(bare import, resolved via the repo-root sys.path entry both web_api and
web-platform routers already insert).
"""
import os
import logging
import psycopg2

logger = logging.getLogger(__name__)

DB_DSN = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/trading_platform"
)


def _conn():
    return psycopg2.connect(DB_DSN, connect_timeout=5)


# ---------------------------------------------------------------------------
# Order intent log
# ---------------------------------------------------------------------------
def log_order(security_id, symbol, order_id, order_type, trigger_price=None, quantity=None):
    """Record why a forever-order was placed. Safe to call best-effort —
    never raises; a DB hiccup here should not block a real trade action."""
    if not order_id:
        return
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sl_order_log (security_id, symbol, order_id, order_type, trigger_price, quantity)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (order_id) DO UPDATE SET
                    order_type = EXCLUDED.order_type,
                    trigger_price = EXCLUDED.trigger_price
            """, (str(security_id), symbol, str(order_id), order_type, trigger_price, quantity))
    except Exception as e:
        logger.warning(f"log_order failed ({symbol}/{order_id}): {e}")


def pending_order_types(security_id, active_order_ids):
    """Given the list of order_ids currently resting at the broker for this
    security, return the set of order_type tags we logged for them (e.g.
    {'EXIT'} or {'SAFETY'} or {} if we never tagged any of them — e.g. legacy
    orders placed before this tracking existed)."""
    if not active_order_ids:
        return set()
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT order_type FROM sl_order_log
                WHERE security_id = %s AND order_id = ANY(%s)
            """, (str(security_id), [str(o) for o in active_order_ids]))
            return {row[0] for row in cur.fetchall()}
    except Exception as e:
        logger.warning(f"pending_order_types failed ({security_id}): {e}")
        return set()


# ---------------------------------------------------------------------------
# Position snapshot (current state per security_id)
# ---------------------------------------------------------------------------
def upsert_snapshot(security_id, symbol, **fields):
    """Upsert the live-state columns we know about. Only columns passed in
    `fields` are updated; others keep their previous value. Call this on
    every /sl-alerts poll (to keep buy price / structural SL / current SL
    fresh) and after every action (to reflect the new status immediately)."""
    allowed = {
        "quantity", "buy_price", "structural_sl", "structural_sl_source",
        "current_sl_price", "current_sl_basis", "current_sl_order_id",
        "status", "exit_order_id", "exit_trigger_price", "sell_price",
        "r_multiple", "half_booked",
    }
    cols = {k: v for k, v in fields.items() if k in allowed}
    if not cols:
        return
    try:
        with _conn() as conn, conn.cursor() as cur:
            col_names = ["security_id", "symbol"] + list(cols.keys())
            placeholders = ["%s"] * len(col_names)
            update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols.keys())
            cur.execute(f"""
                INSERT INTO position_snapshot ({", ".join(col_names)}, last_updated)
                VALUES ({", ".join(placeholders)}, NOW())
                ON CONFLICT (security_id) DO UPDATE SET
                    {update_clause}, last_updated = NOW()
            """, [str(security_id), symbol] + list(cols.values()))
    except Exception as e:
        logger.warning(f"upsert_snapshot failed ({symbol}/{security_id}): {e}")


def mark_closed_if_absent(open_security_ids):
    """Best-effort reconciliation: any position_snapshot row still marked
    OPEN/EXIT_PENDING/HALF_BOOKED whose security_id is no longer in the
    current holdings list has likely been filled/closed. Mark it CLOSED and
    use the exit_trigger_price as a best-effort sell_price (the true fill
    price would need Dhan's trade-book, which is a further improvement)."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT security_id, exit_trigger_price, current_sl_price
                FROM position_snapshot
                WHERE status IN ('OPEN', 'EXIT_PENDING', 'HALF_BOOKED')
            """)
            rows = cur.fetchall()
            for sec_id, exit_trigger, current_sl in rows:
                if sec_id in open_security_ids:
                    continue
                sell_price = exit_trigger or current_sl
                cur.execute("""
                    UPDATE position_snapshot
                    SET status = 'CLOSED', sell_price = %s, last_updated = NOW()
                    WHERE security_id = %s
                """, (sell_price, sec_id))
    except Exception as e:
        logger.warning(f"mark_closed_if_absent failed: {e}")


def get_snapshot(security_id):
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM position_snapshot WHERE security_id = %s", (str(security_id),))
            row = cur.fetchone()
            if not row:
                return None
            cols = [d.name for d in cur.description]
            return dict(zip(cols, row))
    except Exception as e:
        logger.warning(f"get_snapshot failed ({security_id}): {e}")
        return None
