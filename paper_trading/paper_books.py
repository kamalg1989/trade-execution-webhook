#!/usr/bin/env python3
"""Multi-book paper trading — the three validated POSITIONAL presets, forward.

BOOKS (each seeded with Rs.4L, run in parallel against the same market data):
  recommended  = preset #909   composite_rs, static ATR<=5% persist-2 full exit
  aggressive   = preset #1062  + relATR 1.5x/trim-33 exit + breadth-smile 0.5
  combo        = preset #1079  + 52wk-high factor + 14d earnings gate + 6% cash yield

WHY: survivorship-free forward evidence, per book, so the presets can be
compared on data none of them was fitted to. Mirrors the engine mechanics
(see custom-screener/backend/backtest/positional_engine.py) — any drift
between this file and the engine is a bug in this file.

USAGE
  python3 paper_books.py --init                 # create tables, seed all books
  python3 paper_books.py --rebalance [--book b] # 21-session cadence (self-gated)
  python3 paper_books.py --mark      [--book b] # daily guards + mark-to-market
  python3 paper_books.py --status               # all books side by side

SAFETY: reads market_data read-only; writes only paper2_* tables in
trading_platform. Never places an order. The original #823 paper book
(paper_positional.py, paper_* tables) is untouched and keeps running.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import statistics as st
from datetime import date, timedelta

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("paper2")

MARKET_DSN = os.getenv("MARKET_DSN",
                       "postgresql://postgres:postgres@localhost:5432/market_data")
TRADE_DSN = os.getenv("DATABASE_URL",
                      "postgresql://postgres:postgres@localhost:5432/trading_platform")

TOP_N, BUFFER_N, REBALANCE_DAYS = 30, 60, 21
MIN_CLOSE, MIN_TURNOVER_CR, MAX_ATR_PCT = 20.0, 8.0, 5.0
# MIN_TURNOVER_CR stays the basis for breadth200() so the b200 smile is measured
# on ONE consistent universe across all books; the candidate filter is per-book
# via cfg['min_turnover_cr'] (2026-08-24 improvement campaign).
START_CAPITAL = 400_000.0
COST_PCT = 0.32              # per leg, matches the backtest friction model

# ---- the three validated configs (do not drift from the preset buttons) -----
BOOKS = {
    "recommended": dict(min_ifp=0.38, w52=0.0, earn_gate_days=0,
                        rel_atr_mult=None, trim_pct=100.0,
                        smile_cut=None, cash_yield_pct=0.0),
    "aggressive":  dict(min_ifp=0.38, w52=0.0, earn_gate_days=0,
                        rel_atr_mult=1.5, trim_pct=33.0,
                        smile_cut=0.5, cash_yield_pct=0.0),
    "combo":       dict(min_ifp=0.38, w52=1.0, earn_gate_days=14,
                        rel_atr_mult=1.5, trim_pct=33.0,
                        smile_cut=0.5, cash_yield_pct=6.0),
    # 80/20 blend: combo equity sleeve + GOLDBEES 200-DMA long/flat sleeve.
    # Validated 2026-08-23: blend Calmar beats pure combo in every window.
    "etf_blend":   dict(min_ifp=0.38, w52=1.0, earn_gate_days=14,
                        rel_atr_mult=1.5, trim_pct=33.0,
                        smile_cut=0.5, cash_yield_pct=6.0,
                        etf=dict(symbol="GOLDBEES", pct=20.0, ma=200)),
    # 2026-08-24 improvement campaign (173 runs). Combo + two changes:
    #   min_turnover_cr 8 -> 6   and   id_score_w 0 -> 0.5 (frog-in-the-pan).
    # Backtest #30420 vs #2889 at Rs20L/15.6y: 25.19/22.90/Calmar 1.100 vs
    # 23.11/24.51/0.943; worst 12m -14.52% vs -17.58%; 493 vs 752 days underwater.
    # This book exists to FORWARD-test that, because the turnover half selects
    # less liquid names -- exactly where the 269 missing delisted stocks lived --
    # so part of the backtested gain may be survivorship bias. Live data has no
    # survivorship bias, which is the whole point of running it forward.
    "combo_v2":    dict(min_ifp=0.38, w52=1.0, earn_gate_days=14,
                        rel_atr_mult=1.5, trim_pct=33.0,
                        smile_cut=0.5, cash_yield_pct=6.0,
                        min_turnover_cr=6.0, id_score_w=0.5),
}
SMILE_LO, SMILE_HI = 45.0, 60.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper2_books (
    book          text PRIMARY KEY,
    start_capital numeric(16,2) NOT NULL,
    cash_credit   numeric(16,2) NOT NULL DEFAULT 0,
    created_at    timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS paper2_rebalance (
    book          text NOT NULL,
    rebalance_date date NOT NULL,
    n_candidates  integer, n_held integer, n_bought integer, n_sold integer,
    exposure      numeric(6,3),
    capital       numeric(16,2),
    created_at    timestamptz DEFAULT now(),
    PRIMARY KEY (book, rebalance_date)
);
CREATE TABLE IF NOT EXISTS paper2_positions (
    id            serial PRIMARY KEY,
    book          text NOT NULL,
    symbol        text NOT NULL,
    signal_date   date,
    entry_date    date NOT NULL,
    entry_price   numeric(12,2) NOT NULL,
    quantity      integer NOT NULL,
    entry_rank    integer,
    entry_score   numeric(10,4),
    atr_pct_entry numeric(8,3),
    signal_price  numeric(12,2),
    fill_price_raw numeric(12,2),
    slippage_bps  numeric(10,2),
    exit_date     date,
    exit_price    numeric(12,2),
    exit_reason   text,
    realized_pnl  numeric(16,2),
    status        text NOT NULL DEFAULT 'OPEN'
);
CREATE INDEX IF NOT EXISTS idx_paper2_pos_bs ON paper2_positions(book, status);
CREATE TABLE IF NOT EXISTS paper2_equity (
    book          text NOT NULL,
    d             date NOT NULL,
    cash          numeric(16,2) NOT NULL,
    positions_mtm numeric(16,2) NOT NULL,
    equity        numeric(16,2) NOT NULL,
    n_open        integer,
    peak_equity   numeric(16,2),
    drawdown_pct  numeric(8,3),
    created_at    timestamptz DEFAULT now(),
    PRIMARY KEY (book, d)
);
"""


