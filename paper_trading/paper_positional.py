#!/usr/bin/env python3
"""Paper trading for the POSITIONAL momentum strategy (config #823).

WHY THIS EXISTS
  Every backtest number in this programme is survivorship-biased by an estimated
  3-7 CAGR points, and that bias cannot be measured from our own data (the DB
  contains 0 of 269 wipeout delistings). Forward paper trading is the ONLY
  survivorship-free evidence available, and it accrues from today.

CONFIG (engine run #823, 2017-2026: 22.16% CAGR / 19.74% MaxDD / Calmar 1.12)
  composite_rs rank = z(12-1) + z(6m) + z(3m) + z(6m/ATR) + z(-base_range_20d)
  gates: IFP >= 0.40, close >= Rs.20, turnover >= Rs.8cr, ATR <= 5%, close > SMA200
  N=30, exit when rank >= 60 (concentric band), rebalance every 21 sessions,
  inverse-volatility sizing, NO stop loss (measured harmful).

USAGE
  python3 paper_positional.py --init            # create tables, seed capital
  python3 paper_positional.py --rebalance       # run a rebalance (21-day cadence)
  python3 paper_positional.py --mark            # daily mark-to-market
  python3 paper_positional.py --status          # print the book + kill criteria
  python3 paper_positional.py --validate DATE   # reproduce the engine's picks

SAFETY: reads market_data read-only; writes only to the paper_* tables in
trading_platform. It never places an order and never touches the live screener.
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
log = logging.getLogger("paper")

MARKET_DSN = os.getenv("MARKET_DSN",
                       "postgresql://postgres:postgres@localhost:5432/market_data")
TRADE_DSN = os.getenv("DATABASE_URL",
                      "postgresql://postgres:postgres@localhost:5432/trading_platform")

# ---- config #823 (do not drift from the validated engine config) ------------
TOP_N = 30
BUFFER_N = 60            # exit when rank >= this (concentric banding)
REBALANCE_DAYS = 21
MIN_IFP = 0.40
MIN_CLOSE = 20.0
MIN_TURNOVER_CR = 8.0
MAX_ATR_PCT = 5.0
BASE_RANGE_W = 1.0
START_CAPITAL = 400_000.0
COST_PCT = 0.32          # per leg, matches the backtest friction model

RANK_SQL = f"""
    SELECT symbol, close, sma_200, atr_pct, base_range_20d_pct,
           pct_chg_1m, pct_chg_3m, pct_chg_6m, pct_chg_1y
    FROM stock_indicators
    WHERE indicator_date = $1
      AND turnover_1m_avg_cr >= {MIN_TURNOVER_CR}
      AND close > sma_200
      AND pct_chg_1y IS NOT NULL AND pct_chg_6m IS NOT NULL
      AND pct_chg_3m IS NOT NULL
      AND atr_pct IS NOT NULL AND atr_pct > 0 AND atr_pct <= {MAX_ATR_PCT}
      AND ifp_score >= {MIN_IFP}
      AND close >= {MIN_CLOSE}
