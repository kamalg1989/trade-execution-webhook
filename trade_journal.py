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
import push_notify

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
    """Flip PENDING_FILL trades to OPEN once the buy has actually filled.

    Every buy here is placed as a Dhan FOREVER (GTT) order (see
    dhan_client.place_forever_buy / sl_engine.place_*) - those live in
    Dhan's separate /forever/orders namespace with their own order IDs.
    Once a forever order triggers, Dhan does NOT surface a fill under that
    same ID via GET /v2/orders/{id} — confirm_fill() polling that endpoint
    can therefore never resolve a forever buy, and every trade was silently
    stuck at PENDING_FILL forever (confirmed on 2026-08-06: real, currently
    held positions like CRAFTSMAN/AFFLE/NELCO were still sitting at
    PENDING_FILL despite having existed - and profited - for weeks).

    The authoritative source for "did this buy fill" is current holdings:
    if the security is now held, it filled - full stop, regardless of
    which Dhan order-ID namespace the order lives in. Order-ID polling is
    kept as a secondary check for the (rare) case a buy was placed as a
    plain order rather than a forever order.
    """
    import sl_engine  # top-level production module — confirm_fill/get_holdings live here
    updated = 0
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT id, symbol, security_id, buy_order_id FROM trades WHERE status = 'PENDING_FILL'")
            rows = cur.fetchall()
        if not rows:
            return updated

        holdings_by_sec = {}
        try:
            for h in sl_engine.get_holdings():
                holdings_by_sec[str(h.get("securityId", ""))] = h
        except Exception as e:
            logger.warning(f"reconcile_pending_buys: holdings fetch failed: {e}")

        for trade_id, symbol, sec_id, order_id in rows:
            h = holdings_by_sec.get(str(sec_id))
            if h:
                fill_price = h.get("avgPrice")
                with _conn() as conn, conn.cursor() as cur:
                    cur.execute("""
                        UPDATE trades SET actual_buy_price = %s, buy_filled_at = NOW(), status = 'OPEN'
                        WHERE id = %s
                    """, (fill_price, trade_id))
                updated += 1
                logger.info(f"Trade journal: {symbol} buy confirmed via holdings @ {fill_price}")
                continue

            # Not currently held - either still resting, or placed as a
            # plain (non-forever) order that this CAN resolve directly.
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
    """For OPEN/EXIT_PENDING/HALF_BOOKED trades, detect a position that has
    actually been sold (fully exited) and close it out, recording HOW it
    closed from the most recent order we logged for that security
    (sl_order_log):
      - order_type EXIT / HALF_EXIT / MARKET_SELL -> closed_via MANUAL_EXIT / MANUAL_MARKET_SELL
      - order_type SAFETY / CUSTOM / TRAIL / MOVE  -> closed_via SL_TRIGGERED
        (a resting protective stop simply got hit — no same-day manual action)

    Exit/SL orders here are placed as Dhan FOREVER orders too (same
    endpoint as buys - see sl_engine.place_safety_sl / place_exit_forever),
    so - exactly like the PENDING_FILL buy-side bug above - polling
    GET /v2/orders/{order_id} for a triggered forever SELL never resolves
    either. Left unfixed, a position that actually got sold would just sit
    at OPEN forever: still shown as held, never appearing as a closed trade
    anywhere, and liable to be treated as a "fresh" candidate the moment it
    reappears in a scan (which is what "shows as a new buy" looked like).

    The fix mirrors the buy side: the position no longer being in current
    holdings IS the authoritative signal it was sold. Once that's detected,
    the exit price is pulled from today's trade book (falls back to the
    order-ID poll only if the trade book doesn't have it, e.g. this run is
    reconciling a fill from a previous day).
    """
    import sl_engine
    closed = 0
    closed_details = []
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, symbol, security_id, actual_buy_price, buy_filled_at, quantity, risk_per_share
                FROM trades WHERE status IN ('OPEN', 'EXIT_PENDING', 'HALF_BOOKED')
            """)
            open_trades = cur.fetchall()
        if not open_trades:
            return closed, closed_details

        held_qty = {}
        holdings_ok = True
        try:
            for h in sl_engine.get_holdings():
                held_qty[str(h.get("securityId", ""))] = int(h.get("qty") or 0)
        except Exception as e:
            logger.warning(f"reconcile_open_trades: holdings fetch failed: {e}")
            holdings_ok = False  # unknown state — don't assume anything closed

        sells_by_sec = {}
        if holdings_ok:
            try:
                for t in sl_engine.get_trade_book():
                    if str(t.get("transactionType", "")).upper() == "SELL":
                        sells_by_sec.setdefault(str(t.get("securityId", "")), []).append(t)
            except Exception as e:
                logger.warning(f"reconcile_open_trades: trade book fetch failed: {e}")

        for trade_id, symbol, sec_id, buy_price, buy_filled_at, qty, risk_per_share in open_trades:
            sec_id = str(sec_id)
            if not holdings_ok or held_qty.get(sec_id, 0) > 0:
                continue  # still held (or holdings unknown this run) — nothing to close

            with _conn() as conn, conn.cursor() as cur:
                cur.execute("""
                    SELECT order_id, order_type FROM sl_order_log
                    WHERE security_id = %s AND superseded_at IS NULL
                    ORDER BY placed_at DESC
                """, (sec_id,))
                candidates = cur.fetchall()

            fill_price = None
            order_id_used = None
            order_type = candidates[0][1] if candidates else None  # most recent logged order = best guess

            sec_sells = sells_by_sec.get(sec_id)
            if sec_sells:
                last = sec_sells[-1]
                for k in ("tradedPrice", "averageTradedPrice", "price"):
                    v = last.get(k)
                    if v:
                        fill_price = float(v)
                        break
                order_id_used = last.get("orderId")

            if fill_price is None:
                # Not in today's trade book (reconciliation running a day
                # late) — fall back to the order-ID poll for each logged
                # order, in case any of them resolve directly.
                for order_id, o_type in candidates:
                    status, fp = sl_engine.confirm_fill(order_id, symbol)
                    if status == "FILLED":
                        fill_price = fp
                        order_id_used = order_id
                        order_type = o_type
                        break

            if fill_price is None:
                # Both of the above only ever see TODAY (trade book) or
                # never resolve at all (forever-order IDs via GET /orders/
                # {id}) — a position that closed on some earlier day this
                # job didn't catch it on (confirmed 2026-08-13: KTKBANK sold
                # before 2026-08-12's run and sat stuck with a "no exit price
                # found" warning every run since) needs the actual historical
                # trade book. Look back 30 days from the buy date, or 30 days
                # from today if we don't even have that.
                lookback_from = (buy_filled_at.date() if buy_filled_at else datetime.now().date())
                from_date = lookback_from.isoformat()
                to_date = datetime.now().date().isoformat()
                try:
                    hist = sl_engine.get_trade_history(from_date, to_date)
                except Exception as e:
                    hist = []
                    logger.warning(f"reconcile_open_trades: trade history fetch failed for {symbol}: {e}")
                hist_sells = [t for t in hist if str(t.get("securityId")) == sec_id
                              and str(t.get("transactionType", "")).upper() == "SELL"]
                if hist_sells:
                    hist_sells.sort(key=lambda t: t.get("exchangeTime") or "")
                    total_qty = sum(int(t.get("tradedQuantity") or 0) for t in hist_sells)
                    total_val = sum(float(t.get("tradedPrice") or 0) * int(t.get("tradedQuantity") or 0) for t in hist_sells)
                    if total_qty > 0:
                        fill_price = round(total_val / total_qty, 4)
                        order_id_used = hist_sells[-1].get("orderId")
                        logger.info(f"Trade journal: {symbol} exit price recovered from trade history "
                                    f"({len(hist_sells)} fill(s), {from_date}→{to_date}) @ {fill_price}")

            if fill_price is None:
                logger.warning(f"Trade journal: {symbol} no longer held but no exit price found yet "
                                f"(will retry next run)")
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
                """, (order_id_used, fill_price, closed_via, exit_reason, r_realized, holding_days, pnl, trade_id))
            closed += 1
            closed_details.append({
                "symbol": symbol, "closed_via": closed_via,
                "r_realized": r_realized, "pnl": pnl,
            })
            logger.info(f"Trade journal: {symbol} closed via {closed_via} @ {fill_price}")
    except Exception as e:
        logger.warning(f"reconcile_open_trades failed: {e}")
    return closed, closed_details