def rank_sql(cfg) -> str:
    turn = cfg.get("min_turnover_cr", MIN_TURNOVER_CR)
    id_w = cfg.get("id_score_w", 0.0)
    # LEFT JOIN so a missing ID can never drop a candidate; missing values are
    # neutralised at the cross-sectional mean in score_composite (mirrors
    # positional_engine.py).
    id_sel = ", d.id_126 AS id_val" if id_w else ""
    id_join = ("LEFT JOIN stock_information_discreteness d "
               "  ON d.symbol = s.symbol AND d.indicator_date = s.indicator_date"
               if id_w else "")
    return f"""
        SELECT s.symbol, s.close, s.sma_200, s.atr_pct, s.base_range_20d_pct,
               s.dist_52w_high_pct,
               s.pct_chg_1m, s.pct_chg_3m, s.pct_chg_6m, s.pct_chg_1y{id_sel}
        FROM stock_indicators s
        {id_join}
        WHERE s.indicator_date = $1
          AND s.turnover_1m_avg_cr >= {turn}
          AND s.close > s.sma_200
          AND s.pct_chg_1y IS NOT NULL AND s.pct_chg_6m IS NOT NULL
          AND s.pct_chg_3m IS NOT NULL
          AND s.atr_pct IS NOT NULL AND s.atr_pct > 0 AND s.atr_pct <= {MAX_ATR_PCT}
          AND s.ifp_score >= {cfg['min_ifp']}
          AND s.close >= {MIN_CLOSE}
    """