"""


def score_composite(rows: list) -> list:
    """Mirror of positional_engine._score_composite. Cross-sectional z-scores
    computed WITHIN this day's candidate set only (never full history, which
    would leak the future). Clipped to +/-3, missing base_range neutralised at
    the cross-sectional mean."""
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
            recs.append({"symbol": r["symbol"], "close": float(r["close"]),
                         "atr": atr, "f": f})
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    if len(recs) < 5:
        return []
    vals = [x["f"][4] for x in recs if x["f"][4] is not None]
    mu5 = st.fmean(vals) if vals else 0.0
    for x in recs:
        if x["f"][4] is None:
            x["f"][4] = mu5
    weights = [1.0, 1.0, 1.0, 1.0, BASE_RANGE_W]
    stats = []
    for i in range(5):
        col = [x["f"][i] for x in recs]
        mu = st.fmean(col)
        sd = st.pstdev(col) or 1.0
        stats.append((mu, sd))
    for x in recs:
        s = 0.0
        for i, (mu, sd) in enumerate(stats):
            z = max(-3.0, min(3.0, (x["f"][i] - mu) / sd))
            s += weights[i] * z
        x["score"] = s
    recs.sort(key=lambda z: -z["score"])
    return recs


async def latest_indicator_date(mk, on_or_before: date | None = None) -> date | None:
    if on_or_before:
        return await mk.fetchval(
            "SELECT max(indicator_date) FROM stock_indicators WHERE indicator_date <= $1",
            on_or_before)
    return await mk.fetchval("SELECT max(indicator_date) FROM stock_indicators")


async def cmd_init(mk, tr) -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "schema.sql")) as f:
        await tr.execute(f.read())
    seeded = await tr.fetchval("SELECT count(*) FROM paper_equity")
    if not seeded:
        d = await latest_indicator_date(mk)
        await tr.execute(
            "INSERT INTO paper_equity (d, cash, positions_mtm, equity, n_open, "
            "peak_equity, drawdown_pct) VALUES ($1,$2,0,$2,0,$2,0) "
            "ON CONFLICT (d) DO NOTHING", d, START_CAPITAL)
        log.info("seeded paper book at %s with Rs.%.0f", d, START_CAPITAL)
    log.info("paper tables ready")


async def cmd_validate(mk, on: date) -> None:
    """Reproduce the ranking for a historical date so the standalone selector can
    be checked against what the engine actually picked."""
    d = await latest_indicator_date(mk, on)
    rows = await mk.fetch(RANK_SQL, d)
    ranked = score_composite(rows)
    print(f"indicator_date {d}: {len(rows)} candidates -> top {TOP_N}")
    for i, r in enumerate(ranked[:TOP_N], 1):
        print(f"  {i:>3}. {r['symbol']:<14} score {r['score']:>7.3f} "
              f"close {r['close']:>9.2f} atr {r['atr']:.2f}")


async def cmd_rebalance(mk, tr, force: bool = False, as_of: date | None = None) -> None:
    # as_of lets the book be seeded from a past ranking date whose NEXT session
    # already exists (Dhan publishes a day's candle the following morning, so
    # "today" normally has no fill session yet).
    d = await latest_indicator_date(mk, as_of)
    last = await tr.fetchval("SELECT max(rebalance_date) FROM paper_rebalance")
    if last and not force:
        sess = await mk.fetchval(
            "SELECT count(DISTINCT time::date) FROM ohlcv_data "
            "WHERE time::date > $1 AND time::date <= $2", last, d)
        if (sess or 0) < REBALANCE_DAYS:
            log.info("only %s sessions since %s (need %s) — skipping",
                     sess, last, REBALANCE_DAYS)
            return

    rows = await mk.fetch(RANK_SQL, d)
    ranked = score_composite(rows)
    if not ranked:
        log.warning("no candidates on %s — aborting", d)
        return
    rank_of = {r["symbol"]: i for i, r in enumerate(ranked)}
    by_sym = {r["symbol"]: r for r in ranked}

    held = await tr.fetch("SELECT * FROM paper_positions WHERE status='OPEN'")
    held_syms = [h["symbol"] for h in held]

    # SELL: fell outside the concentric band (or out of the ranked set entirely)
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
            "UPDATE paper_positions SET status='CLOSED', exit_date=$1, exit_price=$2,"
            " exit_reason='RANK_DROP', realized_pnl=$3 WHERE id=$4",
            d, px, pnl, h["id"])
        n_sold += 1
    keep = [s for s in held_syms if s not in {h["symbol"] for h in sells}]

    # BUY: fill free slots from the top of the ranking
    slots = TOP_N - len(keep)
    adds = [r for r in ranked if r["symbol"] not in keep][:max(0, slots)]

    eq = await tr.fetchrow("SELECT * FROM paper_equity ORDER BY d DESC LIMIT 1")
    capital = float(eq["equity"]) if eq else START_CAPITAL
    # inverse-volatility sizing across the FULL book, normalised over top_n slots
    # FILL AT THE NEXT SESSION'S OPEN, not this session's close. Validated against
    # engine run #823: its fills dated 2026-08-04 correspond to a ranking on
    # 2026-08-03 (21/21 of its buys sat at rank <=28 of this selector that day).
    # A close-triggered rule cannot be executed at the close that revealed it.
    nxt = await mk.fetchval(
        "SELECT min(time::date) FROM ohlcv_data WHERE time::date > $1", d)
    if nxt is None:
        log.info("no session after %s yet — deferring fills to the next run", d)
        return

    inv = {r["symbol"]: 1.0 / r["atr"] for r in adds if r["atr"] > 0}
    tot = sum(inv.values()) or 1.0
    n_bought = 0
    for r in adds:
        w = (inv.get(r["symbol"], 0.0) / tot) * (len(adds) / max(TOP_N, 1))
        alloc = capital * w
        op = await mk.fetchval(
            "SELECT open FROM ohlcv_data WHERE symbol=$1 AND time::date=$2",
            r["symbol"], nxt)
        # Three distinct prices, stored separately so execution quality is
        # measurable. entry_price alone (cost baked in) cannot answer "what did
        # the overnight gap between signal and fill actually cost us".
        signal_px = float(r["close"])            # close on the ranking day
        fill_raw = float(op if op is not None else r["close"])   # next open
        slip_bps = ((fill_raw / signal_px) - 1) * 10000 if signal_px > 0 else None
        px = fill_raw * (1 + COST_PCT / 100)     # what the book is charged
        qty = int(alloc / px)
        if qty < 1:
            continue
        await tr.execute(
            "INSERT INTO paper_positions (symbol, entry_date, entry_price, quantity,"
            " entry_rank, entry_score, atr_pct_entry, signal_price, fill_price_raw,"
            " slippage_bps, signal_date)"
            " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)"
            " ON CONFLICT (symbol, entry_date) DO NOTHING",
            r["symbol"], nxt, px, qty, rank_of[r["symbol"]] + 1, r["score"], r["atr"],
            signal_px, fill_raw, slip_bps, d)
        n_bought += 1

    await tr.execute(
        "INSERT INTO paper_rebalance (rebalance_date, n_candidates, n_held,"
        " n_bought, n_sold, capital) VALUES ($1,$2,$3,$4,$5,$6)"
        " ON CONFLICT (rebalance_date) DO NOTHING",
        d, len(ranked), len(keep), n_bought, n_sold, capital)
    log.info("rebalance %s: %d candidates, kept %d, sold %d, bought %d",
             d, len(ranked), len(keep), n_sold, n_bought)
    await cmd_mark(mk, tr)


async def atr_ceiling_guard(mk, tr) -> int:
    """Daily ATR ceiling with 2-SESSION PERSISTENCE — mirrors the engine.

    This is the strategy's largest exit mechanism (56% of all exits in run
    #823), so the paper book must reproduce it exactly or it is testing a
    different strategy.

    PERSISTENCE (adopted 2026-08-19): exit only when the ATR ceiling is breached
    on TWO CONSECUTIVE sessions. Post-exit forensics showed the single-breach
    rule was amputating winners — names sold while up went on to beat the market
    by +4.81%, and those sold up >50% by +14.01%. A one-day volatility spike on a
    parabolic winner is noise; a two-day expansion is a real breakdown.
    Validated in all four windows (Calmar +0.06/+0.15/+0.16/+0.05).
    Three sessions scored better in-sample (1.46) but FAILED out-of-sample
    (0.45 vs 0.51) — do not raise this without re-running 2011-2016.

    Detect on day D, fill at D+1 open.
    """
    dates = [r["d"] for r in await mk.fetch(
        "SELECT DISTINCT indicator_date AS d FROM stock_indicators "
        "ORDER BY indicator_date DESC LIMIT 3")]
    if len(dates) < 3:
        return 0
    today, prev, prev2 = dates[0], dates[1], dates[2]
    held = await tr.fetch("SELECT * FROM paper_positions WHERE status='OPEN'")
    n = 0
    for h in held:
        rows = await mk.fetch(
            "SELECT indicator_date, atr_pct FROM stock_indicators "
            "WHERE symbol=$1 AND indicator_date = ANY($2)",
            h["symbol"], [prev, prev2])
        atrs = {r["indicator_date"]: r["atr_pct"] for r in rows}
        a1, a2 = atrs.get(prev), atrs.get(prev2)
        # both sessions must breach; a single spike is ignored
        if a1 is None or a2 is None:
            continue
        if not (float(a1) > MAX_ATR_PCT and float(a2) > MAX_ATR_PCT):
            continue
        atr = a1
        op = await mk.fetchval(
            "SELECT open FROM ohlcv_data WHERE symbol=$1 AND time::date=$2",
            h["symbol"], today)
        if op is None:
            continue
        px = float(op) * (1 - COST_PCT / 100)
        pnl = (px - float(h["entry_price"])) * h["quantity"]
        await tr.execute(
            "UPDATE paper_positions SET status='CLOSED', exit_date=$1, exit_price=$2,"
            " exit_reason='ATR_CEILING', realized_pnl=$3 WHERE id=$4",
            today, px, pnl, h["id"])
        log.info("ATR_CEILING exit %s (atr %.2f%% > %.1f%%) at %.2f",
                 h["symbol"], float(atr), MAX_ATR_PCT, px)
        n += 1
    return n


async def collect_telemetry(mk, tr, d) -> dict:
    """Daily pipeline-integrity checks.

    Every one of these exists because of a failure mode already observed in this
    system, not as generic hygiene:
      * missing quote  -> cmd_mark used to fall back to ENTRY price, so a
        suspended or delisted holding would freeze at cost and the equity curve
        would look healthy. Silent, and exactly the case that matters most.
      * price mismatch -> stock_indicators and ohlcv_data disagree >5% for 4.4%
        of selected positions (measured 2026-08-19). A split or bonus re-adjusts
        ohlcv while the stored entry price does not, producing a phantom loss.
      * extreme move   -> a >20% single-session move on a held name is far more
        often a corporate action than a real print.
      * staleness      -> if the 19:15 pipeline does not run, marks silently
        repeat yesterday and the book looks calm while it is actually blind.
    """
    alerts: list[str] = []
    ind_d = await mk.fetchval("SELECT max(indicator_date) FROM stock_indicators")
    ohlc_d = await mk.fetchval("SELECT max(time::date) FROM ohlcv_data")
    stale = (date.today() - ind_d).days if ind_d else None
    if stale is not None and stale > 4:
        alerts.append(f"STALE DATA: indicators {stale}d old")

    # The newest ohlcv date is NOT a usable mark date. update_today.py posts
    # NIFTY-500 same-day (~499 symbols) while update_ohlcv.py backfills the full
    # ~2,900 the next morning. Marking on the newest date therefore prices a few
    # large caps at today and everything else at yesterday -- a mixed-date
    # snapshot that would show only NIFTY-500 names reacting on a big move day.
    # So marks use the newest FULLY-COVERED session instead.
    mark_d = await mk.fetchval(
        "SELECT time::date FROM ohlcv_data WHERE time::date <= $1 "
        "GROUP BY time::date HAVING count(DISTINCT symbol) > 1500 "
        "ORDER BY 1 DESC LIMIT 1", ind_d)
    prev = await mk.fetchval(
        "SELECT time::date FROM ohlcv_data WHERE time::date < $1 "
        "GROUP BY time::date HAVING count(DISTINCT symbol) > 1500 "
        "ORDER BY 1 DESC LIMIT 1", mark_d)

    open_pos = await tr.fetch("SELECT * FROM paper_positions WHERE status='OPEN'")
    n_quoted = n_missing = n_mismatch = n_extreme = 0
    worst_sym, worst_pct = None, 0.0
    for p in open_pos:
        sym = p["symbol"]
        px = await mk.fetchval(
            "SELECT close FROM ohlcv_data WHERE symbol=$1 AND time::date=$2",
            sym, mark_d)
        if px is None:
            # genuinely absent on a fully-covered session = suspended/halted/
            # delisted, which is the case worth waking someone for
            n_missing += 1
            alerts.append(f"NO QUOTE on full session {mark_d}: {sym}")
            continue
        n_quoted += 1
        # indicators vs ohlcv divergence on the SAME date -> adjustment drift
        icl = await mk.fetchval(
            "SELECT close FROM stock_indicators WHERE symbol=$1 AND indicator_date=$2",
            sym, mark_d)
        if icl and float(px) > 0 and abs(float(icl) - float(px)) / float(px) > 0.05:
            n_mismatch += 1
            alerts.append(f"PRICE MISMATCH: {sym} ind={float(icl):.2f} ohlcv={float(px):.2f}")
        # one-session jump -> probable corporate action
        if prev:
            ppx = await mk.fetchval(
                "SELECT close FROM ohlcv_data WHERE symbol=$1 AND time::date=$2",
                sym, prev)
            if ppx and float(ppx) > 0:
                mv = (float(px) / float(ppx) - 1) * 100
                if abs(mv) > abs(worst_pct):
                    worst_sym, worst_pct = sym, mv
                if abs(mv) > 20:
                    n_extreme += 1
                    alerts.append(f"CORP ACTION? {sym} moved {mv:+.1f}% in one session")

    n_cand = await mk.fetchval(
        f"SELECT count(*) FROM stock_indicators WHERE indicator_date=$1 "
        f"AND turnover_1m_avg_cr >= {MIN_TURNOVER_CR} AND close > sma_200 "
        f"AND atr_pct IS NOT NULL AND atr_pct <= {MAX_ATR_PCT} "
        f"AND ifp_score >= {MIN_IFP} AND close >= {MIN_CLOSE}", ind_d)
    if n_cand is not None and n_cand < 60:
        alerts.append(f"THIN UNIVERSE: only {n_cand} candidates")
    if open_pos and n_quoted == 0:
        alerts.append("NO HOLDINGS QUOTED — mark is meaningless today")

    return {"mark_date": mark_d,
            "indicator_date": ind_d, "ohlcv_date": ohlc_d, "staleness_days": stale,
            "pipeline_ok": bool(stale is not None and stale <= 4 and n_missing == 0),
            "n_open": len(open_pos), "n_quoted": n_quoted,
            "n_missing_quote": n_missing, "n_price_mismatch": n_mismatch,
            "n_extreme_move": n_extreme, "worst_move_symbol": worst_sym,
            "worst_move_pct": round(worst_pct, 2), "n_candidates": n_cand,
            "alerts": " | ".join(alerts[:12])}


async def cmd_mark(mk, tr) -> None:
    await atr_ceiling_guard(mk, tr)
    d = await latest_indicator_date(mk)
    tel = await collect_telemetry(mk, tr, d)
    # price the whole book on ONE fully-covered session, so the snapshot is
    # internally consistent rather than a mix of today and yesterday
    d = tel["mark_date"] or d
    open_pos = await tr.fetch("SELECT * FROM paper_positions WHERE status='OPEN'")
    mtm = 0.0
    for p in open_pos:
        px = await mk.fetchval(
            "SELECT close FROM ohlcv_data WHERE symbol=$1 AND time::date<=$2 "
            "ORDER BY time DESC LIMIT 1", p["symbol"], d)
        # falling back to ENTRY price would hide a dead holding entirely; the
        # telemetry above counts these so a frozen mark is visible, not silent
        mtm += float(px if px is not None else p["entry_price"]) * p["quantity"]
    cost_basis = sum(float(p["entry_price"]) * p["quantity"] for p in open_pos)
    realized = float(await tr.fetchval(
        "SELECT COALESCE(sum(realized_pnl),0) FROM paper_positions WHERE status='CLOSED'"))
    cash = START_CAPITAL + realized - cost_basis
    equity = cash + mtm
    peak = float(await tr.fetchval(
        "SELECT COALESCE(max(peak_equity), $1) FROM paper_equity", START_CAPITAL))
    peak = max(peak, equity)
    dd = (equity / peak - 1) * 100 if peak > 0 else 0.0
    await tr.execute(
        "INSERT INTO paper_equity (d, cash, positions_mtm, equity, n_open,"
        " peak_equity, drawdown_pct) VALUES ($1,$2,$3,$4,$5,$6,$7)"
        " ON CONFLICT (d) DO UPDATE SET cash=$2, positions_mtm=$3, equity=$4,"
        " n_open=$5, peak_equity=$6, drawdown_pct=$7",
        d, cash, mtm, equity, len(open_pos), peak, dd)
    await tr.execute(
        """
        INSERT INTO paper_telemetry
          (d, indicator_date, ohlcv_date, staleness_days, pipeline_ok, n_open,
           n_quoted, n_missing_quote, n_price_mismatch, n_extreme_move,
           worst_move_symbol, worst_move_pct, n_candidates, equity,
           drawdown_pct, alerts)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
        ON CONFLICT (d) DO UPDATE SET
          indicator_date=$2, ohlcv_date=$3, staleness_days=$4, pipeline_ok=$5,
          n_open=$6, n_quoted=$7, n_missing_quote=$8, n_price_mismatch=$9,
          n_extreme_move=$10, worst_move_symbol=$11, worst_move_pct=$12,
          n_candidates=$13, equity=$14, drawdown_pct=$15, alerts=$16
        """,
        d, tel["indicator_date"], tel["ohlcv_date"], tel["staleness_days"],
        tel["pipeline_ok"], tel["n_open"], tel["n_quoted"], tel["n_missing_quote"],
        tel["n_price_mismatch"], tel["n_extreme_move"], tel["worst_move_symbol"],
        tel["worst_move_pct"], tel["n_candidates"], equity, dd, tel["alerts"])
    log.info("mark %s: equity Rs.%.0f (%d open, dd %.2f%%)", d, equity, len(open_pos), dd)
    log.info("  telemetry: %d/%d quoted, %d mismatch, %d extreme, %d candidates, stale %sd",
             tel["n_quoted"], tel["n_open"], tel["n_price_mismatch"],
             tel["n_extreme_move"], tel["n_candidates"], tel["staleness_days"])
    if tel["alerts"]:
        log.warning("  ALERTS: %s", tel["alerts"])


# ---- PRE-REGISTERED KILL CRITERIA -------------------------------------------
# Registered BEFORE any forward data exists, so they cannot be rationalised away
# later. Breaching one is a signal to stop and re-examine, not an automatic halt.
KILL = [
    ("MaxDD > 35%", lambda m: m["dd"] < -35,
     "recalibrated 2026-08-19: the date-aware survivorship model shows honest "
     "history would itself have hit 31.1%, so a 30% trigger fires on normal "
     "behaviour and would train us to override it"),
    ("6-month return < -20%", lambda m: m["r6m"] is not None and m["r6m"] < -20,
     "sustained loss beyond backtest experience"),
    ("12-month return < 0 while Nifty 50 > +10%", lambda m: False,
     "manual check — strategy failing while the market works"),
    ("< 15 positions held for 3 consecutive rebalances", lambda m: m["n_open"] < 15,
     "universe has thinned; gates may be mis-specified for current conditions"),
]


async def cmd_status(mk, tr) -> None:
    eq = await tr.fetch("SELECT * FROM paper_equity ORDER BY d")
    if not eq:
        print("no paper equity history yet — run --init then --rebalance")
        return
    cur = eq[-1]
    first = eq[0]
    days = (cur["d"] - first["d"]).days or 1
    tot = float(cur["equity"]) / float(first["equity"]) - 1
    cagr = ((1 + tot) ** (365.25 / days) - 1) * 100 if days > 30 else None
    r6 = None
    six = [e for e in eq if (cur["d"] - e["d"]).days >= 180]
    if six:
        r6 = (float(cur["equity"]) / float(six[-1]["equity"]) - 1) * 100

    print("=" * 68)
    print("PAPER BOOK — POSITIONAL momentum (config #823)")
    print("=" * 68)
    print(f"  since            : {first['d']}  ({days} days)")
    print(f"  equity           : Rs.{float(cur['equity']):,.0f}")
    print(f"  total return     : {tot*100:+.2f}%")
    print(f"  CAGR (annualised): {f'{cagr:+.2f}%' if cagr else 'n/a (<30d)'}")
    print(f"  drawdown now     : {float(cur['drawdown_pct']):.2f}%")
    print(f"  max drawdown     : {min(float(e['drawdown_pct']) for e in eq):.2f}%")
    print(f"  open positions   : {cur['n_open']}")

    m = {"dd": min(float(e["drawdown_pct"]) for e in eq),
         "r6m": r6, "n_open": cur["n_open"] or 0}
    t = await tr.fetchrow("SELECT * FROM paper_telemetry ORDER BY d DESC LIMIT 1")
    if t:
        print("\n  PIPELINE TELEMETRY (latest)")
        flag = "ok" if t["pipeline_ok"] else "CHECK"
        print(f"    [{flag:>5}] indicators {t['indicator_date']} "
              f"({t['staleness_days']}d old) · ohlcv {t['ohlcv_date']}")
        print(f"    holdings quoted     : {t['n_quoted']}/{t['n_open']}"
              f"{'  <-- STALE MARKS' if t['n_missing_quote'] else ''}")
        print(f"    price mismatches    : {t['n_price_mismatch']}"
              f"{'  <-- adjustment drift' if t['n_price_mismatch'] else ''}")
        print(f"    extreme 1d moves    : {t['n_extreme_move']}"
              f" (worst {t['worst_move_symbol']} {t['worst_move_pct']}%)")
        print(f"    candidates today    : {t['n_candidates']}")
        if t["alerts"]:
            print(f"    ALERTS: {t['alerts']}")

    sl = await tr.fetchrow(
        "SELECT count(slippage_bps) n, round(avg(slippage_bps),1) avg_bps,"
        " round(min(slippage_bps),1) best, round(max(slippage_bps),1) worst"
        " FROM paper_positions WHERE slippage_bps IS NOT NULL")
    if sl and sl["n"]:
        print(f"\n  EXECUTION SLIPPAGE (signal close -> next open, {sl['n']} fills)")
        print(f"    mean {sl['avg_bps']} bps · range {sl['best']} to {sl['worst']} bps")
        print(f"    modelled cost is 32 bps/leg — compare against the mean above")

    print("\n  PRE-REGISTERED KILL CRITERIA")
    for name, fn, why in KILL:
        try:
            hit = fn(m)
        except Exception:  # noqa: BLE001
            hit = False
        print(f"    [{'BREACH' if hit else '  ok  '}] {name}  — {why}")

    rows = await tr.fetch(
        "SELECT symbol, entry_date, entry_price, quantity, entry_rank"
        " FROM paper_positions WHERE status='OPEN' ORDER BY entry_rank")
    if rows:
        print(f"\n  HOLDINGS ({len(rows)})")
        for r in rows:
            px = await mk.fetchval(
                "SELECT close FROM ohlcv_data WHERE symbol=$1 ORDER BY time DESC LIMIT 1",
                r["symbol"])
            pnl = ((float(px) / float(r["entry_price"]) - 1) * 100) if px else 0.0
            print(f"    {r['symbol']:<14} rank {r['entry_rank']:>3}  "
                  f"in {r['entry_date']}  {pnl:+7.2f}%")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--rebalance", action="store_true")
    ap.add_argument("--force", action="store_true", help="rebalance ignoring cadence")
    ap.add_argument("--mark", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--validate", type=lambda s: date.fromisoformat(s))
    ap.add_argument("--as-of", dest="as_of", type=lambda s: date.fromisoformat(s),
                    help="rank as of this date instead of the latest available")
    a = ap.parse_args()

    mk = await asyncpg.connect(MARKET_DSN)
    tr = await asyncpg.connect(TRADE_DSN)
    try:
        if a.init:
            await cmd_init(mk, tr)
        if a.validate:
            await cmd_validate(mk, a.validate)
        if a.rebalance:
            await cmd_rebalance(mk, tr, force=a.force, as_of=a.as_of)
        if a.mark:
            await cmd_mark(mk, tr)
        if a.status:
            await cmd_status(mk, tr)
    finally:
        await mk.close()
        await tr.close()


if __name__ == "__main__":
    asyncio.run(main())
