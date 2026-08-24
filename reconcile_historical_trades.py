"""
reconcile_historical_trades.py — ONE-OFF backfill script (run manually, not
a service). Pulls Dhan's full historical trade book (GET /v2/trades/{from}
/{to}/{page}), FIFO-matches BUY/SELL fills per security into round-tripped
closed positions plus any still-open remainder, and reconciles them into
the `trades` table:

  - If a matching PENDING_FILL/OPEN/EXIT_PENDING/HALF_BOOKED row already
    exists for that security (bought through the app - has reason/entry_type/
    regime/ai_* fields), it's UPDATED in place: filled in with the real
    buy price/date and, if closed, the real sell price/date - so its
    existing reason/AI context is preserved.
  - If no row exists at all (bought some other way - Dhan has no concept
    of "why" a trade was made, only our app does, and only for trades
    placed through it), a NEW row is INSERTED with just the financial
    facts (entry/exit price, qty, dates, P&L). reason/entry_type/etc stay
    NULL - the frontend shows these as "Unknown" rather than blank.

Run with --dry-run first to see exactly what it would do before writing.
"""
import argparse
import logging
import os
from collections import defaultdict
from datetime import datetime, date

import psycopg2
import psycopg2.extras
import requests

logger = logging.getLogger(__name__)

DB_DSN = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/trading_platform")


def _conn():
    return psycopg2.connect(DB_DSN, connect_timeout=5)


def fetch_trade_history(token, client_id, from_date, to_date):
    import sl_engine
    all_trades = []
    for page in range(0, 20):
        r = requests.get(
            f"https://api.dhan.co/v2/trades/{from_date}/{to_date}/{page}",
            headers={"access-token": token, "client-id": client_id}, timeout=20,
        )
        if r.status_code != 200:
            break
        data = r.json()
        if not data:
            break
        all_trades.extend(data)
    return all_trades


SCRIP_MASTER = "/root/trade-execution-webhook/api-scrip-master.csv"