def score_composite(rows: list, w52: float, id_w: float = 0.0) -> list:
    """Mirror of positional_engine._score_composite: z(12-1)+z(6m)+z(3m)+
    z(6m/atr)+z(-base_range) [+ w52*z(dist_52w_high)]. Cross-sectional within
    this day only; clipped +/-3; missing optional values neutralised at mean."""
    recs = []
    for r in rows:
        try:
            m1 = float(r["pct_chg_1m"] or 0) / 100
            m12 = (1 + float(r["pct_chg_1y"]) / 100) / (1 + m1) - 1
            m6 = float(r["pct_chg_6m"]) / 100
            m3 = float(r["pct_chg_3m"]) / 100
            atr = float(r["atr_pct"])
            br = r["base_range_20d_pct"]
            f = [m12, m6, m3, m6 / atr, -float(br) if br is not None else None]
            if w52:
                dh = r["dist_52w_high_pct"]
                f.append(float(dh) if dh is not None else None)
            if id_w:
                # NEGATED: a CONTINUOUS-information name (low/negative ID) must
                # score positively. Same convention as positional_engine.py.
                idv = r["id_val"]
                f.append(-float(idv) if idv is not None else None)
            recs.append({"symbol": r["symbol"], "close": float(r["close"]),
                         "atr": atr, "f": f})
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    if len(recs) < 5:
        return []
    nf = 5 + (1 if w52 else 0) + (1 if id_w else 0)
    weights = ([1.0, 1.0, 1.0, 1.0, 1.0]
               + ([w52] if w52 else [])
               + ([id_w] if id_w else []))
    for i in range(4, nf):                     # neutralise optional factors
        vals = [x["f"][i] for x in recs if x["f"][i] is not None]
        mu_i = st.fmean(vals) if vals else 0.0
        for x in recs:
            if x["f"][i] is None:
                x["f"][i] = mu_i
    stats = []
    for i in range(nf):
        col = [x["f"][i] for x in recs]
        stats.append((st.fmean(col), st.pstdev(col) or 1.0))
    for x in recs:
        s = 0.0
        for i, (mu, sd) in enumerate(stats):
            z = max(-3.0, min(3.0, (x["f"][i] - mu) / sd))
            s += weights[i] * z
        x["score"] = s
    recs.sort(key=lambda z: -z["score"])
    return recs


async def latest_indicator_date(mk, on_or_before=None):
    if on_or_before:
        return await mk.fetchval(
            "SELECT max(indicator_date) FROM stock_indicators WHERE indicator_date <= $1",
            on_or_before)
    return await mk.fetchval("SELECT max(indicator_date) FROM stock_indicators")


async def breadth200(mk, d) -> float | None:
    v = await mk.fetchval(
        "SELECT AVG(CASE WHEN close > sma_200 THEN 100.0 ELSE 0 END) "
        "FROM stock_indicators WHERE indicator_date=$1 "
        f"AND turnover_1m_avg_cr >= {MIN_TURNOVER_CR} AND sma_200 IS NOT NULL", d)
    return float(v) if v is not None else None


async def etf_state(mk, sym: str, on_d, ma: int = 200):
    """Close vs its own N-day SMA on the given session. None if too little data."""
    rows = await mk.fetch(
        "SELECT close FROM ohlcv_data WHERE symbol=$1 AND time::date <= $2 "
        "ORDER BY time DESC LIMIT $3", sym, on_d, ma + 1)
    if len(rows) < ma:
        return None
    closes = [float(r["close"]) for r in rows]
    dma = st.fmean(closes[:ma])
    return {"close": closes[0], "dma": dma, "on": closes[0] > dma}


