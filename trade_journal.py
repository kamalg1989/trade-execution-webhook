"""
trade_journal.py — the `trades` table: a permanent buy-to-sell audit trail
for every recommendation you act on. Complements position_db.py's live
SL-tracker cache (fast-changing, current-state) with a journal (one row per
buy, created at click time, closed once, never mutated again after that).

Fill/close detection reuses sl_engine.confirm_fill(order_id, symbol) — the
exact mechanism the production EOD engine already uses to poll Dhan's
GET /v2/orders/{order_id} and read back the actual traded price.

Import the same way sl_engine/dhan_client/position_db are imported (bare
import, resolved via the repo-root sys.path entry both web_api and
web-platform routers already insert).
"""
import os
import json
import logging
from datetime import datetime

import psycopg2

logger = logging.getLogger(__name__)

DB_DSN = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/trading_platform"
)


def _conn():
    return psycopg2.connect(DB_DSN, connect_timeout=5)


# ---------------------------------------------------------------------------
# Buy time — freeze the recommendation exactly as shown
# ---------------------------------------------------------------------------
def save_trade_on_buy(security_id, symbol, buy_order_id, buy_trigger_price, quantity, rec=None):
    """Insert a new trades row the moment a buy order is placed.

    `rec` is the recommendation dict as served to the frontend (same shape
    as latest_recommendations.json stock entries, merged with AI fields by
    recommendations.py). May be None if bought outside the recommendations
    flow — snapshot fields then stay NULL, order tracking still works."""
    rec = rec or {}
    ai_ratings = rec.get("aiRatings")
    ai_reviewed = bool(rec.get("aiRank") or rec.get("aiConfidence") or ai_ratings)

    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO trades (
                    security_id, symbol, buy_order_id, buy_trigger_price, quantity, status,
                    target_price, structural_sl, confidence, reason, regime, entry_type,
                    signal_bar_date, risk_per_share, rr_ratio, target_strategy,
                    base_stage, base_quality, liquidity_cr, ifp, base_range_pct,
                    ai_reviewed, ai_rank, ai_confidence, ai_verdict, ai_recommendation,
                    ai_ratings, ai_base_type, ai_extended,
                    price_at_recommendation
                ) VALUES (
                    %s, %s, %s, %s, %s, 'PENDING_FILL',
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s
                )
                ON CONFLICT (buy_order_id) DO NOTHING
            """, (
                str(security_id), symbol, str(buy_order_id), buy_trigger_price, quantity,
                rec.get("target"), rec.get("stopLoss"), rec.get("confidence"), rec.get("reason"),
                rec.get("regime"), rec.get("entryType"),
                rec.get("signalBarDate") or None, rec.get("riskPerShare"), rec.get("rrRatio"), rec.get("targetStrategy"),
                rec.get("baseStage"), rec.get("baseQuality"), rec.get("liquidityCr"), rec.get("ifp"), rec.get("baseRangePct"),
                ai_reviewed, rec.get("aiRank"),
                rec.get("aiConfidence"), rec.get("aiVerdict"), rec.get("aiRecommendation"),
                json.dumps(ai_ratings) if ai_ratings else None, rec.get("aiBaseType"), rec.get("aiExtended"),
                rec.get("currentPrice"),
            ))
        logger.info(f"Trade journal: {symbol} buy order {buy_order_id} logged")
    except Exception as e:
        logger.warning(f"save_trade_on_buy failed ({symbol}): {e}")


# ---------------------------------------------------------------------------
# Reconciliation — run daily (or on demand)
# ---------------------------------------------------------------------------
def reconcile_pending_buys():
    """Poll every PENDING_FILL trade's buy_order_id; record the actual fill
    price and flip to OPEN once Dhan confirms it traded."""
    import sl_engine  # top-level production module — confirm_fill lives here
    updated = 0
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT id, symbol, buy_order_id FROM trades WHERE status = 'PENDING_FILL'")
            rows = cur.fetchall()

        for trade_id, symbol, order_id in rows:
            status, fill_price = sl_engine.confirm_fill(order_id, symbol)
            if status == "FILLED":
                with _conn() as conn, conn.cursor() as cur:
                    cur.execute("""
                        UPDATE trades SET actual_buy_price = %s, buy_filled_at = NOW(), status = 'OPEN'
                        WHERE id = %s
                    """, (fill_price, trade_id))
                updated += 1
                logger.info(f"Trade journal: {symbol} buy filled @ {fill_price}")
            elif status == "DEAD":
                with _conn() as conn, conn.cursor() as cur:
                    cur.execute("UPDATE trades SET status = 'CANCELLED' WHERE id = %s", (trade_id,))
                logger.info(f"Trade journal: {symbol} buy order dead/cancelled")
    except Exception as e:
        logger.warning(f"reconcile_pending_buys failed: {e}")
    return updated


def reconcile_open_trades():
    """For OPEN/EXIT_PENDING/HALF_BOOKED trades, check every order we've
    logged against this security (sl_order_log). If one has actually filled
    at the broker, close the trade and record HOW it closed:
      - order_type EXIT / HALF_EXIT / MARKET_SELL -> closed_via MANUAL_EXIT / MANUAL_MARKET_SELL
      - order_type SAFETY / CUSTOM / TRAIL / MOVE  -> closed_via SL_TRIGGERED
        (a resting protective stop simply got hit — no same-day manual action)
    """
    import sl_engine
    closed = 0
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, symbol, security_id, actual_buy_price, buy_filled_at, quantity, risk_per_share
                FROM trades WHERE status IN ('OPEN', 'EXIT_PENDING', 'HALF_BOOKED')
            """)
            open_trades = cur.fetchall()

        for trade_id, symbol, sec_id, buy_price, buy_filled_at, qty, risk_per_share in open_trades:
            with _conn() as conn, conn.cursor() as cur:
                cur.execute("""
                    SELECT order_id, order_type FROM sl_order_log
                    WHERE security_id = %s AND superseded_at IS NULL
                    ORDER BY placed_at DESC
                """, (sec_id,))
                candidates = cur.fetchall()

            for order_id, order_type in candidates:
                status, fill_price = sl_engine.confirm_fill(order_id, symbol)
                if status != "FILLED":
                    continue

                closed_via = "MANUAL_MARKET_SELL" if order_type == "MARKET_SELL" else \
                             "MANUAL_EXIT" if order_type in ("EXIT", "HALF_EXIT") else \
                             "SL_TRIGGERED"
                exit_reason = {"EXIT": "STRUCTURAL_EXIT", "HALF_EXIT": "HALF_BOOK",
                                "MARKET_SELL": "MANUAL_CLOSE"}.get(order_type, "SL_HIT")

                holding_days = (datetime.now() - buy_filled_at).days if buy_filled_at else None
                r_realized = None
                pnl = None
                if fill_price is not None and buy_price:
                    buy_price = float(buy_price)
                    if risk_per_share:
                        r_realized = round((fill_price - buy_price) / float(risk_per_share), 2)
                    pnl = round((fill_price - buy_price) * qty, 2)

                with _conn() as conn, conn.cursor() as cur:
                    cur.execute("""
                        UPDATE trades SET
                            status = 'CLOSED', sell_order_id = %s, sell_price = %s, sell_date = NOW(),
                            closed_via = %s, exit_reason = %s,
                            r_multiple_realized = %s, holding_period_days = %s, realized_pnl = %s
                        WHERE id = %s
                    """, (order_id, fill_price, closed_via, exit_reason, r_realized, holding_days, pnl, trade_id))
                closed += 1
                logger.info(f"Trade journal: {symbol} closed via {closed_via} @ {fill_price}")
                break  # first confirmed fill wins — don't keep checking older orders
    except Exception as e:
        logger.warning(f"reconcile_open_trades failed: {e}")
    return closed


def run_daily_reconciliation():
    """Entry point for the daily cron/systemd timer."""
    filled = reconcile_pending_buys()
    closed = reconcile_open_trades()
    logger.info(f"Daily trade reconciliation: {filled} buys filled, {closed} trades closed")
    return {"buysFilled": filled, "tradesClosed": closed}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_daily_reconciliation()
    print(result)