def _load_security_symbol_map():
    """security_id -> short NSE trading symbol (e.g. "17186" -> "SPRAUTO").
    `symbol` column in `trades` is varchar(20) - Dhan's trade-book
    `customSymbol` field is the full company name (e.g. "SPR AUTO
    TECHNOLOGIES") which overflows it, so resolve the real ticker from the
    scrip master instead, same lookup dhan_client.get_security_id() uses
    in reverse."""
    import csv
    m = {}
    try:
        with open(SCRIP_MASTER, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("SEM_EXM_EXCH_ID") == "NSE" and row.get("SEM_SEGMENT") == "E":
                    m[str(row["SEM_SMST_SECURITY_ID"]).strip()] = str(row["SEM_TRADING_SYMBOL"]).strip().upper()
    except Exception as e:
        logger.warning(f"Failed to load scrip master for symbol lookup: {e}")
    return m


def fifo_match(trades):
    """Group by securityId, sort by time, FIFO-match BUY lots against SELL
    fills. Returns {securityId: {"symbol", "closed": [...], "open_qty",
    "open_lots": [...]}} where each closed entry is one fully round-tripped
    lot: {buy_price, buy_time, sell_price, sell_time, qty}."""
    by_sec = defaultdict(list)
    for t in trades:
        by_sec[str(t.get("securityId", ""))].append(t)

    result = {}
    for sec_id, tlist in by_sec.items():
        tlist.sort(key=lambda t: t.get("exchangeTime") or "")
        buy_queue = []  # [(price, time, remaining_qty)]
        closed = []
        symbol = None
        for t in tlist:
            symbol = (t.get("customSymbol") or symbol or "").strip()
            qty = int(t.get("tradedQuantity") or 0)
            price = float(t.get("tradedPrice") or 0)
            ttype = str(t.get("transactionType", "")).upper()
            ttime = t.get("exchangeTime")
            if ttype == "BUY":
                buy_queue.append([price, ttime, qty])
            elif ttype == "SELL":
                remaining = qty
                while remaining > 0 and buy_queue:
                    lot = buy_queue[0]
                    take = min(remaining, lot[2])
                    closed.append({
                        "buy_price": lot[0], "buy_time": lot[1],
                        "sell_price": price, "sell_time": ttime, "qty": take,
                    })
                    lot[2] -= take
                    remaining -= take
                    if lot[2] <= 0:
                        buy_queue.pop(0)
                # remaining > 0 here would mean a sell with no matching buy
                # in our lookback window (position opened before from_date) -
                # nothing we can do about that, skip it.
        open_qty = sum(l[2] for l in buy_queue)
        result[sec_id] = {
            "symbol": symbol, "closed": closed,
            "open_qty": open_qty, "open_lots": buy_queue,
        }
    return result


def reconcile(from_date, to_date, dry_run=True):
    import sl_engine
    token = sl_engine.get_token()
    client_id = sl_engine.DHAN_CLIENT_ID
    trades = fetch_trade_history(token, client_id, from_date, to_date)
    logger.info(f"Fetched {len(trades)} historical trade fills ({from_date} → {to_date})")
    matched = fifo_match(trades)
    sym_map = _load_security_symbol_map()

    inserted, updated = 0, 0
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, security_id, status, symbol FROM trades")
        existing_rows = cur.fetchall()
    # One existing app-tracked row per security_id, preferring the earliest
    # (oldest) non-CANCELLED one that isn't already CLOSED, to attach real
    # fill data to.
    existing_by_sec = {}
    for r in existing_rows:
        sec = str(r["security_id"])
        if r["status"] == "CANCELLED":
            continue
        if sec not in existing_by_sec or (existing_by_sec[sec]["status"] != "CLOSED" and r["status"] == "CLOSED"):
            existing_by_sec.setdefault(sec, r)

    for sec_id, info in matched.items():
        if not info["closed"]:
            continue
        ticker = sym_map.get(sec_id)
        symbol = (ticker or (info["symbol"] or "")).upper().strip()
        if not symbol.endswith(".NS"):
            symbol = symbol + ".NS" if symbol else symbol
        symbol = symbol[:20]  # symbol column is varchar(20) — last-resort guard

        for lot in info["closed"]:
            buy_time = lot["buy_time"]
            sell_time = lot["sell_time"]
            buy_dt = _parse_dt(buy_time)
            sell_dt = _parse_dt(sell_time)
            pnl = round((lot["sell_price"] - lot["buy_price"]) * lot["qty"], 2)
            holding_days = (sell_dt - buy_dt).days if (buy_dt and sell_dt) else None

            existing = existing_by_sec.get(sec_id)
            if existing and existing["status"] != "CLOSED":
                action = "UPDATE"
                logger.info(f"{action} {existing['symbol']} (id={existing['id']}): "
                            f"buy {lot['buy_price']} → sell {lot['sell_price']} x{lot['qty']}, "
                            f"pnl {pnl}, {holding_days}d")
                if not dry_run:
                    with _conn() as conn, conn.cursor() as cur:
                        cur.execute("""
                            UPDATE trades SET
                                status = 'CLOSED', actual_buy_price = %s, buy_filled_at = %s,
                                sell_price = %s, sell_date = %s, quantity = %s,
                                closed_via = 'HIST_RECONCILE', exit_reason = 'HISTORICAL_BACKFILL',
                                holding_period_days = %s, realized_pnl = %s
                            WHERE id = %s
                        """, (lot["buy_price"], buy_dt, lot["sell_price"], sell_dt,
                              lot["qty"], holding_days, pnl, existing["id"]))
                existing_by_sec[sec_id] = {**existing, "status": "CLOSED"}  # don't reuse for another lot
                updated += 1
            else:
                action = "INSERT"
                logger.info(f"{action} {symbol} (sec {sec_id}, no app record — reason unknown): "
                            f"buy {lot['buy_price']} → sell {lot['sell_price']} x{lot['qty']}, "
                            f"pnl {pnl}, {holding_days}d")
                if not dry_run:
                    with _conn() as conn, conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO trades (
                                security_id, symbol, buy_order_id, buy_trigger_price, quantity, status,
                                actual_buy_price, buy_filled_at, sell_price, sell_date,
                                closed_via, exit_reason, holding_period_days, realized_pnl
                            ) VALUES (
                                %s, %s, %s, %s, %s, 'CLOSED',
                                %s, %s, %s, %s,
                                'HIST_RECONCILE', 'HISTORICAL_BACKFILL', %s, %s
                            )
                            ON CONFLICT (buy_order_id) DO NOTHING
                        """, (sec_id, symbol, _backfill_order_id(sec_id, buy_time), lot["buy_price"], lot["qty"],
                              lot["buy_price"], buy_dt, lot["sell_price"], sell_dt,
                              holding_days, pnl))
                inserted += 1

    logger.info(f"{'[DRY RUN] ' if dry_run else ''}Done: {inserted} inserted, {updated} updated")

    # --- Pass 2: currently-held positions with no app trade row at all -----
    # (bought outside the app, or the buy leg is older than `from_date`).
    # Per user approval: backfill with entry data only — no reason/AI fields,
    # those genuinely don't exist for a position the app never placed.
    open_inserted = backfill_open_positions(matched, existing_by_sec, sym_map, dry_run)
    return inserted, updated, open_inserted


def backfill_open_positions(matched, existing_by_sec, sym_map, dry_run=True):
    import sl_engine
    holdings = sl_engine.get_holdings()
    open_inserted = 0
    for h in holdings:
        sec_id = str(h.get("securityId", ""))
        qty = int(h.get("qty") or h.get("totalQty") or 0)
        if qty <= 0:
            continue
        existing = existing_by_sec.get(sec_id)
        if existing and existing["status"] != "CLOSED":
            continue  # already tracked (OPEN/PENDING_FILL/etc via the app) — leave alone

        ticker = sym_map.get(sec_id)
        symbol = (ticker or h.get("tradingSymbol") or h.get("symbol") or "").upper().strip()
        if not symbol.endswith(".NS"):
            symbol = symbol + ".NS" if symbol else symbol
        symbol = symbol[:20]

        avg_price = h.get("avgCostPrice") or h.get("avgPrice")
        if not avg_price:
            logger.warning(f"backfill_open_positions: {symbol} (sec {sec_id}) held but no avg price — skipping")
            continue
        avg_price = float(avg_price)

        # Best-effort buy date from FIFO-reconstructed open lots (earliest
        # remaining lot), else leave buy_filled_at NULL — we don't fabricate it.
        info = matched.get(sec_id, {})
        open_lots = info.get("open_lots") or []
        buy_dt = _parse_dt(open_lots[0][1]) if open_lots else None

        logger.info(f"INSERT (open) {symbol} (sec {sec_id}, no app record — reason unknown): "
                    f"held qty {qty} @ avg {avg_price}" + (f", entered {buy_dt}" if buy_dt else ""))
        if not dry_run:
            with _conn() as conn, conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO trades (
                        security_id, symbol, buy_order_id, buy_trigger_price, quantity, status,
                        actual_buy_price, buy_filled_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, 'OPEN',
                        %s, %s
                    )
                    ON CONFLICT (buy_order_id) DO NOTHING
                """, (sec_id, symbol, _backfill_order_id(sec_id, f"open-{sec_id}"), avg_price, qty,
                      avg_price, buy_dt))
        open_inserted += 1
    return open_inserted


def _backfill_order_id(sec_id, buy_time):
    """buy_order_id column is varchar(20) — build a short-but-unique
    placeholder ID (real one is lost; Dhan forever-order IDs and plain-order
    IDs live in different namespaces neither of which we can recover here)."""
    import hashlib
    h = hashlib.md5(f"{sec_id}{buy_time}".encode()).hexdigest()[:8]
    return f"BF{sec_id}{h}"[:20]


def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default="2025-01-01")
    ap.add_argument("--to-date", default=date.today().isoformat())
    ap.add_argument("--dry-run", action="store_true", default=False)
    args = ap.parse_args()
    reconcile(args.from_date, args.to_date, dry_run=args.dry_run)