async def adjust_etf_sleeve(mk, tr, book: str, cfg, d, nxt, total_equity: float) -> None:
    """Set the ETF sleeve to target (pct of total equity if regime-on, else 0)
    by closing and reopening the position at the next session's open. Runs at
    each 21-session rebalance; daily regime flips are handled in daily_guard."""
    etf = cfg["etf"]
    sym = etf["symbol"]
    state = await etf_state(mk, sym, d, etf["ma"])
    target_val = total_equity * etf["pct"] / 100.0 if (state and state["on"]) else 0.0
    op = await mk.fetchval(
        "SELECT open FROM ohlcv_data WHERE symbol=$1 AND time::date=$2", sym, nxt)
    if op is None:
        return
    cur = await tr.fetchrow(
        "SELECT * FROM paper2_positions WHERE book=$1 AND symbol=$2 AND status='OPEN'",
        book, sym)
    buy_px = float(op) * (1 + COST_PCT / 100)
    sell_px = float(op) * (1 - COST_PCT / 100)
    target_qty = int(target_val / buy_px) if target_val > 0 else 0
    cur_qty = cur["quantity"] if cur else 0
    if cur_qty and (target_qty == 0 or abs(target_qty - cur_qty) / cur_qty > 0.05):
        pnl = (sell_px - float(cur["entry_price"])) * cur_qty
        await tr.execute(
            "UPDATE paper2_positions SET status='CLOSED', exit_date=$1, exit_price=$2,"
            " exit_reason='ETF_REBALANCE', realized_pnl=$3 WHERE id=$4",
            nxt, sell_px, pnl, cur["id"])
        cur_qty = 0
        log.info("[%s] ETF sleeve closed %s (pnl %.0f)", book, sym, pnl)
    if target_qty > 0 and cur_qty == 0:
        await tr.execute(
            "INSERT INTO paper2_positions (book, symbol, signal_date, entry_date,"
            " entry_price, quantity, signal_price, fill_price_raw)"
            " VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
            book, sym, d, nxt, buy_px, target_qty,
            state["close"] if state else None, float(op))
        log.info("[%s] ETF sleeve: bought %d %s at %.2f (regime ON, dma %.1f)",
                 book, target_qty, sym, buy_px, state["dma"] if state else 0)


async def cmd_init(mk, tr) -> None:
    await tr.execute(SCHEMA)
    d = await latest_indicator_date(mk)
    for book in BOOKS:
        await tr.execute(
            "INSERT INTO paper2_books (book, start_capital) VALUES ($1,$2) "
            "ON CONFLICT (book) DO NOTHING", book, START_CAPITAL)
        await tr.execute(
            "INSERT INTO paper2_equity (book, d, cash, positions_mtm, equity, "
            "n_open, peak_equity, drawdown_pct) VALUES ($1,$2,$3,0,$3,0,$3,0) "
            "ON CONFLICT (book, d) DO NOTHING", book, d, START_CAPITAL)
    log.info("paper2 tables ready; %d books seeded at Rs.%.0f", len(BOOKS), START_CAPITAL)


async def book_cash(tr, book) -> tuple[float, float, float]:
    """(cash, realized, cash_credit) for a book."""
    row = await tr.fetchrow("SELECT * FROM paper2_books WHERE book=$1", book)
    credit = float(row["cash_credit"]) if row else 0.0
    start = float(row["start_capital"]) if row else START_CAPITAL
    realized = float(await tr.fetchval(
        "SELECT COALESCE(sum(realized_pnl),0) FROM paper2_positions "
        "WHERE book=$1 AND status='CLOSED'", book))
    cost_basis = float(await tr.fetchval(
        "SELECT COALESCE(sum(entry_price*quantity),0) FROM paper2_positions "
        "WHERE book=$1 AND status='OPEN'", book))
    return start + realized + credit - cost_basis, realized, credit