def _still_open_count():
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM trades WHERE status IN ('OPEN', 'EXIT_PENDING', 'HALF_BOOKED')")
            return cur.fetchone()[0]
    except Exception:
        return None


def _notify_reconciliation(filled, closed, closed_details):
    """Push the daily SL-engine reconciliation summary. Never raises."""
    try:
        still_open = _still_open_count()
        lines = []
        if closed_details:
            for d in closed_details:
                r = f"{d['r_realized']:+.1f}R" if d["r_realized"] is not None else ""
                pnl = f"₹{d['pnl']:+,.0f}" if d["pnl"] is not None else ""
                via = "SL hit" if d["closed_via"] == "SL_TRIGGERED" else "Manual exit"
                lines.append(f"{d['symbol']}: {via} ({r} {pnl})".strip())
        else:
            lines.append("No positions closed today.")
        if filled:
            lines.append(f"{filled} buy order(s) confirmed filled.")
        if still_open is not None:
            lines.append(f"{still_open} still open.")
        title = f"📒 SL Engine Daily Summary — {closed} closed"
        push_notify.notify_all(title, "\n".join(lines), url="/")
    except Exception as e:
        logger.warning(f"reconciliation push notify failed: {e}")


def run_daily_reconciliation():
    """Entry point for the daily cron/systemd timer."""
    filled = reconcile_pending_buys()
    closed, closed_details = reconcile_open_trades()
    logger.info(f"Daily trade reconciliation: {filled} buys filled, {closed} trades closed")
    _notify_reconciliation(filled, closed, closed_details)
    return {"buysFilled": filled, "tradesClosed": closed}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_daily_reconciliation()
    print(result)