async def cmd_rebalance(mk, tr, book: str, force=False, as_of=None) -> None:
    cfg = BOOKS[book]
    d = await latest_indicator_date(mk, as_of)
    last = await tr.fetchval(
        "SELECT max(rebalance_date) FROM paper2_rebalance WHERE book=$1", book)
    if last and not force:
        sess = await mk.fetchval(
            "SELECT count(DISTINCT time::date) FROM ohlcv_data "
            "WHERE time::date > $1 AND time::date <= $2", last, d)
        if (sess or 0) < REBALANCE_DAYS:
            log.info("[%s] only %s sessions since %s (need %s) — skipping",
                     book, sess, last, REBALANCE_DAYS)
            return

    if cfg.get("id_score_w"):
        id_max = await mk.fetchval(
            "SELECT max(indicator_date) FROM stock_information_discreteness")
        if id_max is None or id_max < d:
            log.error("[%s] ABORT: information-discreteness table is stale "
                      "(current through %s, need %s). Run "
                      "market_data_setup/scripts/compute_information_discreteness.py. "
                      "Proceeding would silently neutralise the frog-in-the-pan "
                      "factor and make this book identical to combo.", book, id_max, d)
            return

    rows = await mk.fetch(rank_sql(cfg), d)
    ranked = score_composite(rows, cfg["w52"], cfg.get("id_score_w", 0.0))
    if not ranked:
        log.warning("[%s] no candidates on %s — aborting", book, d)
        return
    rank_of = {r["symbol"]: i for i, r in enumerate(ranked)}

    held = await tr.fetch(
        "SELECT * FROM paper2_positions WHERE book=$1 AND status='OPEN'", book)
    etf_sym = cfg.get("etf", {}).get("symbol") if cfg.get("etf") else None
    held = [h for h in held if h["symbol"] != etf_sym]
    sells = [h for h in held if rank_of.get(h["symbol"], 10**9) >= BUFFER_N]
    n_sold = 0
    for h in sells:
        px = await mk.fetchval(
            "SELECT close FROM ohlcv_data WHERE symbol=$1 AND time::date<=$2 "
            "ORDER BY time DESC LIMIT 1", h["symbol"], d)
        if px is None:
            continue
        px = float(px) * (1 - COST_PCT / 100)
        pnl = (px - float(h["entry_price"])) * h["quantity"]
        await tr.execute(
            "UPDATE paper2_positions SET status='CLOSED', exit_date=$1, exit_price=$2,"
            " exit_reason='RANK_DROP', realized_pnl=$3 WHERE id=$4",
            d, px, pnl, h["id"])
        n_sold += 1
    keep = [h["symbol"] for h in held if h["symbol"] not in {x["symbol"] for x in sells}]

    slots = TOP_N - len(keep)
    buyable = [r for r in ranked if r["symbol"] not in keep]

    nxt = await mk.fetchval(
        "SELECT min(time::date) FROM ohlcv_data WHERE time::date > $1", d)
    if nxt is None:
        log.info("[%s] no session after %s yet — deferring fills", book, d)
        return

    # earnings gate (combo): skip buys reporting within N days of the fill
    if cfg["earn_gate_days"] and buyable:
        blocked = {r["symbol"] for r in await mk.fetch(
            "SELECT DISTINCT symbol FROM earnings_filings "
            "WHERE symbol = ANY($1) AND broadcast_date >= $2 AND broadcast_date <= $3",
            [r["symbol"] for r in buyable], nxt,
            nxt + timedelta(days=cfg["earn_gate_days"]))}
        if blocked:
            log.info("[%s] earnings gate blocks: %s", book, sorted(blocked))
        buyable = [r for r in buyable if r["symbol"] not in blocked]
    adds = buyable[:max(0, slots)]

    # breadth-smile: halve NEW-entry exposure only inside the chop band
    exposure = 1.0
    if cfg["smile_cut"] is not None:
        b = await breadth200(mk, d)
        if b is not None and SMILE_LO <= b < SMILE_HI:
            exposure = cfg["smile_cut"]
            log.info("[%s] breadth %.1f%% in chop band -> exposure %.2f", book, b, exposure)

    cash, _, _ = await book_cash(tr, book)
    eq = await tr.fetchrow(
        "SELECT equity FROM paper2_equity WHERE book=$1 ORDER BY d DESC LIMIT 1", book)
    total_equity = float(eq["equity"]) if eq else START_CAPITAL
    capital = total_equity
    if cfg.get("etf"):
        # ETF sleeve first (its target comes off total equity), stocks get the rest
        await adjust_etf_sleeve(mk, tr, book, cfg, d, nxt, total_equity)
        cash, _, _ = await book_cash(tr, book)
        capital = total_equity * (1 - cfg["etf"]["pct"] / 100.0)
    capital *= exposure

    inv = {r["symbol"]: 1.0 / r["atr"] for r in adds if r["atr"] > 0}
    tot = sum(inv.values()) or 1.0
    n_bought = 0
    for r in adds:
        w = (inv.get(r["symbol"], 0.0) / tot) * (len(adds) / max(TOP_N, 1))
        alloc = min(capital * w, max(cash, 0.0))
        op = await mk.fetchval(
            "SELECT open FROM ohlcv_data WHERE symbol=$1 AND time::date=$2",
            r["symbol"], nxt)
        signal_px = float(r["close"])
        fill_raw = float(op if op is not None else r["close"])
        slip_bps = ((fill_raw / signal_px) - 1) * 10000 if signal_px > 0 else None
        px = fill_raw * (1 + COST_PCT / 100)
        qty = int(alloc / px)
        if qty < 1:
            continue
        await tr.execute(
            "INSERT INTO paper2_positions (book, symbol, signal_date, entry_date,"
            " entry_price, quantity, entry_rank, entry_score, atr_pct_entry,"
            " signal_price, fill_price_raw, slippage_bps)"
            " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
            book, r["symbol"], d, nxt, px, qty, rank_of[r["symbol"]] + 1,
            r["score"], r["atr"], signal_px, fill_raw, slip_bps)
        cash -= px * qty
        n_bought += 1

    await tr.execute(
        "INSERT INTO paper2_rebalance (book, rebalance_date, n_candidates, n_held,"
        " n_bought, n_sold, exposure, capital) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)"
        " ON CONFLICT (book, rebalance_date) DO NOTHING",
        book, d, len(ranked), len(keep), n_bought, n_sold, exposure, capital)
    log.info("[%s] rebalance %s: %d cand, kept %d, sold %d, bought %d (exp %.2f)",
             book, d, len(ranked), len(keep), n_sold, n_bought, exposure)


async def daily_guard(mk, tr, book: str) -> int:
    """Volatility exit, detect on 2 consecutive completed sessions, fill today open.
      recommended: static ATR > 5% both sessions  -> exit 100%
      agg/combo:   ATR > 1.5x its own 20-session mean AND close < EMA21,
                   both sessions -> trim 33% (engine OPTION A+B+C)."""
    cfg = BOOKS[book]
    dates = [r["d"] for r in await mk.fetch(
        "SELECT DISTINCT indicator_date AS d FROM stock_indicators "
        "ORDER BY indicator_date DESC LIMIT 3")]
    if len(dates) < 3:
        return 0
    today, prev, prev2 = dates[0], dates[1], dates[2]
    held = await tr.fetch(
        "SELECT * FROM paper2_positions WHERE book=$1 AND status='OPEN'", book)
    etf_sym = None
    if cfg.get("etf"):
        etf = cfg["etf"]
        etf_sym = etf["symbol"]
        stp = await etf_state(mk, etf_sym, prev, etf["ma"])
        cur = next((h for h in held if h["symbol"] == etf_sym), None)
        op = await mk.fetchval(
            "SELECT open FROM ohlcv_data WHERE symbol=$1 AND time::date=$2",
            etf_sym, today)
        if stp is not None and op is not None:
            if cur is not None and not stp["on"]:
                px = float(op) * (1 - COST_PCT / 100)
                pnl = (px - float(cur["entry_price"])) * cur["quantity"]
                await tr.execute(
                    "UPDATE paper2_positions SET status='CLOSED', exit_date=$1,"
                    " exit_price=$2, exit_reason='REGIME_OFF', realized_pnl=$3 WHERE id=$4",
                    today, px, pnl, cur["id"])
                log.info("[%s] REGIME_OFF: sold ETF %s at %.2f", book, etf_sym, px)
            elif cur is None and stp["on"]:
                eqr = await tr.fetchrow(
                    "SELECT equity FROM paper2_equity WHERE book=$1 ORDER BY d DESC LIMIT 1",
                    book)
                tv = (float(eqr["equity"]) if eqr else START_CAPITAL) * etf["pct"] / 100.0
                buy_px = float(op) * (1 + COST_PCT / 100)
                qty = int(tv / buy_px)
                if qty >= 1:
                    await tr.execute(
                        "INSERT INTO paper2_positions (book, symbol, signal_date,"
                        " entry_date, entry_price, quantity, fill_price_raw)"
                        " VALUES ($1,$2,$3,$4,$5,$6,$7)",
                        book, etf_sym, prev, today, buy_px, qty, float(op))
                    log.info("[%s] REGIME_ON: bought %d %s at %.2f", book, qty, etf_sym, buy_px)
    n = 0
    for h in held:
        sym = h["symbol"]
        if etf_sym and sym == etf_sym:
            continue
        if cfg["rel_atr_mult"] is None:
            rows = await mk.fetch(
                "SELECT indicator_date, atr_pct FROM stock_indicators "
                "WHERE symbol=$1 AND indicator_date = ANY($2)", sym, [prev, prev2])
            atrs = {r["indicator_date"]: r["atr_pct"] for r in rows}
            a1, a2 = atrs.get(prev), atrs.get(prev2)
            if a1 is None or a2 is None:
                continue
            hit = float(a1) > MAX_ATR_PCT and float(a2) > MAX_ATR_PCT
            reason = "ATR_CEILING"
        else:
            rows = await mk.fetch(
                "SELECT indicator_date, atr_pct, close, ema_21 FROM stock_indicators "
                "WHERE symbol=$1 AND indicator_date <= $2 "
                "ORDER BY indicator_date DESC LIMIT 23", sym, prev)
            if len(rows) < 22:
                continue
            def breach(idx):
                r0 = rows[idx]
                if r0["atr_pct"] is None or r0["close"] is None or r0["ema_21"] is None:
                    return False
                hist = [float(x["atr_pct"]) for x in rows[idx:idx + 21]
                        if x["atr_pct"] is not None]
                if len(hist) < 10:
                    return False
                norm = st.fmean(hist)
                return (norm > 0 and float(r0["atr_pct"]) / norm > cfg["rel_atr_mult"]
                        and float(r0["close"]) < float(r0["ema_21"]))
            hit = breach(0) and breach(1)     # prev and prev2
            reason = "ATR_TRIM" if cfg["trim_pct"] < 100 else "ATR_REL"
        if not hit:
            continue
        op = await mk.fetchval(
            "SELECT open FROM ohlcv_data WHERE symbol=$1 AND time::date=$2", sym, today)
        if op is None:
            continue
        px = float(op) * (1 - COST_PCT / 100)
        if cfg["trim_pct"] < 100 and h["quantity"] > 1:
            sell_qty = max(1, int(h["quantity"] * cfg["trim_pct"] / 100.0))
            pnl = (px - float(h["entry_price"])) * sell_qty
            await tr.execute(
                "INSERT INTO paper2_positions (book, symbol, signal_date, entry_date,"
                " entry_price, quantity, entry_rank, entry_score, atr_pct_entry,"
                " exit_date, exit_price, exit_reason, realized_pnl, status)"
                " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,'CLOSED')",
                book, sym, h["signal_date"], h["entry_date"], h["entry_price"],
                sell_qty, h["entry_rank"], h["entry_score"], h["atr_pct_entry"],
                today, px, reason, pnl)
            await tr.execute(
                "UPDATE paper2_positions SET quantity = quantity - $2 WHERE id=$1",
                h["id"], sell_qty)
            log.info("[%s] %s %s: trimmed %d at %.2f", book, reason, sym, sell_qty, px)
        else:
            pnl = (px - float(h["entry_price"])) * h["quantity"]
            await tr.execute(
                "UPDATE paper2_positions SET status='CLOSED', exit_date=$1,"
                " exit_price=$2, exit_reason=$3, realized_pnl=$4 WHERE id=$5",
                today, px, reason, pnl, h["id"])
            log.info("[%s] %s exit %s at %.2f", book, reason, sym, px)
        n += 1
    return n


async def cmd_mark(mk, tr, book: str) -> None:
    cfg = BOOKS[book]
    await daily_guard(mk, tr, book)
    ind_d = await latest_indicator_date(mk)
    mark_d = await mk.fetchval(
        "SELECT time::date FROM ohlcv_data WHERE time::date <= $1 "
        "GROUP BY time::date HAVING count(DISTINCT symbol) > 1500 "
        "ORDER BY 1 DESC LIMIT 1", ind_d) or ind_d
    # cash yield accrual (combo): one trading day per NEW mark date
    if cfg["cash_yield_pct"]:
        last_d = await tr.fetchval(
            "SELECT max(d) FROM paper2_equity WHERE book=$1", book)
        if last_d is None or mark_d > last_d:
            cash_now, _, _ = await book_cash(tr, book)
            if cash_now > 0:
                credit = cash_now * (cfg["cash_yield_pct"] / 100.0) / 252.0
                await tr.execute(
                    "UPDATE paper2_books SET cash_credit = cash_credit + $2 "
                    "WHERE book=$1", book, credit)
    open_pos = await tr.fetch(
        "SELECT * FROM paper2_positions WHERE book=$1 AND status='OPEN'", book)
    mtm = 0.0
    for p in open_pos:
        px = await mk.fetchval(
            "SELECT close FROM ohlcv_data WHERE symbol=$1 AND time::date<=$2 "
            "ORDER BY time DESC LIMIT 1", p["symbol"], mark_d)
        mtm += float(px if px is not None else p["entry_price"]) * p["quantity"]
    cash, realized, credit = await book_cash(tr, book)
    equity = cash + mtm
    peak = float(await tr.fetchval(
        "SELECT COALESCE(max(peak_equity), $2) FROM paper2_equity WHERE book=$1",
        book, START_CAPITAL))
    peak = max(peak, equity)
    dd = (equity / peak - 1) * 100 if peak > 0 else 0.0
    await tr.execute(
        "INSERT INTO paper2_equity (book, d, cash, positions_mtm, equity, n_open,"
        " peak_equity, drawdown_pct) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)"
        " ON CONFLICT (book, d) DO UPDATE SET cash=$3, positions_mtm=$4, equity=$5,"
        " n_open=$6, peak_equity=$7, drawdown_pct=$8",
        book, mark_d, cash, mtm, equity, len(open_pos), peak, dd)
    log.info("[%s] mark %s: equity Rs.%.0f (%d open, dd %.2f%%, credit %.0f)",
             book, mark_d, equity, len(open_pos), dd, credit)


async def cmd_status(tr) -> None:
    print(f"{'BOOK':<13}{'EQUITY':>12}{'RET%':>8}{'DD%':>8}{'OPEN':>6}{'CLOSED':>8}")
    for book in BOOKS:
        eq = await tr.fetchrow(
            "SELECT * FROM paper2_equity WHERE book=$1 ORDER BY d DESC LIMIT 1", book)
        n_closed = await tr.fetchval(
            "SELECT count(*) FROM paper2_positions WHERE book=$1 AND status='CLOSED'", book)
        if eq is None:
            print(f"{book:<13}{'—':>12}")
            continue
        ret = (float(eq["equity"]) / START_CAPITAL - 1) * 100
        print(f"{book:<13}{float(eq['equity']):>12,.0f}{ret:>8.2f}"
              f"{float(eq['drawdown_pct']):>8.2f}{eq['n_open']:>6}{n_closed:>8}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--rebalance", action="store_true")
    ap.add_argument("--mark", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--as-of", type=str, default=None)
    ap.add_argument("--book", choices=list(BOOKS), default=None)
    a = ap.parse_args()
    books = [a.book] if a.book else list(BOOKS)
    as_of = date.fromisoformat(a.as_of) if a.as_of else None

    mk = await asyncpg.connect(MARKET_DSN)
    tr = await asyncpg.connect(TRADE_DSN)
    try:
        if a.init:
            await cmd_init(mk, tr)
        if a.rebalance:
            for b in books:
                await cmd_rebalance(mk, tr, b, force=a.force, as_of=as_of)
                await cmd_mark(mk, tr, b)
        if a.mark and not a.rebalance:
            for b in books:
                await cmd_mark(mk, tr, b)
        if a.status:
            await cmd_status(tr)
    finally:
        await mk.close()
        await tr.close()

asyncio.run(main())
